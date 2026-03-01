"""
run_research_harness.py (trading_dashboard)

Canonical KPI research harness for the `PRIVATE/trading_dashboard/` repo.

It runs fully offline against enriched CSVs placed under:
  data/feature_store/enriched/<dataset>/stock_data/

Outputs are written under:
  data/research_runs/harness/<run_id>/

Config:
  research/harness/config/config_research_harness_sample_100_1W.json
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

import itertools
import random


SCRIPT_DIR = Path(__file__).resolve().parent
HARNESS_DIR = SCRIPT_DIR.parent  # .../research/harness
RESEARCH_DIR = HARNESS_DIR.parent  # .../research
TRADING_DIR = RESEARCH_DIR.parent  # .../PRIVATE/trading_dashboard (repo root)

# Ensure repo root is importable when launched from anywhere.
import sys  # noqa: E402

if str(TRADING_DIR) not in sys.path:
    sys.path.insert(0, str(TRADING_DIR))

from trading_dashboard.kpis.catalog import KPI_ORDER, compute_kpi_state_map  # noqa: E402


DEFAULT_CONFIG_JSON = HARNESS_DIR / "config" / "config_research_harness_sample_100_1W.json"
DEFAULT_INPUT_STOCK_DATA_DIR = TRADING_DIR / "data" / "feature_store" / "enriched" / "sample_100" / "stock_data"
RUNS_DIR = TRADING_DIR / "data" / "research_runs" / "harness"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_path_component(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return "_"
    out: list[str] = []
    for ch in s:
        if ch.isalnum() or ch in ("-", "_", ".", "@"):
            out.append(ch)
        else:
            out.append("_")
    return "".join(out)


def _read_json(path: Path) -> dict:
    try:
        if path.exists() and path.stat().st_size > 0:
            obj = json.loads(path.read_text(encoding="utf-8"))
            return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}
    return {}


def _resolve_trading_relative(p: str) -> Path:
    s = str(p or "").strip()
    pp = Path(s).expanduser()
    if pp.is_absolute():
        return pp

    # Accept both styles:
    # - "research/harness/..." (relative to repo root)
    # - "PRIVATE/trading_dashboard/..." (relative to workspace root)
    s2 = s.replace("\\", "/")
    if s2.startswith("PRIVATE/"):
        workspace_dir = TRADING_DIR.parents[1]
        return (workspace_dir / pp).resolve()

    return (TRADING_DIR / pp).resolve()


def _load_enriched_csv(stock_data_dir: Path, symbol: str, tf: str) -> pd.DataFrame:
    p = stock_data_dir / f"{symbol}_{tf}.csv"
    if not p.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(p, parse_dates=[0], index_col=0)
    except Exception:
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    df.index = pd.to_datetime(df.index, errors="coerce").tz_localize(None)
    return df.sort_index()


def _normal_sf(z: float) -> float:
    # Survival function for standard normal using erfc
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def _one_sided_binom_pvalue(*, n: int, k: int, p0: float, greater: bool) -> float | None:
    # Normal approximation; returns None if invalid.
    if n <= 0:
        return None
    if not (0.0 < p0 < 1.0):
        return None
    phat = float(k) / float(n)
    denom = math.sqrt(p0 * (1.0 - p0) / float(n))
    if denom <= 0:
        return None
    z = (phat - p0) / denom
    if greater:
        return float(_normal_sf(z))
    # P(X <= k) approx = SF(-z)
    return float(_normal_sf(-z))


def _bh_fdr(pvals: list[float | None]) -> list[float | None]:
    # Benjamini-Hochberg q-values; preserves None.
    idx = [(i, p) for i, p in enumerate(pvals) if p is not None and math.isfinite(float(p))]
    if not idx:
        return [None for _ in pvals]
    idx.sort(key=lambda t: float(t[1]))
    m = len(idx)
    q = [None for _ in pvals]
    prev = 1.0
    for rank in range(m, 0, -1):
        i, p = idx[rank - 1]
        val = min(prev, float(p) * float(m) / float(rank))
        prev = val
        q[i] = float(val)
    return q


def _forward_window_extrema(arr: np.ndarray, *, h: int, mode: str) -> np.ndarray:
    """
    Compute forward-looking extrema over the next h bars (inclusive of t+1..t+h).
    mode: "min" or "max"
    Returns array length n with NaNs where unavailable.
    """
    n = int(arr.size)
    out = np.full(n, np.nan, dtype=float)
    if h <= 0 or n == 0:
        return out
    for i in range(n):
        j0 = i + 1
        j1 = min(n, i + h + 1)
        if j0 >= j1:
            continue
        w = arr[j0:j1]
        if mode == "min":
            out[i] = float(np.nanmin(w))
        else:
            out[i] = float(np.nanmax(w))
    return out


@dataclass
class _Agg:
    n: int = 0
    n_win: int = 0
    ret_sum: float = 0.0
    ret_vals: list[float] | None = None  # for quantiles
    mae_vals: list[float] | None = None  # adverse excursion (as return)

    def add(self, ret: np.ndarray, win: np.ndarray, mae: np.ndarray) -> None:
        mask = np.isfinite(ret) & np.isfinite(mae)
        if not np.any(mask):
            return
        r = ret[mask]
        w = win[mask]
        m = mae[mask]
        self.n += int(r.size)
        self.n_win += int(np.sum(w))
        self.ret_sum += float(np.sum(r))
        if self.ret_vals is None:
            self.ret_vals = []
        if self.mae_vals is None:
            self.mae_vals = []
        self.ret_vals.extend([float(x) for x in r.tolist()])
        self.mae_vals.extend([float(x) for x in m.tolist()])

    def finalize(self) -> dict:
        if self.n <= 0:
            return {"n": 0}
        wr = float(self.n_win) / float(self.n)
        mean_ret = float(self.ret_sum) / float(self.n)
        out = {"n": int(self.n), "n_win": int(self.n_win), "win_rate": wr, "mean_return": mean_ret}
        if self.ret_vals:
            vv = np.asarray(self.ret_vals, dtype=float)
            out["median_return"] = float(np.nanmedian(vv))
            out["p05_return"] = float(np.nanpercentile(vv, 5))
            out["p95_return"] = float(np.nanpercentile(vv, 95))
        if self.mae_vals:
            mm = np.asarray(self.mae_vals, dtype=float)
            out["mae_p95"] = float(np.nanpercentile(mm, 95))
            out["mae_max"] = float(np.nanmax(mm))
        return out


def _years_covered(idx: pd.DatetimeIndex) -> float:
    if idx is None or len(idx) < 2:
        return 0.0
    dt = (pd.to_datetime(idx.max()) - pd.to_datetime(idx.min())).total_seconds()
    return max(0.0, float(dt) / (365.25 * 24 * 3600.0))


def _combo_name(items: Iterable[str]) -> str:
    return " & ".join([str(x) for x in items])


def _indicator_config_path_from_run_metadata(run_metadata_path: Path | None) -> Path | None:
    if run_metadata_path is None or (not run_metadata_path.exists()):
        return None
    meta = _read_json(run_metadata_path)
    p = str(meta.get("indicator_config_json") or "").strip()
    if not p:
        return None
    try:
        pp = Path(p).expanduser()
        if pp.exists():
            return pp.resolve()
        # Backwards-compatible fallback: optimised indicator_config JSONs were moved under research/.
        if str(pp.name or "").startswith("indicator_config_optimised_"):
            alt = TRADING_DIR / "research" / "indicator_config_optimiser" / "configs" / pp.name
            if alt.exists():
                return alt.resolve()
    except Exception:
        return None
    return None


def _nwe_is_non_repainting(indicator_config_path: Path | None) -> bool:
    """
    Return True if config explicitly forces all present NWE envelope variants to be non-repainting.
    If config is missing/unreadable or value not present, return False (conservative).
    """
    if indicator_config_path is None or (not indicator_config_path.exists()):
        return False
    cfg = _read_json(indicator_config_path)
    try:
        if not isinstance(cfg, dict):
            return False
        keys = ["NWE_Envelope_MAE", "NWE_Envelope_STD", "NWE_Envelope"]  # include legacy key
        found_any = False
        for k in keys:
            entry = cfg.get(k)
            if not isinstance(entry, dict):
                continue
            params = entry.get("params", entry)
            if not isinstance(params, dict):
                continue
            if "repaint" not in params:
                continue
            found_any = True
            repaint = params.get("repaint")
            if repaint is True:
                return False
            if repaint is not False:
                # unknown / non-bool => conservative
                return False
        return bool(found_any)
    except Exception:
        return False


def _infer_symbols_from_stock_data_dir(stock_data_dir: Path, tf: str) -> list[str]:
    out: list[str] = []
    for p in sorted(stock_data_dir.glob(f"*_{tf}.csv")):
        name = p.name
        suf = f"_{tf}.csv"
        if name.endswith(suf):
            out.append(name[: -len(suf)])
    # De-dupe preserving order
    seen = set()
    dedup: list[str] = []
    for s in out:
        if s in seen:
            continue
        seen.add(s)
        dedup.append(s)
    return dedup


def _evaluate_for_tf(
    *,
    tf: str,
    symbols: list[str],
    horizons: list[int],
    time_slices: int,
    allow_repainting: bool,
    combo_max_k: int,
    combo_candidate_kpis: int,
    combo_max_per_k: int,
    combo_min_occurrences: int,
    stock_data_dir: Path,
    out_dir: Path,
    run_metadata_path: Path | None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "plots").mkdir(parents=True, exist_ok=True)

    # Collect per-symbol frames + states
    sym_frames: dict[str, pd.DataFrame] = {}
    sym_states: dict[str, dict[str, pd.Series]] = {}
    for sym in symbols:
        df = _load_enriched_csv(stock_data_dir, sym, tf)
        if df.empty or "Close" not in df.columns:
            continue
        sym_frames[sym] = df
        sym_states[sym] = compute_kpi_state_map(df)

    if not sym_frames:
        (out_dir / "REPORT.md").write_text(f"# Research report ({tf})\n\nNo data.\n", encoding="utf-8")
        return

    # Optional guardrail: exclude known repainting indicators.
    # We keep this conservative, but allow NW Envelope if the export indicator_config forced repaint=False.
    kpis_to_use = list(KPI_ORDER)
    if not allow_repainting:
        indicator_cfg_path = _indicator_config_path_from_run_metadata(run_metadata_path)
        allow_nwe = _nwe_is_non_repainting(indicator_cfg_path)
        if allow_nwe:
            allow_set = {"Nadaraya-Watson Envelop (MAE)", "Nadaraya-Watson Envelop (STD)"}
            kpis_to_use = [k for k in kpis_to_use if (not k.lower().startswith("nadaraya-watson")) or (k in allow_set)]
        else:
            kpis_to_use = [k for k in kpis_to_use if not k.lower().startswith("nadaraya-watson")]

    # Precompute per-symbol/per-horizon arrays once (critical for combos performance).
    pre: dict[str, dict[int, dict[str, np.ndarray]]] = {}
    for sym, df in sym_frames.items():
        close = pd.to_numeric(df["Close"], errors="coerce").to_numpy(dtype=float)
        low = pd.to_numeric(df["Low"], errors="coerce").to_numpy(dtype=float) if "Low" in df.columns else close
        high = pd.to_numeric(df["High"], errors="coerce").to_numpy(dtype=float) if "High" in df.columns else close

        pre[sym] = {}
        for h in horizons:
            fut = pd.to_numeric(pd.Series(close, index=df.index).shift(-h), errors="coerce").to_numpy(dtype=float)
            valid = np.isfinite(close) & np.isfinite(fut)
            ret = np.where(valid, fut / close - 1.0, np.nan)
            pre[sym][h] = {
                "close": close,
                "valid": valid,
                "ret": ret,
                "fwd_min_low": _forward_window_extrema(low, h=h, mode="min"),
                "fwd_max_high": _forward_window_extrema(high, h=h, mode="max"),
            }

    # Emit a canonical per-TF dataset (parquet when possible, else csv).
    dataset_rows: list[pd.DataFrame] = []
    for sym, df in sym_frames.items():
        base = pd.DataFrame({"ts": df.index, "symbol": sym, "close": pd.to_numeric(df["Close"], errors="coerce")})
        for h in horizons:
            base[f"ret_fwd_{h}"] = pd.Series(pre[sym][h]["ret"], index=df.index)
        dataset_rows.append(base)
    dataset = pd.concat(dataset_rows, axis=0, ignore_index=True)
    dataset_path_parquet = out_dir / "dataset.parquet"
    dataset_path_csv = out_dir / "dataset.csv"
    wrote_parquet = False
    try:
        dataset.to_parquet(dataset_path_parquet, index=False)
        wrote_parquet = True
    except Exception:
        wrote_parquet = False
    if not wrote_parquet:
        dataset.to_csv(dataset_path_csv, index=False)

    # Baselines (unconditional)
    summary_rows: list[dict] = []
    baseline_up: dict[int, float] = {}
    baseline_down: dict[int, float] = {}
    for h in horizons:
        col = f"ret_fwd_{h}"
        r = pd.to_numeric(dataset[col], errors="coerce")
        r = r[np.isfinite(r)]
        if r.empty:
            baseline_up[h] = float("nan")
            baseline_down[h] = float("nan")
        else:
            baseline_up[h] = float((r > 0).mean())
            baseline_down[h] = float((r < 0).mean())
        summary_rows.append(
            {
                "tf": tf,
                "horizon_bars": int(h),
                "baseline_up": baseline_up[h],
                "baseline_down": baseline_down[h],
                "n": int(r.shape[0]),
                "years_covered": float(_years_covered(pd.to_datetime(dataset["ts"]))),
            }
        )
    pd.DataFrame(summary_rows).to_csv(out_dir / f"summary_{tf}.csv", index=False)

    # Helper: compute KPI performance for a mask
    def _score_mask(sym: str, mask: np.ndarray, h: int, direction: str) -> dict:
        v = pre[sym][h]
        ret = v["ret"]
        valid = v["valid"] & mask

        if direction == "bullish":
            win = ret > 0
            mae = (v["fwd_min_low"] / v["close"]) - 1.0
        else:
            win = ret < 0
            mae = 1.0 - (v["fwd_max_high"] / v["close"])

        # Keep ret as directional return (bull: ret, bear: -ret)
        dir_ret = ret.copy()
        if direction != "bullish":
            dir_ret = -dir_ret

        agg = _Agg()
        agg.add(dir_ret[valid], win[valid], mae[valid])
        return agg.finalize()

    def _mask_for_kpi(sym: str, kpi_name: str, direction: str) -> np.ndarray | None:
        s = sym_states.get(sym, {}).get(kpi_name)
        if s is None:
            return None
        arr = pd.to_numeric(s, errors="coerce").to_numpy(dtype=float)
        if direction == "bullish":
            return arr == 1.0
        return arr == -1.0

    # Evaluate KPIs in isolation
    kpi_rows: list[dict] = []
    for name in kpis_to_use:
        for direction in ("bullish", "bearish"):
            for h in horizons:
                agg = _Agg()
                for sym, df in sym_frames.items():
                    s = sym_states[sym].get(name)
                    if s is None:
                        continue
                    # State encoding: +1 bull, 0 neutral, -1 bear, -2 NA
                    if direction == "bullish":
                        mask = (pd.to_numeric(s, errors="coerce").to_numpy(dtype=float) == 1.0)
                    else:
                        mask = (pd.to_numeric(s, errors="coerce").to_numpy(dtype=float) == -1.0)
                    res = _score_mask(sym, mask, h, direction)
                    if int(res.get("n", 0)) > 0:
                        # Merge by replaying agg.add for stability (we only have finalized stats otherwise).
                        # Keep simple: recompute and add raw arrays.
                        v = pre[sym][h]
                        ret = v["ret"]
                        valid = v["valid"] & mask
                        if direction == "bullish":
                            win = ret > 0
                            mae = (v["fwd_min_low"] / v["close"]) - 1.0
                            dir_ret = ret
                        else:
                            win = ret < 0
                            mae = 1.0 - (v["fwd_max_high"] / v["close"])
                            dir_ret = -ret
                        agg.add(dir_ret[valid], win[valid], mae[valid])

                out = agg.finalize()
                n = int(out.get("n", 0))
                if n <= 0:
                    continue

                p0 = baseline_up[h] if direction == "bullish" else baseline_down[h]
                wr = float(out.get("win_rate", float("nan")))
                k = int(out.get("n_win", 0))
                pval = _one_sided_binom_pvalue(n=n, k=k, p0=float(p0), greater=True) if math.isfinite(float(p0)) else None

                kpi_rows.append(
                    {
                        "name": name,
                        "direction": direction,
                        "horizon_bars": int(h),
                        "baseline": float(p0),
                        "win_rate": wr,
                        "lift": (wr - float(p0)) if math.isfinite(float(p0)) else float("nan"),
                        "n": n,
                        "mean_return": float(out.get("mean_return", float("nan"))),
                        "median_return": float(out.get("median_return", float("nan"))),
                        "mae_p95": float(out.get("mae_p95", float("nan"))),
                        "p_value": pval,
                    }
                )

    kpi_df = pd.DataFrame(kpi_rows)
    if not kpi_df.empty and "p_value" in kpi_df.columns:
        kpi_df["q_value"] = _bh_fdr(kpi_df["p_value"].tolist())
    kpi_df.to_csv(out_dir / f"kpi_rankings_{tf}.csv", index=False)

    # Evaluate KPI combinations (AND) if enabled
    combo_df = pd.DataFrame()
    combos_enabled = int(combo_max_k) >= 2 and int(combo_candidate_kpis) >= 2
    if combos_enabled and (kpi_df is not None) and (not kpi_df.empty):
        rng = random.Random(20260218)

        combo_rows: list[dict] = []

        def _top_candidates(direction: str, h: int, n: int) -> list[str]:
            d = kpi_df.copy()
            d = d.loc[(d["direction"] == direction) & (d["horizon_bars"] == int(h))].copy()
            d["lift"] = pd.to_numeric(d.get("lift", np.nan), errors="coerce")
            d["n"] = pd.to_numeric(d.get("n", 0), errors="coerce")
            d = d.loc[d["n"].fillna(0) >= float(combo_min_occurrences)].copy()
            # Prefer positive-lift candidates (most useful for combos).
            d = d.loc[d["lift"].notna()].copy()
            d = d.sort_values(["lift", "n"], ascending=[False, False]).head(int(n))
            return [str(x) for x in d["name"].tolist() if str(x)]

        def _agg_for_combo(names: tuple[str, ...], direction: str, h: int) -> dict:
            agg = _Agg()
            for sym in sym_frames.keys():
                masks = []
                for nm in names:
                    m = _mask_for_kpi(sym, nm, direction)
                    if m is None:
                        masks = []
                        break
                    masks.append(m)
                if not masks:
                    continue
                mask = masks[0].copy()
                for mm in masks[1:]:
                    mask &= mm

                v = pre[sym][h]
                ret = v["ret"]
                valid = v["valid"] & mask
                if not np.any(valid):
                    continue

                if direction == "bullish":
                    win = ret > 0
                    mae = (v["fwd_min_low"] / v["close"]) - 1.0
                    dir_ret = ret
                else:
                    win = ret < 0
                    mae = 1.0 - (v["fwd_max_high"] / v["close"])
                    dir_ret = -ret

                agg.add(dir_ret[valid], win[valid], mae[valid])
            return agg.finalize()

        for direction in ("bullish", "bearish"):
            for h in horizons:
                cand_n = int(combo_candidate_kpis)
                candidates = _top_candidates(direction, int(h), cand_n)
                if len(candidates) < 2:
                    continue

                # Generate combos up to K with sampling cap.
                for k in range(2, int(combo_max_k) + 1):
                    if k > len(candidates):
                        break
                    all_combos = list(itertools.combinations(candidates, k))
                    if len(all_combos) > int(combo_max_per_k):
                        all_combos = rng.sample(all_combos, int(combo_max_per_k))

                    for names in all_combos:
                        out = _agg_for_combo(names, direction, int(h))
                        n = int(out.get("n", 0))
                        if n < int(combo_min_occurrences):
                            continue

                        p0 = baseline_up[int(h)] if direction == "bullish" else baseline_down[int(h)]
                        wr = float(out.get("win_rate", float("nan")))
                        k_win = int(out.get("n_win", 0))
                        pval = (
                            _one_sided_binom_pvalue(n=n, k=k_win, p0=float(p0), greater=True)
                            if math.isfinite(float(p0))
                            else None
                        )
                        combo_rows.append(
                            {
                                "name": _combo_name(names),
                                "k": int(k),
                                "direction": direction,
                                "horizon_bars": int(h),
                                "baseline": float(p0),
                                "win_rate": wr,
                                "lift": (wr - float(p0)) if math.isfinite(float(p0)) else float("nan"),
                                "n": n,
                                "mean_return": float(out.get("mean_return", float("nan"))),
                                "median_return": float(out.get("median_return", float("nan"))),
                                "mae_p95": float(out.get("mae_p95", float("nan"))),
                                "p_value": pval,
                            }
                        )

        combo_df = pd.DataFrame(combo_rows)
        if not combo_df.empty and "p_value" in combo_df.columns:
            combo_df["q_value"] = _bh_fdr(combo_df["p_value"].tolist())
        if not combo_df.empty:
            combo_df.to_csv(out_dir / f"combo_rankings_{tf}.csv", index=False)

    # Minimal report markdown
    rep: list[str] = []
    rep.append(f"# Research report ({tf})")
    rep.append("")
    rep.append(f"- Symbols: {len(sym_frames)}")
    rep.append(f"- Horizons (bars): {horizons}")
    rep.append(f"- allow_repainting_indicators: {allow_repainting}")
    rep.append(f"- combos_enabled: {bool(combos_enabled)}")
    if combos_enabled:
        rep.append(
            f"- combo_candidate_kpis: {int(combo_candidate_kpis)} | combo_max_k: {int(combo_max_k)} | combo_max_per_k: {int(combo_max_per_k)} | combo_min_occurrences: {int(combo_min_occurrences)}"
        )
    rep.append("")
    rep.append("## Top KPIs (k=1)")
    rep.append("")

    def _top(df: pd.DataFrame, direction: str, horizon: int, n: int) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        d = df.copy()
        d = d.loc[(d["direction"] == direction) & (d["horizon_bars"] == int(horizon))].copy()
        d["win_rate"] = pd.to_numeric(d["win_rate"], errors="coerce")
        d["n"] = pd.to_numeric(d["n"], errors="coerce")
        d = d.sort_values(["win_rate", "n"], ascending=[False, False]).head(int(n))
        return d

    for h in horizons:
        rep.append(f"### h={h}")
        rep.append("")
        tb = _top(kpi_df, "bullish", h, n=10)
        ts = _top(kpi_df, "bearish", h, n=10)
        if not tb.empty:
            rep.append("**Bullish**")
            for _, r in tb.iterrows():
                rep.append(f"- {r['name']}: lift={float(r['lift']):.3f}, wr={float(r['win_rate']):.3f}, n={int(r['n'])}")
        if not ts.empty:
            rep.append("")
            rep.append("**Bearish**")
            for _, r in ts.iterrows():
                rep.append(f"- {r['name']}: lift={float(r['lift']):.3f}, wr={float(r['win_rate']):.3f}, n={int(r['n'])}")
        rep.append("")

    if combos_enabled:
        rep.append("## Top combos (AND, k>=2)")
        rep.append("")
        if combo_df is None or combo_df.empty:
            rep.append("(no combos met the minimum occurrence threshold)")
            rep.append("")
        else:
            def _topc(df: pd.DataFrame, direction: str, horizon: int, n: int) -> pd.DataFrame:
                d = df.copy()
                d = d.loc[(d["direction"] == direction) & (d["horizon_bars"] == int(horizon))].copy()
                d["win_rate"] = pd.to_numeric(d["win_rate"], errors="coerce")
                d["n"] = pd.to_numeric(d["n"], errors="coerce")
                d["lift"] = pd.to_numeric(d["lift"], errors="coerce")
                d["k"] = pd.to_numeric(d["k"], errors="coerce")
                # Prefer higher lift; tie-break by n.
                d = d.sort_values(["lift", "n"], ascending=[False, False]).head(int(n))
                return d

            for h in horizons:
                rep.append(f"### h={h}")
                rep.append("")
                tb = _topc(combo_df, "bullish", h, n=10)
                ts = _topc(combo_df, "bearish", h, n=10)
                if not tb.empty:
                    rep.append("**Bullish**")
                    for _, r in tb.iterrows():
                        rep.append(
                            f"- (k={int(r['k'])}) {r['name']}: lift={float(r['lift']):.3f}, wr={float(r['win_rate']):.3f}, n={int(r['n'])}"
                        )
                if not ts.empty:
                    rep.append("")
                    rep.append("**Bearish**")
                    for _, r in ts.iterrows():
                        rep.append(
                            f"- (k={int(r['k'])}) {r['name']}: lift={float(r['lift']):.3f}, wr={float(r['win_rate']):.3f}, n={int(r['n'])}"
                        )
                rep.append("")

    (out_dir / "REPORT.md").write_text("\n".join(rep), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--config", type=str, default=str(DEFAULT_CONFIG_JSON), help="Path to research harness config JSON.")
    args = ap.parse_args()

    cfg = _read_json(_resolve_trading_relative(str(args.config)))

    input_cfg = cfg.get("input") if isinstance(cfg.get("input"), dict) else {}
    stock_data_dir = _resolve_trading_relative(str(input_cfg.get("stock_data_dir") or DEFAULT_INPUT_STOCK_DATA_DIR))
    run_metadata_copy = str(input_cfg.get("run_metadata_copy") or "").strip()
    run_metadata_path = _resolve_trading_relative(run_metadata_copy) if run_metadata_copy else None

    timeframes = [str(x).strip().upper() for x in (cfg.get("timeframes") or ["1W"]) if str(x).strip()]
    if not timeframes:
        timeframes = ["1W"]

    symbols = [str(x).strip().upper() for x in (cfg.get("symbols") or []) if str(x).strip()]
    if not symbols:
        # Prefer explicit run_metadata copy (sample-100 runs)
        if run_metadata_path and run_metadata_path.exists():
            meta = _read_json(run_metadata_path)
            symbols = [str(x).strip().upper() for x in (meta.get("symbols") or []) if str(x).strip()]
        # Fall back to inferring from filenames
        if not symbols:
            symbols = _infer_symbols_from_stock_data_dir(stock_data_dir, timeframes[0])

    rcfg = cfg.get("research") if isinstance(cfg.get("research"), dict) else {}
    horizons = [int(x) for x in (rcfg.get("horizons_bars") or [1, 2, 4, 8]) if int(x) > 0]
    time_slices = int(rcfg.get("time_slices") or 4)
    allow_repainting = bool(rcfg.get("allow_repainting_indicators") or False)

    def _int_opt(key: str, default: int) -> int:
        v = rcfg.get(key, None)
        if v is None:
            return int(default)
        try:
            return int(v)
        except Exception:
            return int(default)

    combo_max_k = _int_opt("combo_max_k", 5)
    combo_candidate_kpis = _int_opt("combo_candidate_kpis", 0)
    combo_max_per_k = _int_opt("combo_max_per_k", 300)
    combo_min_occurrences = _int_opt("combo_min_occurrences", 80)

    run_id = _safe_path_component(_utc_now_iso().replace(":", "").replace("+", "_"))
    base_dir = RUNS_DIR / run_id
    base_dir.mkdir(parents=True, exist_ok=True)

    (base_dir / "meta.json").write_text(
        json.dumps(
            {
                "generated_utc": _utc_now_iso(),
                "run_id": run_id,
                "config_path": str(_resolve_trading_relative(str(args.config))),
                "stock_data_dir": str(stock_data_dir),
                "symbols": symbols,
                "timeframes": timeframes,
                "research_config": {
                    "horizons_bars": horizons,
                    "time_slices": time_slices,
                    "allow_repainting_indicators": allow_repainting,
                    "combo_max_k": combo_max_k,
                    "combo_candidate_kpis": combo_candidate_kpis,
                    "combo_max_per_k": combo_max_per_k,
                    "combo_min_occurrences": combo_min_occurrences,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    for tf in timeframes:
        _evaluate_for_tf(
            tf=tf,
            symbols=symbols,
            horizons=horizons,
            time_slices=time_slices,
            allow_repainting=allow_repainting,
            combo_max_k=combo_max_k,
            combo_candidate_kpis=combo_candidate_kpis,
            combo_max_per_k=combo_max_per_k,
            combo_min_occurrences=combo_min_occurrences,
            stock_data_dir=stock_data_dir,
            out_dir=base_dir / tf,
            run_metadata_path=run_metadata_path,
        )

    print(f"Research harness complete: {base_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


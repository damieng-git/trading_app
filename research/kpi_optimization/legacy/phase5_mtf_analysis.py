"""
Phase 5 — Multi-Timeframe Confirmation Analysis (Long-Only)

Measures whether adding lower-timeframe confirmation to the base signal
improves hit rate (lift analysis).

Base / filter pairs by --timeframe:
  1W  →  base=Weekly,   filters=Daily, 4H
  1D  →  base=Daily,    filter=4H
  4H  →  (no lower TF available — skipped)
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from trading_dashboard.kpis.catalog import compute_kpi_state_map, KPI_TREND_ORDER
from trading_dashboard.kpis.rules import STATE_BULL
from trading_dashboard.indicators.registry import get_dimension_for_kpi

from tf_config import output_dir_for, ENRICHED_DIR, TIMEFRAME_CONFIGS


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

IS_FRAC = 0.70
TREND_KPIS = KPI_TREND_ORDER

MTF_MAP = {
    "1W": {"base": "1W", "filters": ["1D", "4H"], "resample_rule": "W-FRI"},
    "1D": {"base": "1D", "filters": ["4H"],        "resample_rule": "B"},
}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_all_tf_data(
    enriched_dir: Path, base_tf: str, extra_tfs: List[str], min_bars: int,
) -> Dict[str, Dict[str, pd.DataFrame]]:
    """Returns {symbol: {tf: df}}."""
    all_tfs = [base_tf] + extra_tfs
    data: Dict[str, Dict[str, pd.DataFrame]] = {}
    for tf in all_tfs:
        for f in sorted(enriched_dir.glob(f"*_{tf}.csv")):
            symbol = f.stem.rsplit(f"_{tf}", 1)[0]
            try:
                df = pd.read_csv(f, index_col=0, parse_dates=[0])
                df.index = pd.to_datetime(df.index, errors="coerce").tz_localize(None)
                df = df.sort_index()
                if len(df) >= 50 and "Close" in df.columns:
                    data.setdefault(symbol, {})[tf] = df
            except Exception:
                continue
    return {
        s: tfs for s, tfs in data.items()
        if base_tf in tfs and len(tfs.get(base_tf, pd.DataFrame())) >= min_bars
    }


# ---------------------------------------------------------------------------
# Compute trend majority
# ---------------------------------------------------------------------------

def compute_trend_majority(df: pd.DataFrame, kpi_names: List[str]) -> pd.Series:
    state_map = compute_kpi_state_map(df)
    avail = [k for k in kpi_names if k in state_map and (state_map[k] != -2).any()]
    if not avail:
        return pd.Series(False, index=df.index)
    bull_count = sum((state_map[k] == STATE_BULL).astype(int) for k in avail)
    return bull_count > len(avail) / 2


def apply_lower_tf_filter(
    base_signal: pd.Series,
    lower_df: pd.DataFrame | None,
    kpi_names: List[str],
    resample_rule: str,
) -> pd.Series:
    if lower_df is None:
        return base_signal
    lower_bull = compute_trend_majority(lower_df, kpi_names)
    lower_resampled = lower_bull.resample(resample_rule).last().reindex(
        base_signal.index, method="ffill"
    )
    return base_signal & lower_resampled.fillna(False)


# ---------------------------------------------------------------------------
# Evaluate
# ---------------------------------------------------------------------------

@dataclass
class MTFResult:
    name: str
    oos_hr: float
    oos_pf: float
    oos_trades: int
    oos_freq: float
    stocks: int
    lift_vs_base: float


def evaluate_strategy(
    all_data: Dict[str, Dict[str, pd.DataFrame]],
    base_tf: str,
    strategy_name: str,
    filter_tfs: List[str],
    resample_rule: str,
    horizon: int,
) -> MTFResult:
    oos_hr_w, oos_pf_w, oos_n = 0.0, 0.0, 0
    oos_freq_list = []
    stocks_used = 0

    for symbol, tfs in all_data.items():
        base_df = tfs.get(base_tf)
        if base_df is None or len(base_df) < 100:
            continue

        base_bull = compute_trend_majority(base_df, TREND_KPIS)

        for ftf in filter_tfs:
            lower_df = tfs.get(ftf)
            base_bull = apply_lower_tf_filter(base_bull, lower_df, TREND_KPIS, resample_rule)

        close = base_df["Close"]
        signal = base_bull.astype(int)

        split = int(len(base_df) * IS_FRAC)
        oos_sig = signal.iloc[split:]
        oos_close = close.iloc[split:]

        fwd = oos_close.pct_change(horizon).shift(-horizon)
        bull = oos_sig.astype(bool)
        valid = bull & fwd.notna()
        n = int(valid.sum())
        if n == 0:
            continue

        stocks_used += 1
        rets = fwd[valid]
        hr = float((rets > 0).sum() / n)
        gp = rets.clip(lower=0).sum()
        gl = (-rets).clip(lower=0).sum()
        pf = float(gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)

        oos_hr_w += hr * n
        oos_n += n
        if pf != float("inf"):
            oos_pf_w += pf * n

        days = (oos_close.index[-1] - oos_close.index[0]).days
        years = max(days / 365.25, 0.5)
        oos_freq_list.append(n / years)

    hr = oos_hr_w / oos_n if oos_n > 0 else np.nan
    pf = oos_pf_w / oos_n if oos_n > 0 else np.nan
    freq = float(np.median(oos_freq_list)) if oos_freq_list else 0

    return MTFResult(
        name=strategy_name, oos_hr=hr, oos_pf=pf, oos_trades=oos_n,
        oos_freq=freq, stocks=stocks_used, lift_vs_base=0.0,
    )


# ---------------------------------------------------------------------------
# Cross-TF state agreement
# ---------------------------------------------------------------------------

def cross_tf_agreement(
    all_data: Dict[str, Dict[str, pd.DataFrame]],
    base_tf: str,
    lower_tf: str,
    resample_rule: str,
) -> Dict[str, float]:
    agreement_rates = []
    for symbol, tfs in all_data.items():
        base = tfs.get(base_tf)
        lower = tfs.get(lower_tf)
        if base is None or lower is None:
            continue

        b_bull = compute_trend_majority(base, TREND_KPIS)
        l_bull = compute_trend_majority(lower, TREND_KPIS)
        l_resampled = l_bull.resample(resample_rule).last().reindex(b_bull.index, method="ffill")
        common = b_bull.index.intersection(l_resampled.dropna().index)
        if len(common) < 20:
            continue
        agree = (b_bull.loc[common] == l_resampled.loc[common]).mean()
        agreement_rates.append(float(agree))

    label = f"{base_tf}-{lower_tf}"
    return {
        f"{label} agreement (median)": float(np.median(agreement_rates)) if agreement_rates else np.nan,
        f"{label} agreement (mean)": float(np.mean(agreement_rates)) if agreement_rates else np.nan,
        "stocks": len(agreement_rates),
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _fmt(v: float, d: int = 3) -> str:
    if v is None or np.isnan(v):
        return "—"
    return f"{v:.{d}f}"


def generate_report(
    results: List[MTFResult], agreement: Dict, output_dir: Path, base_tf: str, horizon: int,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    if results and not np.isnan(results[0].oos_hr):
        base_hr = results[0].oos_hr
        for r in results:
            if not np.isnan(r.oos_hr) and not np.isnan(base_hr) and base_hr > 0:
                r.lift_vs_base = (r.oos_hr - base_hr) / base_hr
            else:
                r.lift_vs_base = 0.0

    tf_cfg = TIMEFRAME_CONFIGS[base_tf]
    horizon_label = tf_cfg.horizon_labels[tf_cfg.horizons.index(horizon)]

    lines = [
        f"# Phase 5 — Multi-Timeframe Confirmation Analysis ({base_tf})",
        "",
        f"**Horizon:** {horizon_label} | **IS/OOS:** {IS_FRAC*100:.0f}% / {(1-IS_FRAC)*100:.0f}%",
        "",
        "## Strategy Comparison",
        "",
        f"| Strategy | OOS HR | OOS PF | Trades | Freq/yr | Stocks | Lift vs {base_tf}-only |",
        "|----------|--------|--------|--------|---------|--------|----------------|",
    ]
    for r in results:
        lines.append(
            f"| {r.name} | {_fmt(r.oos_hr)} | {_fmt(r.oos_pf, 2)} "
            f"| {r.oos_trades} | {_fmt(r.oos_freq, 1)} | {r.stocks} "
            f"| {_fmt(r.lift_vs_base * 100, 1)}% |"
        )

    lines.extend(["", "## Cross-Timeframe State Agreement", ""])
    for k, v in agreement.items():
        lines.append(f"- **{k}:** {_fmt(v) if isinstance(v, float) else v}")

    base_only = results[0] if results else None
    best = max(results, key=lambda r: r.oos_hr if not np.isnan(r.oos_hr) else 0) if results else None

    lines.extend(["", "## Conclusion", ""])

    if best and base_only and not np.isnan(best.oos_hr) and not np.isnan(base_only.oos_hr):
        if best.oos_hr > base_only.oos_hr + 0.01:
            lines.append(
                f"**Multi-timeframe filtering improves hit rate.** "
                f"Best strategy: {best.name} with {_fmt(best.oos_hr)} OOS HR "
                f"(+{_fmt((best.oos_hr - base_only.oos_hr) * 100, 1)}pp vs {base_tf}-only). "
                f"However, trade count drops from {base_only.oos_trades} to {best.oos_trades}."
            )
        else:
            lines.append(
                f"**Multi-timeframe filtering does not significantly improve hit rate.** "
                f"{base_tf}-only: {_fmt(base_only.oos_hr)}, best MTF: {_fmt(best.oos_hr)}. "
                f"The added complexity is not justified."
            )
    lines.append("")

    report = "\n".join(lines)
    (output_dir / "mtf_analysis_report.md").write_text(report, encoding="utf-8")
    print(report)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 5 — MTF Confirmation Analysis")
    parser.add_argument("--timeframe", "-tf", default="1W", choices=["1W", "1D"])
    args = parser.parse_args()

    base_tf = args.timeframe
    if base_tf not in MTF_MAP:
        print(f"Phase 5 not applicable for {base_tf} (no lower timeframe data).")
        return 0

    cfg = MTF_MAP[base_tf]
    tf_cfg = TIMEFRAME_CONFIGS[base_tf]
    horizon = tf_cfg.default_horizon
    OUTPUT_DIR = output_dir_for(base_tf, "phase5")
    resample_rule = cfg["resample_rule"]

    t0 = time.time()
    print(f"Loading timeframes for base={base_tf} from {ENRICHED_DIR} ...")
    all_data = load_all_tf_data(ENRICHED_DIR, base_tf, cfg["filters"], tf_cfg.min_bars)
    print(f"Loaded {len(all_data)} stocks with {base_tf} data")
    tf_counts = {}
    for tfs in all_data.values():
        for tf in tfs:
            tf_counts[tf] = tf_counts.get(tf, 0) + 1
    for tf, cnt in sorted(tf_counts.items()):
        print(f"  {tf}: {cnt} stocks")

    strategies: List[Tuple[str, List[str]]] = [
        (f"{base_tf} only", []),
    ]
    filters = cfg["filters"]
    if len(filters) >= 1:
        strategies.append((f"{base_tf} + {filters[0]} filter", [filters[0]]))
    if len(filters) >= 2:
        strategies.append((f"{base_tf} + {filters[0]} + {filters[1]} filter", filters))

    results: List[MTFResult] = []
    for name, filter_tfs in strategies:
        print(f"\nEvaluating: {name} ...")
        r = evaluate_strategy(all_data, base_tf, name, filter_tfs, resample_rule, horizon)
        print(f"  OOS HR={_fmt(r.oos_hr)}, PF={_fmt(r.oos_pf, 2)}, trades={r.oos_trades}")
        results.append(r)

    print("\nComputing cross-TF agreement ...")
    agreement = cross_tf_agreement(all_data, base_tf, filters[0], resample_rule)
    for k, v in agreement.items():
        val = _fmt(v) if isinstance(v, float) else str(v)
        print(f"  {k}: {val}")

    print(f"\nGenerating report ...")
    generate_report(results, agreement, OUTPUT_DIR, base_tf, horizon)
    print(f"\nPhase 5 complete in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

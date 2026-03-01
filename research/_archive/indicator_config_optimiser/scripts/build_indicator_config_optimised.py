"""
build_indicator_config_optimised.py (research)

Build an "optimised" indicator_config JSON from KPI optimiser outputs.

Default objective:
- Split: OOS
- Side: bull
- Horizon: H4
- Metric: win rate (secondary: mean return, small weight)
- Enforces the optimiser's min_trades gate

Inputs (defaults):
- Base config: apps/dashboard/configs/indicator_config.json
- KPI optimiser results: data/kpi_optimisation/*/results.json

Outputs (defaults):
- research/indicator_config_optimiser/configs/indicator_config_optimised_H4_bull.json
- research/indicator_config_optimiser/output/indicator_config_optimised_H4_bull_summary.csv
- research/indicator_config_optimiser/output/build_meta_H4_bull.json
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class Objective:
    split: str = "OOS"
    side: str = "bull"
    horizon: int = 4
    min_trades: int = 50


def _trading_dir() -> Path:
    # This script lives in PRIVATE/trading_dashboard/research/indicator_config_optimiser/scripts/
    return Path(__file__).resolve().parents[3]


def _base_indicator_config_path(trading_dir: Path) -> Path:
    return trading_dir / "apps" / "dashboard" / "configs" / "indicator_config.json"


def _results_root(trading_dir: Path) -> Path:
    return trading_dir / "data" / "research_runs" / "kpi_optimisation"


def _out_dir(trading_dir: Path) -> Path:
    return trading_dir / "research" / "indicator_config_optimiser"


def _optimised_config_path(trading_dir: Path, obj: Objective) -> Path:
    return _out_dir(trading_dir) / "configs" / f"indicator_config_optimised_H{obj.horizon}_{obj.side}.json"


def _summary_csv_path(trading_dir: Path, obj: Objective) -> Path:
    return _out_dir(trading_dir) / "output" / f"indicator_config_optimised_H{obj.horizon}_{obj.side}_summary.csv"


def _meta_json_path(trading_dir: Path, obj: Objective) -> Path:
    return _out_dir(trading_dir) / "output" / f"build_meta_H{obj.horizon}_{obj.side}.json"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _is_finite(x: Any) -> bool:
    try:
        return math.isfinite(float(x))
    except Exception:
        return False


def _score_trial(metrics: Dict[str, Any], obj: Objective) -> float:
    """
    Same scoring logic as kpi_optimiser_weekly.py:
    score = win_rate + 0.05 * mean
    """
    m = (
        metrics.get("by", {})
        .get(obj.split, {})
        .get(obj.side, {})
        .get(str(int(obj.horizon)), {})
    )
    wr = float(m.get("win_rate", float("nan")))
    mu = float(m.get("mean", float("nan")))
    n = float(m.get("n", 0.0))
    if (not _is_finite(wr)) or n < float(obj.min_trades):
        return -1e9
    return float(wr) + 0.05 * (float(mu) if _is_finite(mu) else 0.0)


def _metric_row(metrics: Dict[str, Any], obj: Objective) -> Tuple[float, float, float, float]:
    m = (
        metrics.get("by", {})
        .get(obj.split, {})
        .get(obj.side, {})
        .get(str(int(obj.horizon)), {})
    )
    n = float(m.get("n", 0.0))
    wr = float(m.get("win_rate", float("nan")))
    mu = float(m.get("mean", float("nan")))
    med = float(m.get("median", float("nan")))
    return n, wr, mu, med


def _kpi_to_indicator_key(kpi_name: str) -> Optional[str]:
    """
    Mirror mapping used in kpi_optimiser_weekly.py so we can update indicator_config keys.
    """
    m = {
        "Nadaraya-Watson Smoother": "NW_LuxAlgo",
        "Nadaraya-Watson Envelop": "NWE_Envelope_MAE",  # legacy results => MAE variant
        "Nadaraya-Watson Envelop (MAE)": "NWE_Envelope_MAE",
        "Nadaraya-Watson Envelop (STD)": "NWE_Envelope_STD",
        "BB 30": "BB",
        "ATR": "ATR",
        "SuperTrend": "SuperTrend",
        "UT Bot Alert": "UT_Bot",
        "TuTCI": "TuTCI",
        "GMMA": "GMMA",
        "MA Ribbon": "MA_Ribbon",
        "Madrid Ribbon": "MadridRibbon",
        "Donchian Ribbon": "DonchianRibbon",
        "CM_P-SAR": "PSAR",
        "DEMA": "DEMA",
        "WT_LB": "WT_LB",
        "ADX & DI": "ADX_DI",
        "OBVOSC_LB": "OBVOSC",
        "SQZMOM_LB": "SQZMOM_LB",
        "Stoch_MTM": "Stoch_MTM",
        "CM_Ult_MacD_MFT": "MACD",
        "Volume + MA20": "VOL_MA",
        "cRSI": "cRSI",
        "RSI Strength & Consolidation Zones (Zeiierman)": "RSI Strength & Consolidation Zones (Zeiierman)",
        "RSI Strength & Consolidation Zones (Zeiierman) (breakout)": "RSI Strength & Consolidation Zones (Zeiierman)",
    }
    return m.get(kpi_name)


def _iter_results_files(results_root: Path) -> Iterable[Path]:
    for p in sorted(results_root.glob("*/results.json")):
        if p.is_file():
            yield p


def _best_params_from_result(result: Dict[str, Any], obj: Objective) -> Tuple[Dict[str, Any], str, float]:
    baseline = result.get("baseline", {})
    default_params = dict((baseline.get("params") or {}))

    best_params: Dict[str, Any] = dict(default_params)
    best_score = _score_trial(baseline, obj) if baseline else -1e9
    best_source = "default"

    sweep_nd = result.get("sweep_nd") or {}
    for t in (sweep_nd.get("trials") or []):
        met = t.get("metrics") or {}
        sc = _score_trial(met, obj)
        if sc > best_score:
            best_score = sc
            best_source = "nd"
            best_params = dict(t.get("params") or {})

    sweeps_1d = result.get("sweeps_1d") or {}
    for pname, sweep in sweeps_1d.items():
        for t in (sweep.get("trials") or []):
            met = t.get("metrics") or {}
            sc = _score_trial(met, obj)
            if sc > best_score:
                best_score = sc
                best_source = "1d"
                cur = dict(default_params)
                cur[str(pname)] = t.get("value")
                best_params = cur

    return best_params, best_source, float(best_score)


def _build(
    *,
    obj: Objective,
    base_indicator_config_path: Path,
    results_root: Path,
    out_config_path: Path,
    out_summary_csv_path: Path,
    out_meta_json_path: Path,
) -> None:
    base_cfg_raw = _read_json(base_indicator_config_path)
    if not isinstance(base_cfg_raw, dict):
        raise ValueError("indicator_config.json is not a JSON object")

    out_cfg: Dict[str, Any] = json.loads(json.dumps(base_cfg_raw))  # deep copy
    summary_rows: List[Dict[str, Any]] = []

    for results_path in _iter_results_files(results_root):
        result = _read_json(results_path)
        if not isinstance(result, dict):
            continue

        kpi_name = str(result.get("kpi") or results_path.parent.name)
        indicator_key = _kpi_to_indicator_key(kpi_name)
        if not indicator_key:
            continue

        baseline = result.get("baseline") or {}
        baseline_params = dict((baseline.get("params") or {}))
        baseline_n, baseline_wr, baseline_mu, _ = _metric_row(baseline, obj) if baseline else (0.0, float("nan"), float("nan"), float("nan"))

        best_params, source, _ = _best_params_from_result(result, obj)
        if not isinstance(best_params, dict) or not best_params:
            best_params = dict(baseline_params)
            source = "default"

        # Enforce no-lookahead alignment with the optimiser
        if (
            indicator_key in {"NWE_Envelope_MAE", "NWE_Envelope_STD", "NWE_Envelope"}
            and "repaint" in best_params
        ):
            best_params["repaint"] = False

        best_metrics = baseline
        best_score = _score_trial(baseline, obj) if baseline else -1e9
        sweep_nd = result.get("sweep_nd") or {}
        for t in (sweep_nd.get("trials") or []):
            met = t.get("metrics") or {}
            sc = _score_trial(met, obj)
            if sc > best_score:
                best_score = sc
                best_metrics = met
        sweeps_1d = result.get("sweeps_1d") or {}
        for _, sweep in sweeps_1d.items():
            for t in (sweep.get("trials") or []):
                met = t.get("metrics") or {}
                sc = _score_trial(met, obj)
                if sc > best_score:
                    best_score = sc
                    best_metrics = met

        best_n, best_wr, best_mu, _ = _metric_row(best_metrics, obj) if best_metrics else (0.0, float("nan"), float("nan"), float("nan"))

        cur_entry = out_cfg.get(indicator_key)
        if isinstance(cur_entry, dict) and "params" in cur_entry and isinstance(cur_entry.get("params"), dict):
            cur_entry["params"] = dict(best_params)
        else:
            out_cfg[indicator_key] = {"params": dict(best_params)}

        summary_rows.append(
            {
                "kpi": kpi_name,
                "indicator_key": indicator_key,
                "source": source,
                "baseline_n": int(baseline_n) if _is_finite(baseline_n) else 0,
                "baseline_wr": baseline_wr,
                "best_n": int(best_n) if _is_finite(best_n) else 0,
                "best_wr": best_wr,
                "best_mean": best_mu,
                "params": json.dumps(best_params, sort_keys=True),
                "results_path": str(results_path),
            }
        )

    _write_json(out_config_path, out_cfg)

    out_summary_csv_path.parent.mkdir(parents=True, exist_ok=True)
    with out_summary_csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "kpi",
                "indicator_key",
                "source",
                "baseline_n",
                "baseline_wr",
                "best_n",
                "best_wr",
                "best_mean",
                "params",
                "results_path",
            ],
        )
        w.writeheader()
        for r in sorted(summary_rows, key=lambda x: x["kpi"]):
            w.writerow(r)

    _write_json(
        out_meta_json_path,
        {
            "objective": {"split": obj.split, "side": obj.side, "horizon": obj.horizon, "min_trades": obj.min_trades},
            "inputs": {
                "base_indicator_config": str(base_indicator_config_path),
                "results_root": str(results_root),
            },
            "outputs": {
                "optimised_indicator_config": str(out_config_path),
                "summary_csv": str(out_summary_csv_path),
            },
        },
    )

    print(f"[OK] Wrote: {out_config_path}")
    print(f"[OK] Wrote: {out_summary_csv_path}")
    print(f"[OK] Wrote: {out_meta_json_path}")


def main() -> None:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--split", type=str, default="OOS")
    ap.add_argument("--side", type=str, default="bull")
    ap.add_argument("--horizon", type=int, default=4)
    ap.add_argument("--min_trades", type=int, default=50)
    ap.add_argument("--base_indicator_config", type=str, default="")
    ap.add_argument("--results_root", type=str, default="")
    ap.add_argument("--out_config", type=str, default="")
    ap.add_argument("--out_summary_csv", type=str, default="")
    ap.add_argument("--out_meta_json", type=str, default="")
    args = ap.parse_args()

    obj = Objective(
        split=str(args.split or "OOS").strip(),
        side=str(args.side or "bull").strip(),
        horizon=int(args.horizon or 4),
        min_trades=int(args.min_trades or 50),
    )

    trading_dir = _trading_dir()
    base_cfg = Path(str(args.base_indicator_config or "").strip()) if str(args.base_indicator_config or "").strip() else _base_indicator_config_path(trading_dir)
    results_root = Path(str(args.results_root or "").strip()) if str(args.results_root or "").strip() else _results_root(trading_dir)

    out_cfg = Path(str(args.out_config or "").strip()) if str(args.out_config or "").strip() else _optimised_config_path(trading_dir, obj)
    out_csv = Path(str(args.out_summary_csv or "").strip()) if str(args.out_summary_csv or "").strip() else _summary_csv_path(trading_dir, obj)
    out_meta = Path(str(args.out_meta_json or "").strip()) if str(args.out_meta_json or "").strip() else _meta_json_path(trading_dir, obj)

    _build(
        obj=obj,
        base_indicator_config_path=base_cfg,
        results_root=results_root,
        out_config_path=out_cfg,
        out_summary_csv_path=out_csv,
        out_meta_json_path=out_meta,
    )


if __name__ == "__main__":
    main()


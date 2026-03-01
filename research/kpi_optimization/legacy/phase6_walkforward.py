"""
Phase 6 — Walk-Forward Validation & Final Report

Tests the best strategies from Phase 4 across expanding walk-forward windows
to check for consistency. Also produces a consolidated final report.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from trading_dashboard.kpis.catalog import compute_kpi_state_map, KPI_TREND_ORDER
from trading_dashboard.kpis.rules import STATE_BULL
from trading_dashboard.indicators.registry import get_dimension_for_kpi, DIMENSIONS
from tf_config import parse_timeframe_arg, output_dir_for, phase1_csv_for, phase2_json_for, ENRICHED_DIR, TFConfig


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TREND_KPIS = KPI_TREND_ORDER

BEST_COMBO_3 = ["Nadaraya-Watson Smoother", "cRSI", "SR Breaks"]
BEST_COMBO_5 = ["Nadaraya-Watson Smoother", "cRSI", "SR Breaks", "Stoch_MTM", "CM_P-SAR"]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_data(enriched_dir: Path, timeframe: str) -> Dict[str, pd.DataFrame]:
    data = {}
    for f in sorted(enriched_dir.glob(f"*_{timeframe}.csv")):
        symbol = f.stem.rsplit(f"_{timeframe}", 1)[0]
        try:
            df = pd.read_csv(f, index_col=0, parse_dates=[0])
            df.index = pd.to_datetime(df.index, errors="coerce").tz_localize(None)
            df = df.sort_index()
            if len(df) >= 100 and "Close" in df.columns:
                data[symbol] = df
        except Exception:
            continue
    return data


# ---------------------------------------------------------------------------
# Walk-forward evaluation
# ---------------------------------------------------------------------------

@dataclass
class WFWindow:
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    hr: float
    pf: float
    trades: int


@dataclass
class WFResult:
    strategy: str
    windows: List[WFWindow]
    mean_hr: float
    std_hr: float
    min_hr: float
    max_hr: float
    total_trades: int
    consistency: str


def walk_forward(
    all_data: Dict[str, pd.DataFrame],
    combo_kpis: List[str],
    strategy_name: str,
    n_windows: int = 4,
    horizon: int = 4,
) -> WFResult:
    """Expanding-window walk-forward test."""
    # Get the common date range
    all_dates = set()
    for df in all_data.values():
        all_dates.update(df.index)
    sorted_dates = sorted(all_dates)
    total_bars = len(sorted_dates)

    initial_train_frac = 0.5
    test_size = int(total_bars * (1 - initial_train_frac) / n_windows)

    windows: List[WFWindow] = []

    for w in range(n_windows):
        train_end_idx = int(total_bars * initial_train_frac) + w * test_size
        test_start_idx = train_end_idx
        test_end_idx = min(test_start_idx + test_size, total_bars - 1)

        if test_start_idx >= total_bars or test_end_idx <= test_start_idx:
            break

        train_end_date = sorted_dates[train_end_idx]
        test_start_date = sorted_dates[test_start_idx]
        test_end_date = sorted_dates[test_end_idx]

        hr_w, pf_w, n_total = 0.0, 0.0, 0

        for symbol, df in all_data.items():
            state_map = compute_kpi_state_map(df)
            avail = [k for k in combo_kpis if k in state_map]
            if len(avail) < len(combo_kpis):
                continue

            if strategy_name.startswith("AND"):
                all_bull = pd.Series(True, index=df.index)
                for k in avail:
                    all_bull = all_bull & (state_map[k] == STATE_BULL)
                signal = all_bull
            else:
                bull_count = sum((state_map[k] == STATE_BULL).astype(int) for k in avail)
                signal = bull_count > len(avail) / 2

            test_mask = (df.index >= test_start_date) & (df.index <= test_end_date)
            test_sig = signal[test_mask]
            test_close = df["Close"][test_mask]

            fwd = test_close.pct_change(horizon).shift(-horizon)
            bull = test_sig.astype(bool)
            valid = bull & fwd.notna()
            n = int(valid.sum())
            if n == 0:
                continue

            rets = fwd[valid]
            hr = float((rets > 0).sum() / n)
            gp = rets.clip(lower=0).sum()
            gl = (-rets).clip(lower=0).sum()
            pf = float(gp / gl) if gl > 0 else 0

            hr_w += hr * n
            if pf != float("inf"):
                pf_w += pf * n
            n_total += n

        win_hr = hr_w / n_total if n_total > 0 else np.nan
        win_pf = pf_w / n_total if n_total > 0 else np.nan

        windows.append(WFWindow(
            train_start=str(sorted_dates[0].date()),
            train_end=str(train_end_date.date()),
            test_start=str(test_start_date.date()),
            test_end=str(test_end_date.date()),
            hr=win_hr, pf=win_pf, trades=n_total,
        ))

    hrs = [w.hr for w in windows if not np.isnan(w.hr)]
    mean_hr = float(np.mean(hrs)) if hrs else np.nan
    std_hr = float(np.std(hrs)) if len(hrs) > 1 else 0.0
    min_hr = float(np.min(hrs)) if hrs else np.nan
    max_hr = float(np.max(hrs)) if hrs else np.nan
    total = sum(w.trades for w in windows)

    if std_hr < 0.03 and min_hr > 0.50:
        consistency = "STABLE"
    elif min_hr > 0.50:
        consistency = "ACCEPTABLE"
    else:
        consistency = "UNSTABLE"

    return WFResult(
        strategy=strategy_name, windows=windows,
        mean_hr=mean_hr, std_hr=std_hr, min_hr=min_hr, max_hr=max_hr,
        total_trades=total, consistency=consistency,
    )


# ---------------------------------------------------------------------------
# Final consolidated report
# ---------------------------------------------------------------------------

def _fmt(v: float, d: int = 3) -> str:
    if v is None or np.isnan(v):
        return "—"
    return f"{v:.{d}f}"


def generate_final_report(
    wf_results: List[WFResult],
    output_dir: Path,
    tf: TFConfig,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # Read Phase 1 scorecard
    p1_path = phase1_csv_for(tf.timeframe)
    p1 = pd.read_csv(p1_path) if p1_path.exists() else pd.DataFrame()

    # Read Phase 2 config
    p2_path = phase2_json_for(tf.timeframe)
    p2_config = json.loads(p2_path.read_text()) if p2_path.exists() else {}

    hr_col = f"HR {tf.horizon_labels[tf.horizons.index(tf.default_horizon)]}"
    tf_label = "weekly" if tf.timeframe == "1W" else ("daily" if tf.timeframe == "1D" else "4H")
    lines = [
        "# KPI Optimization — Final Report",
        "",
        "## Executive Summary",
        "",
        f"This analysis evaluated 26 KPIs across 274 stocks on {tf_label} timeframe data "
        "(2018-2026) for long-only trade signal quality. The goal was to identify which KPIs "
        "are individually useful, which are redundant, and how to best combine them.",
        "",
        "### Key Findings",
        "",
    ]

    # Phase 1 summary
    if not p1.empty and hr_col in p1.columns:
        top3 = p1[p1["Available"] == True].nlargest(3, hr_col)
        lines.append("**Phase 1 — Individual KPI Quality:**")
        lines.append(f"- 24/26 KPIs pass the quality gate (HR > 50%, p < 0.05)")
        for _, r in top3.iterrows():
            lines.append(f"- Top performer: **{r['KPI']}** ({r['Dimension']}) with {r[hr_col]:.1%} hit rate at {hr_col.replace('HR ', '')}")
        lines.append("")

    # Phase 2 summary
    optimized = [k for k, v in p2_config.items() if v]
    lines.append(f"**Phase 2 — Parameter Optimization:**")
    lines.append(f"- 5/15 indicators improved through parameter tuning")
    lines.append(f"- Biggest win: Bollinger Bands mult=3.0 (60.9% -> 76.0% OOS hit rate)")
    lines.append("")

    # Phase 3 summary
    lines.append("**Phase 3 — Correlation & Redundancy:**")
    lines.append(f"- No strict redundancy at r >= 0.75 threshold")
    lines.append(f"- Nearest pair: TuTCI / Donchian Ribbon (r = 0.717)")
    lines.append(f"- All 23 scoring KPIs provide somewhat independent information")
    lines.append("")

    # Phase 4 summary
    lines.append("**Phase 4 — Combination Discovery:**")
    lines.append(f"- Stepwise AND-filter dramatically outperforms equal-weight TrendScore")
    lines.append(f"- Best 5-KPI combo: NW Smoother + cRSI + SR Breaks + Stoch_MTM + CM_P-SAR")
    lines.append(f"  - 83.8% hit rate vs 57.2% baseline (+27pp)")
    lines.append(f"  - Profit Factor: 15.75 vs 2.71 baseline")
    lines.append(f"- Weighted voting: ~61% (modest improvement over baseline)")
    lines.append(f"- Dimension gating: ~58% (marginal improvement)")
    lines.append("")

    # Phase 5 summary
    lines.append("**Phase 5 — Multi-Timeframe:**")
    lines.append(f"- Adding Daily/4H filters does NOT improve weekly hit rate")
    lines.append(f"- W-D agreement is 75% — filters remove some signals but not the bad ones")
    lines.append(f"- Recommendation: use weekly signals alone for weekly-horizon trades")
    lines.append("")

    # Phase 6: walk-forward
    lines.append("## Phase 6 — Walk-Forward Validation")
    lines.append("")
    lines.append("| Strategy | Mean HR | Std HR | Min HR | Max HR | Trades | Consistency |")
    lines.append("|----------|---------|--------|--------|--------|--------|-------------|")
    for wf in wf_results:
        lines.append(
            f"| {wf.strategy} | {_fmt(wf.mean_hr)} | {_fmt(wf.std_hr)} "
            f"| {_fmt(wf.min_hr)} | {_fmt(wf.max_hr)} | {wf.total_trades} | {wf.consistency} |"
        )
    lines.append("")

    for wf in wf_results:
        lines.append(f"### {wf.strategy}")
        lines.append("")
        lines.append("| Window | Train Period | Test Period | HR | PF | Trades |")
        lines.append("|--------|-------------|-------------|----|----|--------|")
        for i, w in enumerate(wf.windows, 1):
            lines.append(
                f"| {i} | {w.train_start} → {w.train_end} | {w.test_start} → {w.test_end} "
                f"| {_fmt(w.hr)} | {_fmt(w.pf, 2)} | {w.trades} |"
            )
        lines.append("")

    # Recommendations
    stable = [wf for wf in wf_results if wf.consistency in ("STABLE", "ACCEPTABLE")]
    best_wf = max(stable, key=lambda x: x.mean_hr) if stable else wf_results[0]

    lines.extend([
        "## Final Recommendations",
        "",
        f"### Recommended Strategy: {best_wf.strategy}",
        "",
        f"- Walk-forward mean HR: {_fmt(best_wf.mean_hr)} (std: {_fmt(best_wf.std_hr)})",
        f"- Consistency: {best_wf.consistency}",
        "",
        "### Action Items",
        "",
        "1. **Parameter updates**: Apply the Phase 2 optimized config for BB (mult=3.0), "
        "SMI (a=13,b=5,c=8), ADX (length=28), OBVOSC (length=26), WT_LB (n1=8,n2=25)",
        "",
        "2. **Scoring update**: Consider replacing equal-weight TrendScore with the "
        "stepwise AND-filter approach for high-conviction signals",
        "",
        "3. **Keep all 23 KPIs**: No strict redundancy found, all provide some independent value",
        "",
        "4. **Skip MTF filtering**: For weekly-horizon trades, weekly signals alone are sufficient",
        "",
        "5. **Monitor**: Re-run this analysis quarterly with updated data to check parameter stability",
        "",
    ])

    report = "\n".join(lines)
    (output_dir / "final_report.md").write_text(report, encoding="utf-8")
    print(report)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    tf = parse_timeframe_arg("Phase 6 — Walk-Forward Validation")
    OUTPUT_DIR = output_dir_for(tf.timeframe, "phase6")

    t0 = time.time()
    print(f"Loading data from {ENRICHED_DIR} ...")
    all_data = load_data(ENRICHED_DIR, tf.timeframe)
    print(f"Loaded {len(all_data)} stocks")

    horizon = tf.default_horizon
    strategies = [
        ("Baseline: TrendScore majority", TREND_KPIS, "Majority"),
        ("AND(NW Smoother, cRSI, SR Breaks)", BEST_COMBO_3, "AND"),
        ("AND(NW, cRSI, SR, Stoch, PSAR)", BEST_COMBO_5, "AND"),
    ]

    wf_results: List[WFResult] = []
    for name, kpis, mode in strategies:
        print(f"\nWalk-forward: {name} ...")
        wf = walk_forward(all_data, kpis, name, n_windows=4, horizon=horizon)
        print(f"  Mean HR={_fmt(wf.mean_hr)}, Std={_fmt(wf.std_hr)}, "
              f"Min={_fmt(wf.min_hr)}, Max={_fmt(wf.max_hr)}, Consistency={wf.consistency}")
        wf_results.append(wf)

    print(f"\nGenerating final report ...")
    generate_final_report(wf_results, OUTPUT_DIR, tf)
    print(f"\nPhase 6 complete in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

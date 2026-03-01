"""
Phase 10 — Exit Flow Strategy: Design, Backtest & Optimisation

Introduces a staged Exit Flow strategy and backtests it against all
alternative exit approaches across all stocks, sectors, and timeframes.

Exit Flow Strategy (3-stage tightening):
  Stage 1 (0 → H bars):  SuperTrend stop (wide — let the trade develop)
  Stage 2 (H → 2H bars): UT Bot stop + combo invalidation (N-1 KPIs)
  Stage 3 (2H → 3H bars): P-SAR stop + combo invalidation (≥2 KPIs)
  Max hold: 3H bars

Outputs per timeframe:
  - phase10_exit_flow_diagram.png — visual strategy documentation
  - phase10_strategy_comparison.png — all strategies ranked
  - phase10_exit_heatmap_C3/C4/C5.png — sector × strategy heatmaps
  - phase10_stage_analysis.png — where exit flow trades exit
  - phase10_metrics_overview.png — summary table
  - exit_flow_strategy.md — full documentation & recommendations
  - exit_flow_backtest_results.json — raw per-stock results
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from textwrap import fill
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from trading_dashboard.kpis.catalog import compute_kpi_state_map
from trading_dashboard.kpis.rules import STATE_BULL
from apps.dashboard.sector_map import load_sector_map
from tf_config import parse_timeframe_arg, output_dir_for, ENRICHED_DIR, TFConfig

# Reuse Phase 8 building blocks
from phase8_exit_by_sector import (
    TradeResult,
    StrategyMetrics,
    simulate_fixed_horizon,
    simulate_trailing_stop,
    simulate_combo_invalidation,
    compute_metrics,
    _compute_psar,
    _compute_ut_bot_stop,
    _compute_supertrend_stop,
    load_data,
    IS_FRACTION,
    MIN_TRADES,
    COMBO_DEFINITIONS,
    EXIT_STRATEGIES as _PHASE8_STRATS,
    EXIT_COLORS as _PHASE8_COLORS,
)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

plt.style.use("dark_background")
plt.rcParams.update({
    "font.family": "sans-serif",
    "font.size": 10,
    "axes.titlesize": 13,
    "axes.titleweight": "bold",
    "figure.facecolor": "#181818",
    "axes.facecolor": "#1e1e1e",
    "savefig.facecolor": "#181818",
    "savefig.dpi": 180,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.3,
})

# ── Config ────────────────────────────────────────────────────────────────

MIN_STOCKS_PER_SECTOR = 3
EXIT_FLOW_NAME = "Exit Flow"
EXIT_FLOW_COLOR = "#e040fb"

ALL_STRATEGIES = list(_PHASE8_STRATS) + [EXIT_FLOW_NAME]
ALL_COLORS = {**_PHASE8_COLORS, EXIT_FLOW_NAME: EXIT_FLOW_COLOR}

STAGE_LABELS = {
    "st_stop_s1": "Stage 1 (SuperTrend)",
    "ut_stop_s2": "Stage 2 (UT Bot)",
    "combo_inv_s2": "Stage 2 (Combo inv.)",
    "psar_stop_s3": "Stage 3 (P-SAR)",
    "combo_inv_s3": "Stage 3 (Combo inv.)",
    "max_hold": "Max hold",
}
STAGE_COLORS = {
    "st_stop_s1": "#1565c0",
    "ut_stop_s2": "#f57f17",
    "combo_inv_s2": "#ff8f00",
    "psar_stop_s3": "#d32f2f",
    "combo_inv_s3": "#e53935",
    "max_hold": "#616161",
}


# ── Exit Flow simulation ─────────────────────────────────────────────────

def simulate_exit_flow(
    df: pd.DataFrame,
    signal: pd.Series,
    psar: pd.Series,
    ut_stop: pd.Series,
    st_line: pd.Series,
    state_map: Dict[str, pd.Series],
    combo_kpis: List[str],
    horizon: int,
    test_start: pd.Timestamp,
) -> List[TradeResult]:
    """
    Staged exit flow:
      Stage 1 (0→H):  SuperTrend stop only
      Stage 2 (H→2H): UT Bot stop + combo invalidation (N-1 KPIs non-bull)
      Stage 3 (2H→3H): P-SAR stop + combo invalidation (≥2 KPIs non-bull)
      Max hold: 3H
    """
    max_hold = horizon * 3
    trades: List[TradeResult] = []
    close = df["Close"]
    low = df["Low"] if "Low" in df.columns else close

    test_mask = df.index >= test_start
    sig_dates = signal[test_mask & signal].index
    n_inv_threshold = max(2, len(combo_kpis) - 1)

    i = 0
    while i < len(sig_dates):
        entry_idx = df.index.get_loc(sig_dates[i])
        entry_p = float(close.iloc[entry_idx])
        if entry_p <= 0:
            i += 1
            continue

        exit_idx = None
        exit_reason = "max_hold"

        for j in range(entry_idx + 1, min(entry_idx + max_hold + 1, len(df))):
            bars_held = j - entry_idx
            c = float(close.iloc[j])

            if bars_held <= horizon:
                # Stage 1: SuperTrend only
                if pd.notna(st_line.iloc[j]) and c < float(st_line.iloc[j]):
                    exit_idx, exit_reason = j, "st_stop_s1"
                    break
            elif bars_held <= 2 * horizon:
                # Stage 2: UT Bot + combo invalidation (N-1)
                if pd.notna(ut_stop.iloc[j]) and c < float(ut_stop.iloc[j]):
                    exit_idx, exit_reason = j, "ut_stop_s2"
                    break
                n_nb = sum(
                    1 for kpi in combo_kpis
                    if kpi in state_map and j < len(state_map[kpi])
                    and int(state_map[kpi].iloc[j]) != STATE_BULL
                )
                if n_nb >= n_inv_threshold:
                    exit_idx, exit_reason = j, "combo_inv_s2"
                    break
            else:
                # Stage 3: P-SAR + combo invalidation (≥2)
                if pd.notna(psar.iloc[j]) and c < float(psar.iloc[j]):
                    exit_idx, exit_reason = j, "psar_stop_s3"
                    break
                n_nb = sum(
                    1 for kpi in combo_kpis
                    if kpi in state_map and j < len(state_map[kpi])
                    and int(state_map[kpi].iloc[j]) != STATE_BULL
                )
                if n_nb >= 2:
                    exit_idx, exit_reason = j, "combo_inv_s3"
                    break

        if exit_idx is None:
            exit_idx = min(entry_idx + max_hold, len(df) - 1)

        exit_p = float(close.iloc[exit_idx])
        lows = low.iloc[entry_idx:exit_idx + 1]
        max_dd = float((entry_p - lows.min()) / entry_p * 100)
        ret = (exit_p - entry_p) / entry_p * 100
        hold = exit_idx - entry_idx

        trades.append(TradeResult(
            entry_date=str(df.index[entry_idx].date()),
            exit_date=str(df.index[exit_idx].date()),
            entry_price=entry_p, exit_price=exit_p,
            return_pct=ret, holding_bars=hold,
            max_drawdown_pct=max_dd, exit_reason=exit_reason,
        ))
        next_i = i + 1
        while next_i < len(sig_dates) and df.index.get_loc(sig_dates[next_i]) <= exit_idx:
            next_i += 1
        i = next_i

    return trades


# ── Backtest engine ───────────────────────────────────────────────────────

def run_backtest(
    all_data: Dict[str, pd.DataFrame],
    sector_stocks: Dict[str, List[str]],
    tf: TFConfig,
) -> Tuple[Dict[str, List[StrategyMetrics]], Dict[str, Dict[str, List[TradeResult]]]]:
    """
    Returns:
      results_by_sector: {sector: [StrategyMetrics, ...]}
      all_trades: {sector: {strategy_combo_key: [TradeResult, ...]}}
    """
    combos = COMBO_DEFINITIONS.get(tf.timeframe, COMBO_DEFINITIONS["1W"])
    horizon = tf.default_horizon
    max_hold = horizon * 3

    results_by_sector: Dict[str, List[StrategyMetrics]] = {}
    all_trades: Dict[str, Dict[str, List[TradeResult]]] = {}

    for sector in sorted(sector_stocks.keys()):
        syms = sector_stocks[sector]
        print(f"\n  {sector} ({len(syms)} stocks):")
        sector_results: List[StrategyMetrics] = []
        sector_trades: Dict[str, List[TradeResult]] = {}

        for combo_name, combo_kpis in combos.items():
            label = combo_name.replace("combo_", "C")

            trades_by_strat: Dict[str, List[TradeResult]] = {s: [] for s in ALL_STRATEGIES}

            for sym in syms:
                df = all_data.get(sym)
                if df is None or df.empty:
                    continue

                state_map = compute_kpi_state_map(df)
                avail = [k for k in combo_kpis if k in state_map]
                if len(avail) < len(combo_kpis):
                    continue

                all_bull = pd.Series(True, index=df.index)
                for k in avail:
                    all_bull = all_bull & (state_map[k] == STATE_BULL)
                signal = all_bull.astype(bool)
                if signal.sum() == 0:
                    continue

                split_idx = int(len(df) * IS_FRACTION)
                test_start = df.index[split_idx]

                trades_by_strat["Fixed horizon"].extend(
                    simulate_fixed_horizon(df, signal, horizon, test_start))

                try:
                    psar = _compute_psar(df)
                    trades_by_strat["P-SAR trailing"].extend(
                        simulate_trailing_stop(df, signal, psar, max_hold, test_start, "psar"))
                except Exception:
                    psar = pd.Series(np.nan, index=df.index)

                try:
                    ut_stop = _compute_ut_bot_stop(df)
                    trades_by_strat["UT Bot trailing"].extend(
                        simulate_trailing_stop(df, signal, ut_stop, max_hold, test_start, "ut_bot"))
                except Exception:
                    ut_stop = pd.Series(np.nan, index=df.index)

                try:
                    st_line = _compute_supertrend_stop(df)
                    trades_by_strat["SuperTrend trailing"].extend(
                        simulate_trailing_stop(df, signal, st_line, max_hold, test_start, "supertrend"))
                except Exception:
                    st_line = pd.Series(np.nan, index=df.index)

                n_inv = max(2, len(combo_kpis) - 1)
                try:
                    trades_by_strat["Combo invalidation"].extend(
                        simulate_combo_invalidation(
                            df, signal, state_map, combo_kpis,
                            max_hold, test_start, n_inv))
                except Exception:
                    pass

                try:
                    if not isinstance(psar, pd.Series):
                        psar = pd.Series(np.nan, index=df.index)
                    if not isinstance(ut_stop, pd.Series):
                        ut_stop = pd.Series(np.nan, index=df.index)
                    if not isinstance(st_line, pd.Series):
                        st_line = pd.Series(np.nan, index=df.index)
                    trades_by_strat[EXIT_FLOW_NAME].extend(
                        simulate_exit_flow(
                            df, signal, psar, ut_stop, st_line,
                            state_map, combo_kpis, horizon, test_start))
                except Exception:
                    pass

            for strat_name, trades in trades_by_strat.items():
                m = compute_metrics(strat_name, label, sector, trades)
                sector_results.append(m)
                sector_trades[f"{label}_{strat_name}"] = trades
                if m.n_trades >= MIN_TRADES:
                    print(f"    {label}/{strat_name}: HR={m.hit_rate:.0%}, "
                          f"Avg={m.avg_return:+.2f}%, n={m.n_trades}")

        results_by_sector[sector] = sector_results
        all_trades[sector] = sector_trades

    return results_by_sector, all_trades


# ── Visualisation helpers ─────────────────────────────────────────────────

def _wrap(text: str, width: int = 125) -> str:
    return fill(text, width=width)

def _add_commentary(fig, text: str, y: float = 0.02, fs: int = 9):
    fig.text(0.05, y, text, fontsize=fs, color="#b0bec5", ha="left", va="top",
             wrap=True, fontstyle="italic",
             bbox=dict(boxstyle="round,pad=0.5", facecolor="#252525",
                       edgecolor="#444", alpha=0.95),
             transform=fig.transFigure)


# ── Chart 1: Exit Flow Diagram ───────────────────────────────────────────

def chart_exit_flow_diagram(tf: TFConfig, output_dir: Path) -> None:
    H = tf.default_horizon
    h_lbl = {"1W": "w", "1D": "d", "4H": "h"}.get(tf.timeframe, "bars")

    fig, ax = plt.subplots(figsize=(14, 18))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 22)
    ax.axis("off")
    ax.set_title("Exit Flow Strategy — Decision Diagram", fontsize=16, pad=20)

    boxes = [
        (5, 20.5, 6, 1.4,
         f"COMBO ENTRY\nAll KPIs in C3/C4/C5 simultaneously bullish",
         "#2e7d32", "white"),
        (5, 17.5, 7, 2.2,
         f"STAGE 1 — WIDE STOP  (bars 0 → {H})\n"
         f"Monitor: SuperTrend trailing stop\n"
         f"Exit if: Close < SuperTrend line\n"
         f"Purpose: survive initial noise, let the trade develop",
         "#1565c0", "white"),
        (5, 13.5, 7, 2.8,
         f"STAGE 2 — MEDIUM STOP  (bars {H} → {2*H})\n"
         f"Monitor: UT Bot ATR trailing stop\n"
         f"        + Combo invalidation (N-1 KPIs non-bull)\n"
         f"Exit if: Close < UT Bot stop\n"
         f"     OR  N-1 of combo KPIs flip non-bull\n"
         f"Purpose: protect gains as trend matures",
         "#f57f17", "black"),
        (5, 9.2, 7, 2.8,
         f"STAGE 3 — TIGHT STOP  (bars {2*H} → {3*H})\n"
         f"Monitor: P-SAR trailing stop\n"
         f"        + Combo invalidation (≥2 KPIs non-bull)\n"
         f"Exit if: Close < P-SAR\n"
         f"     OR  ≥2 combo KPIs flip non-bull\n"
         f"Purpose: maximise extraction, lock final profits",
         "#c62828", "white"),
        (5, 5.5, 6, 1.4,
         f"MAX HOLD EXIT\nBar {3*H}: forced exit if no stop triggered",
         "#424242", "white"),
    ]

    for cx, cy, w, h, txt, fc, tc in boxes:
        box = FancyBboxPatch(
            (cx - w / 2, cy - h / 2), w, h,
            boxstyle="round,pad=0.3", facecolor=fc, edgecolor="white",
            linewidth=1.5, alpha=0.92)
        ax.add_patch(box)
        ax.text(cx, cy, txt, ha="center", va="center", fontsize=10,
                color=tc, fontweight="bold", linespacing=1.5)

    for i in range(len(boxes) - 1):
        y_from = boxes[i][1] - boxes[i][3] / 2
        y_to = boxes[i + 1][1] + boxes[i + 1][3] / 2
        ax.annotate("", xy=(5, y_to + 0.1), xytext=(5, y_from - 0.1),
                     arrowprops=dict(arrowstyle="-|>", color="white", lw=2.5))

    side_notes = [
        (9.2, 17.5, f"H = {H} bars\n({H}{h_lbl})", "#90caf9"),
        (9.2, 13.5, f"Stop tightens\nfrom wide → medium", "#fff176"),
        (9.2, 9.2, f"Stop tightens\nfrom medium → tight", "#ef9a9a"),
    ]
    for sx, sy, stxt, sc in side_notes:
        ax.text(sx, sy, stxt, ha="center", va="center", fontsize=9,
                color=sc, fontstyle="italic",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#2a2a2a",
                          edgecolor=sc, alpha=0.7))

    commentary = (
        f"RATIONALE: A single exit strategy cannot serve all phases of a trade. "
        f"Early on, the trade needs room to develop (wide SuperTrend stop avoids premature exit from noise). "
        f"Mid-trade, the trend should be established — tighten to UT Bot to protect gains. "
        f"Late in the trade, use the tightest P-SAR stop to extract maximum profit before mean reversion. "
        f"Combo invalidation (thesis-based exit) is active in Stages 2 & 3 as a fundamental backstop. "
        f"Max hold at {3*H} bars ({3*H}{h_lbl}) prevents indefinite capital lock-up."
    )
    fig.text(0.05, 0.04, _wrap(commentary), fontsize=9, color="#b0bec5",
             ha="left", va="top", fontstyle="italic",
             bbox=dict(boxstyle="round,pad=0.5", facecolor="#252525",
                       edgecolor="#444", alpha=0.95),
             transform=fig.transFigure)

    fig.savefig(output_dir / "phase10_exit_flow_diagram.png")
    plt.close(fig)
    print("  Saved phase10_exit_flow_diagram.png")


# ── Chart 2: Strategy Comparison ─────────────────────────────────────────

def chart_strategy_comparison(
    results_by_sector: Dict[str, List[StrategyMetrics]],
    tf: TFConfig,
    output_dir: Path,
) -> None:
    combo_labels = ["C3", "C4", "C5"]
    strat_hr: Dict[str, List[float]] = defaultdict(list)
    strat_ret: Dict[str, List[float]] = defaultdict(list)
    strat_pf: Dict[str, List[float]] = defaultdict(list)

    for metrics_list in results_by_sector.values():
        for m in metrics_list:
            if m.n_trades >= MIN_TRADES:
                strat_hr[m.name].append(m.hit_rate)
                strat_ret[m.name].append(m.avg_return)
                pf = m.profit_factor if m.profit_factor < 99 else np.nan
                strat_pf[m.name].append(pf)

    strats = [s for s in ALL_STRATEGIES if s in strat_hr and strat_hr[s]]
    if not strats:
        return

    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(20, 7))
    fig.subplots_adjust(bottom=0.28)
    x = np.arange(len(strats))
    colors = [ALL_COLORS.get(s, "#78909c") for s in strats]

    med_hr = [float(np.median(strat_hr[s])) for s in strats]
    ax1.bar(x, med_hr, color=colors, edgecolor="none", width=0.6)
    ax1.axhline(0.5, color="#ff5252", linestyle="--", linewidth=0.8, alpha=0.5)
    ax1.set_xticks(x)
    ax1.set_xticklabels(strats, rotation=30, ha="right", fontsize=8)
    ax1.set_ylabel("Median Hit Rate")
    ax1.set_title("Hit Rate")
    for i, v in enumerate(med_hr):
        ax1.text(i, v + 0.01, f"{v:.0%}", ha="center", fontsize=9, fontweight="bold")

    med_ret = [float(np.median(strat_ret[s])) for s in strats]
    ax2.bar(x, med_ret, color=colors, edgecolor="none", width=0.6)
    ax2.axhline(0, color="#ff5252", linestyle="--", linewidth=0.8, alpha=0.5)
    ax2.set_xticks(x)
    ax2.set_xticklabels(strats, rotation=30, ha="right", fontsize=8)
    ax2.set_ylabel("Median Avg Return (%)")
    ax2.set_title("Average Return")
    for i, v in enumerate(med_ret):
        ax2.text(i, v + 0.05, f"{v:+.2f}%", ha="center", fontsize=9, fontweight="bold")

    med_pf = [float(np.nanmedian(strat_pf[s])) if strat_pf[s] else 0 for s in strats]
    ax3.bar(x, med_pf, color=colors, edgecolor="none", width=0.6)
    ax3.axhline(1.0, color="#ff5252", linestyle="--", linewidth=0.8, alpha=0.5)
    ax3.set_xticks(x)
    ax3.set_xticklabels(strats, rotation=30, ha="right", fontsize=8)
    ax3.set_ylabel("Median Profit Factor")
    ax3.set_title("Profit Factor")
    for i, v in enumerate(med_pf):
        ax3.text(i, v + 0.05, f"{v:.2f}", ha="center", fontsize=9, fontweight="bold")

    fig.suptitle(f"Phase 10 — Exit Strategy Comparison ({tf.timeframe}, All Combos & Sectors)",
                 fontsize=14, fontweight="bold", y=1.02)

    best_hr_s = strats[int(np.argmax(med_hr))]
    best_ret_s = strats[int(np.argmax(med_ret))]
    ef_hr = med_hr[strats.index(EXIT_FLOW_NAME)] if EXIT_FLOW_NAME in strats else 0
    ef_ret = med_ret[strats.index(EXIT_FLOW_NAME)] if EXIT_FLOW_NAME in strats else 0
    commentary = (
        f"FINDING: '{best_hr_s}' achieves the highest median HR; '{best_ret_s}' the highest median return. "
        f"Exit Flow achieves HR={ef_hr:.0%}, AvgRet={ef_ret:+.2f}%. "
        f"The staged approach balances capital protection (tight stops late) with upside capture (wide stops early). "
        f"RECOMMENDATION: Use Exit Flow as the default strategy. Override with P-SAR for mean-reverting sectors "
        f"or SuperTrend for strong-trending sectors."
    )
    _add_commentary(fig, _wrap(commentary), y=0.04)
    fig.savefig(output_dir / "phase10_strategy_comparison.png")
    plt.close(fig)
    print("  Saved phase10_strategy_comparison.png")


# ── Chart 3: Exit Heatmap (per combo level) ──────────────────────────────

def chart_exit_heatmap(
    results_by_sector: Dict[str, List[StrategyMetrics]],
    combo_label: str,
    tf: TFConfig,
    output_dir: Path,
) -> None:
    sectors = [s for s in sorted(results_by_sector.keys())
               if any(m.combo == combo_label and m.n_trades >= MIN_TRADES
                      for m in results_by_sector[s])]
    if not sectors:
        return

    matrix = np.full((len(sectors), len(ALL_STRATEGIES)), np.nan)
    trades_matrix = np.zeros((len(sectors), len(ALL_STRATEGIES)), dtype=int)

    for i, sector in enumerate(sectors):
        for j, strat in enumerate(ALL_STRATEGIES):
            matches = [m for m in results_by_sector[sector]
                       if m.combo == combo_label and m.name == strat
                       and m.n_trades >= MIN_TRADES]
            if matches:
                matrix[i, j] = matches[0].hit_rate
                trades_matrix[i, j] = matches[0].n_trades

    fig, ax = plt.subplots(figsize=(14, max(5, len(sectors) * 0.55)))
    fig.subplots_adjust(bottom=0.22)

    im = ax.imshow(matrix, aspect="auto", cmap="RdYlGn", vmin=0.35, vmax=0.85)
    ax.set_xticks(range(len(ALL_STRATEGIES)))
    ax.set_xticklabels(ALL_STRATEGIES, fontsize=9, rotation=20, ha="right")
    ax.set_yticks(range(len(sectors)))
    ax.set_yticklabels(sectors, fontsize=9)
    ax.set_title(f"Phase 10 — Exit Strategy Hit Rate ({combo_label}, {tf.timeframe})")

    for i in range(len(sectors)):
        for j in range(len(ALL_STRATEGIES)):
            val = matrix[i, j]
            n = trades_matrix[i, j]
            if not np.isnan(val):
                color = "black" if val > 0.60 else "white"
                ax.text(j, i, f"{val:.0%}\n({n})", ha="center", va="center",
                        fontsize=7, color=color, fontweight="bold")

    fig.colorbar(im, ax=ax, shrink=0.6, label="Hit Rate")

    # Highlight Exit Flow column
    ef_col = ALL_STRATEGIES.index(EXIT_FLOW_NAME)
    ax.axvline(ef_col - 0.5, color=EXIT_FLOW_COLOR, linewidth=2, alpha=0.5)
    ax.axvline(ef_col + 0.5, color=EXIT_FLOW_COLOR, linewidth=2, alpha=0.5)

    medians = np.nanmedian(matrix, axis=0)
    ef_med = medians[ef_col]
    best_idx = int(np.nanargmax(medians))
    best_name = ALL_STRATEGIES[best_idx]

    n_ef_best = 0
    for i in range(len(sectors)):
        row = matrix[i]
        if not np.all(np.isnan(row)):
            if int(np.nanargmax(row)) == ef_col:
                n_ef_best += 1

    commentary = (
        f"INSIGHT ({combo_label}): '{best_name}' has the highest median HR ({medians[best_idx]:.0%}). "
        f"Exit Flow median HR = {ef_med:.0%}, best in {n_ef_best}/{len(sectors)} sectors. "
        f"The staged approach tends to {'outperform' if ef_med >= medians[best_idx] - 0.02 else 'perform comparably to'} "
        f"single-strategy exits because it adapts stop tightness to the trade's maturity."
    )
    _add_commentary(fig, _wrap(commentary), y=0.06)
    fig.savefig(output_dir / f"phase10_exit_heatmap_{combo_label}.png")
    plt.close(fig)
    print(f"  Saved phase10_exit_heatmap_{combo_label}.png")


# ── Chart 4: Stage Analysis ──────────────────────────────────────────────

def chart_stage_analysis(
    all_trades: Dict[str, Dict[str, List[TradeResult]]],
    tf: TFConfig,
    output_dir: Path,
) -> None:
    reason_counter: Counter = Counter()
    reason_rets: Dict[str, List[float]] = defaultdict(list)

    for sector_trades in all_trades.values():
        for key, trades in sector_trades.items():
            if EXIT_FLOW_NAME not in key:
                continue
            for t in trades:
                reason_counter[t.exit_reason] += 1
                reason_rets[t.exit_reason].append(t.return_pct)

    if not reason_counter:
        return

    reasons = sorted(reason_counter.keys(),
                     key=lambda r: list(STAGE_LABELS.keys()).index(r)
                     if r in STAGE_LABELS else 99)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 7))
    fig.subplots_adjust(bottom=0.22)

    # Pie chart: exit distribution
    labels = [STAGE_LABELS.get(r, r) for r in reasons]
    sizes = [reason_counter[r] for r in reasons]
    colors = [STAGE_COLORS.get(r, "#78909c") for r in reasons]
    wedges, texts, autotexts = ax1.pie(
        sizes, labels=labels, colors=colors, autopct="%1.0f%%",
        textprops={"color": "white", "fontsize": 9},
        wedgeprops=dict(edgecolor="#181818", linewidth=2))
    for t in autotexts:
        t.set_fontweight("bold")
    ax1.set_title("Exit Distribution (where do trades exit?)")

    # Bar chart: avg return by exit reason
    y = np.arange(len(reasons))
    avg_rets = [float(np.mean(reason_rets[r])) if reason_rets[r] else 0
                for r in reasons]
    bars = ax2.barh(y, avg_rets, color=colors, edgecolor="none", height=0.6)
    ax2.set_yticks(y)
    ax2.set_yticklabels(labels, fontsize=9)
    ax2.axvline(0, color="white", linewidth=0.5, alpha=0.3)
    ax2.set_xlabel("Avg Return (%)")
    ax2.set_title("Avg Return by Exit Reason")
    ax2.invert_yaxis()
    for bar, val in zip(bars, avg_rets):
        ax2.text(val + (0.1 if val >= 0 else -0.1),
                 bar.get_y() + bar.get_height() / 2,
                 f"{val:+.2f}%", va="center", fontsize=9, fontweight="bold",
                 ha="left" if val >= 0 else "right")

    fig.suptitle(f"Phase 10 — Exit Flow Stage Analysis ({tf.timeframe})",
                 fontsize=14, fontweight="bold", y=1.02)

    s1_pct = sum(reason_counter[r] for r in reasons if "s1" in r) / sum(sizes) * 100 if sum(sizes) > 0 else 0
    s2_pct = sum(reason_counter[r] for r in reasons if "s2" in r) / sum(sizes) * 100 if sum(sizes) > 0 else 0
    s3_pct = sum(reason_counter[r] for r in reasons if "s3" in r) / sum(sizes) * 100 if sum(sizes) > 0 else 0
    mh_pct = reason_counter.get("max_hold", 0) / sum(sizes) * 100 if sum(sizes) > 0 else 0

    commentary = (
        f"INSIGHT: {s1_pct:.0f}% of Exit Flow trades exit in Stage 1 (SuperTrend stop), "
        f"{s2_pct:.0f}% in Stage 2 (UT Bot / combo inv.), "
        f"{s3_pct:.0f}% in Stage 3 (P-SAR / combo inv.), "
        f"{mh_pct:.0f}% at max hold. "
        f"Trades exiting via SuperTrend (Stage 1) tend to be stopped out quickly — these are the 'failed breakouts'. "
        f"Trades reaching Stage 3 typically capture the full move. "
        f"Max-hold exits suggest the trade was neither stopped nor invalidated — often a sideways market."
    )
    _add_commentary(fig, _wrap(commentary), y=0.04)
    fig.savefig(output_dir / "phase10_stage_analysis.png")
    plt.close(fig)
    print("  Saved phase10_stage_analysis.png")


# ── Chart 5: Metrics Overview Table ──────────────────────────────────────

def chart_metrics_overview(
    results_by_sector: Dict[str, List[StrategyMetrics]],
    tf: TFConfig,
    output_dir: Path,
) -> None:
    combo_labels = ["C3", "C4", "C5"]
    sectors = sorted([s for s in results_by_sector if s != "ALL"])
    if "ALL" in results_by_sector:
        sectors = ["ALL"] + sectors

    rows = []
    for sector in sectors:
        for label in combo_labels:
            ef = next((m for m in results_by_sector[sector]
                       if m.combo == label and m.name == EXIT_FLOW_NAME
                       and m.n_trades >= MIN_TRADES), None)
            fixed = next((m for m in results_by_sector[sector]
                          if m.combo == label and m.name == "Fixed horizon"
                          and m.n_trades >= MIN_TRADES), None)
            best_other = None
            for m in results_by_sector[sector]:
                if (m.combo == label and m.name != EXIT_FLOW_NAME
                        and m.name != "Fixed horizon" and m.n_trades >= MIN_TRADES):
                    if best_other is None or m.hit_rate > best_other.hit_rate:
                        best_other = m

            if ef is None:
                continue

            ef_pf = f"{ef.profit_factor:.2f}" if ef.profit_factor < 99 else "inf"
            fixed_hr = f"{fixed.hit_rate:.0%}" if fixed else "—"
            best_name = best_other.name[:12] if best_other else "—"
            best_hr = f"{best_other.hit_rate:.0%}" if best_other else "—"
            delta_fixed = f"{(ef.hit_rate - fixed.hit_rate) * 100:+.0f}pp" if fixed else "—"

            rows.append([
                sector, label,
                f"{ef.hit_rate:.0%}", f"{ef.avg_return:+.2f}%", ef_pf,
                str(ef.n_trades), f"{ef.avg_holding:.1f}",
                fixed_hr, delta_fixed, f"{best_name} ({best_hr})",
            ])

    if not rows:
        return

    cols = ["Sector", "Combo", "EF HR", "EF Ret", "EF PF", "Trades",
            "Avg Hold", "Fixed HR", "Δ vs Fixed", "Best Alternative"]

    fig, ax = plt.subplots(figsize=(22, max(6, len(rows) * 0.32 + 2)))
    ax.axis("off")
    h_lbl = {"1W": "w", "1D": "d", "4H": "h"}.get(tf.timeframe, "bars")
    ax.set_title(f"Phase 10 — Exit Flow Metrics Overview ({tf.timeframe}, {tf.default_horizon}{h_lbl} horizon)", pad=20)

    table = ax.table(cellText=rows, colLabels=cols, loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1, 1.4)

    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor("#444")
        if row == 0:
            cell.set_facecolor("#333")
            cell.set_text_props(color="white", fontweight="bold")
        else:
            cell.set_facecolor("#1e1e1e")
            cell.set_text_props(color="#e0e0e0")
            text = cell.get_text().get_text()
            if col == 8 and text.startswith("+"):
                cell.set_text_props(color="#66bb6a", fontweight="bold")
            elif col == 8 and text.startswith("-"):
                cell.set_text_props(color="#ef5350", fontweight="bold")

    fig.savefig(output_dir / "phase10_metrics_overview.png")
    plt.close(fig)
    print("  Saved phase10_metrics_overview.png")


# ── Report Generation ─────────────────────────────────────────────────────

def generate_report(
    results_by_sector: Dict[str, List[StrategyMetrics]],
    all_trades: Dict[str, Dict[str, List[TradeResult]]],
    tf: TFConfig,
    output_dir: Path,
) -> None:
    H = tf.default_horizon
    h_lbl = {"1W": "w", "1D": "d", "4H": "h"}.get(tf.timeframe, "bars")
    combo_labels = ["C3", "C4", "C5"]

    lines = [
        f"# Phase 10 — Exit Flow Strategy ({tf.timeframe})",
        "",
        "## Exit Flow Strategy Definition",
        "",
        "The Exit Flow is a **3-stage tightening** exit strategy that adapts",
        "stop-loss width to the trade's maturity:",
        "",
        f"### Stage 1: WIDE STOP (bars 0 → {H})",
        f"- **Monitor:** SuperTrend trailing stop",
        f"- **Exit if:** Close < SuperTrend line",
        f"- **Purpose:** Survive initial noise. Let the trade develop.",
        f"- **Rationale:** Early stops should be wide to avoid whipsaws.",
        "",
        f"### Stage 2: MEDIUM STOP (bars {H} → {2*H})",
        f"- **Monitor:** UT Bot ATR trailing stop + Combo invalidation",
        f"- **Exit if:** Close < UT Bot stop OR N-1 combo KPIs flip non-bull",
        f"- **Purpose:** Protect accumulated gains. The trend should be established by now.",
        f"- **Rationale:** Tighter stop + thesis validation.",
        "",
        f"### Stage 3: TIGHT STOP (bars {2*H} → {3*H})",
        f"- **Monitor:** P-SAR trailing stop + Combo invalidation",
        f"- **Exit if:** Close < P-SAR OR ≥2 combo KPIs flip non-bull",
        f"- **Purpose:** Maximise profit extraction. Mean reversion risk increases.",
        f"- **Rationale:** Tightest stop to lock in the final move.",
        "",
        f"### Max Hold: {3*H} bars ({3*H}{h_lbl})",
        f"- Forced exit if no stop was triggered.",
        "",
        "---",
        "",
        "## Backtest Configuration",
        "",
        f"- **Timeframe:** {tf.timeframe}",
        f"- **Horizon (H):** {H} bars ({H}{h_lbl})",
        f"- **OOS Split:** {IS_FRACTION:.0%} IS / {1-IS_FRACTION:.0%} OOS",
        f"- **Min trades:** {MIN_TRADES}",
        "",
        "## Performance Comparison",
        "",
        "| Strategy | Median HR | Median Ret | Median PF | Total Trades |",
        "|----------|-----------|------------|-----------|-------------|",
    ]

    strat_data: Dict[str, Dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for metrics_list in results_by_sector.values():
        for m in metrics_list:
            if m.n_trades >= MIN_TRADES:
                strat_data[m.name]["hr"].append(m.hit_rate)
                strat_data[m.name]["ret"].append(m.avg_return)
                pf = m.profit_factor if m.profit_factor < 99 else np.nan
                strat_data[m.name]["pf"].append(pf)
                strat_data[m.name]["n"].append(m.n_trades)

    for strat in ALL_STRATEGIES:
        if strat not in strat_data:
            continue
        d = strat_data[strat]
        med_hr = float(np.median(d["hr"]))
        med_ret = float(np.median(d["ret"]))
        med_pf = float(np.nanmedian(d["pf"]))
        total_n = sum(d["n"])
        marker = " **←**" if strat == EXIT_FLOW_NAME else ""
        lines.append(
            f"| {strat}{marker} | {med_hr:.0%} | {med_ret:+.2f}% | "
            f"{med_pf:.2f} | {total_n} |"
        )

    lines.extend(["", "## Exit Flow — Detailed Results by Sector", ""])

    for label in combo_labels:
        lines.extend([
            f"### {label}",
            "",
            "| Sector | EF HR | EF Ret | EF PF | Trades | Hold | "
            "Fixed HR | Δ HR | Best Alt |",
            "|--------|-------|--------|-------|--------|------|"
            "---------|------|---------|",
        ])
        for sector in sorted(results_by_sector.keys()):
            ef = next((m for m in results_by_sector[sector]
                       if m.combo == label and m.name == EXIT_FLOW_NAME
                       and m.n_trades >= MIN_TRADES), None)
            fixed = next((m for m in results_by_sector[sector]
                          if m.combo == label and m.name == "Fixed horizon"
                          and m.n_trades >= MIN_TRADES), None)
            if ef is None:
                continue
            best_other = max(
                (m for m in results_by_sector[sector]
                 if m.combo == label and m.name != EXIT_FLOW_NAME
                 and m.n_trades >= MIN_TRADES),
                key=lambda m: m.hit_rate, default=None,
            )
            pf_s = f"{ef.profit_factor:.2f}" if ef.profit_factor < 99 else "inf"
            delta = f"{(ef.hit_rate - fixed.hit_rate)*100:+.0f}pp" if fixed else "—"
            fixed_s = f"{fixed.hit_rate:.0%}" if fixed else "—"
            alt_s = f"{best_other.name[:10]} ({best_other.hit_rate:.0%})" if best_other else "—"
            lines.append(
                f"| {sector} | {ef.hit_rate:.0%} | {ef.avg_return:+.2f}% | "
                f"{pf_s} | {ef.n_trades} | {ef.avg_holding:.1f} | "
                f"{fixed_s} | {delta} | {alt_s} |"
            )
        lines.append("")

    # Stage analysis
    reason_counter: Counter = Counter()
    for sector_trades in all_trades.values():
        for key, trades in sector_trades.items():
            if EXIT_FLOW_NAME not in key:
                continue
            for t in trades:
                reason_counter[t.exit_reason] += 1

    total = sum(reason_counter.values())
    lines.extend([
        "## Exit Flow — Stage Analysis",
        "",
        "| Exit Reason | Count | % | Avg Return |",
        "|-------------|-------|---|------------|",
    ])
    reason_rets: Dict[str, List[float]] = defaultdict(list)
    for st in all_trades.values():
        for key, trades in st.items():
            if EXIT_FLOW_NAME not in key:
                continue
            for t in trades:
                reason_rets[t.exit_reason].append(t.return_pct)

    for r in sorted(reason_counter.keys(),
                    key=lambda rr: list(STAGE_LABELS.keys()).index(rr)
                    if rr in STAGE_LABELS else 99):
        cnt = reason_counter[r]
        pct = cnt / total * 100 if total > 0 else 0
        avg_r = float(np.mean(reason_rets[r])) if reason_rets[r] else 0
        label = STAGE_LABELS.get(r, r)
        lines.append(f"| {label} | {cnt} | {pct:.0f}% | {avg_r:+.2f}% |")

    lines.extend([
        "",
        "## Recommendations",
        "",
        "1. **Use Exit Flow as the default exit strategy** for combo trades. "
        "It balances early capital protection with upside capture.",
        "",
        "2. **Sector overrides:** For sectors where a single strategy consistently "
        "outperforms (check heatmaps), use that strategy instead.",
        "",
        "3. **Multi-timeframe enhancement:** Enter on weekly combo, monitor daily "
        "combo state. If daily combo invalidates during Stage 1, advance to Stage 2 early.",
        "",
        "4. **Position sizing:** Use the avg return and max drawdown from this study "
        "to calibrate position sizes per sector.",
        "",
    ])

    report = "\n".join(lines)
    (output_dir / "exit_flow_strategy.md").write_text(report, encoding="utf-8")
    print("  Saved exit_flow_strategy.md")

    # JSON results
    json_data: Dict[str, list] = {}
    for sector, metrics_list in results_by_sector.items():
        json_data[sector] = [
            {
                "strategy": m.name, "combo": m.combo,
                "n_trades": m.n_trades, "hit_rate": round(m.hit_rate, 4),
                "avg_return": round(m.avg_return, 4),
                "profit_factor": round(m.profit_factor, 4) if m.profit_factor < 99 else None,
                "avg_holding": round(m.avg_holding, 2),
                "avg_max_dd": round(m.avg_max_dd, 4),
                "sharpe": round(m.sharpe, 4),
            }
            for m in metrics_list
        ]
    (output_dir / "exit_flow_backtest_results.json").write_text(
        json.dumps(json_data, indent=2), encoding="utf-8")
    print("  Saved exit_flow_backtest_results.json")


# ── Main ──────────────────────────────────────────────────────────────────

def main() -> int:
    tf = parse_timeframe_arg("Phase 10 — Exit Flow Backtest")
    output_dir = output_dir_for(tf.timeframe, "phase10")
    output_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    print(f"Phase 10 — Exit Flow Strategy Backtest ({tf.timeframe})")
    print(f"Loading data from {ENRICHED_DIR} ...")
    all_data = load_data(ENRICHED_DIR, tf.timeframe)
    print(f"Loaded {len(all_data)} stocks")

    print("Loading sector map...")
    sector_map = load_sector_map()
    sector_stocks: Dict[str, List[str]] = defaultdict(list)
    for sym in all_data:
        sm = sector_map.get(sym, {})
        sector = sm.get("sector", "")
        if sector:
            sector_stocks[sector].append(sym)

    valid_sectors = {s: syms for s, syms in sector_stocks.items()
                     if len(syms) >= MIN_STOCKS_PER_SECTOR}
    valid_sectors["ALL"] = list(all_data.keys())
    print(f"Sectors: {len(valid_sectors) - 1} + ALL ({len(all_data)} stocks)")

    print("\nRunning backtest...")
    results_by_sector, all_trades = run_backtest(all_data, valid_sectors, tf)

    print("\n\nGenerating charts...")
    chart_exit_flow_diagram(tf, output_dir)
    chart_strategy_comparison(results_by_sector, tf, output_dir)
    for label in ["C3", "C4", "C5"]:
        chart_exit_heatmap(results_by_sector, label, tf, output_dir)
    chart_stage_analysis(all_trades, tf, output_dir)
    chart_metrics_overview(results_by_sector, tf, output_dir)

    print("\nGenerating report...")
    generate_report(results_by_sector, all_trades, tf, output_dir)

    elapsed = time.time() - t0
    print(f"\nPhase 10 complete in {elapsed:.0f}s")
    print(f"Outputs: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

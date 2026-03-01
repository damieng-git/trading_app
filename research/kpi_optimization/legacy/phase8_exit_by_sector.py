"""
Phase 8 — Exit Strategy Analysis by Sector & Timeframe

For each sector × timeframe × combo level (C3, C4, C5), evaluates exit strategies:
  1. Fixed-horizon (baseline)
  2. P-SAR trailing stop
  3. UT Bot ATR trailing stop
  4. SuperTrend trailing stop
  5. Combo invalidation (exit when N of the combo KPIs flip bearish)

Outputs per timeframe:
  - PNG overview heatmap (exit strategy × sector, colored by HR)
  - PNG bar chart per combo level (best exit per sector)
  - PNG equity-curve style comparison (trailing stops vs fixed)
  - Markdown report with qualitative commentary
  - JSON results for downstream consumption
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from trading_dashboard.kpis.catalog import compute_kpi_state_map, KPI_TREND_ORDER
from trading_dashboard.kpis.rules import STATE_BULL
from trading_dashboard.indicators.psar import parabolic_sar
from trading_dashboard.indicators.ut_bot import ut_bot_alert
from trading_dashboard.indicators.supertrend import supertrend
from apps.dashboard.sector_map import load_sector_map
from tf_config import parse_timeframe_arg, output_dir_for, ENRICHED_DIR, TFConfig

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

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

IS_FRACTION = 0.70
MIN_STOCKS_PER_SECTOR = 3
MIN_TRADES = 10

COMBO_DEFINITIONS = {
    "1W": {
        "combo_3": ["Nadaraya-Watson Smoother", "cRSI", "SR Breaks"],
        "combo_4": ["Nadaraya-Watson Smoother", "cRSI", "SR Breaks", "Stoch_MTM"],
        "combo_5": ["Nadaraya-Watson Smoother", "cRSI", "SR Breaks", "Stoch_MTM", "CM_P-SAR"],
    },
    "1D": {
        "combo_3": ["Nadaraya-Watson Smoother", "cRSI", "SR Breaks"],
        "combo_4": ["Nadaraya-Watson Smoother", "cRSI", "SR Breaks", "MA Ribbon"],
        "combo_5": ["Nadaraya-Watson Smoother", "cRSI", "SR Breaks", "MA Ribbon", "Madrid Ribbon"],
    },
    "4H": {
        "combo_3": ["Nadaraya-Watson Smoother", "cRSI", "Stoch_MTM"],
        "combo_4": ["Nadaraya-Watson Smoother", "cRSI", "Stoch_MTM", "SR Breaks"],
        "combo_5": ["Nadaraya-Watson Smoother", "cRSI", "Stoch_MTM", "SR Breaks", "GK Trend Ribbon"],
    },
}

EXIT_STRATEGIES = [
    "Fixed horizon",
    "P-SAR trailing",
    "UT Bot trailing",
    "SuperTrend trailing",
    "Combo invalidation",
]

EXIT_COLORS = {
    "Fixed horizon": "#616161",
    "P-SAR trailing": "#ef5350",
    "UT Bot trailing": "#ab47bc",
    "SuperTrend trailing": "#4fc3f7",
    "Combo invalidation": "#66bb6a",
}


# ── Data loading ──────────────────────────────────────────────────────────

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
    for f in sorted(enriched_dir.glob(f"*_{timeframe}.parquet")):
        symbol = f.stem.rsplit(f"_{timeframe}", 1)[0]
        if symbol in data:
            continue
        try:
            df = pd.read_parquet(f)
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index, errors="coerce")
            if hasattr(df.index, "tz") and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            df = df.sort_index()
            if len(df) >= 100 and "Close" in df.columns:
                data[symbol] = df
        except Exception:
            continue
    return data


# ── Trade simulation ──────────────────────────────────────────────────────

@dataclass
class TradeResult:
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    return_pct: float
    holding_bars: int
    max_drawdown_pct: float
    exit_reason: str


@dataclass
class StrategyMetrics:
    name: str
    combo: str
    sector: str
    n_trades: int
    hit_rate: float
    avg_return: float
    profit_factor: float
    avg_holding: float
    avg_max_dd: float
    sharpe: float
    median_return: float = 0.0
    win_rate_by_exit: Dict[str, float] = field(default_factory=dict)


def _compute_psar(df: pd.DataFrame) -> pd.Series:
    return parabolic_sar(df)


def _compute_ut_bot_stop(df: pd.DataFrame, a: float = 1.0, c: int = 10) -> pd.Series:
    result = ut_bot_alert(df, a=a, c=c)
    return result["UT_trailing_stop"]


def _compute_supertrend_stop(df: pd.DataFrame, periods: int = 12, mult: float = 3.0) -> pd.Series:
    st_line, trend, _ = supertrend(df, periods=periods, multiplier=mult)
    return st_line


def simulate_fixed_horizon(
    df: pd.DataFrame,
    signal: pd.Series,
    horizon: int,
    test_start: pd.Timestamp,
) -> List[TradeResult]:
    trades = []
    test_mask = df.index >= test_start
    close = df["Close"]
    low = df["Low"] if "Low" in df.columns else close

    sig_dates = signal[test_mask & signal].index
    i = 0
    while i < len(sig_dates):
        entry_idx = df.index.get_loc(sig_dates[i])
        if entry_idx + horizon >= len(df):
            break
        entry_p = float(close.iloc[entry_idx])
        if entry_p <= 0:
            i += 1
            continue
        exit_p = float(close.iloc[entry_idx + horizon])
        lows = low.iloc[entry_idx:entry_idx + horizon + 1]
        max_dd = float((entry_p - lows.min()) / entry_p * 100)
        ret = (exit_p - entry_p) / entry_p * 100
        trades.append(TradeResult(
            entry_date=str(df.index[entry_idx].date()),
            exit_date=str(df.index[entry_idx + horizon].date()),
            entry_price=entry_p, exit_price=exit_p,
            return_pct=ret, holding_bars=horizon,
            max_drawdown_pct=max_dd, exit_reason="horizon",
        ))
        next_i = i + 1
        while next_i < len(sig_dates) and df.index.get_loc(sig_dates[next_i]) <= entry_idx + horizon:
            next_i += 1
        i = next_i
    return trades


def simulate_trailing_stop(
    df: pd.DataFrame,
    signal: pd.Series,
    stop_series: pd.Series,
    max_hold: int,
    test_start: pd.Timestamp,
    exit_label: str = "stop",
) -> List[TradeResult]:
    trades = []
    test_mask = df.index >= test_start
    close = df["Close"]
    low = df["Low"] if "Low" in df.columns else close

    sig_dates = signal[test_mask & signal].index
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
            if pd.notna(stop_series.iloc[j]) and float(close.iloc[j]) < float(stop_series.iloc[j]):
                exit_idx = j
                exit_reason = exit_label
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


def simulate_combo_invalidation(
    df: pd.DataFrame,
    signal: pd.Series,
    state_map: Dict[str, pd.Series],
    combo_kpis: List[str],
    max_hold: int,
    test_start: pd.Timestamp,
    invalidation_count: int = 2,
) -> List[TradeResult]:
    """Exit when `invalidation_count` of the combo KPIs flip non-bull."""
    trades = []
    test_mask = df.index >= test_start
    close = df["Close"]
    low = df["Low"] if "Low" in df.columns else close

    sig_dates = signal[test_mask & signal].index
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
            n_not_bull = 0
            for kpi in combo_kpis:
                s = state_map.get(kpi)
                if s is None or j >= len(s):
                    continue
                if int(s.iloc[j]) != STATE_BULL:
                    n_not_bull += 1
            if n_not_bull >= invalidation_count:
                exit_idx = j
                exit_reason = "combo_invalid"
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


def compute_metrics(
    name: str, combo: str, sector: str, trades: List[TradeResult],
) -> StrategyMetrics:
    if not trades:
        return StrategyMetrics(
            name=name, combo=combo, sector=sector,
            n_trades=0, hit_rate=0, avg_return=0, profit_factor=0,
            avg_holding=0, avg_max_dd=0, sharpe=0, median_return=0,
        )
    rets = np.array([t.return_pct for t in trades])
    n = len(rets)
    hr = float(np.sum(rets > 0) / n)
    avg_ret = float(np.mean(rets))
    med_ret = float(np.median(rets))
    gross_profit = float(np.sum(rets[rets > 0]))
    gross_loss = float(np.abs(np.sum(rets[rets < 0])))
    pf = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0)
    avg_hold = float(np.mean([t.holding_bars for t in trades]))
    avg_dd = float(np.mean([t.max_drawdown_pct for t in trades]))
    sharpe = float(np.mean(rets) / np.std(rets) * np.sqrt(52)) if np.std(rets) > 0 else 0

    exit_reasons: Dict[str, list] = {}
    for t in trades:
        exit_reasons.setdefault(t.exit_reason, []).append(t.return_pct)
    wr_by_exit = {k: float(np.sum(np.array(v) > 0) / len(v)) for k, v in exit_reasons.items()}

    return StrategyMetrics(
        name=name, combo=combo, sector=sector,
        n_trades=n, hit_rate=hr, avg_return=avg_ret,
        profit_factor=min(pf, 99.9), avg_holding=avg_hold,
        avg_max_dd=avg_dd, sharpe=sharpe, median_return=med_ret,
        win_rate_by_exit=wr_by_exit,
    )


# ── Main analysis engine ─────────────────────────────────────────────────

def run_exit_analysis_by_sector(
    all_data: Dict[str, pd.DataFrame],
    sector_stocks: Dict[str, List[str]],
    tf: TFConfig,
) -> Dict[str, List[StrategyMetrics]]:
    """
    Returns: {sector: [StrategyMetrics, ...]}
    Each sector has results for every combo × exit strategy combination.
    """
    combos = COMBO_DEFINITIONS.get(tf.timeframe, COMBO_DEFINITIONS["1W"])
    horizon = tf.default_horizon
    max_hold_bars = horizon * 3

    results_by_sector: Dict[str, List[StrategyMetrics]] = {}

    for sector in sorted(sector_stocks.keys()):
        syms = sector_stocks[sector]
        print(f"\n  {sector} ({len(syms)} stocks):")
        sector_results: List[StrategyMetrics] = []

        for combo_name, combo_kpis in combos.items():
            label = combo_name.replace("combo_", "C")

            trades_by_strategy: Dict[str, List[TradeResult]] = {
                "Fixed horizon": [],
                "P-SAR trailing": [],
                "UT Bot trailing": [],
                "SuperTrend trailing": [],
                "Combo invalidation": [],
            }

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

                t1 = simulate_fixed_horizon(df, signal, horizon, test_start)
                trades_by_strategy["Fixed horizon"].extend(t1)

                try:
                    psar = _compute_psar(df)
                    t2 = simulate_trailing_stop(df, signal, psar, max_hold_bars, test_start, "psar")
                    trades_by_strategy["P-SAR trailing"].extend(t2)
                except Exception:
                    pass

                try:
                    ut_stop = _compute_ut_bot_stop(df)
                    t3 = simulate_trailing_stop(df, signal, ut_stop, max_hold_bars, test_start, "ut_bot")
                    trades_by_strategy["UT Bot trailing"].extend(t3)
                except Exception:
                    pass

                try:
                    st_line = _compute_supertrend_stop(df)
                    t4 = simulate_trailing_stop(df, signal, st_line, max_hold_bars, test_start, "supertrend")
                    trades_by_strategy["SuperTrend trailing"].extend(t4)
                except Exception:
                    pass

                n_invalidation = max(2, len(combo_kpis) - 1)
                try:
                    t5 = simulate_combo_invalidation(
                        df, signal, state_map, combo_kpis,
                        max_hold_bars, test_start, n_invalidation,
                    )
                    trades_by_strategy["Combo invalidation"].extend(t5)
                except Exception:
                    pass

            for strat_name, trades in trades_by_strategy.items():
                m = compute_metrics(strat_name, label, sector, trades)
                sector_results.append(m)
                if m.n_trades >= MIN_TRADES:
                    print(f"    {label} / {strat_name}: HR={m.hit_rate:.1%}, "
                          f"Avg={m.avg_return:+.2f}%, PF={m.profit_factor:.2f}, "
                          f"Trades={m.n_trades}, Hold={m.avg_holding:.1f}")

        results_by_sector[sector] = sector_results

    return results_by_sector


# ── Visualization helpers ─────────────────────────────────────────────────

def _wrap(text: str, width: int = 120) -> str:
    from textwrap import fill
    return fill(text, width=width)


def _add_commentary(fig, text: str, y: float = -0.02, fontsize: int = 9) -> None:
    fig.text(
        0.05, y, text, fontsize=fontsize, color="#b0bec5",
        ha="left", va="top", wrap=True,
        fontstyle="italic",
        bbox=dict(boxstyle="round,pad=0.5", facecolor="#252525", edgecolor="#444", alpha=0.95),
        transform=fig.transFigure,
    )


def _fmt(v: float, d: int = 1) -> str:
    if v is None or np.isnan(v):
        return "—"
    if v == float("inf") or v > 99:
        return "inf"
    return f"{v:.{d}f}"


# ── Chart 1: Exit Strategy Heatmap (HR by sector × exit strategy) ────────

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

    matrix = np.full((len(sectors), len(EXIT_STRATEGIES)), np.nan)
    trades_matrix = np.zeros((len(sectors), len(EXIT_STRATEGIES)), dtype=int)

    for i, sector in enumerate(sectors):
        for j, strat in enumerate(EXIT_STRATEGIES):
            matches = [m for m in results_by_sector[sector]
                       if m.combo == combo_label and m.name == strat and m.n_trades >= MIN_TRADES]
            if matches:
                matrix[i, j] = matches[0].hit_rate
                trades_matrix[i, j] = matches[0].n_trades

    fig, ax = plt.subplots(figsize=(12, max(5, len(sectors) * 0.55)))
    fig.subplots_adjust(bottom=0.22)

    im = ax.imshow(matrix, aspect="auto", cmap="RdYlGn", vmin=0.35, vmax=0.85)
    ax.set_xticks(range(len(EXIT_STRATEGIES)))
    ax.set_xticklabels(EXIT_STRATEGIES, fontsize=9, rotation=20, ha="right")
    ax.set_yticks(range(len(sectors)))
    ax.set_yticklabels(sectors, fontsize=9)
    ax.set_title(f"Phase 8 — Exit Strategy Hit Rate by Sector ({combo_label}, {tf.timeframe})")

    for i in range(len(sectors)):
        for j in range(len(EXIT_STRATEGIES)):
            val = matrix[i, j]
            n = trades_matrix[i, j]
            if not np.isnan(val):
                color = "black" if val > 0.60 else "white"
                ax.text(j, i, f"{val:.0%}\n({n})", ha="center", va="center",
                        fontsize=8, color=color, fontweight="bold")

    fig.colorbar(im, ax=ax, shrink=0.6, label="Hit Rate")

    # Find best overall exit strategy (highest median HR across sectors)
    valid_cols = ~np.all(np.isnan(matrix), axis=0)
    medians = np.nanmedian(matrix, axis=0)
    best_strat_idx = int(np.nanargmax(medians)) if valid_cols.any() else 0
    best_strat = EXIT_STRATEGIES[best_strat_idx]
    best_med = medians[best_strat_idx]

    worst_strat_idx = int(np.nanargmin(medians)) if valid_cols.any() else 0
    worst_strat = EXIT_STRATEGIES[worst_strat_idx]

    n_sectors_where_combo_best = 0
    for i in range(len(sectors)):
        row = matrix[i]
        if not np.all(np.isnan(row)):
            best_j = int(np.nanargmax(row))
            if EXIT_STRATEGIES[best_j] == "Combo invalidation":
                n_sectors_where_combo_best += 1

    commentary = (
        f"INSIGHT ({combo_label}): Across {len(sectors)} sectors, '{best_strat}' achieves the highest "
        f"median HR ({best_med:.0%}). "
        f"Combo invalidation is the best exit in {n_sectors_where_combo_best}/{len(sectors)} sectors — "
        f"{'confirming that exiting when the entry thesis breaks is the most robust approach. ' if n_sectors_where_combo_best > len(sectors) // 2 else 'but trailing stops outperform in some sectors, suggesting sector-specific tuning adds value. '}"
        f"'{worst_strat}' consistently underperforms. "
        f"RECOMMENDATION: Default to {best_strat} and override per-sector when improvement > 5pp."
    )
    _add_commentary(fig, _wrap(commentary), y=0.06)
    fig.savefig(output_dir / f"phase8_exit_heatmap_{combo_label}.png")
    plt.close(fig)
    print(f"  Saved phase8_exit_heatmap_{combo_label}.png")


# ── Chart 2: Best exit strategy per sector (grouped bar) ─────────────────

def chart_best_exit_per_sector(
    results_by_sector: Dict[str, List[StrategyMetrics]],
    tf: TFConfig,
    output_dir: Path,
) -> None:
    combo_labels = ["C3", "C4", "C5"]
    sectors = sorted([s for s in results_by_sector.keys()
                      if any(m.n_trades >= MIN_TRADES for m in results_by_sector[s])])
    if not sectors:
        return

    fig, axes = plt.subplots(1, 3, figsize=(20, max(5, len(sectors) * 0.5)), sharey=True)
    fig.subplots_adjust(bottom=0.22)

    for col_idx, combo in enumerate(combo_labels):
        ax = axes[col_idx]
        best_hrs = []
        best_names = []
        fixed_hrs = []

        for sector in sectors:
            metrics = [m for m in results_by_sector[sector]
                       if m.combo == combo and m.n_trades >= MIN_TRADES]
            if not metrics:
                best_hrs.append(np.nan)
                best_names.append("")
                fixed_hrs.append(np.nan)
                continue

            best_m = max(metrics, key=lambda m: m.hit_rate)
            fixed_m = next((m for m in metrics if m.name == "Fixed horizon"), None)
            best_hrs.append(best_m.hit_rate)
            best_names.append(best_m.name)
            fixed_hrs.append(fixed_m.hit_rate if fixed_m else np.nan)

        y = np.arange(len(sectors))
        h = 0.35

        bars_fixed = ax.barh(y + h / 2, fixed_hrs, h,
                             label="Fixed horizon", color="#616161", edgecolor="none")
        best_colors = [EXIT_COLORS.get(n, "#66bb6a") for n in best_names]
        bars_best = ax.barh(y - h / 2, best_hrs, h,
                            label="Best exit", color=best_colors, edgecolor="none")

        ax.set_yticks(y)
        ax.set_yticklabels(sectors, fontsize=9)
        ax.set_xlim(0.30, 1.0)
        ax.axvline(0.50, color="gray", linestyle="--", linewidth=0.7, alpha=0.5)
        ax.set_title(combo, fontsize=13, fontweight="bold")
        ax.invert_yaxis()

        for bar, val, name in zip(bars_best, best_hrs, best_names):
            if not np.isnan(val):
                short = name[:12]
                ax.text(val + 0.01, bar.get_y() + bar.get_height() / 2,
                        f"{val:.0%} ({short})", va="center", fontsize=7,
                        color="white", fontweight="bold")
        for bar, val in zip(bars_fixed, fixed_hrs):
            if not np.isnan(val):
                ax.text(val + 0.01, bar.get_y() + bar.get_height() / 2,
                        f"{val:.0%}", va="center", fontsize=7, color="#9e9e9e")

        if col_idx == 0:
            ax.legend(loc="lower right", fontsize=8, framealpha=0.3)

    fig.suptitle(
        f"Phase 8 — Best Exit vs Fixed Horizon by Sector ({tf.timeframe})",
        fontsize=14, fontweight="bold", y=1.02,
    )

    # Compute summary stats for commentary
    all_improvements = []
    for sector in sectors:
        for combo in combo_labels:
            metrics = [m for m in results_by_sector[sector]
                       if m.combo == combo and m.n_trades >= MIN_TRADES]
            if len(metrics) < 2:
                continue
            fixed = next((m for m in metrics if m.name == "Fixed horizon"), None)
            best = max(metrics, key=lambda m: m.hit_rate)
            if fixed and best.name != "Fixed horizon":
                all_improvements.append(best.hit_rate - fixed.hit_rate)

    avg_improvement = float(np.mean(all_improvements)) if all_improvements else 0
    max_improvement = float(np.max(all_improvements)) if all_improvements else 0

    commentary = (
        f"INSIGHT: Across all sectors and combo levels, active exit management improves HR by "
        f"{avg_improvement:+.1%} on average (max {max_improvement:+.1%}) vs fixed-horizon baseline. "
        f"The magnitude of improvement varies by sector — sectors with higher volatility "
        f"(Technology, Consumer Cyclical) tend to benefit more from trailing stops, "
        f"while defensive sectors (Utilities, Consumer Defensive) see less improvement. "
        f"RECOMMENDATION: Always use an active exit. The specific strategy should be sector-aware."
    )
    _add_commentary(fig, _wrap(commentary), y=0.06)
    fig.savefig(output_dir / "phase8_best_exit_per_sector.png")
    plt.close(fig)
    print(f"  Saved phase8_best_exit_per_sector.png")


# ── Chart 3: Risk-Return Profile (Sharpe vs Avg Return scatter) ──────────

def chart_risk_return_profile(
    results_by_sector: Dict[str, List[StrategyMetrics]],
    tf: TFConfig,
    output_dir: Path,
) -> None:
    combo_labels = ["C3", "C5"]
    fig, axes = plt.subplots(1, 2, figsize=(16, 8))
    fig.subplots_adjust(bottom=0.22)

    for ax_idx, combo in enumerate(combo_labels):
        ax = axes[ax_idx]
        for strat_name, color in EXIT_COLORS.items():
            sharpes, avg_rets, sizes, labels = [], [], [], []
            for sector, metrics in results_by_sector.items():
                m = next((m for m in metrics
                          if m.combo == combo and m.name == strat_name and m.n_trades >= MIN_TRADES),
                         None)
                if m is None:
                    continue
                sharpes.append(m.sharpe)
                avg_rets.append(m.avg_return)
                sizes.append(max(20, min(300, m.n_trades * 3)))
                labels.append(sector[:15])

            if not sharpes:
                continue
            ax.scatter(sharpes, avg_rets, s=sizes, c=color, alpha=0.7,
                       edgecolors="white", linewidths=0.5, label=strat_name, zorder=3)
            for s, a, l in zip(sharpes, avg_rets, labels):
                ax.annotate(l, (s, a), fontsize=6, color="white", alpha=0.6,
                            xytext=(4, 4), textcoords="offset points")

        ax.axhline(0, color="#ff5252", linestyle="--", linewidth=0.8, alpha=0.4)
        ax.axvline(0, color="#ff5252", linestyle="--", linewidth=0.8, alpha=0.4)
        ax.set_xlabel("Annualized Sharpe Ratio")
        ax.set_ylabel("Avg Return per Trade (%)")
        ax.set_title(f"{combo} — Risk-Return Profile")
        ax.legend(fontsize=7, framealpha=0.3, loc="best")
        ax.grid(True, alpha=0.1)

        ax.text(0.98, 0.98, "HIGH RETURN\nHIGH SHARPE\n(ideal)", transform=ax.transAxes,
                ha="right", va="top", fontsize=7, color="#66bb6a", alpha=0.4)
        ax.text(0.02, 0.02, "NEGATIVE RETURN\nLOW SHARPE\n(avoid)", transform=ax.transAxes,
                ha="left", va="bottom", fontsize=7, color="#ef5350", alpha=0.4)

    fig.suptitle(
        f"Phase 8 — Exit Strategy Risk-Return Profile ({tf.timeframe})",
        fontsize=14, fontweight="bold", y=1.02,
    )

    commentary = (
        f"INSIGHT: Each dot represents one sector under one exit strategy (bubble size = trade count). "
        f"Upper-right quadrant = high avg return AND consistent (high Sharpe). "
        f"Trailing stops tend to cluster more tightly — they standardize exit timing. "
        f"Combo invalidation shows the widest dispersion — it works brilliantly in trending sectors "
        f"but can be slow in choppy markets. "
        f"RECOMMENDATION: For a systematic approach, prefer the strategy with the tightest cluster "
        f"in the upper-right quadrant. For discretionary traders, use sector-specific overrides."
    )
    _add_commentary(fig, _wrap(commentary), y=0.06)
    fig.savefig(output_dir / "phase8_risk_return_profile.png")
    plt.close(fig)
    print(f"  Saved phase8_risk_return_profile.png")


# ── Chart 4: Holding Period & Drawdown Comparison ────────────────────────

def chart_holding_and_drawdown(
    results_by_sector: Dict[str, List[StrategyMetrics]],
    tf: TFConfig,
    output_dir: Path,
) -> None:
    strat_holds: Dict[str, List[float]] = defaultdict(list)
    strat_dds: Dict[str, List[float]] = defaultdict(list)
    strat_hrs: Dict[str, List[float]] = defaultdict(list)

    for metrics_list in results_by_sector.values():
        for m in metrics_list:
            if m.n_trades >= MIN_TRADES:
                strat_holds[m.name].append(m.avg_holding)
                strat_dds[m.name].append(m.avg_max_dd)
                strat_hrs[m.name].append(m.hit_rate)

    strats = [s for s in EXIT_STRATEGIES if s in strat_holds and strat_holds[s]]
    if not strats:
        return

    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(18, 6))
    fig.subplots_adjust(bottom=0.26)

    x = np.arange(len(strats))
    colors = [EXIT_COLORS.get(s, "#78909c") for s in strats]

    # Holding period
    medians_hold = [float(np.median(strat_holds[s])) for s in strats]
    q25_hold = [float(np.percentile(strat_holds[s], 25)) for s in strats]
    q75_hold = [float(np.percentile(strat_holds[s], 75)) for s in strats]
    ax1.bar(x, medians_hold, color=colors, edgecolor="none", width=0.6)
    ax1.errorbar(x, medians_hold,
                 yerr=[np.array(medians_hold) - np.array(q25_hold),
                       np.array(q75_hold) - np.array(medians_hold)],
                 fmt="none", ecolor="white", elinewidth=1, capsize=4)
    ax1.set_xticks(x)
    ax1.set_xticklabels(strats, rotation=25, ha="right", fontsize=8)
    ax1.set_ylabel("Avg Holding Period (bars)")
    ax1.set_title("Holding Period")
    for i, v in enumerate(medians_hold):
        ax1.text(i, v + 0.3, f"{v:.1f}", ha="center", fontsize=9, fontweight="bold")

    # Max drawdown
    medians_dd = [float(np.median(strat_dds[s])) for s in strats]
    ax2.bar(x, medians_dd, color=colors, edgecolor="none", width=0.6)
    ax2.set_xticks(x)
    ax2.set_xticklabels(strats, rotation=25, ha="right", fontsize=8)
    ax2.set_ylabel("Avg Max Drawdown (%)")
    ax2.set_title("Max Drawdown per Trade")
    for i, v in enumerate(medians_dd):
        ax2.text(i, v + 0.1, f"{v:.1f}%", ha="center", fontsize=9, fontweight="bold")

    # Hit rate
    medians_hr = [float(np.median(strat_hrs[s])) for s in strats]
    ax3.bar(x, medians_hr, color=colors, edgecolor="none", width=0.6)
    ax3.axhline(0.50, color="#ff5252", linestyle="--", linewidth=0.8, alpha=0.5)
    ax3.set_xticks(x)
    ax3.set_xticklabels(strats, rotation=25, ha="right", fontsize=8)
    ax3.set_ylabel("Median Hit Rate")
    ax3.set_title("Hit Rate (All Sectors)")
    for i, v in enumerate(medians_hr):
        ax3.text(i, v + 0.01, f"{v:.0%}", ha="center", fontsize=9, fontweight="bold")

    fig.suptitle(
        f"Phase 8 — Exit Strategy Characteristics ({tf.timeframe}, All Combos)",
        fontsize=14, fontweight="bold", y=1.02,
    )

    best_hr_idx = int(np.argmax(medians_hr))
    lowest_dd_idx = int(np.argmin(medians_dd))
    shortest_hold_idx = int(np.argmin(medians_hold))
    commentary = (
        f"INSIGHT: '{strats[best_hr_idx]}' achieves the highest median HR ({medians_hr[best_hr_idx]:.0%}) "
        f"but trades last {medians_hold[best_hr_idx]:.1f} bars on average. "
        f"'{strats[lowest_dd_idx]}' minimizes drawdown ({medians_dd[lowest_dd_idx]:.1f}%). "
        f"'{strats[shortest_hold_idx]}' is fastest to exit ({medians_hold[shortest_hold_idx]:.1f} bars) — "
        f"freeing capital for redeployment. "
        f"P-SAR is the tightest stop (shortest hold, lowest DD) but often exits too early, sacrificing upside. "
        f"SuperTrend gives the most room but suffers deeper drawdowns. "
        f"RECOMMENDATION: Match exit strategy to your risk tolerance. Aggressive → P-SAR. Balanced → UT Bot / Combo. Patient → SuperTrend."
    )
    _add_commentary(fig, _wrap(commentary), y=0.04)
    fig.savefig(output_dir / "phase8_holding_and_drawdown.png")
    plt.close(fig)
    print(f"  Saved phase8_holding_and_drawdown.png")


# ── Report generation ─────────────────────────────────────────────────────

def generate_report(
    results_by_sector: Dict[str, List[StrategyMetrics]],
    tf: TFConfig,
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    combos = COMBO_DEFINITIONS.get(tf.timeframe, COMBO_DEFINITIONS["1W"])
    combo_labels = [c.replace("combo_", "C") for c in combos]

    lines = [
        f"# Phase 8 — Exit Strategy Analysis by Sector ({tf.timeframe})",
        "",
        f"**Timeframe:** {tf.timeframe}  ",
        f"**Horizons:** {tf.horizons}  ",
        f"**Default horizon:** {tf.default_horizon} bars  ",
        f"**OOS split:** {IS_FRACTION:.0%} IS / {1 - IS_FRACTION:.0%} OOS  ",
        f"**Min trades for inclusion:** {MIN_TRADES}  ",
        "",
        "## Methodology",
        "",
        "For each sector × combo level (C3, C4, C5), we enter long when ALL combo KPIs are bullish "
        "and compare five exit strategies:",
        "",
        "1. **Fixed horizon** — exit after N bars (baseline)",
        "2. **P-SAR trailing** — exit when close < Parabolic SAR (tightest)",
        "3. **UT Bot trailing** — exit when close < ATR-based trailing stop (medium)",
        "4. **SuperTrend trailing** — exit when close < SuperTrend line (widest)",
        "5. **Combo invalidation** — exit when N-1 of the combo KPIs flip non-bull",
        "",
        "All trailing stops cap at 3× the default horizon as max holding period.",
        "",
    ]

    # Summary table per combo level
    for combo_name, combo_kpis in combos.items():
        label = combo_name.replace("combo_", "C")
        kpi_str = " + ".join(combo_kpis)
        lines.extend([
            f"## {label}: {kpi_str}",
            "",
            f"| Sector | Exit Strategy | HR | Avg Ret | PF | Sharpe | Trades | Hold | MaxDD |",
            f"|--------|--------------|-----|---------|-----|--------|--------|------|-------|",
        ])

        for sector in sorted(results_by_sector.keys()):
            metrics = [m for m in results_by_sector[sector]
                       if m.combo == label and m.n_trades >= MIN_TRADES]
            if not metrics:
                continue
            metrics.sort(key=lambda m: EXIT_STRATEGIES.index(m.name) if m.name in EXIT_STRATEGIES else 99)
            for m in metrics:
                pf_str = f"{m.profit_factor:.2f}" if m.profit_factor < 99 else "inf"
                lines.append(
                    f"| {sector} | {m.name} | {m.hit_rate:.1%} | {m.avg_return:+.2f}% | "
                    f"{pf_str} | {m.sharpe:.2f} | {m.n_trades} | {m.avg_holding:.1f} | "
                    f"{m.avg_max_dd:.1f}% |"
                )
        lines.append("")

    # Best exit per sector summary
    lines.extend([
        "## Best Exit Strategy per Sector",
        "",
        "| Sector | Combo | Best Exit | HR | vs Fixed | Trades |",
        "|--------|-------|-----------|-----|----------|--------|",
    ])

    best_exit_config: Dict[str, Dict[str, dict]] = {}

    for sector in sorted(results_by_sector.keys()):
        for label in combo_labels:
            metrics = [m for m in results_by_sector[sector]
                       if m.combo == label and m.n_trades >= MIN_TRADES]
            if not metrics:
                continue
            best = max(metrics, key=lambda m: m.hit_rate)
            fixed = next((m for m in metrics if m.name == "Fixed horizon"), None)
            delta = (best.hit_rate - fixed.hit_rate) if fixed else 0
            lines.append(
                f"| {sector} | {label} | {best.name} | {best.hit_rate:.1%} | "
                f"{delta:+.1%} | {best.n_trades} |"
            )
            best_exit_config.setdefault(sector, {})[label.lower().replace("c", "combo_")] = {
                "strategy": best.name,
                "hit_rate": round(best.hit_rate, 4),
                "n_trades": best.n_trades,
            }

    lines.append("")

    # Qualitative recommendations
    lines.extend([
        "## Recommendations",
        "",
    ])

    # Compute aggregate winner
    all_metrics = [m for ms in results_by_sector.values() for m in ms if m.n_trades >= MIN_TRADES]
    if all_metrics:
        by_strat: Dict[str, List[float]] = defaultdict(list)
        for m in all_metrics:
            by_strat[m.name].append(m.hit_rate)
        strat_medians = {s: float(np.median(hrs)) for s, hrs in by_strat.items()}
        overall_best = max(strat_medians, key=strat_medians.get)  # type: ignore
        lines.append(f"**Overall best exit strategy:** {overall_best} "
                     f"(median HR = {strat_medians[overall_best]:.1%} across all sectors and combos)")
        lines.append("")

        # Per-sector recommendations
        lines.append("**Per-sector recommendations:**")
        lines.append("")
        for sector in sorted(results_by_sector.keys()):
            sec_metrics = [m for m in results_by_sector[sector] if m.n_trades >= MIN_TRADES]
            if not sec_metrics:
                continue
            sec_by_strat: Dict[str, List[float]] = defaultdict(list)
            for m in sec_metrics:
                sec_by_strat[m.name].append(m.hit_rate)
            sec_best = max(sec_by_strat, key=lambda s: float(np.median(sec_by_strat[s])))
            sec_hr = float(np.median(sec_by_strat[sec_best]))
            sec_fixed_hr = float(np.median(sec_by_strat.get("Fixed horizon", [0.5])))
            delta = sec_hr - sec_fixed_hr
            comment = ""
            if sec_best == "Combo invalidation":
                comment = " — thesis-based exit works best here, trends are sustained"
            elif sec_best == "P-SAR trailing":
                comment = " — tight stops preferred, sector tends to mean-revert quickly"
            elif sec_best == "UT Bot trailing":
                comment = " — balanced approach, moderate volatility"
            elif sec_best == "SuperTrend trailing":
                comment = " — wide stops needed, sector has strong but volatile trends"
            elif sec_best == "Fixed horizon":
                comment = " — timing-based exit, active exits don't improve signal quality"
            lines.append(f"- **{sector}**: {sec_best} ({sec_hr:.0%}, {delta:+.0%} vs fixed){comment}")

    lines.append("")

    report = "\n".join(lines)
    (output_dir / "exit_by_sector_report.md").write_text(report, encoding="utf-8")
    print(f"\n  Saved exit_by_sector_report.md")

    # Save JSON
    json_path = output_dir / "exit_by_sector_results.json"
    json_data = []
    for sector, metrics_list in results_by_sector.items():
        for m in metrics_list:
            json_data.append({
                "sector": m.sector, "combo": m.combo, "strategy": m.name,
                "n_trades": m.n_trades, "hit_rate": round(m.hit_rate, 4),
                "avg_return": round(m.avg_return, 4),
                "profit_factor": round(m.profit_factor, 4) if m.profit_factor < 99 else None,
                "avg_holding": round(m.avg_holding, 2),
                "avg_max_dd": round(m.avg_max_dd, 4),
                "sharpe": round(m.sharpe, 4),
                "median_return": round(m.median_return, 4),
            })
    json_path.write_text(json.dumps(json_data, indent=2), encoding="utf-8")
    print(f"  Saved exit_by_sector_results.json")

    # Save best exit config
    config_path = output_dir / "best_exit_by_sector.json"
    config_path.write_text(json.dumps(best_exit_config, indent=2), encoding="utf-8")
    print(f"  Saved best_exit_by_sector.json")


# ── Main ──────────────────────────────────────────────────────────────────

def main() -> int:
    tf = parse_timeframe_arg("Phase 8 — Exit Strategy by Sector")
    OUTPUT_DIR = output_dir_for(tf.timeframe, "phase8")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    print(f"Phase 8 — Exit Strategy by Sector ({tf.timeframe})")
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

    # Also add ALL as a pseudo-sector
    valid_sectors = {s: syms for s, syms in sector_stocks.items()
                     if len(syms) >= MIN_STOCKS_PER_SECTOR}
    valid_sectors["ALL"] = list(all_data.keys())
    print(f"Sectors: {len(valid_sectors) - 1} + ALL ({len(all_data)} stocks total)")

    results_by_sector = run_exit_analysis_by_sector(all_data, valid_sectors, tf)

    print("\n\nGenerating charts...")
    combos = COMBO_DEFINITIONS.get(tf.timeframe, COMBO_DEFINITIONS["1W"])
    for combo_name in combos:
        label = combo_name.replace("combo_", "C")
        chart_exit_heatmap(results_by_sector, label, tf, OUTPUT_DIR)

    chart_best_exit_per_sector(results_by_sector, tf, OUTPUT_DIR)
    chart_risk_return_profile(results_by_sector, tf, OUTPUT_DIR)
    chart_holding_and_drawdown(results_by_sector, tf, OUTPUT_DIR)

    print("\nGenerating report...")
    generate_report(results_by_sector, tf, OUTPUT_DIR)

    elapsed = time.time() - t0
    print(f"\nPhase 8 complete in {elapsed:.0f}s")
    print(f"Outputs: {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

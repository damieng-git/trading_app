"""
Phase 7 — Exit Rules Analysis

Evaluates systematic exit strategies for combo signals:
  1. Fixed-horizon exit (baseline from Phase 4/6)
  2. P-SAR trailing stop
  3. UT Bot ATR trailing stop
  4. SuperTrend trailing stop
  5. Time + trailing hybrid (hold until stop OR max holding period)

For each exit strategy, measures:
  - Hit rate (% of trades with positive return)
  - Average return per trade
  - Profit factor
  - Max drawdown per trade
  - Sharpe ratio (annualized)
  - Average holding period
"""

from __future__ import annotations

import json
import sys
import time
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
from tf_config import parse_timeframe_arg, output_dir_for, ENRICHED_DIR, TFConfig

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

COMBO_DEFINITIONS = {
    "1W": {
        "combo_3": ["Nadaraya-Watson Smoother", "cRSI", "SR Breaks"],
        "combo_5": ["Nadaraya-Watson Smoother", "cRSI", "SR Breaks", "Stoch_MTM", "CM_P-SAR"],
    },
    "1D": {
        "combo_3": ["Nadaraya-Watson Smoother", "cRSI", "SR Breaks"],
        "combo_5": ["Nadaraya-Watson Smoother", "cRSI", "SR Breaks", "MA Ribbon", "Madrid Ribbon"],
    },
    "4H": {
        "combo_3": ["Nadaraya-Watson Smoother", "cRSI", "Stoch_MTM"],
        "combo_5": ["Nadaraya-Watson Smoother", "cRSI", "Stoch_MTM", "SR Breaks", "GK Trend Ribbon"],
    },
}

IS_FRACTION = 0.7

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
# Exit strategy implementations
# ---------------------------------------------------------------------------

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


def _compute_psar_series(df: pd.DataFrame) -> pd.Series:
    """Compute P-SAR for the dataframe."""
    return parabolic_sar(df)


def _compute_ut_bot_stop(df: pd.DataFrame, a: float = 1.0, c: int = 10) -> pd.Series:
    """Compute UT Bot trailing stop series."""
    result = ut_bot_alert(df, a=a, c=c)
    return result["UT_trailing_stop"]


def _compute_supertrend_stop(df: pd.DataFrame, periods: int = 12, mult: float = 3.0) -> Tuple[pd.Series, pd.Series]:
    """Compute SuperTrend line and trend direction."""
    st_line, trend, _ = supertrend(df, periods=periods, multiplier=mult)
    return st_line, trend


def simulate_trades_fixed_horizon(
    df: pd.DataFrame,
    signal: pd.Series,
    horizon: int,
    test_start: pd.Timestamp,
) -> List[TradeResult]:
    """Baseline: exit after fixed number of bars."""
    trades = []
    test_mask = df.index >= test_start
    close = df["Close"]
    high = df["High"] if "High" in df.columns else close
    low = df["Low"] if "Low" in df.columns else close

    sig_dates = signal[test_mask & signal].index
    i = 0
    while i < len(sig_dates):
        entry_idx = df.index.get_loc(sig_dates[i])
        if entry_idx + horizon >= len(df):
            break
        entry_p = float(close.iloc[entry_idx])
        exit_p = float(close.iloc[entry_idx + horizon])
        lows_during = low.iloc[entry_idx:entry_idx + horizon + 1]
        max_dd = float((entry_p - lows_during.min()) / entry_p * 100) if entry_p > 0 else 0
        ret = (exit_p - entry_p) / entry_p * 100 if entry_p > 0 else 0
        trades.append(TradeResult(
            entry_date=str(df.index[entry_idx].date()),
            exit_date=str(df.index[entry_idx + horizon].date()),
            entry_price=entry_p, exit_price=exit_p,
            return_pct=ret, holding_bars=horizon,
            max_drawdown_pct=max_dd, exit_reason="horizon",
        ))
        # Skip bars within the holding period
        next_i = i + 1
        while next_i < len(sig_dates) and df.index.get_loc(sig_dates[next_i]) <= entry_idx + horizon:
            next_i += 1
        i = next_i
    return trades


def simulate_trades_trailing_stop(
    df: pd.DataFrame,
    signal: pd.Series,
    stop_series: pd.Series,
    max_hold: int,
    test_start: pd.Timestamp,
    exit_label: str = "stop",
) -> List[TradeResult]:
    """Exit when close crosses below stop_series, or after max_hold bars."""
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
        lows_during = low.iloc[entry_idx:exit_idx + 1]
        max_dd = float((entry_p - lows_during.min()) / entry_p * 100)
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


# ---------------------------------------------------------------------------
# Strategy evaluation
# ---------------------------------------------------------------------------

@dataclass
class StrategyMetrics:
    name: str
    combo: str
    n_trades: int
    hit_rate: float
    avg_return: float
    profit_factor: float
    avg_holding: float
    avg_max_dd: float
    sharpe: float
    win_rate_by_exit: Dict[str, float] = field(default_factory=dict)


def compute_metrics(name: str, combo: str, trades: List[TradeResult]) -> StrategyMetrics:
    if not trades:
        return StrategyMetrics(name=name, combo=combo, n_trades=0, hit_rate=0,
                               avg_return=0, profit_factor=0, avg_holding=0,
                               avg_max_dd=0, sharpe=0)
    rets = np.array([t.return_pct for t in trades])
    n = len(rets)
    hr = float(np.sum(rets > 0) / n)
    avg_ret = float(np.mean(rets))
    gross_profit = float(np.sum(rets[rets > 0]))
    gross_loss = float(np.abs(np.sum(rets[rets < 0])))
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    avg_hold = float(np.mean([t.holding_bars for t in trades]))
    avg_dd = float(np.mean([t.max_drawdown_pct for t in trades]))
    sharpe = float(np.mean(rets) / np.std(rets) * np.sqrt(52)) if np.std(rets) > 0 else 0

    exit_reasons = {}
    for t in trades:
        exit_reasons.setdefault(t.exit_reason, []).append(t.return_pct)
    wr_by_exit = {k: float(np.sum(np.array(v) > 0) / len(v)) for k, v in exit_reasons.items()}

    return StrategyMetrics(
        name=name, combo=combo, n_trades=n, hit_rate=hr, avg_return=avg_ret,
        profit_factor=pf, avg_holding=avg_hold, avg_max_dd=avg_dd,
        sharpe=sharpe, win_rate_by_exit=wr_by_exit,
    )


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def run_exit_analysis(
    all_data: Dict[str, pd.DataFrame],
    tf: TFConfig,
) -> List[StrategyMetrics]:
    combos = COMBO_DEFINITIONS.get(tf.timeframe, COMBO_DEFINITIONS["1W"])
    horizon = tf.default_horizon
    max_hold_bars = horizon * 3

    results: List[StrategyMetrics] = []

    for combo_name, combo_kpis in combos.items():
        label = combo_name.replace("combo_", "C")
        print(f"\n  Analyzing {label}: {', '.join(combo_kpis)}")

        all_trades_by_strategy: Dict[str, List[TradeResult]] = {
            f"Fixed {horizon}-bar": [],
            "P-SAR trailing stop": [],
            "UT Bot trailing stop": [],
            "SuperTrend trailing stop": [],
        }

        for sym, df in all_data.items():
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

            # Strategy 1: Fixed horizon
            t1 = simulate_trades_fixed_horizon(df, signal, horizon, test_start)
            all_trades_by_strategy[f"Fixed {horizon}-bar"].extend(t1)

            # Strategy 2: P-SAR trailing stop
            try:
                psar = _compute_psar_series(df)
                t2 = simulate_trades_trailing_stop(df, signal, psar, max_hold_bars, test_start, "psar_stop")
                all_trades_by_strategy["P-SAR trailing stop"].extend(t2)
            except Exception:
                pass

            # Strategy 3: UT Bot trailing stop
            try:
                ut_stop = _compute_ut_bot_stop(df)
                t3 = simulate_trades_trailing_stop(df, signal, ut_stop, max_hold_bars, test_start, "ut_bot_stop")
                all_trades_by_strategy["UT Bot trailing stop"].extend(t3)
            except Exception:
                pass

            # Strategy 4: SuperTrend trailing stop
            try:
                st_line, st_trend = _compute_supertrend_stop(df)
                t4 = simulate_trades_trailing_stop(df, signal, st_line, max_hold_bars, test_start, "supertrend_stop")
                all_trades_by_strategy["SuperTrend trailing stop"].extend(t4)
            except Exception:
                pass

        for strat_name, trades in all_trades_by_strategy.items():
            m = compute_metrics(strat_name, label, trades)
            results.append(m)
            print(f"    {strat_name}: HR={m.hit_rate:.1%}, Avg={m.avg_return:+.2f}%, "
                  f"PF={m.profit_factor:.2f}, Trades={m.n_trades}, AvgHold={m.avg_holding:.1f}")

    return results


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(results: List[StrategyMetrics], output_dir: Path, tf: TFConfig) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Phase 7 — Exit Rules Analysis",
        "",
        f"**Timeframe:** {tf.timeframe}  ",
        f"**Default horizon:** {tf.default_horizon} bars  ",
        f"**OOS split:** {IS_FRACTION:.0%} IS / {1 - IS_FRACTION:.0%} OOS  ",
        "",
        "## Summary",
        "",
        "Compares four exit strategies across combo signals:",
        "",
        "1. **Fixed horizon** (baseline) — exit after N bars",
        "2. **P-SAR trailing stop** — exit when close < P-SAR",
        "3. **UT Bot trailing stop** — exit when close < ATR-based trailing stop",
        "4. **SuperTrend trailing stop** — exit when close < SuperTrend line",
        "",
        "All trailing stops have a maximum holding period of 3x the default horizon.",
        "",
        "## Results",
        "",
        "| Combo | Exit Strategy | Trades | Hit Rate | Avg Return | PF | Avg Hold | Avg MaxDD | Sharpe |",
        "|-------|--------------|--------|----------|------------|-----|----------|-----------|--------|",
    ]

    for m in results:
        pf_str = f"{m.profit_factor:.2f}" if m.profit_factor < 100 else "inf"
        lines.append(
            f"| {m.combo} | {m.name} | {m.n_trades} | {m.hit_rate:.1%} | "
            f"{m.avg_return:+.2f}% | {pf_str} | {m.avg_holding:.1f} | "
            f"{m.avg_max_dd:.2f}% | {m.sharpe:.2f} |"
        )

    lines.extend(["", "## Exit Reason Breakdown", ""])
    for m in results:
        if m.win_rate_by_exit:
            lines.append(f"**{m.combo} / {m.name}:**")
            for reason, wr in sorted(m.win_rate_by_exit.items()):
                lines.append(f"  - {reason}: {wr:.1%} win rate")
            lines.append("")

    # Recommendations
    trailing_results = [m for m in results if "trailing" in m.name.lower() and m.n_trades > 0]
    baseline_results = [m for m in results if "Fixed" in m.name and m.n_trades > 0]

    lines.extend([
        "## Recommendations",
        "",
    ])

    if trailing_results and baseline_results:
        best_trailing = max(trailing_results, key=lambda x: x.sharpe)
        best_baseline = max(baseline_results, key=lambda x: x.hit_rate)

        lines.append(f"- **Best risk-adjusted exit**: {best_trailing.name} ({best_trailing.combo}) "
                     f"with Sharpe {best_trailing.sharpe:.2f}")
        lines.append(f"- **Best hit rate**: {best_baseline.name} ({best_baseline.combo}) "
                     f"with HR {best_baseline.hit_rate:.1%}")
        lines.append("")

        if best_trailing.sharpe > 0.5:
            lines.append(f"The trailing stop approach ({best_trailing.name}) provides meaningful "
                        f"risk-adjusted improvement. Consider using it as the primary exit rule.")
        else:
            lines.append("Trailing stops show marginal improvement. The fixed-horizon approach "
                        "remains a strong baseline. Consider combining: exit at trailing stop OR "
                        "after max holding period, whichever comes first.")

    lines.append("")

    report = "\n".join(lines)
    report_path = output_dir / "exit_rules_report.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"\nReport saved to {report_path}")

    # Save raw results as JSON
    json_path = output_dir / "exit_rules_results.json"
    json_data = []
    for m in results:
        json_data.append({
            "name": m.name, "combo": m.combo, "n_trades": m.n_trades,
            "hit_rate": round(m.hit_rate, 4), "avg_return": round(m.avg_return, 4),
            "profit_factor": round(m.profit_factor, 4) if m.profit_factor < 1e6 else None,
            "avg_holding": round(m.avg_holding, 2), "avg_max_dd": round(m.avg_max_dd, 4),
            "sharpe": round(m.sharpe, 4), "win_rate_by_exit": m.win_rate_by_exit,
        })
    json_path.write_text(json.dumps(json_data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    tf = parse_timeframe_arg("Phase 7 — Exit Rules Analysis")
    OUTPUT_DIR = output_dir_for(tf.timeframe, "phase7")

    t0 = time.time()
    print(f"Phase 7 — Exit Rules Analysis ({tf.timeframe})")
    print(f"Loading data from {ENRICHED_DIR} ...")
    all_data = load_data(ENRICHED_DIR, tf.timeframe)
    print(f"Loaded {len(all_data)} stocks")

    results = run_exit_analysis(all_data, tf)
    generate_report(results, OUTPUT_DIR, tf)

    print(f"\nPhase 7 complete in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

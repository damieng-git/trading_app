"""
Phase 1 — Individual KPI Quality Assessment (Weekly, Long-Only)

For each KPI in isolation, measures:
- Hit rate at 1w, 4w, 13w horizons (% of bull signals followed by positive return)
- Profit factor (gross profit / gross loss on bull signals)
- Signal Sharpe (mean / std of bull-signal returns)
- Signal frequency (avg bull signals per stock per year)
- Avg bull streak duration (consecutive bull bars)
- Binomial p-value vs 50% null (is the hit rate statistically significant?)
- Total trade count across all stocks

Outputs a ranked scorecard as markdown + CSV.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from trading_dashboard.kpis.catalog import compute_kpi_state_map, KPI_TREND_ORDER, KPI_BREAKOUT_ORDER, KPI_ORDER
from trading_dashboard.kpis.rules import STATE_BULL, STATE_BEAR, STATE_NA, STATE_NEUTRAL
from trading_dashboard.indicators.registry import get_dimension_for_kpi, DIMENSIONS
from tf_config import parse_timeframe_arg, output_dir_for, ENRICHED_DIR, TFConfig

MIN_TRADES = 30


def load_weekly_data(enriched_dir: Path, timeframe: str, min_bars: int) -> Dict[str, pd.DataFrame]:
    """Load all enriched CSVs from a directory for the given timeframe."""
    data: Dict[str, pd.DataFrame] = {}
    pattern = f"*_{timeframe}.csv"
    files = sorted(enriched_dir.glob(pattern))
    for f in files:
        symbol = f.stem.rsplit(f"_{timeframe}", 1)[0]
        try:
            df = pd.read_csv(f, index_col=0, parse_dates=[0])
            df.index = pd.to_datetime(df.index, errors="coerce").tz_localize(None)
            df = df.sort_index()
            if len(df) >= min_bars and "Close" in df.columns:
                data[symbol] = df
        except Exception:
            continue
    return data


# ---------------------------------------------------------------------------
# Per-KPI metrics
# ---------------------------------------------------------------------------

@dataclass
class KPIMetrics:
    kpi_name: str
    dimension: str
    kpi_type: str
    hit_rates: Dict[int, float] = field(default_factory=dict)
    profit_factors: Dict[int, float] = field(default_factory=dict)
    sharpe_ratios: Dict[int, float] = field(default_factory=dict)
    signal_freq_per_year: float = 0.0
    avg_bull_streak: float = 0.0
    total_bull_signals: int = 0
    total_bars: int = 0
    stocks_with_data: int = 0
    pvalue_4w: float = 1.0
    available: bool = True


def _hit_rate(bull_mask: pd.Series, fwd_ret: pd.Series) -> float:
    valid = bull_mask & fwd_ret.notna()
    n = valid.sum()
    if n == 0:
        return np.nan
    return float((fwd_ret[valid] > 0).sum() / n)


def _profit_factor(bull_mask: pd.Series, fwd_ret: pd.Series) -> float:
    valid = bull_mask & fwd_ret.notna()
    rets = fwd_ret[valid]
    if len(rets) == 0:
        return np.nan
    gross_profit = rets.clip(lower=0).sum()
    gross_loss = (-rets).clip(lower=0).sum()
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else np.nan
    return float(gross_profit / gross_loss)


def _signal_sharpe(bull_mask: pd.Series, fwd_ret: pd.Series) -> float:
    valid = bull_mask & fwd_ret.notna()
    rets = fwd_ret[valid]
    if len(rets) < 5:
        return np.nan
    std = rets.std()
    if std == 0 or np.isnan(std):
        return np.nan
    return float(rets.mean() / std)


def _avg_streak_length(states: pd.Series, target: int = STATE_BULL) -> float:
    """Average consecutive run length of target state."""
    is_target = (states == target).astype(int)
    if is_target.sum() == 0:
        return 0.0
    groups = (is_target != is_target.shift()).cumsum()
    streaks = is_target.groupby(groups).sum()
    streaks = streaks[streaks > 0]
    if len(streaks) == 0:
        return 0.0
    return float(streaks.mean())


def evaluate_kpi(
    kpi_name: str,
    all_data: Dict[str, pd.DataFrame],
    horizons: List[int],
    default_horizon: int,
) -> KPIMetrics:
    """Evaluate a single KPI across all stocks."""
    dimension = get_dimension_for_kpi(kpi_name) or "unknown"
    kpi_type = "trend" if kpi_name in KPI_TREND_ORDER else "breakout"

    per_stock_hr: Dict[int, List[float]] = {h: [] for h in horizons}
    per_stock_pf: Dict[int, List[float]] = {h: [] for h in horizons}
    per_stock_sh: Dict[int, List[float]] = {h: [] for h in horizons}
    per_stock_freq: List[float] = []
    per_stock_streak: List[float] = []
    total_bull = 0
    total_bars = 0
    stocks_ok = 0

    all_bull_4w: List[int] = []
    all_correct_4w: List[int] = []

    for symbol, df in all_data.items():
        state_map = compute_kpi_state_map(df)
        states = state_map.get(kpi_name)
        if states is None:
            continue

        active = states.isin([STATE_BULL, STATE_BEAR, STATE_NEUTRAL])
        na_frac = (~active).sum() / max(len(states), 1)
        if na_frac > 0.8:
            continue

        stocks_ok += 1
        close = df["Close"]
        bull = states == STATE_BULL
        n_bull = int(bull.sum())
        total_bull += n_bull
        total_bars += int(active.sum())

        years = max((df.index[-1] - df.index[0]).days / 365.25, 0.5)
        per_stock_freq.append(n_bull / years)
        per_stock_streak.append(_avg_streak_length(states, STATE_BULL))

        for h in horizons:
            fwd = close.pct_change(h).shift(-h)
            hr = _hit_rate(bull, fwd)
            pf = _profit_factor(bull, fwd)
            sh = _signal_sharpe(bull, fwd)
            if not np.isnan(hr):
                per_stock_hr[h].append(hr)
            if not np.isnan(pf) and pf != float("inf"):
                per_stock_pf[h].append(pf)
            if not np.isnan(sh):
                per_stock_sh[h].append(sh)

            if h == default_horizon:
                valid = bull & fwd.notna()
                n = int(valid.sum())
                correct = int((fwd[valid] > 0).sum())
                all_bull_4w.append(n)
                all_correct_4w.append(correct)

    m = KPIMetrics(
        kpi_name=kpi_name,
        dimension=dimension,
        kpi_type=kpi_type,
        stocks_with_data=stocks_ok,
        total_bull_signals=total_bull,
        total_bars=total_bars,
    )

    if stocks_ok == 0:
        m.available = False
        return m

    for h in horizons:
        m.hit_rates[h] = float(np.median(per_stock_hr[h])) if per_stock_hr[h] else np.nan
        m.profit_factors[h] = float(np.median(per_stock_pf[h])) if per_stock_pf[h] else np.nan
        m.sharpe_ratios[h] = float(np.median(per_stock_sh[h])) if per_stock_sh[h] else np.nan

    m.signal_freq_per_year = float(np.median(per_stock_freq)) if per_stock_freq else 0.0
    m.avg_bull_streak = float(np.median(per_stock_streak)) if per_stock_streak else 0.0

    total_n = sum(all_bull_4w)
    total_c = sum(all_correct_4w)
    if total_n >= MIN_TRADES:
        m.pvalue_4w = float(sp_stats.binomtest(total_c, total_n, 0.5).pvalue)
    else:
        m.pvalue_4w = 1.0

    return m


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _fmt(v: float, decimals: int = 3) -> str:
    if v is None or np.isnan(v):
        return "—"
    if v == float("inf"):
        return "∞"
    return f"{v:.{decimals}f}"


def _significance(pvalue: float) -> str:
    if pvalue < 0.001:
        return "***"
    if pvalue < 0.01:
        return "**"
    if pvalue < 0.05:
        return "*"
    return ""


def generate_report(
    results: List[KPIMetrics],
    output_dir: Path,
    timeframe: str,
    horizons: List[int],
    horizon_labels: List[str],
    default_horizon: int,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    default_label = horizon_labels[horizons.index(default_horizon)]
    hr_cols = {h: f"HR {lbl}" for h, lbl in zip(horizons, horizon_labels)}
    pf_col = f"PF {default_label}"
    sharpe_col = f"Sharpe {default_label}"
    pvalue_col = f"p-value {default_label}"

    rows = []
    for r in results:
        row = {
            "KPI": r.kpi_name,
            "Dimension": DIMENSIONS.get(r.dimension, r.dimension),
            "Type": r.kpi_type,
            "Stocks": r.stocks_with_data,
            "Bull Signals": r.total_bull_signals,
            "Freq/yr": r.signal_freq_per_year,
            "Avg Streak": r.avg_bull_streak,
            "Sig": _significance(r.pvalue_4w),
            "Available": r.available,
        }
        for h in horizons:
            row[hr_cols[h]] = r.hit_rates.get(h, np.nan)
        row[pf_col] = r.profit_factors.get(default_horizon, np.nan)
        row[sharpe_col] = r.sharpe_ratios.get(default_horizon, np.nan)
        row[pvalue_col] = r.pvalue_4w
        rows.append(row)

    df = pd.DataFrame(rows)
    df.to_csv(output_dir / "kpi_scorecard.csv", index=False)

    available = df[df["Available"]].copy()
    available = available.sort_values(hr_cols[default_horizon], ascending=False)

    unavailable = df[~df["Available"]]

    hr_header = " | ".join(hr_cols[h] for h in horizons)
    sep_cells = "|".join(["-------"] * (9 + len(horizons)))
    lines = [
        f"# Phase 1 — KPI Quality Scorecard ({timeframe}, Long-Only)",
        "",
        f"**Sample:** {results[0].stocks_with_data if results else 0}+ stocks from sample_100 "
        f"| **Timeframe:** {timeframe} "
        f"| **Horizons:** {', '.join(horizon_labels)}",
        f"| **Min trades for significance:** {MIN_TRADES}",
        "",
        f"## Scoring KPIs — Ranked by {default_label.upper()} Hit Rate",
        "",
        f"| Rank | KPI | Dimension | {hr_header} | {pf_col} | {sharpe_col} | Freq/yr | Streak | Trades | Sig |",
        f"|{sep_cells}|",
    ]

    for i, (_, row) in enumerate(available.iterrows(), 1):
        hr_cells = " | ".join(_fmt(row[hr_cols[h]]) for h in horizons)
        lines.append(
            f"| {i} | {row['KPI']} | {row['Dimension']} "
            f"| {hr_cells} "
            f"| {_fmt(row[pf_col], 2)} | {_fmt(row[sharpe_col])} "
            f"| {_fmt(row['Freq/yr'], 1)} | {_fmt(row['Avg Streak'], 1)} "
            f"| {row['Bull Signals']} | {row['Sig']} |"
        )

    lines.append("")

    passing = available[(available[hr_cols[default_horizon]] > 0.50) & (available[pvalue_col] < 0.05)]
    failing = available[(available[hr_cols[default_horizon]] <= 0.50) | (available[pvalue_col] >= 0.05)]

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **KPIs evaluated:** {len(available)}")
    lines.append(f"- **Pass quality gate** ({hr_cols[default_horizon]} > 50% AND p < 0.05): **{len(passing)}**")
    lines.append(f"- **Fail quality gate:** {len(failing)}")
    lines.append("")

    if len(passing) > 0:
        lines.append("### Passing KPIs")
        lines.append("")
        for _, row in passing.iterrows():
            lines.append(f"- **{row['KPI']}** ({row['Dimension']}): "
                         f"{hr_cols[default_horizon]} = {_fmt(row[hr_cols[default_horizon]])}, PF = {_fmt(row[pf_col], 2)}, "
                         f"p = {_fmt(row[pvalue_col], 4)}{row['Sig']}")
        lines.append("")

    if len(failing) > 0:
        lines.append("### Failing KPIs (do not pass quality gate)")
        lines.append("")
        for _, row in failing.iterrows():
            reason = []
            if row[hr_cols[default_horizon]] <= 0.50:
                reason.append(f"{hr_cols[default_horizon]} = {_fmt(row[hr_cols[default_horizon]])}")
            if row[pvalue_col] >= 0.05:
                reason.append(f"p = {_fmt(row[pvalue_col], 4)}")
            lines.append(f"- **{row['KPI']}** ({row['Dimension']}): {', '.join(reason)}")
        lines.append("")

    if len(unavailable) > 0:
        lines.append("### Unavailable KPIs (missing data — need re-enrichment)")
        lines.append("")
        for _, row in unavailable.iterrows():
            lines.append(f"- {row['KPI']} ({row['Dimension']})")
        lines.append("")

    lines.append("## Interpretation Guide")
    lines.append("")
    lines.append("- **HR (Hit Rate):** Fraction of bull signals followed by positive return. >0.50 = better than coin flip.")
    lines.append("- **PF (Profit Factor):** Gross profit / gross loss. >1.0 = profitable on average.")
    lines.append("- **Sharpe:** Mean return / std deviation of returns on bull signals. Higher = more consistent.")
    lines.append("- **Freq/yr:** Median bull signals per stock per year. Very low = unreliable, very high = noisy.")
    lines.append("- **Streak:** Median consecutive bull bars. Long streaks = trend-following, short = event-based.")
    lines.append("- **Sig:** Statistical significance vs 50% null. *** p<0.001, ** p<0.01, * p<0.05.")
    lines.append("")

    report = "\n".join(lines)
    (output_dir / "kpi_scorecard.md").write_text(report, encoding="utf-8")
    print(report)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    tf = parse_timeframe_arg("Phase 1 — KPI Quality Scorecard")
    output_dir = output_dir_for(tf.timeframe, "phase1")

    print(f"Loading enriched data from {ENRICHED_DIR} ...")
    t0 = time.time()
    all_data = load_weekly_data(ENRICHED_DIR, tf.timeframe, tf.min_bars)
    print(f"Loaded {len(all_data)} stocks in {time.time() - t0:.1f}s")

    if not all_data:
        print("ERROR: No enriched data found. Run a stock_export first.")
        return 1

    print(f"\nEvaluating {len(KPI_ORDER)} KPIs across {len(all_data)} stocks ...")
    results: List[KPIMetrics] = []

    for i, kpi_name in enumerate(KPI_ORDER, 1):
        print(f"  [{i}/{len(KPI_ORDER)}] {kpi_name} ...", end=" ", flush=True)
        t1 = time.time()
        m = evaluate_kpi(kpi_name, all_data, tf.horizons, tf.default_horizon)
        elapsed = time.time() - t1
        if m.available:
            print(f"OK ({m.stocks_with_data} stocks, {m.total_bull_signals} signals, {elapsed:.1f}s)")
        else:
            print(f"UNAVAILABLE (no data)")
        results.append(m)

    print(f"\nGenerating report to {output_dir} ...")
    generate_report(
        results, output_dir,
        tf.timeframe, tf.horizons, tf.horizon_labels, tf.default_horizon,
    )
    print(f"\nPhase 1 complete in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

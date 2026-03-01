"""
Phase 4 — KPI Combination Discovery (Weekly, Long-Only)

Three approaches compared:
  A) Forward stepwise selection (AND-filter)
  B) Weighted voting (optimize weights, threshold)
  C) Dimension gating (require each dimension to agree)

Uses IS/OOS time-split. Reports hit rate, profit factor, signal frequency.
"""

from __future__ import annotations

import json
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from trading_dashboard.kpis.catalog import compute_kpi_state_map, KPI_TREND_ORDER
from trading_dashboard.kpis.rules import STATE_BULL, STATE_BEAR, STATE_NA
from trading_dashboard.indicators.registry import get_dimension_for_kpi, DIMENSIONS
from tf_config import parse_timeframe_arg, output_dir_for, phase1_csv_for, ENRICHED_DIR


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

IS_FRAC = 0.70
SEED = 42

TREND_KPIS = KPI_TREND_ORDER


# ---------------------------------------------------------------------------
# Data
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


def build_state_matrices(
    all_data: Dict[str, pd.DataFrame],
    kpi_names: List[str],
) -> Dict[str, Tuple[pd.DataFrame, pd.Series]]:
    """Returns {symbol: (state_df, close)} for IS/OOS usage."""
    result = {}
    for symbol, df in all_data.items():
        state_map = compute_kpi_state_map(df)
        cols = {}
        for kpi in kpi_names:
            s = state_map.get(kpi)
            if s is not None:
                cols[kpi] = s
        if len(cols) >= 5:
            result[symbol] = (pd.DataFrame(cols, index=df.index), df["Close"])
    return result


# ---------------------------------------------------------------------------
# Metrics helpers
# ---------------------------------------------------------------------------

def _evaluate_signal(
    signal: pd.Series,
    close: pd.Series,
    horizon: int,
) -> Tuple[float, float, int, float]:
    """Returns (hit_rate, profit_factor, n_trades, signals_per_year)."""
    fwd = close.pct_change(horizon).shift(-horizon)
    bull = signal.astype(bool)
    valid = bull & fwd.notna()
    n = int(valid.sum())

    if n == 0:
        return np.nan, np.nan, 0, 0.0

    rets = fwd[valid]
    hr = float((rets > 0).sum() / n)
    gp = rets.clip(lower=0).sum()
    gl = (-rets).clip(lower=0).sum()
    pf = float(gp / gl) if gl > 0 else (float("inf") if gp > 0 else np.nan)

    days = (close.index[-1] - close.index[0]).days
    years = max(days / 365.25, 0.5)
    freq = n / years

    return hr, pf, n, freq


@dataclass
class ComboResult:
    name: str
    description: str
    is_hr: float
    oos_hr: float
    is_pf: float
    oos_pf: float
    is_trades: int
    oos_trades: int
    oos_freq: float


# ---------------------------------------------------------------------------
# Approach A: Forward Stepwise Selection
# ---------------------------------------------------------------------------

def stepwise_selection(
    stock_data: Dict[str, Tuple[pd.DataFrame, pd.Series]],
    kpi_names: List[str],
    horizon: int = 4,
) -> List[ComboResult]:
    """Greedily add KPIs as AND-filters."""
    results: List[ComboResult] = []

    ranked = sorted(kpi_names, key=lambda k: _kpi_oos_hr(k, stock_data, horizon), reverse=True)

    selected: List[str] = []
    best_oos = 0.0

    for step in range(min(8, len(ranked))):
        best_add = None
        best_result = None

        candidates = [k for k in ranked if k not in selected]
        for candidate in candidates:
            test_set = selected + [candidate]
            r = _evaluate_combo_and(test_set, stock_data, horizon, f"Step {step + 1}: +{candidate}")
            if r.oos_trades < 50:
                continue
            if r.oos_hr > best_oos or best_result is None:
                best_add = candidate
                best_result = r
                best_oos = r.oos_hr

        if best_add is None:
            break

        selected.append(best_add)
        best_result.name = f"AND({', '.join(selected)})"
        best_result.description = f"Step {step + 1}: added {best_add}"
        results.append(best_result)

        if best_result.oos_freq < 1.0:
            break

    return results


def _kpi_oos_hr(kpi: str, stock_data: Dict[str, Tuple[pd.DataFrame, pd.Series]], horizon: int) -> float:
    """Quick OOS hit rate for a single KPI."""
    hrs = []
    for symbol, (states_df, close) in stock_data.items():
        if kpi not in states_df.columns:
            continue
        split = int(len(states_df) * IS_FRAC)
        oos_states = states_df[kpi].iloc[split:]
        oos_close = close.iloc[split:]
        bull = oos_states == STATE_BULL
        fwd = oos_close.pct_change(horizon).shift(-horizon)
        valid = bull & fwd.notna()
        n = valid.sum()
        if n > 0:
            hrs.append(float((fwd[valid] > 0).sum() / n))
    return float(np.median(hrs)) if hrs else 0.0


def _evaluate_combo_and(
    kpis: List[str],
    stock_data: Dict[str, Tuple[pd.DataFrame, pd.Series]],
    horizon: int,
    desc: str,
) -> ComboResult:
    """Evaluate an AND-combination (all KPIs must be bull)."""
    is_hr_w, is_pf_w, is_n, oos_hr_w, oos_pf_w, oos_n = 0, 0, 0, 0, 0, 0
    oos_freq_list = []

    for symbol, (states_df, close) in stock_data.items():
        avail = [k for k in kpis if k in states_df.columns]
        if len(avail) < len(kpis):
            continue

        all_bull = (states_df[avail] == STATE_BULL).all(axis=1).astype(int)
        split = int(len(states_df) * IS_FRAC)

        is_sig = all_bull.iloc[:split]
        is_close = close.iloc[:split]
        oos_sig = all_bull.iloc[split:]
        oos_close = close.iloc[split:]

        hr_is, pf_is, n_is, _ = _evaluate_signal(is_sig, is_close, horizon)
        hr_oos, pf_oos, n_oos, freq_oos = _evaluate_signal(oos_sig, oos_close, horizon)

        if not np.isnan(hr_is):
            is_hr_w += hr_is * n_is
            is_n += n_is
        if not np.isnan(pf_is) and pf_is != float("inf"):
            is_pf_w += pf_is * n_is
        if not np.isnan(hr_oos):
            oos_hr_w += hr_oos * n_oos
            oos_n += n_oos
            oos_freq_list.append(freq_oos)
        if not np.isnan(pf_oos) and pf_oos != float("inf"):
            oos_pf_w += pf_oos * n_oos

    return ComboResult(
        name=desc, description=desc,
        is_hr=is_hr_w / is_n if is_n > 0 else np.nan,
        oos_hr=oos_hr_w / oos_n if oos_n > 0 else np.nan,
        is_pf=is_pf_w / is_n if is_n > 0 else np.nan,
        oos_pf=oos_pf_w / oos_n if oos_n > 0 else np.nan,
        is_trades=is_n, oos_trades=oos_n,
        oos_freq=float(np.median(oos_freq_list)) if oos_freq_list else 0,
    )


# ---------------------------------------------------------------------------
# Approach B: Weighted Voting
# ---------------------------------------------------------------------------

def weighted_voting_search(
    stock_data: Dict[str, Tuple[pd.DataFrame, pd.Series]],
    kpi_names: List[str],
    n_trials: int = 200,
    horizon: int = 4,
) -> List[ComboResult]:
    """Random search for optimal weights and threshold."""
    rng = random.Random(SEED)
    results: List[ComboResult] = []

    for trial in range(n_trials):
        n_kpis = len(kpi_names)
        raw_weights = [rng.random() for _ in range(n_kpis)]
        s = sum(raw_weights)
        weights = {k: w / s for k, w in zip(kpi_names, raw_weights)}
        threshold = rng.uniform(0.1, 0.7)

        r = _evaluate_weighted(weights, threshold, stock_data, kpi_names, horizon)
        if r.oos_trades >= 100:
            results.append(r)

    results.sort(key=lambda r: r.oos_hr, reverse=True)
    return results[:10]


def _evaluate_weighted(
    weights: Dict[str, float],
    threshold: float,
    stock_data: Dict[str, Tuple[pd.DataFrame, pd.Series]],
    kpi_names: List[str],
    horizon: int,
) -> ComboResult:
    is_hr_w, is_pf_w, is_n = 0.0, 0.0, 0
    oos_hr_w, oos_pf_w, oos_n = 0.0, 0.0, 0
    oos_freq_list = []

    for symbol, (states_df, close) in stock_data.items():
        avail = [k for k in kpi_names if k in states_df.columns]
        if len(avail) < 5:
            continue

        w_sum = sum(weights.get(k, 0) for k in avail)
        if w_sum == 0:
            continue

        score = sum(
            weights.get(k, 0) / w_sum * states_df[k].replace(-2, 0).astype(float)
            for k in avail
        )
        signal = (score >= threshold).astype(int)

        split = int(len(states_df) * IS_FRAC)
        hr_is, pf_is, n_is, _ = _evaluate_signal(signal.iloc[:split], close.iloc[:split], horizon)
        hr_oos, pf_oos, n_oos, freq = _evaluate_signal(signal.iloc[split:], close.iloc[split:], horizon)

        if not np.isnan(hr_is):
            is_hr_w += hr_is * n_is
            is_n += n_is
        if not np.isnan(pf_is) and pf_is != float("inf"):
            is_pf_w += pf_is * n_is
        if not np.isnan(hr_oos):
            oos_hr_w += hr_oos * n_oos
            oos_n += n_oos
            oos_freq_list.append(freq)
        if not np.isnan(pf_oos) and pf_oos != float("inf"):
            oos_pf_w += pf_oos * n_oos

    top_w = sorted(weights.items(), key=lambda x: -x[1])[:3]
    desc = f"thr={threshold:.2f}, top: {', '.join(f'{k}={v:.2f}' for k, v in top_w)}"

    return ComboResult(
        name=f"Weighted(thr={threshold:.2f})", description=desc,
        is_hr=is_hr_w / is_n if is_n > 0 else np.nan,
        oos_hr=oos_hr_w / oos_n if oos_n > 0 else np.nan,
        is_pf=is_pf_w / is_n if is_n > 0 else np.nan,
        oos_pf=oos_pf_w / oos_n if oos_n > 0 else np.nan,
        is_trades=is_n, oos_trades=oos_n,
        oos_freq=float(np.median(oos_freq_list)) if oos_freq_list else 0,
    )


# ---------------------------------------------------------------------------
# Approach C: Dimension Gating
# ---------------------------------------------------------------------------

def dimension_gating(
    stock_data: Dict[str, Tuple[pd.DataFrame, pd.Series]],
    kpi_names: List[str],
    horizon: int = 4,
) -> List[ComboResult]:
    """Require majority-bull in each dimension for entry."""
    dim_map: Dict[str, List[str]] = {}
    for kpi in kpi_names:
        dim = get_dimension_for_kpi(kpi) or "unknown"
        if dim in ("other", "unknown"):
            continue
        dim_map.setdefault(dim, []).append(kpi)

    strategies = [
        ("All dims majority", list(dim_map.keys()), 0.5),
        ("Trend+Momentum majority", ["trend", "momentum"], 0.5),
        ("Trend+Momentum+RS majority", ["trend", "momentum", "relative_strength"], 0.5),
        ("Trend+Momentum 2/3", ["trend", "momentum"], 0.67),
        ("All dims 2/3", list(dim_map.keys()), 0.67),
    ]

    results = []
    for name, required_dims, min_frac in strategies:
        r = _evaluate_dim_gating(dim_map, required_dims, min_frac, stock_data, kpi_names, horizon, name)
        results.append(r)

    return results


def _evaluate_dim_gating(
    dim_map: Dict[str, List[str]],
    required_dims: List[str],
    min_frac: float,
    stock_data: Dict[str, Tuple[pd.DataFrame, pd.Series]],
    kpi_names: List[str],
    horizon: int,
    name: str,
) -> ComboResult:
    is_hr_w, is_pf_w, is_n = 0.0, 0.0, 0
    oos_hr_w, oos_pf_w, oos_n = 0.0, 0.0, 0
    oos_freq_list = []

    for symbol, (states_df, close) in stock_data.items():
        dim_pass = None
        for dim in required_dims:
            kpis = [k for k in dim_map.get(dim, []) if k in states_df.columns]
            if not kpis:
                continue
            bull_frac = (states_df[kpis] == STATE_BULL).sum(axis=1) / len(kpis)
            dim_ok = bull_frac >= min_frac
            dim_pass = dim_ok if dim_pass is None else (dim_pass & dim_ok)

        if dim_pass is None:
            continue

        signal = dim_pass.astype(int)
        split = int(len(states_df) * IS_FRAC)

        hr_is, pf_is, n_is, _ = _evaluate_signal(signal.iloc[:split], close.iloc[:split], horizon)
        hr_oos, pf_oos, n_oos, freq = _evaluate_signal(signal.iloc[split:], close.iloc[split:], horizon)

        if not np.isnan(hr_is):
            is_hr_w += hr_is * n_is
            is_n += n_is
        if not np.isnan(pf_is) and pf_is != float("inf"):
            is_pf_w += pf_is * n_is
        if not np.isnan(hr_oos):
            oos_hr_w += hr_oos * n_oos
            oos_n += n_oos
            oos_freq_list.append(freq)
        if not np.isnan(pf_oos) and pf_oos != float("inf"):
            oos_pf_w += pf_oos * n_oos

    return ComboResult(
        name=name, description=f"dims={required_dims}, min_frac={min_frac}",
        is_hr=is_hr_w / is_n if is_n > 0 else np.nan,
        oos_hr=oos_hr_w / oos_n if oos_n > 0 else np.nan,
        is_pf=is_pf_w / is_n if is_n > 0 else np.nan,
        oos_pf=oos_pf_w / oos_n if oos_n > 0 else np.nan,
        is_trades=is_n, oos_trades=oos_n,
        oos_freq=float(np.median(oos_freq_list)) if oos_freq_list else 0,
    )


# ---------------------------------------------------------------------------
# Baseline: equal-weight TrendScore > 0
# ---------------------------------------------------------------------------

def baseline_trendscore(
    stock_data: Dict[str, Tuple[pd.DataFrame, pd.Series]],
    kpi_names: List[str],
    horizon: int = 4,
) -> ComboResult:
    is_hr_w, is_pf_w, is_n = 0.0, 0.0, 0
    oos_hr_w, oos_pf_w, oos_n = 0.0, 0.0, 0
    oos_freq_list = []

    for symbol, (states_df, close) in stock_data.items():
        avail = [k for k in kpi_names if k in states_df.columns]
        if len(avail) < 5:
            continue
        score = states_df[avail].replace(-2, 0).sum(axis=1)
        signal = (score > 0).astype(int)

        split = int(len(states_df) * IS_FRAC)
        hr_is, pf_is, n_is, _ = _evaluate_signal(signal.iloc[:split], close.iloc[:split], horizon)
        hr_oos, pf_oos, n_oos, freq = _evaluate_signal(signal.iloc[split:], close.iloc[split:], horizon)

        if not np.isnan(hr_is):
            is_hr_w += hr_is * n_is
            is_n += n_is
        if not np.isnan(pf_is) and pf_is != float("inf"):
            is_pf_w += pf_is * n_is
        if not np.isnan(hr_oos):
            oos_hr_w += hr_oos * n_oos
            oos_n += n_oos
            oos_freq_list.append(freq)
        if not np.isnan(pf_oos) and pf_oos != float("inf"):
            oos_pf_w += pf_oos * n_oos

    return ComboResult(
        name="Baseline: TrendScore > 0", description="Equal-weight sum of trend KPIs > 0",
        is_hr=is_hr_w / is_n if is_n > 0 else np.nan,
        oos_hr=oos_hr_w / oos_n if oos_n > 0 else np.nan,
        is_pf=is_pf_w / is_n if is_n > 0 else np.nan,
        oos_pf=oos_pf_w / oos_n if oos_n > 0 else np.nan,
        is_trades=is_n, oos_trades=oos_n,
        oos_freq=float(np.median(oos_freq_list)) if oos_freq_list else 0,
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _fmt(v: float, d: int = 3) -> str:
    if v is None or np.isnan(v):
        return "—"
    if v == float("inf"):
        return "inf"
    return f"{v:.{d}f}"


def generate_report(
    baseline: ComboResult,
    stepwise: List[ComboResult],
    weighted: List[ComboResult],
    gating: List[ComboResult],
    output_dir: Path,
    timeframe: str,
    horizon: int,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    all_results = [baseline] + stepwise + weighted[:5] + gating
    all_results.sort(key=lambda r: r.oos_hr if not np.isnan(r.oos_hr) else 0, reverse=True)

    h_label = "w" if timeframe == "1W" else ("d" if timeframe == "1D" else "h")
    lines = [
        f"# Phase 4 — KPI Combination Discovery ({timeframe}, Long-Only)",
        "",
        f"**IS/OOS:** {IS_FRAC*100:.0f}% / {(1-IS_FRAC)*100:.0f}% | **Horizon:** {horizon}{h_label}",
        "",
        "## All Strategies Ranked by OOS Hit Rate",
        "",
        "| Rank | Strategy | IS HR | OOS HR | IS PF | OOS PF | Trades (OOS) | Freq/yr | Approach |",
        "|------|----------|-------|--------|-------|--------|-------------|---------|----------|",
    ]

    for i, r in enumerate(all_results, 1):
        approach = "Baseline" if "Baseline" in r.name else ("Stepwise" if "AND" in r.name else ("Weighted" if "Weighted" in r.name else "DimGate"))
        lines.append(
            f"| {i} | {r.name} | {_fmt(r.is_hr)} | {_fmt(r.oos_hr)} "
            f"| {_fmt(r.is_pf, 2)} | {_fmt(r.oos_pf, 2)} "
            f"| {r.oos_trades} | {_fmt(r.oos_freq, 1)} | {approach} |"
        )

    lines.extend(["", "## Approach A: Forward Stepwise Selection", ""])
    for r in stepwise:
        lines.append(f"- **{r.name}**: OOS HR={_fmt(r.oos_hr)}, PF={_fmt(r.oos_pf, 2)}, "
                     f"trades={r.oos_trades}, freq={_fmt(r.oos_freq, 1)}/yr")

    lines.extend(["", "## Approach B: Weighted Voting (top 5 of 200 trials)", ""])
    for r in weighted[:5]:
        lines.append(f"- **{r.name}**: OOS HR={_fmt(r.oos_hr)}, PF={_fmt(r.oos_pf, 2)}, "
                     f"trades={r.oos_trades} — {r.description}")

    lines.extend(["", "## Approach C: Dimension Gating", ""])
    for r in gating:
        lines.append(f"- **{r.name}**: OOS HR={_fmt(r.oos_hr)}, PF={_fmt(r.oos_pf, 2)}, "
                     f"trades={r.oos_trades}, freq={_fmt(r.oos_freq, 1)}/yr — {r.description}")

    winner = all_results[0]
    lines.extend([
        "",
        "## Recommendation",
        "",
        f"**Best strategy:** {winner.name}",
        f"- OOS Hit Rate: {_fmt(winner.oos_hr)}",
        f"- OOS Profit Factor: {_fmt(winner.oos_pf, 2)}",
        f"- OOS Trades: {winner.oos_trades}",
        f"- Signal Frequency: {_fmt(winner.oos_freq, 1)}/yr",
        "",
        f"**vs Baseline (TrendScore > 0):** OOS HR = {_fmt(baseline.oos_hr)} "
        f"(delta = {_fmt(winner.oos_hr - baseline.oos_hr) if not np.isnan(winner.oos_hr) and not np.isnan(baseline.oos_hr) else '—'})",
        "",
    ])

    report = "\n".join(lines)
    (output_dir / "combination_report.md").write_text(report, encoding="utf-8")
    print(report)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    tf = parse_timeframe_arg("Phase 4 — Combination Discovery")
    OUTPUT_DIR = output_dir_for(tf.timeframe, "phase4")

    t0 = time.time()
    print(f"Loading data from {ENRICHED_DIR} ...")
    all_data = load_data(ENRICHED_DIR, tf.timeframe)
    print(f"Loaded {len(all_data)} stocks")

    print("Building state matrices ...")
    stock_data = build_state_matrices(all_data, TREND_KPIS)
    print(f"State matrices for {len(stock_data)} stocks")

    horizon = tf.default_horizon
    print("\n--- Baseline ---")
    baseline = baseline_trendscore(stock_data, TREND_KPIS, horizon=horizon)
    print(f"  TrendScore > 0: OOS HR = {_fmt(baseline.oos_hr)}, PF = {_fmt(baseline.oos_pf, 2)}")

    print("\n--- Approach A: Stepwise Selection ---")
    stepwise = stepwise_selection(stock_data, TREND_KPIS, horizon=horizon)
    for r in stepwise:
        print(f"  {r.description}: OOS HR = {_fmt(r.oos_hr)}")

    print("\n--- Approach B: Weighted Voting (200 trials) ---")
    weighted = weighted_voting_search(stock_data, TREND_KPIS, n_trials=200, horizon=horizon)
    for r in weighted[:3]:
        print(f"  {r.name}: OOS HR = {_fmt(r.oos_hr)}")

    print("\n--- Approach C: Dimension Gating ---")
    gating = dimension_gating(stock_data, TREND_KPIS, horizon=horizon)
    for r in gating:
        print(f"  {r.name}: OOS HR = {_fmt(r.oos_hr)}")

    print(f"\nGenerating report ...")
    generate_report(baseline, stepwise, weighted, gating, OUTPUT_DIR, tf.timeframe, horizon)
    print(f"\nPhase 4 complete in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

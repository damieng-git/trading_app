"""
Phase 11 — Entry Combo Optimization for Exit Flow v3

Two-stage funnel:
  Stage 1 (fast): Vectorized screening of all C(n,k) combos.
    - Forward return at horizon H
    - ATR-hit approximation: does min(Low[t+1..t+M]) < Close[t] - K*ATR?
    - Keep top candidates by composite score

  Stage 2 (precise): Full v3 exit flow simulation on top candidates.
    - Actual trade-by-trade simulation with 2-stage exits
    - Tracks: hit rate, avg return, ATR hit %, stage distribution, worst trade

Objectives:
  (a) Minimize ATR-hit rate (safer entries)
  (b) Maximize returns via exit flow (higher returns)
  (c) Pareto-optimal: best return among low-ATR-hit combos

Outputs per timeframe:
  - Pareto frontier charts (return vs ATR-hit rate)
  - KPI importance (frequency in top combos)
  - Comparison vs current production combos
  - Recommendations per sector
"""

from __future__ import annotations

import heapq
import json
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from textwrap import fill
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from trading_dashboard.kpis.catalog import (
    compute_kpi_state_map,
    KPI_TREND_ORDER,
    KPI_BREAKOUT_ORDER,
)
from trading_dashboard.kpis.rules import STATE_BULL, STATE_NA
from apps.dashboard.sector_map import load_sector_map
from tf_config import ENRICHED_DIR, TFConfig, TIMEFRAME_CONFIGS, output_dir_for
from phase8_exit_by_sector import load_data, IS_FRACTION, COMBO_DEFINITIONS

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

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

V3_PARAMS = {
    "4H": {"T": 4, "M": 48, "K": 3.5},
    "1D": {"T": 4, "M": 40, "K": 3.5},
    "1W": {"T": 2, "M": 20, "K": 2.0},
}

ATR_PERIOD = 14
MIN_KPI_COVERAGE = 0.30
TOP_N_SCREEN = 60        # per objective → up to 180 unique candidates after merge
TOP_N_FINAL = 10

MIN_TRADES_BY_TF = {"4H": 50, "1D": 50, "1W": 10}

EXCLUDED_KPIS = {
    "Nadaraya-Watson Envelop (Repainting)",  # uses future data (repaint=True)
}

ALL_KPIS: List[str] = []
_seen: set = set()
for _k in list(KPI_TREND_ORDER) + list(KPI_BREAKOUT_ORDER) + ["GK Trend Ribbon", "Impulse Trend"]:
    if _k not in _seen and _k not in EXCLUDED_KPIS:
        ALL_KPIS.append(_k)
        _seen.add(_k)

KPI_SHORT: Dict[str, str] = {
    "Nadaraya-Watson Smoother": "NW Smooth",
    "Nadaraya-Watson Envelop (MAE)": "NWE MAE",
    "Nadaraya-Watson Envelop (STD)": "NWE STD",
    "BB 30": "BB 30",
    "cRSI": "cRSI",
    "SR Breaks": "SR Breaks",
    "Stoch_MTM": "Stoch MTM",
    "CM_P-SAR": "P-SAR",
    "MA Ribbon": "MA Rib",
    "Madrid Ribbon": "Madrid",
    "Donchian Ribbon": "Donchian",
    "CM_Ult_MacD_MFT": "MACD MFT",
    "GK Trend Ribbon": "GK Trend",
    "Impulse Trend": "Impulse",
    "SQZMOM_LB": "SQZ Mom",
    "Ichimoku": "Ichimoku",
    "ADX & DI": "ADX",
    "SuperTrend": "SuperTrend",
    "UT Bot Alert": "UT Bot",
    "Mansfield RS": "Mansfield",
    "DEMA": "DEMA",
    "GMMA": "GMMA",
    "WT_LB": "WT",
    "OBVOSC_LB": "OBV Osc",
    "TuTCI": "TuTCI",
    "RSI Strength & Consolidation Zones (Zeiierman)": "Zeiierman",
    "Breakout Targets": "Breakout T",
}


def _short(kpi: str) -> str:
    return KPI_SHORT.get(kpi, kpi[:12])


def _short_list(kpis: List[str], sep: str = " + ") -> str:
    return sep.join(_short(k) for k in kpis) if kpis else "—"


# ── ATR computation ──────────────────────────────────────────────────────

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, pc = df["High"], df["Low"], df["Close"].shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()


# ── Stage 1: Fast vectorized screening ───────────────────────────────────

@dataclass
class ScreenArrays:
    """Pre-computed arrays for fast combo screening (OOS portion only)."""
    close: np.ndarray
    low_min_forward: np.ndarray   # rolling min of Low over next M bars
    atr: np.ndarray
    fwd_return: np.ndarray        # forward return at horizon H
    valid: np.ndarray             # True where fwd_return is valid
    bulls: Dict[str, np.ndarray]
    n_stocks: int
    kpi_coverage: Dict[str, float]


def precompute_screen_arrays(
    all_data: Dict[str, pd.DataFrame],
    kpi_names: List[str],
    horizon: int,
    M: int,
) -> ScreenArrays:
    all_close, all_low_min, all_atr, all_fwd = [], [], [], []
    kpi_bulls: Dict[str, List[np.ndarray]] = {k: [] for k in kpi_names}
    kpi_avail: Dict[str, int] = {k: 0 for k in kpi_names}
    n_stocks = 0

    for sym, df in all_data.items():
        if df.empty:
            continue
        split_idx = int(len(df) * IS_FRACTION)
        oos = df.iloc[split_idx:].copy()
        if len(oos) < M + 5:
            continue

        close = oos["Close"].to_numpy(dtype=float)
        low = oos["Low"].to_numpy(dtype=float)
        n = len(oos)

        low_min_fwd = np.full(n, np.nan)
        for i in range(n - 1):
            end = min(i + M + 1, n)
            low_min_fwd[i] = np.nanmin(low[i + 1:end])

        atr_full = compute_atr(df, ATR_PERIOD)
        atr_oos = atr_full.iloc[split_idx:].to_numpy(dtype=float)

        fwd = np.full(n, np.nan)
        for i in range(n - horizon):
            if close[i] > 0:
                fwd[i] = (close[i + horizon] - close[i]) / close[i] * 100

        state_map = compute_kpi_state_map(df)
        for kpi in kpi_names:
            if kpi in state_map:
                full_bull = (state_map[kpi] == STATE_BULL).to_numpy(dtype=bool)
                kpi_bulls[kpi].append(full_bull[split_idx:])
                if (state_map[kpi] != STATE_NA).any():
                    kpi_avail[kpi] += 1
            else:
                kpi_bulls[kpi].append(np.zeros(n, dtype=bool))

        all_close.append(close)
        all_low_min.append(low_min_fwd)
        all_atr.append(atr_oos)
        all_fwd.append(fwd)
        n_stocks += 1

    if not all_close:
        return ScreenArrays(
            np.array([]), np.array([]), np.array([]), np.array([]),
            np.array([], dtype=bool), {}, 0, {},
        )

    close_cat = np.concatenate(all_close)
    fwd_cat = np.concatenate(all_fwd)

    return ScreenArrays(
        close=close_cat,
        low_min_forward=np.concatenate(all_low_min),
        atr=np.concatenate(all_atr),
        fwd_return=fwd_cat,
        valid=~np.isnan(fwd_cat),
        bulls={k: np.concatenate(v) for k, v in kpi_bulls.items()},
        n_stocks=n_stocks,
        kpi_coverage={k: kpi_avail[k] / n_stocks if n_stocks else 0 for k in kpi_names},
    )


@dataclass
class ScreenResult:
    kpis: List[str]
    n_trades: int
    avg_return: float
    hit_rate: float
    atr_hit_pct: float
    composite_score: float


def screen_combos(
    sa: ScreenArrays,
    k: int,
    kpi_names: List[str],
    K_atr: float,
    min_trades: int,
    top_n: int = TOP_N_SCREEN,
) -> List[ScreenResult]:
    """Screen all C(n,k) combos using three objective heaps, then merge unique."""
    available = [kpi for kpi in kpi_names if sa.kpi_coverage.get(kpi, 0) >= MIN_KPI_COVERAGE]
    if len(available) < k:
        return []

    hard_stop = sa.close - K_atr * sa.atr
    atr_would_hit = sa.low_min_forward < hard_stop

    # Three heaps: best-return, best-hitrate, best-balanced
    heap_ret: List[Tuple[float, int, Tuple[str, ...], int, float, float, float]] = []
    heap_hr: List[Tuple[float, int, Tuple[str, ...], int, float, float, float]] = []
    heap_bal: List[Tuple[float, int, Tuple[str, ...], int, float, float, float]] = []
    ctr = 0

    for combo in combinations(available, k):
        combined = sa.bulls[combo[0]].copy()
        for kpi in combo[1:]:
            combined &= sa.bulls[kpi]
        combined &= sa.valid

        n = int(combined.sum())
        if n < min_trades:
            continue

        rets = sa.fwd_return[combined]
        avg_ret = float(np.mean(rets))
        hr = float(np.sum(rets > 0) / n) * 100
        atr_hits = float(np.sum(atr_would_hit[combined]) / n) * 100

        score_ret = avg_ret - 2.0 * atr_hits
        score_hr = hr - atr_hits
        score_bal = (hr / 100) * avg_ret - 1.5 * atr_hits

        entry = (combo, n, avg_ret, hr, atr_hits)

        for heap, score in [(heap_ret, score_ret), (heap_hr, score_hr), (heap_bal, score_bal)]:
            if len(heap) < top_n:
                heapq.heappush(heap, (score, ctr, *entry))
            elif score > heap[0][0]:
                heapq.heapreplace(heap, (score, ctr, *entry))
        ctr += 1

    # Merge unique combos from all three heaps
    seen: set = set()
    merged: List[ScreenResult] = []
    for heap in [heap_ret, heap_hr, heap_bal]:
        for sc, _, combo, n_t, ar, hr_v, ah in heap:
            key = tuple(sorted(combo))
            if key not in seen:
                seen.add(key)
                merged.append(ScreenResult(
                    kpis=list(combo), n_trades=n_t,
                    avg_return=ar, hit_rate=hr_v, atr_hit_pct=ah,
                    composite_score=(hr_v / 100) * ar - 1.5 * ah,
                ))

    merged.sort(key=lambda r: r.composite_score, reverse=True)
    return merged


# ── Stage 2: Full v3 exit flow simulation ────────────────────────────────

@dataclass
class FullResult:
    kpis: List[str]
    n_trades: int
    hit_rate: float
    avg_return: float
    median_return: float
    worst_trade: float
    profit_factor: float
    atr_hit_pct: float
    pct_strict: float
    pct_maxhold: float
    avg_hold: float


def simulate_v3_for_combo(
    all_data: Dict[str, pd.DataFrame],
    combo_kpis: List[str],
    T: int,
    M: int,
    K: float,
) -> Optional[FullResult]:
    all_rets: List[float] = []
    exit_reasons: Dict[str, int] = {"atr": 0, "lenient": 0, "strict": 0, "maxhold": 0}
    all_holds: List[int] = []

    for sym, df in all_data.items():
        if df.empty:
            continue
        state_map = compute_kpi_state_map(df)
        avail = [k for k in combo_kpis if k in state_map]
        if len(avail) < len(combo_kpis):
            continue

        all_bull = pd.Series(True, index=df.index)
        for kpi in avail:
            all_bull = all_bull & (state_map[kpi] == STATE_BULL)
        signal = all_bull.astype(bool)
        if signal.sum() == 0:
            continue

        split_idx = int(len(df) * IS_FRACTION)
        test_start = df.index[split_idx]

        close = df["Close"].to_numpy(dtype=float)
        atr = compute_atr(df, ATR_PERIOD).to_numpy(dtype=float)
        n_kpis = len(combo_kpis)
        test_mask = df.index >= test_start
        sig_dates = signal[test_mask & signal].index

        i = 0
        while i < len(sig_dates):
            entry_idx = df.index.get_loc(sig_dates[i])
            ep = float(close[entry_idx])
            if ep <= 0:
                i += 1
                continue
            hard_stop = ep - K * atr[entry_idx] if atr[entry_idx] > 0 else -np.inf

            exit_idx = None
            reason = "maxhold"

            for j in range(entry_idx + 1, min(entry_idx + M + 1, len(df))):
                bars = j - entry_idx
                c = float(close[j])

                if c < hard_stop:
                    exit_idx, reason = j, "atr"
                    break

                n_nb = sum(
                    1 for kpi in combo_kpis
                    if kpi in state_map and j < len(state_map[kpi])
                    and int(state_map[kpi].iloc[j]) != STATE_BULL
                )

                if bars <= T:
                    if n_nb >= n_kpis:
                        exit_idx, reason = j, "lenient"
                        break
                else:
                    if n_nb >= 2:
                        exit_idx, reason = j, "strict"
                        break

            if exit_idx is None:
                exit_idx = min(entry_idx + M, len(df) - 1)

            xp = float(close[exit_idx])
            ret = (xp - ep) / ep * 100
            hold = exit_idx - entry_idx
            if hold > 0:
                all_rets.append(ret)
                exit_reasons[reason] += 1
                all_holds.append(hold)

            next_i = i + 1
            while next_i < len(sig_dates) and df.index.get_loc(sig_dates[next_i]) <= exit_idx:
                next_i += 1
            i = next_i

    n = len(all_rets)
    if n < 5:
        return None

    hr = sum(1 for r in all_rets if r > 0) / n * 100
    avg = float(np.mean(all_rets))
    med = float(np.median(all_rets))
    worst = min(all_rets)
    wins = sum(r for r in all_rets if r > 0)
    losses = abs(sum(r for r in all_rets if r <= 0))
    pf = wins / losses if losses > 0 else 999.0
    total_exits = sum(exit_reasons.values())
    atr_pct = exit_reasons["atr"] / total_exits * 100 if total_exits else 0
    strict_pct = exit_reasons["strict"] / total_exits * 100 if total_exits else 0
    mh_pct = exit_reasons["maxhold"] / total_exits * 100 if total_exits else 0
    avg_hold = float(np.mean(all_holds)) if all_holds else 0

    return FullResult(
        kpis=combo_kpis, n_trades=n, hit_rate=hr, avg_return=avg,
        median_return=med, worst_trade=worst, profit_factor=pf,
        atr_hit_pct=atr_pct, pct_strict=strict_pct, pct_maxhold=mh_pct,
        avg_hold=avg_hold,
    )


# ── Charts ───────────────────────────────────────────────────────────────

def chart_pareto(
    results: List[FullResult],
    current: Optional[FullResult],
    k: int,
    tf: str,
    out_dir: Path,
):
    if not results:
        return

    fig, axes = plt.subplots(1, 2, figsize=(22, 10))

    # Left panel: Hit Rate vs Avg Return (the user's core question)
    ax = axes[0]
    hrs = [r.hit_rate for r in results]
    rets = [r.avg_return for r in results]
    sizes = [max(30, min(300, r.n_trades * 1.5)) for r in results]
    sc = ax.scatter(hrs, rets, s=sizes, c=rets, cmap="RdYlGn",
                    edgecolors="white", linewidth=0.8, alpha=0.85, zorder=5)

    for i, r in enumerate(results[:8]):
        ax.annotate(
            f"{_short_list(r.kpis, ', ')}\nn={r.n_trades}",
            (r.hit_rate, r.avg_return),
            fontsize=7, color="white", alpha=0.9,
            xytext=(8, 4), textcoords="offset points",
            bbox=dict(boxstyle="round,pad=0.2", facecolor="#333", alpha=0.7),
        )

    if current:
        ax.scatter([current.hit_rate], [current.avg_return], s=250,
                   marker="*", c="cyan", edgecolors="white", linewidth=1.5,
                   zorder=10, label=f"Current (n={current.n_trades}): {_short_list(current.kpis)}")
        ax.legend(loc="lower right", fontsize=8)

    ax.set_xlabel("Hit Rate (%)")
    ax.set_ylabel("Avg Return (%)")
    ax.set_title(f"Hit Rate vs Return\n(bubble size = trade count)")
    ax.grid(True, alpha=0.2)

    # Right panel: ATR Hit Rate vs Avg Return
    ax2 = axes[1]
    atr_hits = [r.atr_hit_pct for r in results]
    sc2 = ax2.scatter(atr_hits, rets, s=sizes, c=[r.hit_rate for r in results],
                      cmap="RdYlGn", edgecolors="white", linewidth=0.8, alpha=0.85, zorder=5)
    for i, r in enumerate(results[:8]):
        ax2.annotate(
            f"{_short_list(r.kpis, ', ')}\nn={r.n_trades}",
            (r.atr_hit_pct, r.avg_return),
            fontsize=7, color="white", alpha=0.9,
            xytext=(8, 4), textcoords="offset points",
            bbox=dict(boxstyle="round,pad=0.2", facecolor="#333", alpha=0.7),
        )
    if current:
        ax2.scatter([current.atr_hit_pct], [current.avg_return], s=250,
                    marker="*", c="cyan", edgecolors="white", linewidth=1.5, zorder=10)
    ax2.set_xlabel("ATR Hit Rate (%)")
    ax2.set_ylabel("Avg Return (%)")
    ax2.set_title(f"Safety vs Return\n(color = hit rate)")
    ax2.grid(True, alpha=0.2)
    fig.colorbar(sc2, ax=ax2, label="Hit Rate (%)", shrink=0.8)

    fig.suptitle(f"{tf} C{k} — Entry Combo Pareto Frontier (min {MIN_TRADES_BY_TF.get(tf, 50)} trades)",
                 fontsize=14, fontweight="bold")
    plt.tight_layout()
    fname = out_dir / f"pareto_C{k}.png"
    fig.savefig(fname)
    plt.close(fig)
    print(f"    Saved {fname.name}")


def chart_kpi_importance(
    results_by_k: Dict[int, List[FullResult]],
    tf: str,
    out_dir: Path,
):
    counter: Counter = Counter()
    for k, results in results_by_k.items():
        for r in results[:TOP_N_FINAL]:
            for kpi in r.kpis:
                counter[kpi] += 1

    if not counter:
        return

    kpis_sorted = counter.most_common(20)
    names = [_short(k) for k, _ in kpis_sorted]
    counts = [c for _, c in kpis_sorted]

    fig, ax = plt.subplots(figsize=(14, 8))
    colors = ["#66bb6a" if c >= 3 else "#42a5f5" if c >= 2 else "#78909c" for c in counts]
    ax.barh(range(len(names)), counts, color=colors)
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names)
    ax.set_xlabel("Appearances in Top-10 Combos (C3 + C4 + C5)")
    ax.set_title(f"{tf} — KPI Importance in Optimized Entry Combos")
    ax.invert_yaxis()

    for i, c in enumerate(counts):
        ax.text(c + 0.2, i, str(c), va="center", fontsize=9, fontweight="bold")

    plt.tight_layout()
    fname = out_dir / f"kpi_importance.png"
    fig.savefig(fname)
    plt.close(fig)
    print(f"    Saved {fname.name}")


def chart_comparison(
    best_new: Dict[str, Dict[int, FullResult]],
    current: Dict[int, Optional[FullResult]],
    tf: str,
    out_dir: Path,
):
    ks = [3, 4, 5]
    objectives = list(best_new.keys())
    if not objectives:
        return

    fig, axes = plt.subplots(2, 2, figsize=(22, 16))
    metrics = [
        ("avg_return", "Avg Return (%)", axes[0, 0]),
        ("hit_rate", "Hit Rate (%)", axes[0, 1]),
        ("atr_hit_pct", "ATR Hit Rate (%)", axes[1, 0]),
        ("n_trades", "Trade Count", axes[1, 1]),
    ]

    obj_colors = {"return": "#ef5350", "hitrate": "#42a5f5", "balanced": "#66bb6a"}
    obj_labels = {"return": "Best Return", "hitrate": "Best Hit Rate", "balanced": "Best Balanced"}

    for attr, ylabel, ax in metrics:
        x = np.arange(len(ks))
        n_groups = 1 + len(objectives)
        w = 0.8 / n_groups

        cur_vals = [getattr(current.get(k), attr, 0) if current.get(k) else 0 for k in ks]
        bars_cur = ax.bar(x - (n_groups - 1) * w / 2, cur_vals, w,
                          color="#78909c", label="Current", edgecolor="white", linewidth=0.5)
        for i, cv in enumerate(cur_vals):
            ax.text(x[i] - (n_groups - 1) * w / 2, cv + 0.3, f"{cv:.1f}",
                    ha="center", fontsize=7, fontweight="bold")

        for j, obj in enumerate(objectives):
            obj_results = best_new[obj]
            vals = [getattr(obj_results.get(k), attr, 0) if obj_results.get(k) else 0 for k in ks]
            offset = x - (n_groups - 1) * w / 2 + (j + 1) * w
            bars = ax.bar(offset, vals, w, color=obj_colors.get(obj, "#aaa"),
                          label=obj_labels.get(obj, obj), edgecolor="white", linewidth=0.5)
            for i, v in enumerate(vals):
                ax.text(offset[i], v + 0.3, f"{v:.1f}", ha="center", fontsize=7,
                        fontweight="bold", color=obj_colors.get(obj, "white"))

        ax.set_xticks(x)
        ax.set_xticklabels([f"C{k}" for k in ks])
        ax.set_ylabel(ylabel)
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.2, axis="y")

    fig.suptitle(f"{tf} — Current vs Optimized Entry Combos (3 objectives)", fontsize=14, fontweight="bold")
    plt.tight_layout()
    fname = out_dir / f"current_vs_optimized.png"
    fig.savefig(fname)
    plt.close(fig)
    print(f"    Saved {fname.name}")


def chart_top_combos_table(
    results: List[FullResult],
    current: Optional[FullResult],
    k: int,
    tf: str,
    out_dir: Path,
):
    if not results:
        return

    show = results[:TOP_N_FINAL]
    if current:
        show = [current] + show

    fig, ax = plt.subplots(figsize=(18, max(4, len(show) * 0.7 + 2)))
    ax.axis("off")

    headers = ["Rank", "KPIs", "n", "HR%", "Avg%", "Med%", "Worst%", "PF", "ATR%", "Strict%", "MaxH%", "AvgHold"]
    col_widths = [0.04, 0.28, 0.05, 0.05, 0.06, 0.06, 0.06, 0.05, 0.06, 0.06, 0.06, 0.06]

    cell_text = []
    cell_colors = []
    for i, r in enumerate(show):
        is_current = current and r is current
        rank = "CUR" if is_current else str(i if is_current else i)
        row = [
            rank,
            _short_list(r.kpis),
            str(r.n_trades),
            f"{r.hit_rate:.0f}",
            f"{r.avg_return:+.2f}",
            f"{r.median_return:+.2f}",
            f"{r.worst_trade:+.1f}",
            f"{r.profit_factor:.1f}",
            f"{r.atr_hit_pct:.1f}",
            f"{r.pct_strict:.0f}",
            f"{r.pct_maxhold:.0f}",
            f"{r.avg_hold:.0f}",
        ]
        cell_text.append(row)
        bg = "#1a3a1a" if is_current else ("#1e1e1e" if i % 2 == 0 else "#252525")
        cell_colors.append([bg] * len(headers))

    table = ax.table(
        cellText=cell_text,
        colLabels=headers,
        colWidths=col_widths,
        loc="center",
        cellLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1.0, 1.6)

    for (row, col), cell in table.get_celld().items():
        if row == 0:
            cell.set_facecolor("#333")
            cell.set_text_props(fontweight="bold", color="white")
        else:
            cell.set_facecolor(cell_colors[row - 1][col])
            cell.set_text_props(color="white")
        cell.set_edgecolor("#444")

    ax.set_title(f"{tf} C{k} — Top Entry Combos (Exit Flow v3)", fontsize=13, fontweight="bold", pad=20)
    plt.tight_layout()
    fname = out_dir / f"top_combos_C{k}.png"
    fig.savefig(fname)
    plt.close(fig)
    print(f"    Saved {fname.name}")


# ── Report ───────────────────────────────────────────────────────────────

def generate_report(
    all_results: Dict[str, Dict[int, List[FullResult]]],
    all_current: Dict[str, Dict[int, Optional[FullResult]]],
    out_dir: Path,
):
    OBJ_LABELS = {"return": "Best Return", "hitrate": "Best Hit Rate", "balanced": "Balanced"}
    OBJECTIVES = ["return", "hitrate", "balanced"]

    lines = ["# Phase 11 — Entry Combo Optimization for Exit Flow v3\n"]
    lines.append("## Methodology\n")
    lines.append(
        "Two-stage funnel: (1) vectorized screening of all C(n,k) KPI combos using three "
        "objectives (best return, best hit rate, balanced), (2) full bar-by-bar v3 exit flow "
        "simulation on merged unique candidates. Minimum trade thresholds enforced per timeframe "
        f"(4H: {MIN_TRADES_BY_TF['4H']}, 1D: {MIN_TRADES_BY_TF['1D']}, 1W: {MIN_TRADES_BY_TF['1W']}).\n"
    )
    lines.append("**Scoring:**\n")
    lines.append("- **Best Return:** avg_return - 2 * atr_hit")
    lines.append("- **Best Hit Rate:** hit_rate - atr_hit")
    lines.append("- **Balanced:** (hit_rate/100) * avg_return - 1.5 * atr_hit\n")

    for tf in ["4H", "1D", "1W"]:
        tf_results = all_results.get(tf, {})
        tf_current = all_current.get(tf, {})
        if not tf_results:
            continue

        lines.append(f"## {tf} (min {MIN_TRADES_BY_TF.get(tf, 50)} trades)\n")
        for k in [3, 4, 5]:
            results = tf_results.get(k, [])
            cur = tf_current.get(k)
            if not results:
                lines.append(f"### C{k}\n_No combos met the minimum trade threshold._\n")
                continue

            lines.append(f"### C{k}\n")

            for obj in OBJECTIVES:
                best = _pick_best(results, obj)
                if not best:
                    continue
                lines.append(f"**{OBJ_LABELS[obj]}:**\n")
                lines.append(f"| KPIs | n | HR | Avg Ret | ATR Hit | PF | AvgHold |")
                lines.append(f"|------|---|----|---------|---------|----|---------|")
                lines.append(
                    f"| {_short_list(best.kpis)} | {best.n_trades} | "
                    f"{best.hit_rate:.0f}% | {best.avg_return:+.2f}% | {best.atr_hit_pct:.1f}% | "
                    f"{best.profit_factor:.1f} | {best.avg_hold:.0f} |"
                )
                lines.append("")

            if cur:
                lines.append(f"**Current:**\n")
                lines.append(f"| KPIs | n | HR | Avg Ret | ATR Hit |")
                lines.append(f"|------|---|----|---------|---------| ")
                lines.append(
                    f"| {_short_list(cur.kpis)} | {cur.n_trades} | "
                    f"{cur.hit_rate:.0f}% | {cur.avg_return:+.2f}% | {cur.atr_hit_pct:.1f}% |"
                )
                lines.append("")

            # Top 5 balanced
            lines.append("**Top 5 (balanced):**\n")
            lines.append(f"| # | KPIs | n | HR | Avg Ret | ATR | PF |")
            lines.append(f"|---|------|---|----|---------|---------|----|")
            top5 = sorted(results, key=lambda r: (r.hit_rate / 100) * r.avg_return - 1.5 * r.atr_hit_pct, reverse=True)[:5]
            for i, r in enumerate(top5):
                lines.append(
                    f"| {i + 1} | {_short_list(r.kpis)} | {r.n_trades} | "
                    f"{r.hit_rate:.0f}% | {r.avg_return:+.2f}% | {r.atr_hit_pct:.1f}% | {r.profit_factor:.1f} |"
                )
            lines.append("")

    lines.append("## Key Findings\n")
    lines.append("- With higher minimum trade thresholds, combo statistics are more reliable")
    lines.append("- Three objectives allow choosing based on your risk preference")
    lines.append("- Walk-forward testing recommended before production deployment\n")

    report_path = out_dir / "phase11_entry_combo_report.md"
    report_path.write_text("\n".join(lines))
    print(f"  Saved {report_path.name}")


# ── Main ─────────────────────────────────────────────────────────────────

def _pick_best(results: List[FullResult], objective: str) -> Optional[FullResult]:
    if not results:
        return None
    if objective == "return":
        return max(results, key=lambda r: r.avg_return - 2 * r.atr_hit_pct)
    elif objective == "hitrate":
        return max(results, key=lambda r: r.hit_rate - r.atr_hit_pct)
    else:  # balanced
        return max(results, key=lambda r: (r.hit_rate / 100) * r.avg_return - 1.5 * r.atr_hit_pct)


def main():
    t0 = time.time()
    out_root = output_dir_for("all", "phase11")
    out_root.mkdir(parents=True, exist_ok=True)

    OBJECTIVES = ["return", "hitrate", "balanced"]
    OBJ_LABELS = {"return": "Best Return", "hitrate": "Best Hit Rate", "balanced": "Balanced"}

    all_results: Dict[str, Dict[int, List[FullResult]]] = {}
    all_current: Dict[str, Dict[int, Optional[FullResult]]] = {}

    for tf_key, tf_cfg in TIMEFRAME_CONFIGS.items():
        print(f"\n{'=' * 60}")
        print(f"  TIMEFRAME: {tf_key}")
        print(f"{'=' * 60}")

        all_data = load_data(ENRICHED_DIR, tf_key)
        print(f"  Loaded {len(all_data)} stocks")

        params = V3_PARAMS.get(tf_key, V3_PARAMS["1D"])
        T, M, K = params["T"], params["M"], params["K"]
        horizon = tf_cfg.default_horizon
        min_trades = MIN_TRADES_BY_TF.get(tf_key, 50)

        tf_out = output_dir_for(tf_key, "phase11")
        tf_out.mkdir(parents=True, exist_ok=True)

        print(f"  Precomputing screen arrays (horizon={horizon}, M={M})...")
        print(f"  Min trades threshold: {min_trades}")
        sa = precompute_screen_arrays(all_data, ALL_KPIS, horizon, M)
        print(f"  Screen arrays: {sa.n_stocks} stocks, {len(sa.close)} total bars")

        tf_results: Dict[int, List[FullResult]] = {}
        tf_current: Dict[int, Optional[FullResult]] = {}

        current_combos = COMBO_DEFINITIONS.get(tf_key, COMBO_DEFINITIONS["1W"])

        for k in [3, 4, 5]:
            combo_key = f"combo_{k}"
            cur_kpis = current_combos.get(combo_key, [])

            print(f"\n  C{k}: Screening all {k}-KPI combos (min {min_trades} trades)...")
            screened = screen_combos(sa, k, ALL_KPIS, K, min_trades=min_trades, top_n=TOP_N_SCREEN)
            print(f"    {len(screened)} unique candidates from 3-objective screening")

            if screened:
                top3 = sorted(screened, key=lambda s: s.composite_score, reverse=True)[:3]
                print(f"    Top 3 balanced scores:")
                for s in top3:
                    print(f"      {_short_list(s.kpis):50s} ret={s.avg_return:+.2f}% hr={s.hit_rate:.0f}% atr={s.atr_hit_pct:.1f}% n={s.n_trades}")

            print(f"    Running full v3 simulation on {len(screened)} candidates...")
            full_results: List[FullResult] = []
            for i, sr in enumerate(screened):
                fr = simulate_v3_for_combo(all_data, sr.kpis, T, M, K)
                if fr and fr.n_trades >= min_trades:
                    full_results.append(fr)
                if (i + 1) % 20 == 0:
                    print(f"      {i + 1}/{len(screened)} done")

            full_results.sort(
                key=lambda r: (r.hit_rate / 100) * r.avg_return - 1.5 * r.atr_hit_pct,
                reverse=True,
            )
            tf_results[k] = full_results

            if cur_kpis:
                print(f"    Simulating current combo: {_short_list(cur_kpis)}")
                cur_result = simulate_v3_for_combo(all_data, cur_kpis, T, M, K)
                tf_current[k] = cur_result
                if cur_result:
                    print(f"    Current: n={cur_result.n_trades}, HR={cur_result.hit_rate:.0f}%, Avg={cur_result.avg_return:+.2f}%, ATR={cur_result.atr_hit_pct:.1f}%")
            else:
                tf_current[k] = None

            if full_results:
                for obj in OBJECTIVES:
                    best = _pick_best(full_results, obj)
                    if best:
                        print(f"    {OBJ_LABELS[obj]:18s}: {_short_list(best.kpis):45s} n={best.n_trades:4d} HR={best.hit_rate:.0f}% Avg={best.avg_return:+.2f}% ATR={best.atr_hit_pct:.1f}%")

            chart_pareto(full_results, tf_current.get(k), k, tf_key, tf_out)
            chart_top_combos_table(full_results, tf_current.get(k), k, tf_key, tf_out)

        chart_kpi_importance(tf_results, tf_key, tf_out)

        best_by_obj: Dict[str, Dict[int, FullResult]] = {}
        for obj in OBJECTIVES:
            best_by_obj[obj] = {}
            for k, rs in tf_results.items():
                b = _pick_best(rs, obj)
                if b:
                    best_by_obj[obj][k] = b
        chart_comparison(best_by_obj, tf_current, tf_key, tf_out)

        all_results[tf_key] = tf_results
        all_current[tf_key] = tf_current

    generate_report(all_results, all_current, out_root)

    json_out: Dict[str, Any] = {}
    for tf, tf_res in all_results.items():
        json_out[tf] = {}
        for k, results in tf_res.items():
            if not results:
                continue
            for obj in OBJECTIVES:
                best = _pick_best(results, obj)
                if best:
                    json_out[tf][f"C{k}_{obj}"] = {
                        "kpis": best.kpis,
                        "n_trades": best.n_trades,
                        "hit_rate": round(best.hit_rate, 1),
                        "avg_return": round(best.avg_return, 2),
                        "atr_hit_pct": round(best.atr_hit_pct, 1),
                        "profit_factor": round(best.profit_factor, 1),
                        "worst_trade": round(best.worst_trade, 1),
                        "avg_hold": round(best.avg_hold, 0),
                    }
        cur_dict = all_current.get(tf, {})
        for k, cur in cur_dict.items():
            if cur:
                json_out[tf][f"C{k}_current"] = {
                    "kpis": cur.kpis,
                    "n_trades": cur.n_trades,
                    "hit_rate": round(cur.hit_rate, 1),
                    "avg_return": round(cur.avg_return, 2),
                    "atr_hit_pct": round(cur.atr_hit_pct, 1),
                }

    json_path = out_root / "phase11_results.json"
    json_path.write_text(json.dumps(json_out, indent=2, default=str))
    print(f"  Saved {json_path.name}")

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.0f}s")


if __name__ == "__main__":
    main()

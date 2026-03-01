"""
Phase 11 v4 — Entry Combo Optimization with Volume Filter + Tiered Strategy

Changes vs v3:
  - Tiered scoring per combo level:
      C3 = "workhorse" (rewards trade volume)
      C4 = balanced
      C5 = "golden combo" (rewards quality/hit rate)
  - Volume confirmation filter: optionally require Vol > MA20 at entry
  - "Volume + MA20" added to KPI pool
  - Weekly included with min_trades=5
  - All three timeframes tested
"""

from __future__ import annotations

import heapq
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
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
from tf_config import ENRICHED_DIR, TFConfig, TIMEFRAME_CONFIGS, output_dir_for
from phase8_exit_by_sector import load_data, IS_FRACTION, COMBO_DEFINITIONS

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

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
TOP_N_SCREEN = 60
TOP_N_FINAL = 10

MIN_TRADES_BY_TF = {"4H": 30, "1D": 30, "1W": 5}

EXCLUDED_KPIS = {
    "Nadaraya-Watson Envelop (Repainting)",
}

ALL_KPIS: List[str] = []
_seen: set = set()
for _k in list(KPI_TREND_ORDER) + list(KPI_BREAKOUT_ORDER) + [
    "GK Trend Ribbon", "Impulse Trend", "Volume + MA20",
    "SuperTrend", "UT Bot Alert", "Breakout Targets",
]:
    if _k not in _seen and _k not in EXCLUDED_KPIS:
        ALL_KPIS.append(_k)
        _seen.add(_k)

KPI_SHORT: Dict[str, str] = {
    "Nadaraya-Watson Smoother": "NW Smooth",
    "Nadaraya-Watson Envelop (MAE)": "NWE MAE",
    "Nadaraya-Watson Envelop (STD)": "NWE STD",
    "BB 30": "BB 30",
    "cRSI": "cRSI",
    "SR Breaks": "SR Brk",
    "Stoch_MTM": "Stoch",
    "CM_P-SAR": "P-SAR",
    "MA Ribbon": "MA Rib",
    "Madrid Ribbon": "Madrid",
    "Donchian Ribbon": "Donchian",
    "CM_Ult_MacD_MFT": "MACD",
    "GK Trend Ribbon": "GK Trend",
    "Impulse Trend": "Impulse",
    "SQZMOM_LB": "SQZ Mom",
    "Ichimoku": "Ichimoku",
    "ADX & DI": "ADX",
    "SuperTrend": "SuperTr",
    "UT Bot Alert": "UT Bot",
    "Mansfield RS": "Mansf",
    "DEMA": "DEMA",
    "GMMA": "GMMA",
    "WT_LB": "WT",
    "OBVOSC_LB": "OBV Osc",
    "TuTCI": "TuTCI",
    "RSI Strength & Consolidation Zones (Zeiierman)": "Zeiier",
    "Volume + MA20": "Vol>MA",
    "Breakout Targets": "BrkTgt",
}


def _short(kpi: str) -> str:
    return KPI_SHORT.get(kpi, kpi[:10])


def _short_list(kpis: List[str], sep: str = " + ") -> str:
    return sep.join(_short(k) for k in kpis) if kpis else "—"


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, pc = df["High"], df["Low"], df["Close"].shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()


# ── Tiered scoring ───────────────────────────────────────────────────────

def score_c3(avg_ret: float, hr: float, atr: float, n: int) -> float:
    """C3 = workhorse: rewards trade volume and decent returns."""
    return avg_ret * np.log1p(n) - atr


def score_c4(avg_ret: float, hr: float, atr: float, n: int) -> float:
    """C4 = balanced: weights hit rate and return equally."""
    return (hr / 100) * avg_ret - 1.5 * atr


def score_c5(avg_ret: float, hr: float, atr: float, n: int) -> float:
    """C5 = golden combo: heavily rewards quality and safety."""
    return (hr / 100) ** 2 * avg_ret - 3.0 * atr


TIER_SCORES = {3: score_c3, 4: score_c4, 5: score_c5}
TIER_LABELS = {3: "Workhorse", 4: "Balanced", 5: "Golden"}


# ── Stage 1: Fast screening ─────────────────────────────────────────────

@dataclass
class ScreenArrays:
    close: np.ndarray
    low_min_forward: np.ndarray
    atr: np.ndarray
    fwd_return: np.ndarray
    valid: np.ndarray
    bulls: Dict[str, np.ndarray]
    vol_above_ma: np.ndarray
    n_stocks: int
    kpi_coverage: Dict[str, float]


def precompute_screen_arrays(
    all_data: Dict[str, pd.DataFrame],
    kpi_names: List[str],
    horizon: int,
    M: int,
) -> ScreenArrays:
    all_close, all_low_min, all_atr, all_fwd, all_vol = [], [], [], [], []
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

        if "Vol_gt_MA20" in df.columns:
            vol_flag = df["Vol_gt_MA20"].iloc[split_idx:].fillna(False).astype(bool).to_numpy()
        elif "Volume" in df.columns and "Vol_MA20" in df.columns:
            v = df["Volume"].iloc[split_idx:].to_numpy(dtype=float)
            ma = df["Vol_MA20"].iloc[split_idx:].to_numpy(dtype=float)
            vol_flag = v > ma
        else:
            vol_flag = np.ones(n, dtype=bool)

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
        all_vol.append(vol_flag)
        n_stocks += 1

    if not all_close:
        return ScreenArrays(
            np.array([]), np.array([]), np.array([]), np.array([]),
            np.array([], dtype=bool), {}, np.array([], dtype=bool), 0, {},
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
        vol_above_ma=np.concatenate(all_vol),
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
    score: float
    vol_filtered: bool


def screen_combos(
    sa: ScreenArrays,
    k: int,
    kpi_names: List[str],
    K_atr: float,
    score_fn,
    min_trades: int,
    use_vol_filter: bool = False,
    top_n: int = TOP_N_SCREEN,
) -> List[ScreenResult]:
    available = [kpi for kpi in kpi_names if sa.kpi_coverage.get(kpi, 0) >= MIN_KPI_COVERAGE]
    if len(available) < k:
        return []

    hard_stop = sa.close - K_atr * sa.atr
    atr_would_hit = sa.low_min_forward < hard_stop

    heap: List[Tuple[float, int, Tuple[str, ...], int, float, float, float]] = []
    ctr = 0

    for combo in combinations(available, k):
        combined = sa.bulls[combo[0]].copy()
        for kpi in combo[1:]:
            combined &= sa.bulls[kpi]
        combined &= sa.valid

        if use_vol_filter:
            combined &= sa.vol_above_ma

        n = int(combined.sum())
        if n < min_trades:
            continue

        rets = sa.fwd_return[combined]
        avg_ret = float(np.mean(rets))
        hr = float(np.sum(rets > 0) / n) * 100
        atr_hits = float(np.sum(atr_would_hit[combined]) / n) * 100

        sc = score_fn(avg_ret, hr, atr_hits, n)

        if len(heap) < top_n:
            heapq.heappush(heap, (sc, ctr, combo, n, avg_ret, hr, atr_hits))
        elif sc > heap[0][0]:
            heapq.heapreplace(heap, (sc, ctr, combo, n, avg_ret, hr, atr_hits))
        ctr += 1

    heap.sort(reverse=True)

    return [
        ScreenResult(
            kpis=list(combo), n_trades=n_t,
            avg_return=ar, hit_rate=hr_v, atr_hit_pct=ah,
            score=sc, vol_filtered=use_vol_filter,
        )
        for sc, _, combo, n_t, ar, hr_v, ah in heap
    ]


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
    vol_filtered: bool
    total_return: float


def simulate_v3_for_combo(
    all_data: Dict[str, pd.DataFrame],
    combo_kpis: List[str],
    T: int, M: int, K: float,
    require_vol: bool = False,
    min_n: int = 5,
) -> Optional[FullResult]:
    all_rets: List[float] = []
    exit_reasons: Dict[str, int] = {"atr": 0, "lenient": 0, "strict": 0, "maxhold": 0}
    all_holds: List[int] = []

    for sym, df in all_data.items():
        if df.empty:
            continue
        state_map = compute_kpi_state_map(df)
        avail = [kk for kk in combo_kpis if kk in state_map]
        if len(avail) < len(combo_kpis):
            continue

        all_bull = pd.Series(True, index=df.index)
        for kpi in avail:
            all_bull = all_bull & (state_map[kpi] == STATE_BULL)
        signal = all_bull.astype(bool)

        if require_vol:
            if "Vol_gt_MA20" in df.columns:
                vol_ok = df["Vol_gt_MA20"].fillna(False).astype(bool)
            elif "Volume" in df.columns and "Vol_MA20" in df.columns:
                vol_ok = df["Volume"] > df["Vol_MA20"]
            else:
                vol_ok = pd.Series(True, index=df.index)
            signal = signal & vol_ok

        if signal.sum() == 0:
            continue

        split_idx = int(len(df) * IS_FRACTION)
        test_start = df.index[split_idx]

        close = df["Close"].to_numpy(dtype=float)
        atr_arr = compute_atr(df, ATR_PERIOD).to_numpy(dtype=float)
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
            hard_stop = ep - K * atr_arr[entry_idx] if atr_arr[entry_idx] > 0 else -np.inf

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
    if n < min_n:
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
    total_ret = float(np.sum(all_rets))

    return FullResult(
        kpis=combo_kpis, n_trades=n, hit_rate=hr, avg_return=avg,
        median_return=med, worst_trade=worst, profit_factor=pf,
        atr_hit_pct=atr_pct, pct_strict=strict_pct, pct_maxhold=mh_pct,
        avg_hold=avg_hold, vol_filtered=require_vol, total_return=total_ret,
    )


# ── Charts ───────────────────────────────────────────────────────────────

def chart_pareto(
    results: List[FullResult],
    current: Optional[FullResult],
    k: int, tf: str, tier: str,
    out_dir: Path,
):
    if not results:
        return

    fig, axes = plt.subplots(1, 2, figsize=(22, 10))

    ax = axes[0]
    hrs = [r.hit_rate for r in results]
    rets = [r.avg_return for r in results]
    sizes = [max(30, min(300, r.n_trades * 1.2)) for r in results]
    colors_ret = rets

    sc = ax.scatter(hrs, rets, s=sizes, c=colors_ret, cmap="RdYlGn",
                    edgecolors="white", linewidth=0.8, alpha=0.85, zorder=5)
    for i, r in enumerate(results[:6]):
        vl = " +vol" if r.vol_filtered else ""
        ax.annotate(
            f"{_short_list(r.kpis, ', ')}{vl}\nn={r.n_trades}",
            (r.hit_rate, r.avg_return),
            fontsize=6.5, color="white", alpha=0.9,
            xytext=(8, 4), textcoords="offset points",
            bbox=dict(boxstyle="round,pad=0.2", facecolor="#333", alpha=0.7),
        )
    if current:
        ax.scatter([current.hit_rate], [current.avg_return], s=250,
                   marker="*", c="cyan", edgecolors="white", linewidth=1.5,
                   zorder=10, label=f"Current (n={current.n_trades})")
        ax.legend(loc="lower right", fontsize=8)
    ax.set_xlabel("Hit Rate (%)")
    ax.set_ylabel("Avg Return (%)")
    ax.set_title(f"Hit Rate vs Return (bubble = trades)")
    ax.grid(True, alpha=0.2)

    # Right panel: n_trades vs total return
    ax2 = axes[1]
    tots = [r.total_return for r in results]
    ns = [r.n_trades for r in results]
    sc2 = ax2.scatter(ns, tots, s=sizes, c=[r.hit_rate for r in results],
                      cmap="RdYlGn", edgecolors="white", linewidth=0.8, alpha=0.85, zorder=5)
    for i, r in enumerate(results[:6]):
        vl = " +vol" if r.vol_filtered else ""
        ax2.annotate(
            f"{_short_list(r.kpis, ', ')}{vl}",
            (r.n_trades, r.total_return),
            fontsize=6.5, color="white", alpha=0.9,
            xytext=(8, 4), textcoords="offset points",
            bbox=dict(boxstyle="round,pad=0.2", facecolor="#333", alpha=0.7),
        )
    if current:
        ax2.scatter([current.n_trades], [current.total_return], s=250,
                    marker="*", c="cyan", edgecolors="white", linewidth=1.5, zorder=10)
    ax2.set_xlabel("Number of Trades")
    ax2.set_ylabel("Total Cumulative Return (%)")
    ax2.set_title(f"Trade Volume vs Total P&L (color = HR%)")
    ax2.grid(True, alpha=0.2)
    fig.colorbar(sc2, ax=ax2, label="Hit Rate (%)", shrink=0.8)

    min_t = MIN_TRADES_BY_TF.get(tf, 30)
    fig.suptitle(f"{tf} C{k} [{tier}] — Entry Combo Frontier (min {min_t} trades)",
                 fontsize=14, fontweight="bold")
    plt.tight_layout()
    fname = out_dir / f"pareto_C{k}.png"
    fig.savefig(fname)
    plt.close(fig)
    print(f"    Saved {fname.name}")


def chart_vol_impact(
    no_vol: List[FullResult],
    with_vol: List[FullResult],
    tf: str,
    out_dir: Path,
):
    """Compare top combos with and without volume filter."""
    if not no_vol and not with_vol:
        return

    def top5(lst):
        return sorted(lst, key=lambda r: (r.hit_rate / 100) * r.avg_return, reverse=True)[:5]

    nv = top5(no_vol)
    wv = top5(with_vol)

    fig, ax = plt.subplots(figsize=(16, 10))
    ax.axis("off")

    headers = ["Filter", "KPIs", "n", "HR%", "Avg%", "TotRet%", "ATR%", "PF", "AvgH"]
    cell_text = []
    cell_colors = []

    for r in nv:
        row = [
            "No vol",
            _short_list(r.kpis),
            str(r.n_trades),
            f"{r.hit_rate:.0f}",
            f"{r.avg_return:+.2f}",
            f"{r.total_return:+.0f}",
            f"{r.atr_hit_pct:.1f}",
            f"{r.profit_factor:.1f}",
            f"{r.avg_hold:.0f}",
        ]
        cell_text.append(row)
        cell_colors.append(["#1e1e1e"] * len(headers))

    for r in wv:
        row = [
            "+Vol>MA",
            _short_list(r.kpis),
            str(r.n_trades),
            f"{r.hit_rate:.0f}",
            f"{r.avg_return:+.2f}",
            f"{r.total_return:+.0f}",
            f"{r.atr_hit_pct:.1f}",
            f"{r.profit_factor:.1f}",
            f"{r.avg_hold:.0f}",
        ]
        cell_text.append(row)
        cell_colors.append(["#1a2a1a"] * len(headers))

    if not cell_text:
        plt.close(fig)
        return

    table = ax.table(
        cellText=cell_text,
        colLabels=headers,
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

    ax.set_title(f"{tf} — Volume Filter Impact on Top Combos (all C-levels merged)",
                 fontsize=13, fontweight="bold", pad=20)
    plt.tight_layout()
    fname = out_dir / f"volume_impact.png"
    fig.savefig(fname)
    plt.close(fig)
    print(f"    Saved {fname.name}")


def chart_tier_summary(
    tf_results: Dict[int, List[FullResult]],
    tf_current: Dict[int, Optional[FullResult]],
    tf: str,
    out_dir: Path,
):
    """4-panel comparison: current vs optimised per tier."""
    ks = [3, 4, 5]
    fig, axes = plt.subplots(2, 2, figsize=(20, 14))

    metrics = [
        ("avg_return", "Avg Return (%)", axes[0, 0]),
        ("hit_rate", "Hit Rate (%)", axes[0, 1]),
        ("n_trades", "Trade Count", axes[1, 0]),
        ("total_return", "Total Cumul. Return (%)", axes[1, 1]),
    ]

    for attr, ylabel, ax in metrics:
        x = np.arange(len(ks))
        w = 0.35

        cur_vals = []
        opt_vals = []
        for k in ks:
            cur = tf_current.get(k)
            cur_vals.append(getattr(cur, attr, 0) if cur else 0)
            res = tf_results.get(k, [])
            opt_vals.append(getattr(res[0], attr, 0) if res else 0)

        ax.bar(x - w / 2, cur_vals, w, color="#78909c", label="Current", edgecolor="white", linewidth=0.5)
        ax.bar(x + w / 2, opt_vals, w, color="#66bb6a", label="Optimized", edgecolor="white", linewidth=0.5)

        for i, (cv, nv) in enumerate(zip(cur_vals, opt_vals)):
            ax.text(x[i] - w / 2, cv + 0.3, f"{cv:.1f}", ha="center", fontsize=8, fontweight="bold")
            ax.text(x[i] + w / 2, nv + 0.3, f"{nv:.1f}", ha="center", fontsize=8, fontweight="bold", color="#66bb6a")

        ax.set_xticks(x)
        ax.set_xticklabels([f"C{k} ({TIER_LABELS[k]})" for k in ks])
        ax.set_ylabel(ylabel)
        ax.legend(fontsize=9)
        ax.grid(True, alpha=0.2, axis="y")

    fig.suptitle(f"{tf} — Tiered Strategy: Current vs Optimized", fontsize=14, fontweight="bold")
    plt.tight_layout()
    fname = out_dir / f"tier_summary.png"
    fig.savefig(fname)
    plt.close(fig)
    print(f"    Saved {fname.name}")


def chart_top_table(
    results: List[FullResult],
    current: Optional[FullResult],
    k: int, tf: str, tier: str,
    out_dir: Path,
):
    if not results:
        return

    show = results[:TOP_N_FINAL]
    if current:
        show = [current] + show

    fig, ax = plt.subplots(figsize=(20, max(4, len(show) * 0.7 + 2)))
    ax.axis("off")

    headers = ["Rank", "KPIs", "Vol", "n", "HR%", "Avg%", "TotRet%", "Med%", "Worst%", "PF", "ATR%", "AvgH"]

    cell_text = []
    cell_colors = []
    for i, r in enumerate(show):
        is_current = current and r is current
        rank = "CUR" if is_current else str(i)
        row = [
            rank,
            _short_list(r.kpis),
            "Y" if r.vol_filtered else "N",
            str(r.n_trades),
            f"{r.hit_rate:.0f}",
            f"{r.avg_return:+.2f}",
            f"{r.total_return:+.0f}",
            f"{r.median_return:+.2f}",
            f"{r.worst_trade:+.1f}",
            f"{r.profit_factor:.1f}",
            f"{r.atr_hit_pct:.1f}",
            f"{r.avg_hold:.0f}",
        ]
        cell_text.append(row)
        bg = "#1a3a1a" if is_current else ("#1e1e1e" if i % 2 == 0 else "#252525")
        cell_colors.append([bg] * len(headers))

    table = ax.table(
        cellText=cell_text, colLabels=headers, loc="center", cellLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1.0, 1.5)

    for (row, col), cell in table.get_celld().items():
        if row == 0:
            cell.set_facecolor("#333")
            cell.set_text_props(fontweight="bold", color="white")
        else:
            cell.set_facecolor(cell_colors[row - 1][col])
            cell.set_text_props(color="white")
        cell.set_edgecolor("#444")

    ax.set_title(f"{tf} C{k} [{tier}] — Top Entry Combos (Exit Flow v3)",
                 fontsize=13, fontweight="bold", pad=20)
    plt.tight_layout()
    fname = out_dir / f"top_combos_C{k}.png"
    fig.savefig(fname)
    plt.close(fig)
    print(f"    Saved {fname.name}")


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    t0 = time.time()
    out_root = output_dir_for("all", "phase11v4")
    out_root.mkdir(parents=True, exist_ok=True)

    all_json: Dict[str, Any] = {}

    for tf_key, tf_cfg in TIMEFRAME_CONFIGS.items():
        print(f"\n{'=' * 60}")
        print(f"  TIMEFRAME: {tf_key}")
        print(f"{'=' * 60}")

        all_data = load_data(ENRICHED_DIR, tf_key)
        print(f"  Loaded {len(all_data)} stocks")

        params = V3_PARAMS.get(tf_key, V3_PARAMS["1D"])
        T, M, K = params["T"], params["M"], params["K"]
        horizon = tf_cfg.default_horizon
        min_trades = MIN_TRADES_BY_TF.get(tf_key, 30)

        tf_out = output_dir_for(tf_key, "phase11v4")
        tf_out.mkdir(parents=True, exist_ok=True)

        print(f"  Precomputing screen arrays (horizon={horizon}, M={M})...")
        print(f"  Min trades: {min_trades}")
        sa = precompute_screen_arrays(all_data, ALL_KPIS, horizon, M)
        print(f"  Screen arrays: {sa.n_stocks} stocks, {len(sa.close)} total bars")
        vol_avail = sa.vol_above_ma.sum()
        print(f"  Volume filter: {vol_avail}/{len(sa.vol_above_ma)} bars with Vol > MA20 ({100*vol_avail/max(1,len(sa.vol_above_ma)):.0f}%)")

        tf_results: Dict[int, List[FullResult]] = {}
        tf_current: Dict[int, Optional[FullResult]] = {}
        all_no_vol: List[FullResult] = []
        all_with_vol: List[FullResult] = []

        current_combos = COMBO_DEFINITIONS.get(tf_key, COMBO_DEFINITIONS["1W"])

        for k in [3, 4, 5]:
            combo_key = f"combo_{k}"
            cur_kpis = current_combos.get(combo_key, [])
            score_fn = TIER_SCORES[k]
            tier = TIER_LABELS[k]

            # Screen without and with volume filter
            print(f"\n  C{k} [{tier}]: Screening (min {min_trades} trades)...")
            screened_nv = screen_combos(sa, k, ALL_KPIS, K, score_fn, min_trades,
                                        use_vol_filter=False, top_n=TOP_N_SCREEN)
            screened_wv = screen_combos(sa, k, ALL_KPIS, K, score_fn, min_trades,
                                        use_vol_filter=True, top_n=TOP_N_SCREEN)
            print(f"    {len(screened_nv)} without vol, {len(screened_wv)} with vol filter")

            # Merge unique for simulation
            seen_kpi_sets: set = set()
            to_simulate: List[Tuple[List[str], bool]] = []
            for sr in screened_nv:
                key = (tuple(sorted(sr.kpis)), False)
                if key not in seen_kpi_sets:
                    seen_kpi_sets.add(key)
                    to_simulate.append((sr.kpis, False))
            for sr in screened_wv:
                key = (tuple(sorted(sr.kpis)), True)
                if key not in seen_kpi_sets:
                    seen_kpi_sets.add(key)
                    to_simulate.append((sr.kpis, True))

            print(f"    Running full v3 simulation on {len(to_simulate)} unique combos...")
            full_results: List[FullResult] = []
            for i, (kpis, vol) in enumerate(to_simulate):
                fr = simulate_v3_for_combo(all_data, kpis, T, M, K,
                                           require_vol=vol, min_n=max(3, min_trades // 2))
                if fr and fr.n_trades >= min_trades:
                    full_results.append(fr)
                    if vol:
                        all_with_vol.append(fr)
                    else:
                        all_no_vol.append(fr)
                if (i + 1) % 20 == 0:
                    print(f"      {i + 1}/{len(to_simulate)} done")

            full_results.sort(key=lambda r: score_fn(r.avg_return, r.hit_rate, r.atr_hit_pct, r.n_trades), reverse=True)
            tf_results[k] = full_results

            if cur_kpis:
                print(f"    Simulating current: {_short_list(cur_kpis)}")
                cur = simulate_v3_for_combo(all_data, cur_kpis, T, M, K, require_vol=False, min_n=3)
                tf_current[k] = cur
                if cur:
                    print(f"    Current: n={cur.n_trades} HR={cur.hit_rate:.0f}% Avg={cur.avg_return:+.2f}% TotRet={cur.total_return:+.0f}%")
                # Also with vol
                cur_v = simulate_v3_for_combo(all_data, cur_kpis, T, M, K, require_vol=True, min_n=3)
                if cur_v:
                    print(f"    Cur+Vol: n={cur_v.n_trades} HR={cur_v.hit_rate:.0f}% Avg={cur_v.avg_return:+.2f}% TotRet={cur_v.total_return:+.0f}%")
            else:
                tf_current[k] = None

            if full_results:
                best = full_results[0]
                vl = " +vol" if best.vol_filtered else ""
                print(f"    BEST: {_short_list(best.kpis)}{vl}")
                print(f"      n={best.n_trades} HR={best.hit_rate:.0f}% Avg={best.avg_return:+.2f}% TotRet={best.total_return:+.0f}%")

            chart_pareto(full_results, tf_current.get(k), k, tf_key, tier, tf_out)
            chart_top_table(full_results, tf_current.get(k), k, tf_key, tier, tf_out)

        chart_tier_summary(tf_results, tf_current, tf_key, tf_out)
        chart_vol_impact(all_no_vol, all_with_vol, tf_key, tf_out)

        # JSON output
        tf_json: Dict[str, Any] = {}
        for k in [3, 4, 5]:
            results = tf_results.get(k, [])
            if results:
                best = results[0]
                tf_json[f"C{k}_{TIER_LABELS[k]}"] = {
                    "kpis": best.kpis,
                    "vol_filter": best.vol_filtered,
                    "n_trades": best.n_trades,
                    "hit_rate": round(best.hit_rate, 1),
                    "avg_return": round(best.avg_return, 2),
                    "total_return": round(best.total_return, 0),
                    "atr_hit_pct": round(best.atr_hit_pct, 1),
                    "profit_factor": round(best.profit_factor, 1),
                    "worst_trade": round(best.worst_trade, 1),
                    "avg_hold": round(best.avg_hold, 0),
                }
            cur = tf_current.get(k)
            if cur:
                tf_json[f"C{k}_current"] = {
                    "kpis": cur.kpis,
                    "n_trades": cur.n_trades,
                    "hit_rate": round(cur.hit_rate, 1),
                    "avg_return": round(cur.avg_return, 2),
                    "total_return": round(cur.total_return, 0),
                    "atr_hit_pct": round(cur.atr_hit_pct, 1),
                }
        all_json[tf_key] = tf_json

    json_path = out_root / "phase11v4_results.json"
    json_path.write_text(json.dumps(all_json, indent=2, default=str))
    print(f"\n  Saved {json_path.name}")
    print(f"\nDone in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()

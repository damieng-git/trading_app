"""
Phase 10v3 — Exit Flow Grid Search

Systematically sweeps exit flow parameters to find the optimal configuration
per timeframe (4H, 1D, 1W) and combo level (C3, C4, C5).

Parameters swept:
  T  = lenient-to-strict transition bar
  M  = max hold bar (forced exit)
  K  = ATR safety-net multiplier
  For C4/C5: intermediate threshold stage

The 2-stage model:
  Stage A  (0 → T bars): Exit only if ALL N KPIs flip non-bull  + ATR safety
  Stage B  (T → M bars): Exit if >=2 KPIs flip non-bull         + ATR safety
  Max Hold (M bars):     Forced exit

For C4/C5 3-stage variant:
  Stage A  (0  → T1): ALL N KPIs flip       + ATR safety
  Stage B  (T1 → T2): >=threshold KPIs flip + ATR safety
  Stage C  (T2 → M):  >=2 KPIs flip         + ATR safety
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from trading_dashboard.kpis.catalog import compute_kpi_state_map
from trading_dashboard.kpis.rules import STATE_BULL
from tf_config import ENRICHED_DIR, TFConfig, TIMEFRAME_CONFIGS, output_dir_for
from phase8_exit_by_sector import load_data, COMBO_DEFINITIONS, IS_FRACTION

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.colors import Normalize
from matplotlib.cm import ScalarMappable

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

ATR_PERIOD = 14

GRID_PARAMS = {
    "1D": {
        "T_values": [2, 3, 4, 5, 6, 7, 8, 9, 10, 12],
        "M_values": [10, 15, 20, 25, 30, 35, 40],
        "K_values": [2.0, 2.5, 3.0, 3.5],
    },
    "1W": {
        "T_values": [2, 3, 4, 5, 6, 7, 8],
        "M_values": [6, 8, 10, 12, 16, 20],
        "K_values": [2.0, 2.5, 3.0, 3.5],
    },
    "4H": {
        "T_values": [2, 4, 6, 8, 10, 12, 16],
        "M_values": [12, 16, 20, 24, 30, 36, 48],
        "K_values": [2.0, 2.5, 3.0, 3.5],
    },
}


# ── ATR ──────────────────────────────────────────────────────────────────

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, pc = df["High"], df["Low"], df["Close"].shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()


# ── Grid cell result ─────────────────────────────────────────────────────

@dataclass
class GridResult:
    T: int
    M: int
    K: float
    n_trades: int
    hit_rate: float
    avg_return: float
    median_return: float
    worst_trade: float
    profit_factor: float
    pct_atr: float
    pct_lenient: float
    pct_strict: float
    pct_maxhold: float
    avg_ret_atr: float
    avg_ret_lenient: float
    avg_ret_strict: float
    avg_ret_maxhold: float


# ── 2-stage simulation ───────────────────────────────────────────────────

def simulate_2stage(
    df: pd.DataFrame,
    signal: pd.Series,
    state_map: Dict[str, pd.Series],
    combo_kpis: List[str],
    T: int,
    M: int,
    K: float,
    test_start: pd.Timestamp,
) -> Dict[str, List[float]]:
    """
    Returns dict of exit_reason -> list of return percentages.
    """
    close = df["Close"]
    atr = compute_atr(df, ATR_PERIOD)
    n_kpis = len(combo_kpis)
    test_mask = df.index >= test_start
    sig_dates = signal[test_mask & signal].index

    exits: Dict[str, List[float]] = {
        "atr": [], "lenient": [], "strict": [], "maxhold": [],
    }

    i = 0
    while i < len(sig_dates):
        entry_idx = df.index.get_loc(sig_dates[i])
        ep = float(close.iloc[entry_idx])
        if ep <= 0:
            i += 1
            continue
        atr_val = float(atr.iloc[entry_idx])
        hard_stop = ep - K * atr_val if atr_val > 0 else -np.inf

        exit_idx = None
        reason = "maxhold"

        for j in range(entry_idx + 1, min(entry_idx + M + 1, len(df))):
            bars = j - entry_idx
            c = float(close.iloc[j])

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

        xp = float(close.iloc[exit_idx])
        ret = (xp - ep) / ep * 100
        exits[reason].append(ret)

        next_i = i + 1
        while next_i < len(sig_dates) and df.index.get_loc(sig_dates[next_i]) <= exit_idx:
            next_i += 1
        i = next_i

    return exits


# ── 3-stage simulation (C4/C5) ───────────────────────────────────────────

def simulate_3stage(
    df: pd.DataFrame,
    signal: pd.Series,
    state_map: Dict[str, pd.Series],
    combo_kpis: List[str],
    T1: int,
    T2: int,
    M: int,
    K: float,
    intermediate_thresh: int,
    test_start: pd.Timestamp,
) -> Dict[str, List[float]]:
    close = df["Close"]
    atr = compute_atr(df, ATR_PERIOD)
    n_kpis = len(combo_kpis)
    test_mask = df.index >= test_start
    sig_dates = signal[test_mask & signal].index

    exits: Dict[str, List[float]] = {
        "atr": [], "lenient": [], "intermediate": [], "strict": [], "maxhold": [],
    }

    i = 0
    while i < len(sig_dates):
        entry_idx = df.index.get_loc(sig_dates[i])
        ep = float(close.iloc[entry_idx])
        if ep <= 0:
            i += 1
            continue
        atr_val = float(atr.iloc[entry_idx])
        hard_stop = ep - K * atr_val if atr_val > 0 else -np.inf

        exit_idx = None
        reason = "maxhold"

        for j in range(entry_idx + 1, min(entry_idx + M + 1, len(df))):
            bars = j - entry_idx
            c = float(close.iloc[j])

            if c < hard_stop:
                exit_idx, reason = j, "atr"
                break

            n_nb = sum(
                1 for kpi in combo_kpis
                if kpi in state_map and j < len(state_map[kpi])
                and int(state_map[kpi].iloc[j]) != STATE_BULL
            )

            if bars <= T1:
                if n_nb >= n_kpis:
                    exit_idx, reason = j, "lenient"
                    break
            elif bars <= T2:
                if n_nb >= intermediate_thresh:
                    exit_idx, reason = j, "intermediate"
                    break
            else:
                if n_nb >= 2:
                    exit_idx, reason = j, "strict"
                    break

        if exit_idx is None:
            exit_idx = min(entry_idx + M, len(df) - 1)

        xp = float(close.iloc[exit_idx])
        ret = (xp - ep) / ep * 100
        exits[reason].append(ret)

        next_i = i + 1
        while next_i < len(sig_dates) and df.index.get_loc(sig_dates[next_i]) <= exit_idx:
            next_i += 1
        i = next_i

    return exits


# ── Aggregate results across stocks ──────────────────────────────────────

def _stats(rets: List[float]) -> Tuple[float, float, float]:
    if not rets:
        return 0.0, 0.0, 0.0
    n = len(rets)
    hr = sum(1 for r in rets if r > 0) / n * 100
    avg = float(np.mean(rets))
    return n, hr, avg


def run_grid_search_2stage(
    all_data: Dict[str, pd.DataFrame],
    combo_kpis: List[str],
    T_values: List[int],
    M_values: List[int],
    K_values: List[float],
) -> List[GridResult]:
    results: List[GridResult] = []

    precomputed = {}
    for sym, df in all_data.items():
        if df.empty:
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
        precomputed[sym] = (df, signal, state_map, test_start)

    total_combos = len(T_values) * len(M_values) * len(K_values)
    done = 0

    for K in K_values:
        for T in T_values:
            for M in M_values:
                if T >= M:
                    continue
                agg = {"atr": [], "lenient": [], "strict": [], "maxhold": []}

                for sym, (df, signal, state_map, test_start) in precomputed.items():
                    exits = simulate_2stage(df, signal, state_map, combo_kpis, T, M, K, test_start)
                    for k, v in exits.items():
                        agg[k].extend(v)

                all_rets = []
                for v in agg.values():
                    all_rets.extend(v)

                n = len(all_rets)
                if n < 20:
                    continue

                hr = sum(1 for r in all_rets if r > 0) / n * 100
                avg = float(np.mean(all_rets))
                med = float(np.median(all_rets))
                worst = min(all_rets)
                wins = sum(r for r in all_rets if r > 0)
                losses = abs(sum(r for r in all_rets if r <= 0))
                pf = wins / losses if losses > 0 else 999.0

                def _pct_and_avg(lst):
                    p = len(lst) / n * 100 if n else 0
                    a = float(np.mean(lst)) if lst else 0
                    return p, a

                p_atr, a_atr = _pct_and_avg(agg["atr"])
                p_len, a_len = _pct_and_avg(agg["lenient"])
                p_str, a_str = _pct_and_avg(agg["strict"])
                p_mh, a_mh = _pct_and_avg(agg["maxhold"])

                results.append(GridResult(
                    T=T, M=M, K=K, n_trades=n,
                    hit_rate=hr, avg_return=avg, median_return=med,
                    worst_trade=worst, profit_factor=pf,
                    pct_atr=p_atr, pct_lenient=p_len, pct_strict=p_str, pct_maxhold=p_mh,
                    avg_ret_atr=a_atr, avg_ret_lenient=a_len, avg_ret_strict=a_str, avg_ret_maxhold=a_mh,
                ))

                done += 1

    return results


def run_grid_search_3stage(
    all_data: Dict[str, pd.DataFrame],
    combo_kpis: List[str],
    best_T: int,
    best_M: int,
    best_K: float,
) -> List[Dict]:
    """Test intermediate thresholds for C4/C5 at the optimal 2-stage params."""
    n_kpis = len(combo_kpis)
    if n_kpis < 4:
        return []

    precomputed = {}
    for sym, df in all_data.items():
        if df.empty:
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
        precomputed[sym] = (df, signal, state_map, test_start)

    possible_thresholds = list(range(3, n_kpis))

    results_3s = []

    results_3s.append({
        "type": "2-stage baseline",
        "T1": best_T, "T2": None, "M": best_M, "K": best_K,
        "intermediate_thresh": None,
    })

    for thresh in possible_thresholds:
        for T2 in range(best_T + 1, best_M):
            agg = {"atr": [], "lenient": [], "intermediate": [], "strict": [], "maxhold": []}
            for sym, (df, signal, state_map, test_start) in precomputed.items():
                exits = simulate_3stage(
                    df, signal, state_map, combo_kpis,
                    best_T, T2, best_M, best_K, thresh, test_start,
                )
                for k, v in exits.items():
                    agg[k].extend(v)

            all_rets = []
            for v in agg.values():
                all_rets.extend(v)
            n = len(all_rets)
            if n < 20:
                continue

            hr = sum(1 for r in all_rets if r > 0) / n * 100
            avg = float(np.mean(all_rets))

            results_3s.append({
                "type": "3-stage",
                "T1": best_T, "T2": T2, "M": best_M, "K": best_K,
                "intermediate_thresh": thresh,
                "n_trades": n, "hit_rate": hr, "avg_return": avg,
            })

    agg_2s = {"atr": [], "lenient": [], "strict": [], "maxhold": []}
    for sym, (df, signal, state_map, test_start) in precomputed.items():
        exits = simulate_2stage(df, signal, state_map, combo_kpis, best_T, best_M, best_K, test_start)
        for k, v in exits.items():
            agg_2s[k].extend(v)
    all_2s = []
    for v in agg_2s.values():
        all_2s.extend(v)
    n2 = len(all_2s)
    if n2 > 0:
        results_3s[0]["n_trades"] = n2
        results_3s[0]["hit_rate"] = sum(1 for r in all_2s if r > 0) / n2 * 100
        results_3s[0]["avg_return"] = float(np.mean(all_2s))

    return results_3s


# ── Charting ─────────────────────────────────────────────────────────────

def chart_heatmap(
    results: List[GridResult],
    best_K: float,
    tf: str,
    combo_label: str,
    out_dir: Path,
):
    """Heatmap: T vs M, color = avg return, for the best ATR K."""
    filtered = [r for r in results if r.K == best_K]
    if not filtered:
        return

    T_vals = sorted(set(r.T for r in filtered))
    M_vals = sorted(set(r.M for r in filtered))

    grid_return = np.full((len(M_vals), len(T_vals)), np.nan)
    grid_hr = np.full((len(M_vals), len(T_vals)), np.nan)
    grid_n = np.full((len(M_vals), len(T_vals)), np.nan)
    grid_mh_pct = np.full((len(M_vals), len(T_vals)), np.nan)

    t_idx = {t: i for i, t in enumerate(T_vals)}
    m_idx = {m: i for i, m in enumerate(M_vals)}

    for r in filtered:
        ti, mi = t_idx[r.T], m_idx[r.M]
        grid_return[mi, ti] = r.avg_return
        grid_hr[mi, ti] = r.hit_rate
        grid_n[mi, ti] = r.n_trades
        grid_mh_pct[mi, ti] = r.pct_maxhold

    fig, axes = plt.subplots(1, 3, figsize=(22, 7))

    datasets = [
        (grid_return, "Avg Return (%)", "RdYlGn", axes[0]),
        (grid_hr, "Hit Rate (%)", "RdYlGn", axes[1]),
        (grid_mh_pct, "% Max Hold Exits", "RdYlGn_r", axes[2]),
    ]

    best_r = max(filtered, key=lambda r: r.avg_return)

    for data, title, cmap, ax in datasets:
        im = ax.imshow(data, cmap=cmap, aspect="auto", origin="lower")
        ax.set_xticks(range(len(T_vals)))
        ax.set_xticklabels(T_vals)
        ax.set_yticks(range(len(M_vals)))
        ax.set_yticklabels(M_vals)
        ax.set_xlabel("Transition Bar (T)")
        ax.set_ylabel("Max Hold (M)")
        ax.set_title(title)
        fig.colorbar(im, ax=ax, shrink=0.8)

        for mi_i in range(len(M_vals)):
            for ti_i in range(len(T_vals)):
                val = data[mi_i, ti_i]
                if not np.isnan(val):
                    color = "black" if val > np.nanmedian(data) else "white"
                    ax.text(ti_i, mi_i, f"{val:.1f}", ha="center", va="center",
                            fontsize=7, color=color, fontweight="bold")

        if title == "Avg Return (%)":
            bti, bmi = t_idx[best_r.T], m_idx[best_r.M]
            ax.plot(bti, bmi, "s", markersize=18, markerfacecolor="none",
                    markeredgecolor="cyan", markeredgewidth=2.5)

    fig.suptitle(
        f"{tf} {combo_label} — 2-Stage Grid Search (ATR K={best_K})\n"
        f"Best: T={best_r.T}, M={best_r.M} → "
        f"HR={best_r.hit_rate:.0f}%, Avg={best_r.avg_return:+.2f}%, "
        f"n={best_r.n_trades}, MaxHold={best_r.pct_maxhold:.0f}%",
        fontsize=14, fontweight="bold", y=1.02,
    )

    plt.tight_layout()
    fname = out_dir / f"heatmap_{combo_label}_K{best_K}.png"
    fig.savefig(fname)
    plt.close(fig)
    print(f"    Saved {fname.name}")


def chart_atr_comparison(
    results: List[GridResult],
    tf: str,
    combo_label: str,
    out_dir: Path,
):
    """Bar chart comparing best (T, M) at each ATR K."""
    K_vals = sorted(set(r.K for r in results))
    best_per_k = {}
    for K in K_vals:
        filtered = [r for r in results if r.K == K]
        if filtered:
            best_per_k[K] = max(filtered, key=lambda r: r.avg_return)

    if not best_per_k:
        return

    fig, axes = plt.subplots(1, 3, figsize=(18, 6))

    ks = list(best_per_k.keys())
    hrs = [best_per_k[k].hit_rate for k in ks]
    avgs = [best_per_k[k].avg_return for k in ks]
    labels = [f"K={k}\nT={best_per_k[k].T}, M={best_per_k[k].M}" for k in ks]

    colors = ["#42a5f5", "#66bb6a", "#ffa726", "#ef5350"]

    axes[0].bar(range(len(ks)), avgs, color=colors[:len(ks)])
    axes[0].set_xticks(range(len(ks)))
    axes[0].set_xticklabels(labels, fontsize=8)
    axes[0].set_ylabel("Avg Return (%)")
    axes[0].set_title("Best Avg Return per ATR K")
    for i, v in enumerate(avgs):
        axes[0].text(i, v + 0.1, f"{v:+.2f}%", ha="center", fontsize=9, fontweight="bold")

    axes[1].bar(range(len(ks)), hrs, color=colors[:len(ks)])
    axes[1].set_xticks(range(len(ks)))
    axes[1].set_xticklabels(labels, fontsize=8)
    axes[1].set_ylabel("Hit Rate (%)")
    axes[1].set_title("Best Hit Rate per ATR K")
    for i, v in enumerate(hrs):
        axes[1].text(i, v + 0.5, f"{v:.0f}%", ha="center", fontsize=9, fontweight="bold")

    n_trades = [best_per_k[k].n_trades for k in ks]
    axes[2].bar(range(len(ks)), n_trades, color=colors[:len(ks)])
    axes[2].set_xticks(range(len(ks)))
    axes[2].set_xticklabels(labels, fontsize=8)
    axes[2].set_ylabel("# Trades")
    axes[2].set_title("Trade Count per ATR K")

    fig.suptitle(f"{tf} {combo_label} — ATR Multiplier Comparison", fontsize=14, fontweight="bold")
    plt.tight_layout()
    fname = out_dir / f"atr_comparison_{combo_label}.png"
    fig.savefig(fname)
    plt.close(fig)
    print(f"    Saved {fname.name}")


def chart_stage_distribution(
    results: List[GridResult],
    best_K: float,
    tf: str,
    combo_label: str,
    out_dir: Path,
):
    """Stacked bar chart showing exit reason distribution at best K."""
    filtered = [r for r in results if r.K == best_K]
    filtered.sort(key=lambda r: r.M * 1000 + r.T)

    top_n = sorted(filtered, key=lambda r: r.avg_return, reverse=True)[:10]
    top_n.sort(key=lambda r: r.avg_return)

    if not top_n:
        return

    fig, ax = plt.subplots(figsize=(14, 8))

    labels = [f"T={r.T} M={r.M}" for r in top_n]
    y = range(len(top_n))
    bar_h = 0.6

    colors = {"atr": "#f44336", "lenient": "#1565c0", "strict": "#66bb6a", "maxhold": "#616161"}

    for r_i, r in enumerate(top_n):
        left = 0
        for reason, pct_attr, avg_attr in [
            ("atr", "pct_atr", "avg_ret_atr"),
            ("lenient", "pct_lenient", "avg_ret_lenient"),
            ("strict", "pct_strict", "avg_ret_strict"),
            ("maxhold", "pct_maxhold", "avg_ret_maxhold"),
        ]:
            pct = getattr(r, pct_attr)
            avg_r = getattr(r, avg_attr)
            if pct > 0:
                bar = ax.barh(r_i, pct, height=bar_h, left=left,
                              color=colors[reason], edgecolor="#333", linewidth=0.5)
                if pct > 8:
                    ax.text(left + pct / 2, r_i, f"{pct:.0f}%\n({avg_r:+.1f}%)",
                            ha="center", va="center", fontsize=7, fontweight="bold")
                left += pct

        ax.text(101, r_i, f"HR={r.hit_rate:.0f}%  Avg={r.avg_return:+.2f}%  n={r.n_trades}",
                va="center", fontsize=8)

    ax.set_yticks(range(len(top_n)))
    ax.set_yticklabels(labels)
    ax.set_xlabel("% of Trades")
    ax.set_xlim(0, 150)

    from matplotlib.patches import Patch
    legend_items = [Patch(facecolor=colors[k], label=k.title()) for k in colors]
    ax.legend(handles=legend_items, loc="lower right", fontsize=9)

    ax.set_title(f"{tf} {combo_label} — Stage Distribution (Top 10 by Avg Return, K={best_K})")
    plt.tight_layout()
    fname = out_dir / f"stage_distribution_{combo_label}_K{best_K}.png"
    fig.savefig(fname)
    plt.close(fig)
    print(f"    Saved {fname.name}")


def chart_3stage_comparison(
    results_3s: List[Dict],
    tf: str,
    combo_label: str,
    out_dir: Path,
):
    """Compare 2-stage baseline vs 3-stage variants."""
    if len(results_3s) < 2:
        return

    valid = [r for r in results_3s if "avg_return" in r and r.get("n_trades", 0) > 0]
    if not valid:
        return

    baseline = valid[0]
    variants = valid[1:]

    if not variants:
        return

    best_variant = max(variants, key=lambda r: r.get("avg_return", -999))

    fig, axes = plt.subplots(1, 2, figsize=(16, 7))

    by_thresh = defaultdict(list)
    for v in variants:
        by_thresh[v["intermediate_thresh"]].append(v)

    ax = axes[0]
    for thresh, items in sorted(by_thresh.items()):
        t2_vals = [it["T2"] for it in items]
        avg_vals = [it["avg_return"] for it in items]
        ax.plot(t2_vals, avg_vals, "o-", label=f"≥{thresh} KPIs", markersize=5)

    ax.axhline(baseline.get("avg_return", 0), color="cyan", linestyle="--",
               linewidth=2, label=f"2-stage baseline ({baseline.get('avg_return', 0):+.2f}%)")
    ax.set_xlabel("Intermediate Transition Bar (T2)")
    ax.set_ylabel("Avg Return (%)")
    ax.set_title("3-Stage Avg Return by T2 and Threshold")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.2)

    ax = axes[1]
    for thresh, items in sorted(by_thresh.items()):
        t2_vals = [it["T2"] for it in items]
        hr_vals = [it["hit_rate"] for it in items]
        ax.plot(t2_vals, hr_vals, "o-", label=f"≥{thresh} KPIs", markersize=5)

    ax.axhline(baseline.get("hit_rate", 0), color="cyan", linestyle="--",
               linewidth=2, label=f"2-stage baseline ({baseline.get('hit_rate', 0):.0f}%)")
    ax.set_xlabel("Intermediate Transition Bar (T2)")
    ax.set_ylabel("Hit Rate (%)")
    ax.set_title("3-Stage Hit Rate by T2 and Threshold")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.2)

    fig.suptitle(
        f"{tf} {combo_label} — 2-Stage vs 3-Stage Comparison\n"
        f"Best 3-stage: T2={best_variant.get('T2')}, thresh≥{best_variant.get('intermediate_thresh')} "
        f"→ Avg={best_variant.get('avg_return', 0):+.2f}%  "
        f"(baseline: {baseline.get('avg_return', 0):+.2f}%)",
        fontsize=13, fontweight="bold", y=1.02,
    )
    plt.tight_layout()
    fname = out_dir / f"3stage_comparison_{combo_label}.png"
    fig.savefig(fname)
    plt.close(fig)
    print(f"    Saved {fname.name}")


def chart_summary_overview(
    all_optimal: Dict[str, Dict[str, Dict]],
    out_dir: Path,
):
    """Single summary chart with optimal params for every tf x combo."""
    combos = ["C3", "C4", "C5"]
    tfs = ["4H", "1D", "1W"]

    fig, axes = plt.subplots(2, 1, figsize=(16, 10))

    x = np.arange(len(combos))
    width = 0.25
    tf_colors = {"4H": "#42a5f5", "1D": "#66bb6a", "1W": "#ffa726"}

    for i_tf, tf in enumerate(tfs):
        avgs, hrs = [], []
        for combo in combos:
            opt = all_optimal.get(tf, {}).get(combo, {})
            avgs.append(opt.get("avg_return", 0))
            hrs.append(opt.get("hit_rate", 0))
        offset = (i_tf - 1) * width
        bars = axes[0].bar(x + offset, avgs, width, color=tf_colors[tf], label=tf)
        for j, v in enumerate(avgs):
            axes[0].text(x[j] + offset, v + 0.1, f"{v:+.1f}%", ha="center", fontsize=8, fontweight="bold")

        bars = axes[1].bar(x + offset, hrs, width, color=tf_colors[tf], label=tf)
        for j, v in enumerate(hrs):
            axes[1].text(x[j] + offset, v + 0.5, f"{v:.0f}%", ha="center", fontsize=8, fontweight="bold")

    axes[0].set_ylabel("Avg Return (%)")
    axes[0].set_title("Optimal Avg Return by Timeframe × Combo")
    axes[0].set_xticks(x)
    axes[0].set_xticklabels(combos)
    axes[0].legend()
    axes[0].grid(True, alpha=0.2, axis="y")

    axes[1].set_ylabel("Hit Rate (%)")
    axes[1].set_title("Optimal Hit Rate by Timeframe × Combo")
    axes[1].set_xticks(x)
    axes[1].set_xticklabels(combos)
    axes[1].legend()
    axes[1].grid(True, alpha=0.2, axis="y")

    plt.tight_layout()
    fname = out_dir / "summary_overview.png"
    fig.savefig(fname)
    plt.close(fig)
    print(f"  Saved {fname.name}")


# ── Report generation ────────────────────────────────────────────────────

def generate_report(
    all_optimal: Dict[str, Dict[str, Dict]],
    all_3stage: Dict[str, Dict[str, List[Dict]]],
    out_dir: Path,
):
    lines = ["# Phase 10v3 — Exit Flow Grid Search Results\n"]

    lines.append("## Optimal 2-Stage Parameters\n")
    for tf in ["4H", "1D", "1W"]:
        lines.append(f"### {tf}\n")
        lines.append("| Combo | T (transition) | M (max hold) | K (ATR) | HR | Avg Return | Median | Worst | PF | n | %MaxHold |")
        lines.append("|-------|---------------|-------------|---------|-----|-----------|--------|-------|-----|---|---------|")
        for combo in ["C3", "C4", "C5"]:
            opt = all_optimal.get(tf, {}).get(combo, {})
            if opt:
                lines.append(
                    f"| {combo} | {opt.get('T', '?')} bars | {opt.get('M', '?')} bars | "
                    f"{opt.get('K', '?')} | {opt.get('hit_rate', 0):.0f}% | "
                    f"{opt.get('avg_return', 0):+.2f}% | {opt.get('median_return', 0):+.2f}% | "
                    f"{opt.get('worst_trade', 0):+.1f}% | {opt.get('profit_factor', 0):.1f} | "
                    f"{opt.get('n_trades', 0)} | {opt.get('pct_maxhold', 0):.0f}% |"
                )
        lines.append("")

    lines.append("## 3-Stage Analysis (C4/C5)\n")
    for tf in ["4H", "1D", "1W"]:
        for combo in ["C4", "C5"]:
            results = all_3stage.get(tf, {}).get(combo, [])
            if not results:
                continue
            lines.append(f"### {tf} {combo}\n")
            baseline = results[0] if results else {}
            valid_variants = [r for r in results[1:] if "avg_return" in r]
            if valid_variants:
                best_3s = max(valid_variants, key=lambda r: r.get("avg_return", -999))
                lines.append(
                    f"- **2-stage baseline:** Avg={baseline.get('avg_return', 0):+.2f}%, "
                    f"HR={baseline.get('hit_rate', 0):.0f}%\n"
                )
                lines.append(
                    f"- **Best 3-stage:** T2={best_3s.get('T2')}, "
                    f"thresh≥{best_3s.get('intermediate_thresh')}, "
                    f"Avg={best_3s.get('avg_return', 0):+.2f}%, "
                    f"HR={best_3s.get('hit_rate', 0):.0f}%\n"
                )
                delta = best_3s.get("avg_return", 0) - baseline.get("avg_return", 0)
                verdict = "3-stage WINS" if delta > 0.1 else "2-stage is sufficient"
                lines.append(f"- **Verdict:** {verdict} (delta = {delta:+.2f}%)\n")
            lines.append("")

    lines.append("## Recommendations\n")
    lines.append(
        "Parameters above represent the optimal exit flow configuration per timeframe and combo level. "
        "Use these as the production parameters for Exit Flow v3.\n"
    )

    report_path = out_dir / "exit_flow_v3_grid_search_report.md"
    report_path.write_text("\n".join(lines))
    print(f"  Saved {report_path.name}")


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    t0 = time.time()
    out_dir = output_dir_for("all", "phase10v3")
    out_dir.mkdir(parents=True, exist_ok=True)

    all_optimal: Dict[str, Dict[str, Dict]] = {}
    all_3stage: Dict[str, Dict[str, List[Dict]]] = {}

    for tf_key, tf_cfg in TIMEFRAME_CONFIGS.items():
        print(f"\n{'='*60}")
        print(f"  TIMEFRAME: {tf_key}")
        print(f"{'='*60}")

        all_data = load_data(ENRICHED_DIR, tf_key)
        print(f"  Loaded {len(all_data)} stocks")

        combos = COMBO_DEFINITIONS.get(tf_key, COMBO_DEFINITIONS["1W"])
        params = GRID_PARAMS[tf_key]

        all_optimal[tf_key] = {}
        all_3stage[tf_key] = {}

        tf_out = output_dir_for(tf_key, "phase10v3")
        tf_out.mkdir(parents=True, exist_ok=True)

        for combo_name, combo_kpis in combos.items():
            combo_label = combo_name.replace("combo_", "C").upper()
            n_kpis = len(combo_kpis)
            print(f"\n  {combo_label} ({n_kpis} KPIs: {', '.join(combo_kpis)})")

            print(f"    Running 2-stage grid search...")
            results = run_grid_search_2stage(
                all_data, combo_kpis,
                params["T_values"], params["M_values"], params["K_values"],
            )
            print(f"    {len(results)} grid cells evaluated")

            if not results:
                print(f"    No valid results for {combo_label}")
                continue

            best = max(results, key=lambda r: r.avg_return)
            best_K = best.K
            print(
                f"    BEST: T={best.T}, M={best.M}, K={best.K} → "
                f"HR={best.hit_rate:.0f}%, Avg={best.avg_return:+.2f}%, "
                f"Med={best.median_return:+.2f}%, Worst={best.worst_trade:+.1f}%, "
                f"PF={best.profit_factor:.1f}, n={best.n_trades}"
            )
            print(
                f"    Stage dist: ATR={best.pct_atr:.0f}% ({best.avg_ret_atr:+.1f}%), "
                f"Lenient={best.pct_lenient:.0f}% ({best.avg_ret_lenient:+.1f}%), "
                f"Strict={best.pct_strict:.0f}% ({best.avg_ret_strict:+.1f}%), "
                f"MaxHold={best.pct_maxhold:.0f}% ({best.avg_ret_maxhold:+.1f}%)"
            )

            all_optimal[tf_key][combo_label] = {
                "T": best.T, "M": best.M, "K": best.K,
                "n_trades": best.n_trades, "hit_rate": best.hit_rate,
                "avg_return": best.avg_return, "median_return": best.median_return,
                "worst_trade": best.worst_trade, "profit_factor": best.profit_factor,
                "pct_maxhold": best.pct_maxhold,
                "pct_strict": best.pct_strict,
                "pct_lenient": best.pct_lenient,
                "pct_atr": best.pct_atr,
                "kpis": combo_kpis,
            }

            chart_heatmap(results, best_K, tf_key, combo_label, tf_out)
            chart_atr_comparison(results, tf_key, combo_label, tf_out)
            chart_stage_distribution(results, best_K, tf_key, combo_label, tf_out)

            if n_kpis >= 4:
                print(f"    Running 3-stage test for {combo_label}...")
                results_3s = run_grid_search_3stage(
                    all_data, combo_kpis, best.T, best.M, best.K,
                )
                all_3stage[tf_key][combo_label] = results_3s
                chart_3stage_comparison(results_3s, tf_key, combo_label, tf_out)
                valid_3s = [r for r in results_3s[1:] if "avg_return" in r]
                if valid_3s:
                    best_3s = max(valid_3s, key=lambda r: r.get("avg_return", -999))
                    print(
                        f"    Best 3-stage: T2={best_3s.get('T2')}, "
                        f"thresh≥{best_3s.get('intermediate_thresh')} → "
                        f"Avg={best_3s.get('avg_return', 0):+.2f}%"
                    )

    chart_summary_overview(all_optimal, out_dir)
    generate_report(all_optimal, all_3stage, out_dir)

    json_path = out_dir / "optimal_params.json"
    json_path.write_text(json.dumps(all_optimal, indent=2, default=str))
    print(f"  Saved {json_path.name}")

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.0f}s")


if __name__ == "__main__":
    main()

"""
Phase 9 — Best Return Combination Discovery (Trend + Breakout KPIs)

For each sector × timeframe, exhaustively searches C(N, k) KPI combinations
for k = 3, 4, 5 to find the combination that MAXIMIZES AVERAGE FORWARD RETURN:
    max  avg( Close[t + h] / Close[t] )

Key difference from prior phases:
  - Phase 4 / best_combos_by_sector optimise for HIT RATE
  - Phase 9 optimises for AVERAGE RETURN (price appreciation ratio)

The search universe includes ALL KPIs: 21 Trend + 7 Breakout indicators,
enabling discovery of combos where breakout signals add alpha.

Outputs per timeframe:
  - phase9_best_return_C3.png / C4.png / C5.png — bar charts per sector
  - phase9_kpi_importance.png — KPI frequency in winning combos
  - phase9_return_vs_hitrate.png — scatter: return vs consistency
  - phase9_metrics_overview.png — summary table
  - best_return_combos_report.md — detailed findings & recommendations
  - best_return_combos_by_sector.json — machine-readable results
"""

from __future__ import annotations

import heapq
import json
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from textwrap import fill
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from trading_dashboard.kpis.catalog import (
    compute_kpi_state_map,
    KPI_TREND_ORDER,
    KPI_BREAKOUT_ORDER,
)
from trading_dashboard.kpis.rules import STATE_BULL, STATE_NA
from apps.dashboard.sector_map import load_sector_map
from tf_config import parse_timeframe_arg, output_dir_for, ENRICHED_DIR, TFConfig

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

MIN_STOCKS_PER_SECTOR = 3
MIN_COMBO_TRADES = 15
MIN_KPI_COVERAGE = 0.30
TOP_N_COMBOS = 5
BARS_PER_YEAR = {"1W": 52, "1D": 252, "4H": 1560}

# Full search universe: Trend + Breakout + extra computed KPIs
ALL_KPIS: List[str] = []
_seen: set = set()
for _k in (
    list(KPI_TREND_ORDER)
    + list(KPI_BREAKOUT_ORDER)
    + ["GK Trend Ribbon", "Impulse Trend", "Breakout Targets"]
):
    if _k not in _seen:
        ALL_KPIS.append(_k)
        _seen.add(_k)

BREAKOUT_KPIS = set(KPI_BREAKOUT_ORDER) | {"Breakout Targets"}

KPI_SHORT: Dict[str, str] = {
    "Nadaraya-Watson Smoother": "NW Smooth",
    "Nadaraya-Watson Envelop (MAE)": "NWE MAE",
    "Nadaraya-Watson Envelop (STD)": "NWE STD",
    "Nadaraya-Watson Envelop (Repainting)": "NWE RP",
    "RSI Strength & Consolidation Zones (Zeiierman)": "Zeiierman",
    "CM_Ult_MacD_MFT": "MACD MFT",
    "GK Trend Ribbon": "GK Trend",
    "Impulse Trend": "Impulse",
    "Breakout Targets": "BT",
    "Madrid Ribbon": "Madrid",
    "Donchian Ribbon": "Donchian",
    "Mansfield RS": "Mansfield",
    "UT Bot Alert": "UT Bot",
}

GLOBAL_COMBOS = {
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


def _short(kpi: str) -> str:
    return KPI_SHORT.get(kpi, kpi)


def _short_list(kpis: List[str], sep: str = " + ") -> str:
    return sep.join(_short(k) for k in kpis) if kpis else "—"


# ── Data loading ──────────────────────────────────────────────────────────

def load_data(enriched_dir: Path, timeframe: str) -> Dict[str, pd.DataFrame]:
    data: Dict[str, pd.DataFrame] = {}
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


# ── Precomputation ────────────────────────────────────────────────────────

@dataclass
class SectorArrays:
    fwd: np.ndarray
    valid: np.ndarray
    bulls: Dict[str, np.ndarray]
    n_stocks: int
    kpi_coverage: Dict[str, float]


@dataclass
class ComboMetrics:
    kpis: List[str]
    avg_return: float
    hit_rate: float
    profit_factor: float
    n_trades: int
    median_return: float
    sharpe: float


def precompute_sector_data(
    all_data: Dict[str, pd.DataFrame],
    sector_stocks: Dict[str, List[str]],
    kpi_names: List[str],
    horizon: int,
) -> Dict[str, SectorArrays]:
    result: Dict[str, SectorArrays] = {}

    for sector, syms in sector_stocks.items():
        all_fwds: List[np.ndarray] = []
        kpi_bulls: Dict[str, List[np.ndarray]] = {k: [] for k in kpi_names}
        kpi_avail: Dict[str, int] = {k: 0 for k in kpi_names}

        for sym in syms:
            df = all_data.get(sym)
            if df is None:
                continue
            fwd = df["Close"].pct_change(horizon).shift(-horizon)
            state_map = compute_kpi_state_map(df)
            fwd_arr = fwd.to_numpy(dtype=float)
            n = len(fwd_arr)

            for kpi in kpi_names:
                if kpi in state_map:
                    s = state_map[kpi]
                    bull = (s == STATE_BULL).to_numpy(dtype=bool)
                    kpi_bulls[kpi].append(bull)
                    if (s != STATE_NA).any():
                        kpi_avail[kpi] += 1
                else:
                    kpi_bulls[kpi].append(np.zeros(n, dtype=bool))

            all_fwds.append(fwd_arr)

        if not all_fwds:
            continue

        n_stocks = len(all_fwds)
        fwd_concat = np.concatenate(all_fwds)
        valid = ~np.isnan(fwd_concat)
        coverage = {k: kpi_avail[k] / n_stocks for k in kpi_names}

        result[sector] = SectorArrays(
            fwd=fwd_concat,
            valid=valid,
            bulls={k: np.concatenate(v) for k, v in kpi_bulls.items()},
            n_stocks=n_stocks,
            kpi_coverage=coverage,
        )

    return result


def _compute_combo_metrics(
    fwd: np.ndarray,
    combined: np.ndarray,
    kpis: List[str],
    bpy: int,
) -> ComboMetrics:
    rets = fwd[combined]
    n = len(rets)
    avg_ret = float(np.mean(rets))
    hr = float(np.sum(rets > 0) / n)
    gp = float(np.sum(rets[rets > 0]))
    gl = float(np.abs(np.sum(rets[rets < 0])))
    pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0.0)
    med = float(np.median(rets))
    std = float(np.std(rets))
    sharpe = float(np.mean(rets) / std * np.sqrt(bpy)) if std > 0 else 0.0
    return ComboMetrics(
        kpis=list(kpis),
        avg_return=avg_ret,
        hit_rate=hr,
        profit_factor=min(pf, 99.9),
        n_trades=n,
        median_return=med,
        sharpe=sharpe,
    )


def find_best_combos_by_return(
    sa: SectorArrays,
    k: int,
    kpi_names: List[str],
    bpy: int,
    top_n: int = TOP_N_COMBOS,
    min_trades: int = MIN_COMBO_TRADES,
    min_coverage: float = MIN_KPI_COVERAGE,
) -> List[ComboMetrics]:
    available = [kpi for kpi in kpi_names if sa.kpi_coverage.get(kpi, 0) >= min_coverage]
    if len(available) < k:
        return []

    fwd = sa.fwd
    valid = sa.valid

    # min-heap of (avg_return, counter, combo_tuple) — counter breaks ties
    heap: List[Tuple[float, int, Tuple[str, ...]]] = []
    ctr = 0

    for combo in combinations(available, k):
        combined = sa.bulls[combo[0]].copy()
        for kpi in combo[1:]:
            combined &= sa.bulls[kpi]
        combined &= valid

        n = int(combined.sum())
        if n < min_trades:
            continue

        avg_ret = float(np.mean(fwd[combined]))

        if len(heap) < top_n:
            heapq.heappush(heap, (avg_ret, ctr, combo))
        elif avg_ret > heap[0][0]:
            heapq.heapreplace(heap, (avg_ret, ctr, combo))
        ctr += 1

    heap.sort(reverse=True)

    results: List[ComboMetrics] = []
    for _, _, combo in heap:
        combined = sa.bulls[combo[0]].copy()
        for kpi in combo[1:]:
            combined &= sa.bulls[kpi]
        combined &= valid
        m = _compute_combo_metrics(fwd, combined, list(combo), bpy)
        results.append(m)

    return results


def evaluate_global_combo(
    sa: SectorArrays,
    kpis: List[str],
    bpy: int,
) -> Optional[ComboMetrics]:
    for kpi in kpis:
        if kpi not in sa.bulls:
            return None
    combined = sa.bulls[kpis[0]].copy()
    for kpi in kpis[1:]:
        combined &= sa.bulls[kpi]
    combined &= sa.valid
    n = int(combined.sum())
    if n < 5:
        return None
    return _compute_combo_metrics(sa.fwd, combined, kpis, bpy)


# ── Visualization helpers ─────────────────────────────────────────────────

def _wrap(text: str, width: int = 125) -> str:
    return fill(text, width=width)


def _add_commentary(fig: Any, text: str, y: float = 0.02, fontsize: int = 9) -> None:
    fig.text(
        0.05, y, text, fontsize=fontsize, color="#b0bec5",
        ha="left", va="top", wrap=True, fontstyle="italic",
        bbox=dict(boxstyle="round,pad=0.5", facecolor="#252525",
                  edgecolor="#444", alpha=0.95),
        transform=fig.transFigure,
    )


# ── Chart 1: Best return per combo level (3 PNGs) ────────────────────────

def chart_best_return_per_combo(
    results: Dict[str, Dict[str, List[ComboMetrics]]],
    global_results: Dict[str, Dict[str, Optional[ComboMetrics]]],
    combo_label: str,
    k: int,
    tf: TFConfig,
    output_dir: Path,
) -> None:
    sectors = [s for s in sorted(results.keys()) if results[s].get(combo_label)]
    if not sectors:
        return
    sectors = sorted(sectors, key=lambda s: (s != "ALL", s))

    sector_best = {s: results[s][combo_label][0] for s in sectors}
    sector_global = {s: global_results.get(s, {}).get(combo_label) for s in sectors}

    fig, ax = plt.subplots(figsize=(16, max(6, len(sectors) * 0.65)))
    fig.subplots_adjust(bottom=0.20)

    y = np.arange(len(sectors))
    h = 0.35

    global_rets = [
        sector_global[s].avg_return * 100 if sector_global[s] else 0
        for s in sectors
    ]
    best_rets = [sector_best[s].avg_return * 100 for s in sectors]

    ax.barh(y + h / 2, global_rets, h,
            label="Global combo (HR-optimised)", color="#616161",
            edgecolor="none", alpha=0.8)
    bars_b = ax.barh(y - h / 2, best_rets, h,
                     label="Best return combo", color="#4caf50",
                     edgecolor="none", alpha=0.9)

    ax.set_yticks(y)
    ax.set_yticklabels(sectors, fontsize=9)
    ax.axvline(0, color="white", linewidth=0.5, alpha=0.3)
    ax.set_xlabel("Avg Forward Return (%)")
    h_label = {"1W": "w", "1D": "d", "4H": "h"}.get(tf.timeframe, "bars")
    ax.set_title(
        f"Phase 9 — Best Return {combo_label} by Sector "
        f"({tf.timeframe}, {tf.default_horizon}{h_label} horizon)"
    )
    ax.invert_yaxis()
    ax.legend(loc="lower right", fontsize=9, framealpha=0.3)

    for yy in y:
        ax.axhline(yy, color="#333", linewidth=0.3, zorder=0)

    for bar, val, s in zip(bars_b, best_rets, sectors):
        kpi_str = _short_list(sector_best[s].kpis, " + ")
        hr = sector_best[s].hit_rate
        n = sector_best[s].n_trades
        x_pos = val + 0.15 if val >= 0 else val - 0.15
        ha = "left" if val >= 0 else "right"
        ax.text(x_pos, bar.get_y() + bar.get_height() / 2,
                f"{val:+.2f}%  HR={hr:.0%}  n={n}  [{kpi_str}]",
                va="center", ha=ha, fontsize=7, color="#a5d6a7",
                fontweight="bold")

    for i, (val, s) in enumerate(zip(global_rets, sectors)):
        if val != 0:
            x_pos = val + 0.15 if val >= 0 else val - 0.15
            ha = "left" if val >= 0 else "right"
            ax.text(x_pos, y[i] + h / 2,
                    f"{val:+.2f}%", va="center", ha=ha,
                    fontsize=7, color="#9e9e9e")

    improvements = []
    n_breakout = 0
    for s in sectors:
        best = sector_best[s]
        glob = sector_global.get(s)
        if glob:
            improvements.append((best.avg_return - glob.avg_return) * 100)
        if any(kk in BREAKOUT_KPIS for kk in best.kpis):
            n_breakout += 1

    avg_imp = float(np.mean(improvements)) if improvements else 0
    max_imp = float(np.max(improvements)) if improvements else 0
    bk_pct = n_breakout / len(sectors) * 100 if sectors else 0

    commentary = (
        f"METHODOLOGY: Exhaustive search over C(N,{k}) combinations of "
        f"{len(ALL_KPIS)} KPIs (Trend + Breakout). Entry = all KPIs "
        f"simultaneously bullish. Metric = avg forward return over "
        f"{tf.default_horizon} bars ({tf.default_horizon}{h_label}). "
        f"'Global combo' is the Phase-8 HR-optimised definition. "
        f"FINDING: Return-optimised combos improve avg return by "
        f"{avg_imp:+.2f}pp on average (max {max_imp:+.2f}pp). "
        f"Breakout KPIs appear in {bk_pct:.0f}% of winning combos. "
        f"RECOMMENDATION: Use sector-specific combos for return "
        f"maximisation. Check HR — low HR with high return means "
        f"large wins offset frequent small losses (momentum style)."
    )
    _add_commentary(fig, _wrap(commentary), y=0.04)

    fname = f"phase9_best_return_{combo_label}.png"
    fig.savefig(output_dir / fname)
    plt.close(fig)
    print(f"  Saved {fname}")


# ── Chart 2: KPI Importance ──────────────────────────────────────────────

def chart_kpi_importance(
    results: Dict[str, Dict[str, List[ComboMetrics]]],
    tf: TFConfig,
    output_dir: Path,
) -> None:
    counter: Counter = Counter()
    for sector, combos in results.items():
        for label, metrics_list in combos.items():
            if metrics_list:
                for kpi in metrics_list[0].kpis:
                    counter[kpi] += 1

    if not counter:
        return

    kpis_sorted = counter.most_common()
    names = [_short(k) for k, _ in kpis_sorted]
    counts = [c for _, c in kpis_sorted]
    is_bk = [k in BREAKOUT_KPIS for k, _ in kpis_sorted]
    colors = ["#ff9800" if b else "#42a5f5" for b in is_bk]

    fig, ax = plt.subplots(figsize=(14, max(6, len(names) * 0.38)))
    fig.subplots_adjust(bottom=0.20)

    yy = np.arange(len(names))
    ax.barh(yy, counts, color=colors, edgecolor="none", height=0.7)
    ax.set_yticks(yy)
    ax.set_yticklabels(names, fontsize=9)
    ax.set_xlabel("Appearances in Winning Combos (all sectors × C3/C4/C5)")
    ax.set_title(
        f"Phase 9 — KPI Importance for Return Maximisation ({tf.timeframe})"
    )
    ax.invert_yaxis()

    for i, c in enumerate(counts):
        ax.text(c + 0.2, i, str(c), va="center", fontsize=9,
                fontweight="bold", color="white")

    ax.legend(
        handles=[Patch(facecolor="#42a5f5", label="Trend KPI"),
                 Patch(facecolor="#ff9800", label="Breakout KPI")],
        loc="lower right", fontsize=9, framealpha=0.3,
    )

    n_bk_top10 = sum(1 for k, _ in kpis_sorted[:10] if k in BREAKOUT_KPIS)
    top_bk = [_short(k) for k, _ in kpis_sorted if k in BREAKOUT_KPIS][:3]
    top_tr = [_short(k) for k, _ in kpis_sorted if k not in BREAKOUT_KPIS][:3]

    commentary = (
        f"METHODOLOGY: Count how often each KPI appears in the #1 "
        f"return-optimised combo across all sectors and combo levels. "
        f"Blue = Trend, Orange = Breakout. "
        f"FINDING: Top trend KPIs: {', '.join(top_tr)}. "
        + (f"Top breakout KPIs: {', '.join(top_bk)}. " if top_bk
           else "No breakout KPIs in top combos. ")
        + f"{n_bk_top10} of top 10 KPIs are breakout indicators. "
        + ("RECOMMENDATION: Breakout KPIs add measurable alpha for "
           "return maximisation — include them in signal construction."
           if n_bk_top10 >= 2
           else "RECOMMENDATION: Breakout KPIs have limited impact on "
                "return optimisation — trend KPIs dominate.")
    )
    _add_commentary(fig, _wrap(commentary), y=0.04)

    fig.savefig(output_dir / "phase9_kpi_importance.png")
    plt.close(fig)
    print("  Saved phase9_kpi_importance.png")


# ── Chart 3: Return vs Hit-Rate Scatter ──────────────────────────────────

def chart_return_vs_hitrate(
    results: Dict[str, Dict[str, List[ComboMetrics]]],
    global_results: Dict[str, Dict[str, Optional[ComboMetrics]]],
    tf: TFConfig,
    output_dir: Path,
) -> None:
    combo_labels = ["C3", "C4", "C5"]
    combo_colors = {"C3": "#4fc3f7", "C4": "#66bb6a", "C5": "#ffb74d"}

    fig, axes = plt.subplots(1, 3, figsize=(20, 7))
    fig.subplots_adjust(bottom=0.22)

    for ax_idx, label in enumerate(combo_labels):
        ax = axes[ax_idx]

        for sector, combos in results.items():
            if label not in combos or not combos[label]:
                continue
            best = combos[label][0]
            size = max(30, min(400, best.n_trades * 2))
            has_bk = any(kk in BREAKOUT_KPIS for kk in best.kpis)
            marker = "D" if has_bk else "o"
            ax.scatter(
                best.hit_rate, best.avg_return * 100, s=size,
                c=combo_colors[label], alpha=0.7, edgecolors="white",
                linewidths=0.5, marker=marker, zorder=3,
            )
            ax.annotate(
                sector[:14], (best.hit_rate, best.avg_return * 100),
                fontsize=6, color="white", alpha=0.6,
                xytext=(5, 5), textcoords="offset points",
            )

        for sector, gdict in global_results.items():
            gm = gdict.get(label)
            if gm is None:
                continue
            ax.scatter(
                gm.hit_rate, gm.avg_return * 100, s=60,
                c="#616161", alpha=0.5, marker="x", zorder=2,
            )

        ax.axhline(0, color="#ff5252", linestyle="--", linewidth=0.8,
                   alpha=0.3)
        ax.axvline(0.5, color="#ff5252", linestyle="--", linewidth=0.8,
                   alpha=0.3)
        ax.set_xlabel("Hit Rate")
        ax.set_ylabel("Avg Return (%)")
        ax.set_title(label, fontsize=13, fontweight="bold")
        ax.grid(True, alpha=0.1)

        ax.text(0.97, 0.97, "HIGH RET\nHIGH HR\n(ideal)",
                transform=ax.transAxes, ha="right", va="top",
                fontsize=7, color="#66bb6a", alpha=0.4)
        ax.text(0.03, 0.03, "NEG RET\nLOW HR\n(avoid)",
                transform=ax.transAxes, ha="left", va="bottom",
                fontsize=7, color="#ef5350", alpha=0.4)

    fig.suptitle(
        f"Phase 9 — Avg Return vs Hit Rate ({tf.timeframe})",
        fontsize=14, fontweight="bold", y=1.02,
    )

    commentary = (
        "METHODOLOGY: Each coloured dot = one sector's best "
        "return-optimised combo (◆ = contains breakout KPI, ○ = "
        "trend-only). Grey × = global HR-optimised combo. Size = "
        "trade count. "
        "FINDING: Return-optimised combos often shift up (higher "
        "avg return) but may shift left (lower HR) compared to "
        "global combos. Upper-right = ideal (high return + high "
        "consistency). Upper-left = momentum style (large wins, "
        "frequent small losses). "
        "RECOMMENDATION: For swing trading, prefer upper-right "
        "quadrant. For trend-following, upper-left is acceptable "
        "if PF > 1.5."
    )
    _add_commentary(fig, _wrap(commentary), y=0.04)

    fig.savefig(output_dir / "phase9_return_vs_hitrate.png")
    plt.close(fig)
    print("  Saved phase9_return_vs_hitrate.png")


# ── Chart 4: Metrics Overview Table ──────────────────────────────────────

def chart_metrics_overview(
    results: Dict[str, Dict[str, List[ComboMetrics]]],
    global_results: Dict[str, Dict[str, Optional[ComboMetrics]]],
    tf: TFConfig,
    output_dir: Path,
) -> None:
    combo_labels = ["C3", "C4", "C5"]
    sectors = sorted([s for s in results if s != "ALL"])
    if "ALL" in results:
        sectors = ["ALL"] + sectors

    rows = []
    for sector in sectors:
        for label in combo_labels:
            combos = results.get(sector, {}).get(label, [])
            if not combos:
                continue
            best = combos[0]
            glob = global_results.get(sector, {}).get(label)
            glob_ret = (f"{glob.avg_return * 100:+.2f}%"
                        if glob else "—")
            delta = (f"{(best.avg_return - glob.avg_return) * 100:+.2f}pp"
                     if glob else "—")
            pf_s = (f"{best.profit_factor:.2f}"
                    if best.profit_factor < 99 else "inf")
            rows.append([
                sector, label,
                f"{best.avg_return * 100:+.2f}%",
                f"{best.hit_rate:.0%}",
                pf_s,
                str(best.n_trades),
                glob_ret,
                delta,
                _short_list(best.kpis, " + "),
            ])

    if not rows:
        return

    cols = [
        "Sector", "Level", "Avg Ret", "HR", "PF",
        "Trades", "Global Ret", "Δ vs Global", "Best Combo KPIs",
    ]

    fig, ax = plt.subplots(figsize=(22, max(6, len(rows) * 0.35 + 2)))
    ax.axis("off")
    h_label = {"1W": "w", "1D": "d", "4H": "h"}.get(tf.timeframe, "bars")
    ax.set_title(
        f"Phase 9 — Return-Optimised Combo Summary "
        f"({tf.timeframe}, {tf.default_horizon}{h_label} horizon)",
        pad=20,
    )

    table = ax.table(cellText=rows, colLabels=cols, loc="center",
                     cellLoc="center")
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
            if col in (2, 7) and text not in ("—", ""):
                if text.startswith("+"):
                    cell.set_text_props(color="#66bb6a", fontweight="bold")
                elif text.startswith("-"):
                    cell.set_text_props(color="#ef5350", fontweight="bold")

    fig.savefig(output_dir / "phase9_metrics_overview.png")
    plt.close(fig)
    print("  Saved phase9_metrics_overview.png")


# ── Report ────────────────────────────────────────────────────────────────

def generate_report(
    results: Dict[str, Dict[str, List[ComboMetrics]]],
    global_results: Dict[str, Dict[str, Optional[ComboMetrics]]],
    tf: TFConfig,
    output_dir: Path,
) -> None:
    combo_labels = ["C3", "C4", "C5"]
    h_label = {"1W": "w", "1D": "d", "4H": "h"}.get(tf.timeframe, "bars")

    lines = [
        f"# Phase 9 — Best Return Combination Discovery ({tf.timeframe})",
        "",
        f"**Timeframe:** {tf.timeframe}  ",
        f"**Horizon:** {tf.default_horizon} bars "
        f"({tf.default_horizon}{h_label})  ",
        f"**KPI Universe:** {len(ALL_KPIS)} KPIs "
        f"(Trend + Breakout)  ",
        f"**Min trades:** {MIN_COMBO_TRADES}  ",
        f"**Min KPI coverage:** {MIN_KPI_COVERAGE:.0%} of sector stocks  ",
        "",
        "## Methodology",
        "",
        "For each sector × combo level (C3, C4, C5), we search "
        "exhaustively over all C(N, k) combinations of "
        f"{len(ALL_KPIS)} KPIs (including breakout indicators) to "
        "find the combination that **maximises average forward return**:",
        "",
        "    max  avg( Close[t + h] / Close[t] - 1 )",
        "",
        "This differs from prior phases which optimised for **hit rate**. "
        "A high-return combo may have a lower hit rate but captures "
        "larger price moves when it fires.",
        "",
        "Entry signal: all KPIs in the combo are simultaneously bullish "
        "(AND-filter).",
        "",
    ]

    for label in combo_labels:
        k = int(label[1])
        lines.extend([
            f"## {label} (k={k}) — Best Return Combos by Sector",
            "",
            "| Sector | Avg Return | Ratio | HR | PF | Sharpe | Trades "
            "| vs Global | Best Combo KPIs |",
            "|--------|-----------|-------|-----|-----|--------|--------"
            "|-----------|-----------------|",
        ])

        for sector in sorted(results.keys()):
            combos = results[sector].get(label, [])
            if not combos:
                continue
            best = combos[0]
            glob = global_results.get(sector, {}).get(label)
            delta = (f"{(best.avg_return - glob.avg_return) * 100:+.2f}pp"
                     if glob else "—")
            pf_s = (f"{best.profit_factor:.2f}"
                    if best.profit_factor < 99 else "inf")
            ratio = 1 + best.avg_return
            kpi_str = _short_list(best.kpis, ", ")
            lines.append(
                f"| {sector} | {best.avg_return * 100:+.2f}% | "
                f"{ratio:.4f} | {best.hit_rate:.0%} | {pf_s} | "
                f"{best.sharpe:.2f} | {best.n_trades} | {delta} | "
                f"{kpi_str} |"
            )

        lines.append("")

        all_combos = results.get("ALL", {}).get(label, [])
        if len(all_combos) > 1:
            lines.extend([
                f"### Top {min(TOP_N_COMBOS, len(all_combos))} "
                f"{label} Combos (ALL stocks)",
                "",
            ])
            for rank, m in enumerate(all_combos[:TOP_N_COMBOS], 1):
                pf_s = (f"{m.profit_factor:.2f}"
                        if m.profit_factor < 99 else "inf")
                lines.append(
                    f"  {rank}. **{_short_list(m.kpis)}** — "
                    f"AvgRet={m.avg_return * 100:+.2f}%, "
                    f"HR={m.hit_rate:.0%}, PF={pf_s}, n={m.n_trades}"
                )
            lines.append("")

    # KPI frequency
    counter: Counter = Counter()
    n_trend = 0
    n_breakout = 0
    for combos in results.values():
        for metrics_list in combos.values():
            if metrics_list:
                for kpi in metrics_list[0].kpis:
                    counter[kpi] += 1
                    if kpi in BREAKOUT_KPIS:
                        n_breakout += 1
                    else:
                        n_trend += 1

    lines.extend([
        "## KPI Importance Analysis",
        "",
        f"Total KPI appearances in winning combos: {n_trend + n_breakout} "
        f"({n_trend} trend, {n_breakout} breakout)",
        "",
        "| Rank | KPI | Type | Count |",
        "|------|-----|------|-------|",
    ])
    for rank, (kpi, cnt) in enumerate(counter.most_common(15), 1):
        ktype = "Breakout" if kpi in BREAKOUT_KPIS else "Trend"
        lines.append(f"| {rank} | {kpi} | {ktype} | {cnt} |")

    lines.extend(["", "## Recommendations", ""])

    all_metrics = [
        m
        for combos in results.values()
        for ms in combos.values()
        for m in ms[:1]
    ]
    if all_metrics:
        avg_ret = float(np.mean([m.avg_return for m in all_metrics])) * 100
        med_hr = float(np.median([m.hit_rate for m in all_metrics]))
        lines.append(
            f"**Overall:** avg return across all sector/combo best combos = "
            f"{avg_ret:+.2f}%, median HR = {med_hr:.0%}"
        )
        lines.append("")

    for sector in sorted(results.keys()):
        sector_combos = results[sector]
        if not sector_combos:
            continue
        best_label = max(
            sector_combos,
            key=lambda ll: (sector_combos[ll][0].avg_return
                            if sector_combos[ll] else -999),
        )
        if not sector_combos.get(best_label):
            continue
        best = sector_combos[best_label][0]
        has_bk = any(kk in BREAKOUT_KPIS for kk in best.kpis)
        bk_note = " (includes breakout)" if has_bk else ""
        glob = global_results.get(sector, {}).get(best_label)
        delta_note = ""
        if glob:
            delta = (best.avg_return - glob.avg_return) * 100
            delta_note = f", {delta:+.2f}pp vs global"
        lines.append(
            f"- **{sector}**: Best = {best_label} "
            f"[{_short_list(best.kpis)}] — "
            f"AvgRet={best.avg_return * 100:+.2f}%, "
            f"HR={best.hit_rate:.0%}{delta_note}{bk_note}"
        )

    lines.extend(["", ""])

    report = "\n".join(lines)
    (output_dir / "best_return_combos_report.md").write_text(
        report, encoding="utf-8"
    )
    print("  Saved best_return_combos_report.md")

    json_data: Dict[str, Any] = {}
    for sector, combos in results.items():
        json_data[sector] = {}
        for label, metrics_list in combos.items():
            json_data[sector][label] = [
                {
                    "kpis": m.kpis,
                    "avg_return": round(m.avg_return, 6),
                    "price_ratio": round(1 + m.avg_return, 6),
                    "hit_rate": round(m.hit_rate, 4),
                    "profit_factor": (round(m.profit_factor, 4)
                                      if m.profit_factor < 99 else None),
                    "n_trades": m.n_trades,
                    "median_return": round(m.median_return, 6),
                    "sharpe": round(m.sharpe, 4),
                }
                for m in metrics_list[:TOP_N_COMBOS]
            ]

    (output_dir / "best_return_combos_by_sector.json").write_text(
        json.dumps(json_data, indent=2), encoding="utf-8"
    )
    print("  Saved best_return_combos_by_sector.json")


# ── Main ──────────────────────────────────────────────────────────────────

def main() -> int:
    tf = parse_timeframe_arg("Phase 9 — Best Return Combos")
    output_dir = output_dir_for(tf.timeframe, "phase9")
    output_dir.mkdir(parents=True, exist_ok=True)

    bpy = BARS_PER_YEAR.get(tf.timeframe, 52)
    t0 = time.time()

    print(f"Phase 9 — Best Return Combination Discovery ({tf.timeframe})")
    print(f"KPI universe: {len(ALL_KPIS)} indicators (Trend + Breakout)")
    print(f"Horizon: {tf.default_horizon} bars")
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

    valid_sectors = {
        s: syms for s, syms in sector_stocks.items()
        if len(syms) >= MIN_STOCKS_PER_SECTOR
    }
    valid_sectors["ALL"] = list(all_data.keys())
    print(
        f"Sectors: {len(valid_sectors) - 1} + ALL "
        f"({len(all_data)} stocks total)"
    )

    print("\nPrecomputing sector data...")
    precomputed = precompute_sector_data(
        all_data, valid_sectors, ALL_KPIS, tf.default_horizon,
    )

    results: Dict[str, Dict[str, List[ComboMetrics]]] = {}
    global_results: Dict[str, Dict[str, Optional[ComboMetrics]]] = {}
    global_combos = GLOBAL_COMBOS.get(tf.timeframe, GLOBAL_COMBOS["1W"])

    for sector in sorted(valid_sectors.keys()):
        sa = precomputed.get(sector)
        if sa is None:
            continue
        print(f"\n  {sector} ({sa.n_stocks} stocks):")
        results[sector] = {}
        global_results[sector] = {}

        for k, label, combo_key in [
            (3, "C3", "combo_3"),
            (4, "C4", "combo_4"),
            (5, "C5", "combo_5"),
        ]:
            t1 = time.time()
            top = find_best_combos_by_return(sa, k, ALL_KPIS, bpy)
            elapsed_k = time.time() - t1
            results[sector][label] = top

            if top:
                best = top[0]
                kpi_str = _short_list(best.kpis)
                print(
                    f"    {label}: AvgRet={best.avg_return * 100:+.2f}%, "
                    f"HR={best.hit_rate:.0%}, "
                    f"PF={best.profit_factor:.2f}, "
                    f"n={best.n_trades} — {kpi_str} ({elapsed_k:.1f}s)"
                )
            else:
                print(
                    f"    {label}: no valid combos found ({elapsed_k:.1f}s)"
                )

            gkpis = global_combos.get(combo_key, [])
            gm = evaluate_global_combo(sa, gkpis, bpy) if gkpis else None
            global_results[sector][label] = gm
            if gm:
                print(
                    f"         Global: AvgRet={gm.avg_return * 100:+.2f}%, "
                    f"HR={gm.hit_rate:.0%}, n={gm.n_trades}"
                )

    print("\n\nGenerating charts...")
    for k, label in [(3, "C3"), (4, "C4"), (5, "C5")]:
        chart_best_return_per_combo(
            results, global_results, label, k, tf, output_dir,
        )

    chart_kpi_importance(results, tf, output_dir)
    chart_return_vs_hitrate(results, global_results, tf, output_dir)
    chart_metrics_overview(results, global_results, tf, output_dir)

    print("\nGenerating report...")
    generate_report(results, global_results, tf, output_dir)

    elapsed = time.time() - t0
    print(f"\nPhase 9 complete in {elapsed:.0f}s")
    print(f"Outputs: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

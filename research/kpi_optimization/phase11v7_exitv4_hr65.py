"""
Phase 11 v7 — Exit Flow v4 + HR >= 65% Entry Optimisation

Exit Flow v4: Option C + ATR reset
  - Bar 1..T (lenient): exit only if ALL KPIs turn bearish
  - Bar T+1.. (strict): exit if >= 2 KPIs turn bearish
  - ATR stop: price < stop_price → exit
  - Bar M checkpoint: if ALL KPIs still bullish → reset counter + reset ATR stop
                      else → exit
  - No hard max-hold cap — trade runs as long as signal is valid

Entry: HR >= 65% constraint, maximise total P&L
  - C3 (1x), C4 (1.5x), C5 (2x) tiered sizing
  - Compare weighted vs unweighted P&L

Pipeline:
  Part 1: Exit Flow v4 vs v3 on current combos (how much P&L recovered?)
  Part 2: Screen ALL combos with HR >= 65%, best P&L → sim with Exit Flow v4
  Part 3: Weighted (1x/1.5x/2x) vs unweighted P&L
"""

from __future__ import annotations

import json
import sys
import time
from collections import namedtuple
from dataclasses import dataclass, field
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
    "font.family": "sans-serif", "font.size": 10,
    "axes.titlesize": 13, "axes.titleweight": "bold",
    "figure.facecolor": "#181818", "axes.facecolor": "#1e1e1e",
    "savefig.facecolor": "#181818", "savefig.dpi": 180,
    "savefig.bbox": "tight", "savefig.pad_inches": 0.3,
})

# ── Config ────────────────────────────────────────────────────────────────

V3_PARAMS = {
    "4H": {"T": 4, "M": 48, "K": 3.5},
    "1D": {"T": 4, "M": 40, "K": 3.5},
    "1W": {"T": 2, "M": 20, "K": 2.0},
}
ATR_PERIOD = 14
MIN_KPI_COVERAGE = 0.30
MIN_TRADES = {"4H": 15, "1D": 15, "1W": 5}
HR_FLOOR = 65.0
SIM_TOP_N = 60
MAX_HOLD_HARD_CAP = 500  # absolute safety cap (bars) for v4

EXCLUDED_KPIS = {"Nadaraya-Watson Envelop (Repainting)"}

ALL_KPIS: List[str] = []
_seen: set = set()
for _k in list(KPI_TREND_ORDER) + list(KPI_BREAKOUT_ORDER) + [
    "GK Trend Ribbon", "Impulse Trend", "Volume + MA20",
    "SuperTrend", "UT Bot Alert",
]:
    if _k not in _seen and _k not in EXCLUDED_KPIS:
        ALL_KPIS.append(_k)
        _seen.add(_k)

KPI_SHORT: Dict[str, str] = {
    "Nadaraya-Watson Smoother": "NWSm", "Nadaraya-Watson Envelop (MAE)": "NWE-MAE",
    "Nadaraya-Watson Envelop (STD)": "NWE-STD", "BB 30": "BB30",
    "cRSI": "cRSI", "SR Breaks": "SRBrk", "Stoch_MTM": "Stoch",
    "CM_P-SAR": "PSAR", "MA Ribbon": "MARib", "Madrid Ribbon": "Madrid",
    "Donchian Ribbon": "Donch", "CM_Ult_MacD_MFT": "MACD",
    "GK Trend Ribbon": "GKTr", "Impulse Trend": "Impulse",
    "SQZMOM_LB": "SQZ", "Ichimoku": "Ichi", "ADX & DI": "ADX",
    "SuperTrend": "SupTr", "UT Bot Alert": "UTBot", "Mansfield RS": "Mansf",
    "DEMA": "DEMA", "GMMA": "GMMA", "WT_LB": "WT", "OBVOSC_LB": "OBVOsc",
    "TuTCI": "TuTCI", "RSI Strength & Consolidation Zones (Zeiierman)": "Zeiier",
    "Volume + MA20": "Vol>MA", "Breakout Targets": "BrkTgt",
}


def _s(kpi: str) -> str:
    return KPI_SHORT.get(kpi, kpi[:8])


def _sl(kpis, sep=" + "):
    return sep.join(_s(k) for k in kpis) if kpis else "—"


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, pc = df["High"], df["Low"], df["Close"].shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()


# ── Simulation result ─────────────────────────────────────────────────────

@dataclass
class FR:
    kpis: List[str]
    vol: bool
    n: int
    hr: float
    avg_ret: float
    med_ret: float
    worst: float
    pf: float
    atr_pct: float
    strict_pct: float
    reset_pct: float
    maxh_pct: float
    avg_hold: float
    max_hold: int
    total_ret: float
    resets_per_trade: float
    exit_version: str


def _build_fr(kpis, vol, rets, ex, holds, resets_list, version) -> Optional[FR]:
    n = len(rets)
    if n < 3:
        return None
    hr = sum(1 for r in rets if r > 0) / n * 100
    ar = float(np.mean(rets))
    mr = float(np.median(rets))
    w = min(rets)
    wi = sum(r for r in rets if r > 0)
    lo = abs(sum(r for r in rets if r <= 0))
    pf = wi / lo if lo > 0 else 999.0
    te = sum(ex.values())
    return FR(
        kpis, vol, n, hr, ar, mr, w, pf,
        ex.get("atr", 0) / te * 100 if te else 0,
        ex.get("str", 0) / te * 100 if te else 0,
        ex.get("reset_exit", 0) / te * 100 if te else 0,
        ex.get("mh", 0) / te * 100 if te else 0,
        float(np.mean(holds)) if holds else 0,
        int(np.max(holds)) if holds else 0,
        float(np.sum(rets)),
        float(np.mean(resets_list)) if resets_list else 0,
        version,
    )


# ── Exit Flow v3 (baseline) ──────────────────────────────────────────────

def sim_v3(data, kpis, T, M, K, vol=False, mn=3):
    rets, holds, resets_list = [], [], []
    ex = {"atr": 0, "len": 0, "str": 0, "mh": 0}
    for sym, df in data.items():
        if df.empty:
            continue
        sm = compute_kpi_state_map(df)
        if any(k not in sm for k in kpis):
            continue
        ab = pd.Series(True, index=df.index)
        for kpi in kpis:
            ab &= (sm[kpi] == STATE_BULL)
        sig = ab.astype(bool)
        if vol:
            if "Vol_gt_MA20" in df.columns:
                sig &= df["Vol_gt_MA20"].fillna(False).astype(bool)
        if sig.sum() == 0:
            continue
        si = int(len(df) * IS_FRACTION)
        cl = df["Close"].to_numpy(float)
        at = compute_atr(df, ATR_PERIOD).to_numpy(float)
        nk = len(kpis)
        sd = sig[df.index >= df.index[si]]
        sd = sd[sd].index
        i = 0
        while i < len(sd):
            ei = df.index.get_loc(sd[i])
            ep = float(cl[ei])
            if ep <= 0:
                i += 1; continue
            stop = ep - K * at[ei] if at[ei] > 0 else -np.inf
            xi, reason = None, "mh"
            for j in range(ei + 1, min(ei + M + 1, len(df))):
                bars = j - ei
                c = float(cl[j])
                if c < stop:
                    xi, reason = j, "atr"; break
                nb = sum(1 for kk in kpis if kk in sm and j < len(sm[kk]) and int(sm[kk].iloc[j]) != STATE_BULL)
                if bars <= T:
                    if nb >= nk:
                        xi, reason = j, "len"; break
                else:
                    if nb >= 2:
                        xi, reason = j, "str"; break
            if xi is None:
                xi = min(ei + M, len(df) - 1)
            h = xi - ei
            if h > 0:
                rets.append((float(cl[xi]) - ep) / ep * 100)
                ex[reason] += 1
                holds.append(h)
                resets_list.append(0)
            ni = i + 1
            while ni < len(sd) and df.index.get_loc(sd[ni]) <= xi:
                ni += 1
            i = ni
    if len(rets) < mn:
        return None
    return _build_fr(kpis, vol, rets, ex, holds, resets_list, "v3")


# ── Exit Flow v4 (Option C + ATR reset) ──────────────────────────────────

def sim_v4(data, kpis, T, M, K, vol=False, mn=3):
    rets, holds, resets_list = [], [], []
    ex = {"atr": 0, "len": 0, "str": 0, "reset_exit": 0, "mh": 0}
    for sym, df in data.items():
        if df.empty:
            continue
        sm = compute_kpi_state_map(df)
        if any(k not in sm for k in kpis):
            continue
        ab = pd.Series(True, index=df.index)
        for kpi in kpis:
            ab &= (sm[kpi] == STATE_BULL)
        sig = ab.astype(bool)
        if vol:
            if "Vol_gt_MA20" in df.columns:
                sig &= df["Vol_gt_MA20"].fillna(False).astype(bool)
        if sig.sum() == 0:
            continue
        si = int(len(df) * IS_FRACTION)
        cl = df["Close"].to_numpy(float)
        at = compute_atr(df, ATR_PERIOD).to_numpy(float)
        nk = len(kpis)
        sd = sig[df.index >= df.index[si]]
        sd = sd[sd].index
        i = 0
        while i < len(sd):
            ei = df.index.get_loc(sd[i])
            ep = float(cl[ei])
            if ep <= 0:
                i += 1; continue
            stop_price = ep
            stop = stop_price - K * at[ei] if at[ei] > 0 else -np.inf
            xi, reason = None, "mh"
            n_resets = 0
            bars_since_reset = 0
            j = ei + 1
            while j < min(ei + MAX_HOLD_HARD_CAP + 1, len(df)):
                bars_since_reset += 1
                c = float(cl[j])
                total_bars = j - ei

                if c < stop:
                    xi, reason = j, "atr"; break

                nb = sum(1 for kk in kpis if kk in sm and j < len(sm[kk]) and int(sm[kk].iloc[j]) != STATE_BULL)
                if total_bars <= T:
                    if nb >= nk:
                        xi, reason = j, "len"; break
                else:
                    if nb >= 2:
                        xi, reason = j, "str"; break

                if bars_since_reset >= M:
                    all_bull = (nb == 0)
                    if all_bull:
                        n_resets += 1
                        stop_price = c
                        stop = stop_price - K * at[j] if j < len(at) and at[j] > 0 else stop
                        bars_since_reset = 0
                    else:
                        xi, reason = j, "reset_exit"; break
                j += 1

            if xi is None:
                xi = min(j, len(df) - 1)
            h = xi - ei
            if h > 0:
                rets.append((float(cl[xi]) - ep) / ep * 100)
                ex[reason] += 1
                holds.append(h)
                resets_list.append(n_resets)
            ni = i + 1
            while ni < len(sd) and df.index.get_loc(sd[ni]) <= xi:
                ni += 1
            i = ni
    if len(rets) < mn:
        return None
    return _build_fr(kpis, vol, rets, ex, holds, resets_list, "v4")


# ── Screen with HR floor ─────────────────────────────────────────────────

@dataclass
class SA:
    close: np.ndarray
    atr: np.ndarray
    fwd: np.ndarray
    valid: np.ndarray
    bulls: Dict[str, np.ndarray]
    vol_ok: np.ndarray
    n_stocks: int
    kpi_cov: Dict[str, float]


def build_sa(data, kpis, H):
    cc, aa, ff, vv = [], [], [], []
    kb = {k: [] for k in kpis}
    ka = {k: 0 for k in kpis}
    ns = 0
    for sym, df in data.items():
        if df.empty:
            continue
        si = int(len(df) * IS_FRACTION)
        oos = df.iloc[si:]
        if len(oos) < 20:
            continue
        c = oos["Close"].to_numpy(float)
        n = len(oos)
        a_full = compute_atr(df, ATR_PERIOD)
        a_oos = a_full.iloc[si:].to_numpy(float)
        fwd = np.full(n, np.nan)
        for i in range(n - H):
            if c[i] > 0:
                fwd[i] = (c[i + H] - c[i]) / c[i] * 100
        vf = np.ones(n, dtype=bool)
        if "Vol_gt_MA20" in df.columns:
            vf = df["Vol_gt_MA20"].iloc[si:].fillna(False).astype(bool).to_numpy()
        sm = compute_kpi_state_map(df)
        for kpi in kpis:
            if kpi in sm:
                kb[kpi].append((sm[kpi] == STATE_BULL).to_numpy(bool)[si:])
                if (sm[kpi] != STATE_NA).any():
                    ka[kpi] += 1
            else:
                kb[kpi].append(np.zeros(n, dtype=bool))
        cc.append(c); aa.append(a_oos); ff.append(fwd); vv.append(vf)
        ns += 1
    if not cc:
        e = np.array([])
        return SA(e, e, e, np.array([], dtype=bool), {}, np.array([], dtype=bool), 0, {})
    fc = np.concatenate(ff)
    return SA(
        np.concatenate(cc), np.concatenate(aa), fc,
        ~np.isnan(fc), {k: np.concatenate(v) for k, v in kb.items()},
        np.concatenate(vv), ns, {k: ka[k] / ns if ns else 0 for k in kpis},
    )


SRec = namedtuple("SRec", "kpis vol n avg_ret hr total_pnl")


def screen_hr65(sa, k, kpis, mt, hr_floor=HR_FLOOR):
    avail = [kpi for kpi in kpis if sa.kpi_cov.get(kpi, 0) >= MIN_KPI_COVERAGE]
    if len(avail) < k:
        return []
    results = []
    for combo in combinations(avail, k):
        m = sa.bulls[combo[0]].copy()
        for kpi in combo[1:]:
            m &= sa.bulls[kpi]
        m &= sa.valid
        for use_vol in [False, True]:
            mask = m & sa.vol_ok if use_vol else m
            n = int(mask.sum())
            if n < mt:
                continue
            rets = sa.fwd[mask]
            hr = float(np.sum(rets > 0) / n) * 100
            if hr < hr_floor:
                continue
            ar = float(np.mean(rets))
            tr = float(np.sum(rets))
            results.append(SRec(list(combo), use_vol, n, ar, hr, tr))
    return results


# ── Charts ───────────────────────────────────────────────────────────────

def chart_v3_vs_v4(v3_results, v4_results, tf, out):
    """Side-by-side comparison of v3 vs v4 on current combos."""
    labels, v3_vals, v4_vals = [], {}, {}
    metrics = ["total_ret", "avg_ret", "hr", "avg_hold", "n", "pf"]
    metric_labels = ["Total P&L (%)", "Avg Return (%)", "Hit Rate (%)",
                     "Avg Hold (bars)", "Trade Count", "Profit Factor"]

    for ck in ["C3", "C4", "C5"]:
        if ck in v3_results and ck in v4_results:
            labels.append(ck)
            v3_vals[ck] = v3_results[ck]
            v4_vals[ck] = v4_results[ck]

    if not labels:
        return
    fig, axes = plt.subplots(2, 3, figsize=(22, 12))
    axes = axes.flatten()
    for mi, (attr, ylabel) in enumerate(zip(metrics, metric_labels)):
        ax = axes[mi]
        x = np.arange(len(labels))
        v3v = [getattr(v3_vals[l], attr) for l in labels]
        v4v = [getattr(v4_vals[l], attr) for l in labels]
        ax.bar(x - 0.18, v3v, 0.35, color="#ef5350", label="Exit v3", edgecolor="white", linewidth=0.5)
        ax.bar(x + 0.18, v4v, 0.35, color="#66bb6a", label="Exit v4", edgecolor="white", linewidth=0.5)
        for i, (a, b) in enumerate(zip(v3v, v4v)):
            if a != 0:
                pct = (b - a) / abs(a) * 100
                ax.annotate(f"{pct:+.0f}%", (i + 0.18, b), fontsize=8,
                            ha="center", va="bottom", color="#aaffaa")
        ax.set_xticks(x); ax.set_xticklabels(labels)
        ax.set_ylabel(ylabel); ax.legend(fontsize=8)
        ax.grid(True, alpha=0.15, axis="y")
    fig.suptitle(f"{tf} — Exit Flow v3 vs v4 (Current Combos, {v3_results.get('n_stocks', '?')} stocks)",
                 fontsize=14, fontweight="bold")
    plt.tight_layout()
    fig.savefig(out / "v3_vs_v4.png"); plt.close(fig)
    print(f"    Saved v3_vs_v4.png")


def chart_v4_exit_detail(v4_results, tf, out):
    """Pie/bar chart showing exit reason breakdown and reset distribution."""
    labels, data_rows = [], []
    for ck in ["C3", "C4", "C5"]:
        if ck in v4_results:
            labels.append(ck)
            data_rows.append(v4_results[ck])
    if not labels:
        return
    fig, axes = plt.subplots(1, len(labels), figsize=(7 * len(labels), 6))
    if len(labels) == 1:
        axes = [axes]
    colors = {"ATR Stop": "#ef5350", "Strict Inv.": "#ff7043",
              "Reset Exit": "#ffa726", "Max Hold": "#bdbdbd", "Lenient": "#42a5f5"}
    for ax, ck, fr in zip(axes, labels, data_rows):
        slices = {
            "ATR Stop": fr.atr_pct, "Strict Inv.": fr.strict_pct,
            "Reset Exit": fr.reset_pct, "Max Hold": fr.maxh_pct,
        }
        lenient = 100 - sum(slices.values())
        if lenient > 0.5:
            slices["Lenient"] = lenient
        vals = [v for v in slices.values() if v > 0]
        labs = [k for k, v in slices.items() if v > 0]
        cols = [colors.get(l, "#888") for l in labs]
        ax.pie(vals, labels=[f"{l}\n{v:.0f}%" for l, v in zip(labs, vals)],
               colors=cols, startangle=90, textprops={"fontsize": 9, "color": "white"})
        ax.set_title(f"{ck}: n={fr.n} | AvgHold={fr.avg_hold:.0f} | MaxHold={fr.max_hold}\n"
                     f"Resets/trade={fr.resets_per_trade:.1f}", fontsize=10)
    fig.suptitle(f"{tf} — Exit Flow v4 Detail", fontsize=14, fontweight="bold")
    plt.tight_layout()
    fig.savefig(out / "v4_exit_detail.png"); plt.close(fig)
    print(f"    Saved v4_exit_detail.png")


def chart_hr65_results(results_by_ck, current_by_ck, tf, out):
    if not results_by_ck:
        return
    fig, axes = plt.subplots(1, 3, figsize=(24, 9))
    metrics = [("total_ret", "Total P&L (%)"), ("avg_ret", "Avg Return (%)"), ("hr", "Hit Rate (%)")]
    for ax, (attr, ylabel) in zip(axes, metrics):
        cks = sorted(results_by_ck.keys())
        x = np.arange(len(cks))
        best_vals = [getattr(results_by_ck[ck], attr) if results_by_ck.get(ck) else 0 for ck in cks]
        cur_vals = [getattr(current_by_ck[ck], attr) if current_by_ck.get(ck) else 0 for ck in cks]
        ax.bar(x - 0.2, cur_vals, 0.38, color="#42a5f5", label="Current", edgecolor="white", linewidth=0.5)
        ax.bar(x + 0.2, best_vals, 0.38, color="#66bb6a", label="HR≥65% Best", edgecolor="white", linewidth=0.5)
        for i, (a, b) in enumerate(zip(cur_vals, best_vals)):
            if a != 0:
                pct = (b - a) / abs(a) * 100
                ax.annotate(f"{pct:+.0f}%", (i + 0.2, b), fontsize=9, ha="center", va="bottom", color="#aaffaa")
        ax.set_xticks(x); ax.set_xticklabels(cks); ax.set_ylabel(ylabel)
        ax.legend(fontsize=9); ax.grid(True, alpha=0.15, axis="y")
    fig.suptitle(f"{tf} — HR≥65% P&L-Optimal vs Current (Exit Flow v4)",
                 fontsize=14, fontweight="bold")
    plt.tight_layout()
    fig.savefig(out / "hr65_vs_current.png"); plt.close(fig)
    print(f"    Saved hr65_vs_current.png")


def chart_weighted_pnl(pnl_data, tf, out):
    """Compare unweighted vs weighted (1x/1.5x/2x) P&L."""
    if not pnl_data:
        return
    fig, ax = plt.subplots(figsize=(14, 8))
    labels = list(pnl_data.keys())
    uw = [pnl_data[l]["unweighted"] for l in labels]
    wt = [pnl_data[l]["weighted"] for l in labels]
    x = np.arange(len(labels))
    ax.bar(x - 0.2, uw, 0.38, color="#42a5f5", label="Unweighted (all 1x)", edgecolor="white", linewidth=0.5)
    ax.bar(x + 0.2, wt, 0.38, color="#66bb6a", label="Weighted (1x/1.5x/2x)", edgecolor="white", linewidth=0.5)
    for i, (a, b) in enumerate(zip(uw, wt)):
        diff = b - a
        ax.annotate(f"{diff:+.0f}%", (i + 0.2, b), fontsize=10, ha="center", va="bottom", color="#aaffaa")
    ax.set_xticks(x); ax.set_xticklabels(labels); ax.set_ylabel("Total P&L (%)")
    ax.legend(fontsize=10); ax.grid(True, alpha=0.15, axis="y")
    ax.set_title(f"{tf} — Weighted vs Unweighted P&L (HR≥65%, Exit Flow v4)",
                 fontsize=13, fontweight="bold")
    plt.tight_layout()
    fig.savefig(out / "weighted_pnl.png"); plt.close(fig)
    print(f"    Saved weighted_pnl.png")


def chart_final_summary(all_data, out):
    rows = []
    for tf in ["4H", "1D", "1W"]:
        for ck in ["C3", "C4", "C5"]:
            d = all_data.get(tf, {}).get(ck)
            if d:
                rows.append(d)
    if not rows:
        return
    fig, ax = plt.subplots(figsize=(28, max(4, len(rows) * 0.7 + 3)))
    ax.axis("off")
    hdr = ["TF", "Tier", "Size", "KPIs", "n", "HR%", "Avg%", "PnL% (1x)",
           "PnL% (wtd)", "PF", "AvgHold", "MaxHold", "Resets/t"]
    ct, cc = [], []
    for i, r in enumerate(rows):
        wt = {"C3": "1x", "C4": "1.5x", "C5": "2x"}[r["ck"]]
        ct.append([
            r["tf"], r["ck"], wt, r["kpis_short"],
            str(r["n"]), f"{r['hr']:.0f}", f"{r['avg_ret']:+.2f}",
            f"{r['total_ret']:+.0f}", f"{r['weighted_pnl']:+.0f}",
            f"{r['pf']:.1f}", f"{r['avg_hold']:.0f}", str(r["max_hold"]),
            f"{r['resets']:.1f}",
        ])
        bg = "#1e1e1e" if i % 2 == 0 else "#252525"
        cc.append([bg] * len(hdr))
    t = ax.table(cellText=ct, colLabels=hdr, loc="center", cellLoc="center")
    t.auto_set_font_size(False); t.set_fontsize(8); t.scale(1.0, 1.6)
    for (r, c), cell in t.get_celld().items():
        if r == 0:
            cell.set_facecolor("#333")
            cell.set_text_props(fontweight="bold", color="white")
        else:
            cell.set_facecolor(cc[r - 1][c])
            cell.set_text_props(color="white")
        cell.set_edgecolor("#444")
    ax.set_title("Phase 11 v7 — Final Recommendations: HR≥65%, Exit Flow v4, Weighted Sizing\n"
                 "(320 stocks, C3=1x, C4=1.5x, C5=2x)", fontsize=14, fontweight="bold", pad=25)
    plt.tight_layout()
    fig.savefig(out / "final_summary.png"); plt.close(fig)
    print(f"  Saved final_summary.png")


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    t0 = time.time()
    out_root = output_dir_for("all", "phase11v7")
    out_root.mkdir(parents=True, exist_ok=True)
    all_json: Dict[str, Any] = {}
    all_recs: Dict[str, Dict[str, Dict]] = {}

    for tf_key, tf_cfg in TIMEFRAME_CONFIGS.items():
        print(f"\n{'=' * 70}")
        print(f"  TIMEFRAME: {tf_key}")
        print(f"{'=' * 70}")

        data = load_data(ENRICHED_DIR, tf_key)
        n_stocks = len(data)
        print(f"  Loaded {n_stocks} stocks")
        p = V3_PARAMS[tf_key]
        T, M, K = p["T"], p["M"], p["K"]
        H = tf_cfg.default_horizon
        mt = MIN_TRADES.get(tf_key, 15)

        tf_out = output_dir_for(tf_key, "phase11v7")
        tf_out.mkdir(parents=True, exist_ok=True)

        current_combos = COMBO_DEFINITIONS.get(tf_key, COMBO_DEFINITIONS["1W"])
        tf_json: Dict[str, Any] = {}
        all_recs[tf_key] = {}

        # ── PART 1: Exit Flow v3 vs v4 on current combos ─────────
        print(f"\n  PART 1: Exit Flow v3 vs v4 comparison")
        v3_res, v4_res = {"n_stocks": n_stocks}, {"n_stocks": n_stocks}
        for k in [3, 4, 5]:
            ck = f"C{k}"
            cur_kpis = current_combos.get(f"combo_{k}", [])
            if not cur_kpis:
                continue
            r3 = sim_v3(data, cur_kpis, T, M, K, mn=3)
            r4 = sim_v4(data, cur_kpis, T, M, K, mn=3)
            if r3:
                v3_res[ck] = r3
                print(f"    {ck} v3: n={r3.n:4d} HR={r3.hr:.0f}% Avg={r3.avg_ret:+.2f}% "
                      f"PnL={r3.total_ret:+.0f}% AvgH={r3.avg_hold:.0f} MaxH={r3.max_hold}")
            if r4:
                v4_res[ck] = r4
                print(f"    {ck} v4: n={r4.n:4d} HR={r4.hr:.0f}% Avg={r4.avg_ret:+.2f}% "
                      f"PnL={r4.total_ret:+.0f}% AvgH={r4.avg_hold:.0f} MaxH={r4.max_hold} "
                      f"Resets/t={r4.resets_per_trade:.1f}")
                if r3:
                    pnl_lift = r4.total_ret - r3.total_ret
                    print(f"         Δ PnL = {pnl_lift:+.0f}% ({pnl_lift/abs(r3.total_ret)*100:+.0f}% lift)")

        chart_v3_vs_v4(v3_res, v4_res, tf_key, tf_out)
        chart_v4_exit_detail(v4_res, tf_key, tf_out)
        tf_json["v3_vs_v4"] = {}
        for ck in ["C3", "C4", "C5"]:
            if ck in v3_res and ck in v4_res:
                tf_json["v3_vs_v4"][ck] = {
                    "v3_pnl": round(v3_res[ck].total_ret),
                    "v4_pnl": round(v4_res[ck].total_ret),
                    "v4_avg_hold": round(v4_res[ck].avg_hold, 1),
                    "v4_max_hold": v4_res[ck].max_hold,
                    "v4_resets": round(v4_res[ck].resets_per_trade, 2),
                }

        # ── PART 2: Screen with HR >= 65%, simulate with v4 ──────
        print(f"\n  PART 2: HR≥65% screening + Exit Flow v4 simulation")
        print(f"  Building screen arrays (H={H})...")
        sa = build_sa(data, ALL_KPIS, H)
        print(f"  {sa.n_stocks} stocks in arrays")

        hr65_best: Dict[str, FR] = {}
        hr65_current: Dict[str, FR] = {}
        pnl_data: Dict[str, Dict] = {}

        for k in [3, 4, 5]:
            ck = f"C{k}"
            cur_kpis = current_combos.get(f"combo_{k}", [])

            print(f"\n    {ck}: Screening (HR≥{HR_FLOOR}%, min {mt} trades)...")
            t1 = time.time()
            sr_list = screen_hr65(sa, k, ALL_KPIS, mt, HR_FLOOR)
            print(f"    {len(sr_list):,} combos passed ({time.time()-t1:.1f}s)")

            if not sr_list:
                print(f"    No combos found, skipping")
                continue

            sr_list.sort(key=lambda r: r.total_pnl, reverse=True)
            top = sr_list[:SIM_TOP_N]
            print(f"    Top screen: {_sl(top[0].kpis)} n={top[0].n} HR={top[0].hr:.0f}% PnL={top[0].total_pnl:+.0f}%")

            print(f"    Simulating top {len(top)} with Exit Flow v4...")
            t2 = time.time()
            sim_results: List[FR] = []
            for ci, sr in enumerate(top):
                fr = sim_v4(data, sr.kpis, T, M, K, vol=sr.vol, mn=mt)
                if fr and fr.hr >= HR_FLOOR:
                    sim_results.append(fr)
                if (ci + 1) % 15 == 0:
                    print(f"      {ci+1}/{len(top)} ({time.time()-t2:.0f}s)")
            sim_results.sort(key=lambda r: r.total_ret, reverse=True)
            print(f"    {len(sim_results)} passed HR≥{HR_FLOOR}% after sim ({time.time()-t2:.0f}s)")

            cur_v4 = None
            if cur_kpis:
                cur_v4 = sim_v4(data, cur_kpis, T, M, K, mn=3)
                if cur_v4:
                    hr65_current[ck] = cur_v4
                    print(f"    Current v4: n={cur_v4.n} HR={cur_v4.hr:.0f}% Avg={cur_v4.avg_ret:+.2f}% "
                          f"PnL={cur_v4.total_ret:+.0f}%")

            if sim_results:
                best = sim_results[0]
                hr65_best[ck] = best
                vl = " +vol" if best.vol else ""
                print(f"    Best HR≥65% v4: {_sl(best.kpis)}{vl}")
                print(f"      n={best.n} HR={best.hr:.0f}% Avg={best.avg_ret:+.2f}% "
                      f"PnL={best.total_ret:+.0f}% AvgH={best.avg_hold:.0f} MaxH={best.max_hold} "
                      f"Resets/t={best.resets_per_trade:.1f}")

                weight = {"C3": 1.0, "C4": 1.5, "C5": 2.0}[ck]
                weighted_pnl = best.total_ret * weight
                pnl_data[ck] = {"unweighted": best.total_ret, "weighted": weighted_pnl}

                all_recs[tf_key][ck] = {
                    "tf": tf_key, "ck": ck,
                    "kpis_short": _sl(best.kpis),
                    "kpis": best.kpis, "vol": best.vol,
                    "n": best.n, "hr": best.hr,
                    "avg_ret": best.avg_ret,
                    "total_ret": best.total_ret,
                    "weighted_pnl": weighted_pnl,
                    "pf": best.pf, "avg_hold": best.avg_hold,
                    "max_hold": best.max_hold,
                    "resets": best.resets_per_trade,
                }

                tf_json[ck] = {
                    "best_kpis": best.kpis, "best_vol": best.vol,
                    "n": best.n, "hr": round(best.hr, 1),
                    "avg_ret": round(best.avg_ret, 2),
                    "total_pnl_1x": round(best.total_ret),
                    "total_pnl_wtd": round(weighted_pnl),
                    "pf": round(best.pf, 1),
                    "avg_hold": round(best.avg_hold, 1),
                    "max_hold": best.max_hold,
                    "resets_per_trade": round(best.resets_per_trade, 2),
                    "screened": len(sr_list),
                    "simulated": len(sim_results),
                }

        chart_hr65_results(hr65_best, hr65_current, tf_key, tf_out)
        chart_weighted_pnl(pnl_data, tf_key, tf_out)
        all_json[tf_key] = tf_json

    # ── Final summary ────────────────────────────────────────────
    chart_final_summary(all_recs, out_root)
    jp = out_root / "phase11v7_results.json"
    jp.write_text(json.dumps(all_json, indent=2, default=str))
    (out_root / "phase11v7_recommendations.json").write_text(
        json.dumps({tf: {ck: v for ck, v in cks.items()} for tf, cks in all_recs.items()},
                   indent=2, default=str))

    print(f"\n{'=' * 70}")
    print(f"  FINAL SUMMARY")
    print(f"{'=' * 70}")
    for tf in ["4H", "1D", "1W"]:
        print(f"\n  {tf}:")
        total_uw, total_wt = 0, 0
        for ck in ["C3", "C4", "C5"]:
            rec = all_recs.get(tf, {}).get(ck)
            if rec:
                w = {"C3": "1x", "C4": "1.5x", "C5": "2x"}[ck]
                vl = " +vol" if rec["vol"] else ""
                print(f"    {ck} ({w}): {rec['kpis_short']}{vl} — "
                      f"n={rec['n']} HR={rec['hr']:.0f}% Avg={rec['avg_ret']:+.2f}% "
                      f"PnL(1x)={rec['total_ret']:+.0f}% PnL(wtd)={rec['weighted_pnl']:+.0f}% "
                      f"AvgH={rec['avg_hold']:.0f} MaxH={rec['max_hold']} "
                      f"Resets/t={rec['resets']:.1f}")
                total_uw += rec["total_ret"]
                total_wt += rec["weighted_pnl"]
        print(f"    ── Combined: PnL(1x)={total_uw:+.0f}% | PnL(weighted)={total_wt:+.0f}%")

    print(f"\n  Saved to {out_root}")
    print(f"  Done in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

"""
Phase 11 v5 — P&L-Optimized Entry Combos

Objective: maximize total cumulative return (n_trades * avg_return).

Key questions answered:
  1. BB30 vs NW Envelop (MAE/STD) — which breakout KPI maximises P&L?
  2. Best C3/C4/C5 for total P&L across all timeframes
  3. Does volume filter help or hurt total P&L?
  4. Uses Exit Flow v3 (ATR cut-off + combo invalidation) for simulation
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
TOP_N_SCREEN = 80
MIN_TRADES = {"4H": 10, "1D": 10, "1W": 5}

BREAKOUT_KPIS = ["BB 30", "Nadaraya-Watson Envelop (MAE)", "Nadaraya-Watson Envelop (STD)"]

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
    "Nadaraya-Watson Smoother": "NW Smooth", "Nadaraya-Watson Envelop (MAE)": "NWE MAE",
    "Nadaraya-Watson Envelop (STD)": "NWE STD", "BB 30": "BB 30",
    "cRSI": "cRSI", "SR Breaks": "SR Brk", "Stoch_MTM": "Stoch",
    "CM_P-SAR": "P-SAR", "MA Ribbon": "MA Rib", "Madrid Ribbon": "Madrid",
    "Donchian Ribbon": "Donchian", "CM_Ult_MacD_MFT": "MACD",
    "GK Trend Ribbon": "GK Trend", "Impulse Trend": "Impulse",
    "SQZMOM_LB": "SQZ Mom", "Ichimoku": "Ichimoku", "ADX & DI": "ADX",
    "SuperTrend": "SuperTr", "UT Bot Alert": "UT Bot", "Mansfield RS": "Mansf",
    "DEMA": "DEMA", "GMMA": "GMMA", "WT_LB": "WT", "OBVOSC_LB": "OBV Osc",
    "TuTCI": "TuTCI", "RSI Strength & Consolidation Zones (Zeiierman)": "Zeiier",
    "Volume + MA20": "Vol>MA", "Breakout Targets": "BrkTgt",
}


def _s(kpi: str) -> str:
    return KPI_SHORT.get(kpi, kpi[:10])


def _sl(kpis: List[str], sep: str = " + ") -> str:
    return sep.join(_s(k) for k in kpis) if kpis else "—"


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, pc = df["High"], df["Low"], df["Close"].shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()


# ── Stage 1: Vectorised screen optimised for total P&L ───────────────────

@dataclass
class SA:
    close: np.ndarray
    low_min_fwd: np.ndarray
    atr: np.ndarray
    fwd: np.ndarray
    valid: np.ndarray
    bulls: Dict[str, np.ndarray]
    vol_ok: np.ndarray
    n_stocks: int
    kpi_cov: Dict[str, float]


def build_sa(data: Dict[str, pd.DataFrame], kpis: List[str], H: int, M: int) -> SA:
    cc, lm, aa, ff, vv = [], [], [], [], []
    kb: Dict[str, List[np.ndarray]] = {k: [] for k in kpis}
    ka: Dict[str, int] = {k: 0 for k in kpis}
    ns = 0
    for sym, df in data.items():
        if df.empty:
            continue
        si = int(len(df) * IS_FRACTION)
        oos = df.iloc[si:]
        if len(oos) < M + 5:
            continue
        c = oos["Close"].to_numpy(float)
        lo = oos["Low"].to_numpy(float)
        n = len(oos)
        lmf = np.full(n, np.nan)
        for i in range(n - 1):
            lmf[i] = np.nanmin(lo[i + 1:min(i + M + 1, n)])
        a_full = compute_atr(df, ATR_PERIOD)
        a_oos = a_full.iloc[si:].to_numpy(float)
        fwd = np.full(n, np.nan)
        for i in range(n - H):
            if c[i] > 0:
                fwd[i] = (c[i + H] - c[i]) / c[i] * 100
        if "Vol_gt_MA20" in df.columns:
            vf = df["Vol_gt_MA20"].iloc[si:].fillna(False).astype(bool).to_numpy()
        else:
            vf = np.ones(n, dtype=bool)
        sm = compute_kpi_state_map(df)
        for kpi in kpis:
            if kpi in sm:
                kb[kpi].append((sm[kpi] == STATE_BULL).to_numpy(bool)[si:])
                if (sm[kpi] != STATE_NA).any():
                    ka[kpi] += 1
            else:
                kb[kpi].append(np.zeros(n, dtype=bool))
        cc.append(c); lm.append(lmf); aa.append(a_oos); ff.append(fwd); vv.append(vf)
        ns += 1

    if not cc:
        e = np.array([])
        return SA(e, e, e, e, np.array([], dtype=bool), {}, np.array([], dtype=bool), 0, {})
    fc = np.concatenate(ff)
    return SA(
        np.concatenate(cc), np.concatenate(lm), np.concatenate(aa), fc,
        ~np.isnan(fc), {k: np.concatenate(v) for k, v in kb.items()},
        np.concatenate(vv), ns, {k: ka[k] / ns if ns else 0 for k in kpis},
    )


@dataclass
class SR:
    kpis: List[str]
    n: int
    avg_ret: float
    hr: float
    atr_pct: float
    total_ret: float
    vol: bool


def screen_pnl(sa: SA, k: int, kpis: List[str], K: float, mt: int,
               vol: bool = False, top_n: int = TOP_N_SCREEN) -> List[SR]:
    avail = [kpi for kpi in kpis if sa.kpi_cov.get(kpi, 0) >= MIN_KPI_COVERAGE]
    if len(avail) < k:
        return []
    hs = sa.close - K * sa.atr
    ah = sa.low_min_fwd < hs
    heap: list = []
    ctr = 0
    for combo in combinations(avail, k):
        m = sa.bulls[combo[0]].copy()
        for kpi in combo[1:]:
            m &= sa.bulls[kpi]
        m &= sa.valid
        if vol:
            m &= sa.vol_ok
        n = int(m.sum())
        if n < mt:
            continue
        rets = sa.fwd[m]
        ar = float(np.mean(rets))
        tr = float(np.sum(rets))
        hr = float(np.sum(rets > 0) / n) * 100
        ap = float(np.sum(ah[m]) / n) * 100
        score = tr
        if len(heap) < top_n:
            heapq.heappush(heap, (score, ctr, combo, n, ar, hr, ap, tr))
        elif score > heap[0][0]:
            heapq.heapreplace(heap, (score, ctr, combo, n, ar, hr, ap, tr))
        ctr += 1
    heap.sort(reverse=True)
    return [SR(list(c), n_, ar_, hr_, ap_, tr_, vol)
            for _, _, c, n_, ar_, hr_, ap_, tr_ in heap]


# ── Stage 2: Full v3 exit simulation ─────────────────────────────────────

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
    maxh_pct: float
    avg_hold: float
    total_ret: float


def sim_v3(data: Dict[str, pd.DataFrame], kpis: List[str],
           T: int, M: int, K: float, vol: bool = False, mn: int = 3) -> Optional[FR]:
    rets: List[float] = []
    ex: Dict[str, int] = {"atr": 0, "len": 0, "str": 0, "mh": 0}
    holds: List[int] = []
    for sym, df in data.items():
        if df.empty:
            continue
        sm = compute_kpi_state_map(df)
        if any(kk not in sm for kk in kpis):
            continue
        ab = pd.Series(True, index=df.index)
        for kpi in kpis:
            ab = ab & (sm[kpi] == STATE_BULL)
        sig = ab.astype(bool)
        if vol:
            if "Vol_gt_MA20" in df.columns:
                sig = sig & df["Vol_gt_MA20"].fillna(False).astype(bool)
            elif "Volume" in df.columns and "Vol_MA20" in df.columns:
                sig = sig & (df["Volume"] > df["Vol_MA20"])
        if sig.sum() == 0:
            continue
        si = int(len(df) * IS_FRACTION)
        ts = df.index[si]
        cl = df["Close"].to_numpy(float)
        at = compute_atr(df, ATR_PERIOD).to_numpy(float)
        nk = len(kpis)
        sd = sig[df.index >= ts]
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
            xp = float(cl[xi])
            ret = (xp - ep) / ep * 100
            h = xi - ei
            if h > 0:
                rets.append(ret); ex[reason] += 1; holds.append(h)
            ni = i + 1
            while ni < len(sd) and df.index.get_loc(sd[ni]) <= xi:
                ni += 1
            i = ni
    n = len(rets)
    if n < mn:
        return None
    hr = sum(1 for r in rets if r > 0) / n * 100
    ar = float(np.mean(rets))
    mr = float(np.median(rets))
    w = min(rets)
    wi = sum(r for r in rets if r > 0)
    lo = abs(sum(r for r in rets if r <= 0))
    pf = wi / lo if lo > 0 else 999.0
    te = sum(ex.values())
    return FR(kpis, vol, n, hr, ar, mr, w, pf,
              ex["atr"] / te * 100 if te else 0,
              ex["str"] / te * 100 if te else 0,
              ex["mh"] / te * 100 if te else 0,
              float(np.mean(holds)) if holds else 0,
              float(np.sum(rets)))


# ── Charts ───────────────────────────────────────────────────────────────

def chart_pnl_frontier(results: List[FR], current: Optional[FR],
                       k: int, tf: str, out: Path):
    if not results:
        return
    fig, axes = plt.subplots(1, 2, figsize=(22, 10))
    ax = axes[0]
    ns = [r.n for r in results]
    tots = [r.total_ret for r in results]
    hrs = [r.hr for r in results]
    sizes = [max(20, min(250, r.n * 0.8)) for r in results]
    sc = ax.scatter(ns, tots, s=sizes, c=hrs, cmap="RdYlGn",
                    edgecolors="white", linewidth=0.6, alpha=0.85, zorder=5)
    for i, r in enumerate(results[:6]):
        vl = " +vol" if r.vol else ""
        ax.annotate(f"{_sl(r.kpis, ', ')}{vl}\nPnL={r.total_ret:+.0f}%",
                    (r.n, r.total_ret), fontsize=6.5, color="white", alpha=0.9,
                    xytext=(8, 4), textcoords="offset points",
                    bbox=dict(boxstyle="round,pad=0.2", facecolor="#333", alpha=0.7))
    if current:
        ax.scatter([current.n], [current.total_ret], s=250, marker="*", c="cyan",
                   edgecolors="white", linewidth=1.5, zorder=10,
                   label=f"Current (n={current.n}, PnL={current.total_ret:+.0f}%)")
        ax.legend(loc="lower right", fontsize=8)
    ax.set_xlabel("Number of Trades")
    ax.set_ylabel("Total Cumulative Return (%)")
    ax.set_title("Trades vs Total P&L (color = Hit Rate)")
    ax.grid(True, alpha=0.2)
    fig.colorbar(sc, ax=ax, label="Hit Rate (%)", shrink=0.8)

    ax2 = axes[1]
    avgs = [r.avg_ret for r in results]
    sc2 = ax2.scatter(avgs, tots, s=sizes, c=hrs, cmap="RdYlGn",
                      edgecolors="white", linewidth=0.6, alpha=0.85, zorder=5)
    for i, r in enumerate(results[:6]):
        vl = " +vol" if r.vol else ""
        ax2.annotate(f"{_sl(r.kpis, ', ')}{vl}\nn={r.n}",
                     (r.avg_ret, r.total_ret), fontsize=6.5, color="white", alpha=0.9,
                     xytext=(8, 4), textcoords="offset points",
                     bbox=dict(boxstyle="round,pad=0.2", facecolor="#333", alpha=0.7))
    if current:
        ax2.scatter([current.avg_ret], [current.total_ret], s=250, marker="*", c="cyan",
                    edgecolors="white", linewidth=1.5, zorder=10)
    ax2.set_xlabel("Avg Return per Trade (%)")
    ax2.set_ylabel("Total Cumulative Return (%)")
    ax2.set_title("Avg Return vs Total P&L")
    ax2.grid(True, alpha=0.2)

    fig.suptitle(f"{tf} C{k} — P&L Frontier", fontsize=14, fontweight="bold")
    plt.tight_layout()
    fig.savefig(out / f"pnl_frontier_C{k}.png")
    plt.close(fig)
    print(f"    Saved pnl_frontier_C{k}.png")


def chart_breakout_compare(rows: List[Dict], tf: str, out: Path):
    """Bar chart comparing BB30, NWE MAE, NWE STD across metrics."""
    if not rows:
        return
    fig, axes = plt.subplots(1, 4, figsize=(24, 8))
    bkpis = sorted(set(r["breakout"] for r in rows))
    colors = {"BB 30": "#66bb6a", "NWE MAE": "#42a5f5", "NWE STD": "#ab47bc"}
    metrics = [
        ("total_ret", "Total P&L (%)", axes[0]),
        ("n", "Trade Count", axes[1]),
        ("avg_ret", "Avg Return (%)", axes[2]),
        ("hr", "Hit Rate (%)", axes[3]),
    ]
    combos = sorted(set(r["base"] for r in rows))
    x = np.arange(len(combos))
    w = 0.8 / len(bkpis)
    for attr, ylabel, ax in metrics:
        for j, bk in enumerate(bkpis):
            vals = []
            for base in combos:
                match = [r for r in rows if r["base"] == base and r["breakout"] == bk]
                vals.append(match[0][attr] if match else 0)
            ax.bar(x + j * w - (len(bkpis) - 1) * w / 2, vals, w,
                   color=colors.get(bk, "#aaa"), label=bk, edgecolor="white", linewidth=0.5)
        ax.set_xticks(x)
        ax.set_xticklabels(combos, fontsize=8, rotation=15)
        ax.set_ylabel(ylabel)
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.2, axis="y")
    fig.suptitle(f"{tf} — BB30 vs NWE MAE vs NWE STD (same base KPIs)", fontsize=14, fontweight="bold")
    plt.tight_layout()
    fig.savefig(out / "breakout_comparison.png")
    plt.close(fig)
    print(f"    Saved breakout_comparison.png")


def chart_top_table(results: List[FR], current: Optional[FR],
                    k: int, tf: str, out: Path):
    if not results:
        return
    show = results[:12]
    if current:
        show = [current] + show
    fig, ax = plt.subplots(figsize=(22, max(4, len(show) * 0.65 + 2)))
    ax.axis("off")
    hdr = ["#", "KPIs", "Vol", "n", "HR%", "Avg%", "TotalPnL%", "Med%", "Worst%", "PF", "ATR%", "AvgH"]
    ct, cc = [], []
    for i, r in enumerate(show):
        cur = current and r is current
        ct.append([
            "CUR" if cur else str(i),
            _sl(r.kpis), "Y" if r.vol else "N", str(r.n),
            f"{r.hr:.0f}", f"{r.avg_ret:+.2f}", f"{r.total_ret:+.0f}",
            f"{r.med_ret:+.2f}", f"{r.worst:+.1f}", f"{r.pf:.1f}",
            f"{r.atr_pct:.1f}", f"{r.avg_hold:.0f}",
        ])
        bg = "#1a3a1a" if cur else ("#1e1e1e" if i % 2 == 0 else "#252525")
        cc.append([bg] * len(hdr))
    t = ax.table(cellText=ct, colLabels=hdr, loc="center", cellLoc="center")
    t.auto_set_font_size(False); t.set_fontsize(8); t.scale(1.0, 1.5)
    for (r, c), cell in t.get_celld().items():
        if r == 0:
            cell.set_facecolor("#333"); cell.set_text_props(fontweight="bold", color="white")
        else:
            cell.set_facecolor(cc[r - 1][c]); cell.set_text_props(color="white")
        cell.set_edgecolor("#444")
    ax.set_title(f"{tf} C{k} — Top P&L Entry Combos (Exit Flow v3)", fontsize=13, fontweight="bold", pad=20)
    plt.tight_layout()
    fig.savefig(out / f"top_pnl_C{k}.png")
    plt.close(fig); print(f"    Saved top_pnl_C{k}.png")


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    t0 = time.time()
    out_root = output_dir_for("all", "phase11v5")
    out_root.mkdir(parents=True, exist_ok=True)
    all_json: Dict[str, Any] = {}

    for tf_key, tf_cfg in TIMEFRAME_CONFIGS.items():
        print(f"\n{'=' * 60}")
        print(f"  TIMEFRAME: {tf_key}")
        print(f"{'=' * 60}")

        data = load_data(ENRICHED_DIR, tf_key)
        print(f"  Loaded {len(data)} stocks")
        p = V3_PARAMS.get(tf_key, V3_PARAMS["1D"])
        T, M, K = p["T"], p["M"], p["K"]
        H = tf_cfg.default_horizon
        mt = MIN_TRADES.get(tf_key, 10)

        tf_out = output_dir_for(tf_key, "phase11v5")
        tf_out.mkdir(parents=True, exist_ok=True)

        print(f"  Building screen arrays (H={H}, M={M}, min_trades={mt})...")
        sa = build_sa(data, ALL_KPIS, H, M)
        print(f"  {sa.n_stocks} stocks, {len(sa.close)} bars, vol_ok={sa.vol_ok.sum()}/{len(sa.vol_ok)} ({100*sa.vol_ok.sum()/max(1,len(sa.vol_ok)):.0f}%)")

        current_combos = COMBO_DEFINITIONS.get(tf_key, COMBO_DEFINITIONS["1W"])
        tf_json: Dict[str, Any] = {}

        for k in [3, 4, 5]:
            ck = f"combo_{k}"
            cur_kpis = current_combos.get(ck, [])

            print(f"\n  C{k}: Screening for max P&L (min {mt} trades)...")
            snv = screen_pnl(sa, k, ALL_KPIS, K, mt, vol=False)
            swv = screen_pnl(sa, k, ALL_KPIS, K, mt, vol=True)
            print(f"    {len(snv)} no-vol + {len(swv)} with-vol candidates")

            seen: set = set()
            to_sim: List[Tuple[List[str], bool]] = []
            for sr in snv + swv:
                key = (tuple(sorted(sr.kpis)), sr.vol)
                if key not in seen:
                    seen.add(key)
                    to_sim.append((sr.kpis, sr.vol))

            print(f"    Simulating {len(to_sim)} unique combos...")
            results: List[FR] = []
            for i, (kpis, vol) in enumerate(to_sim):
                fr = sim_v3(data, kpis, T, M, K, vol=vol, mn=mt)
                if fr:
                    results.append(fr)
                if (i + 1) % 20 == 0:
                    print(f"      {i + 1}/{len(to_sim)}")

            results.sort(key=lambda r: r.total_ret, reverse=True)

            cur = None
            if cur_kpis:
                cur = sim_v3(data, cur_kpis, T, M, K, vol=False, mn=3)
                cur_v = sim_v3(data, cur_kpis, T, M, K, vol=True, mn=3)
                if cur:
                    print(f"    Current: n={cur.n} HR={cur.hr:.0f}% Avg={cur.avg_ret:+.2f}% PnL={cur.total_ret:+.0f}%")
                if cur_v:
                    print(f"    Cur+Vol: n={cur_v.n} HR={cur_v.hr:.0f}% Avg={cur_v.avg_ret:+.2f}% PnL={cur_v.total_ret:+.0f}%")

            if results:
                best = results[0]
                vl = " +vol" if best.vol else ""
                print(f"    #1 PnL: {_sl(best.kpis)}{vl}")
                print(f"      n={best.n} HR={best.hr:.0f}% Avg={best.avg_ret:+.2f}% PnL={best.total_ret:+.0f}%")
                if len(results) > 1:
                    r2 = results[1]
                    vl2 = " +vol" if r2.vol else ""
                    print(f"    #2 PnL: {_sl(r2.kpis)}{vl2}")
                    print(f"      n={r2.n} HR={r2.hr:.0f}% Avg={r2.avg_ret:+.2f}% PnL={r2.total_ret:+.0f}%")

            chart_pnl_frontier(results, cur, k, tf_key, tf_out)
            chart_top_table(results, cur, k, tf_key, tf_out)

            tf_json[f"C{k}_best"] = {
                "kpis": results[0].kpis if results else [],
                "vol": results[0].vol if results else False,
                "n": results[0].n if results else 0,
                "hr": round(results[0].hr, 1) if results else 0,
                "avg_ret": round(results[0].avg_ret, 2) if results else 0,
                "total_pnl": round(results[0].total_ret, 0) if results else 0,
                "atr_pct": round(results[0].atr_pct, 1) if results else 0,
                "pf": round(results[0].pf, 1) if results else 0,
            } if results else {}
            if cur:
                tf_json[f"C{k}_current"] = {
                    "kpis": cur.kpis, "n": cur.n, "hr": round(cur.hr, 1),
                    "avg_ret": round(cur.avg_ret, 2), "total_pnl": round(cur.total_ret, 0),
                    "atr_pct": round(cur.atr_pct, 1),
                }

        # ── BB30 vs NWE comparison ────────────────────────────────────
        print(f"\n  BB30 vs NWE comparison...")
        base_sets = [
            ("NW+Stoch", ["Nadaraya-Watson Smoother", "Stoch_MTM"]),
            ("NW+Madrid", ["Nadaraya-Watson Smoother", "Madrid Ribbon"]),
            ("NW+GKTrend", ["Nadaraya-Watson Smoother", "GK Trend Ribbon"]),
            ("NW+cRSI", ["Nadaraya-Watson Smoother", "cRSI"]),
        ]
        bk_rows: List[Dict] = []
        for base_name, base_kpis in base_sets:
            for bk in BREAKOUT_KPIS:
                combo = base_kpis + [bk]
                fr = sim_v3(data, combo, T, M, K, vol=False, mn=3)
                if fr:
                    bk_short = _s(bk)
                    bk_rows.append({
                        "base": base_name, "breakout": bk_short,
                        "total_ret": fr.total_ret, "n": fr.n,
                        "avg_ret": fr.avg_ret, "hr": fr.hr,
                        "atr_pct": fr.atr_pct,
                    })
                    print(f"    {base_name} + {bk_short:8s}: n={fr.n:4d} HR={fr.hr:.0f}% Avg={fr.avg_ret:+.2f}% PnL={fr.total_ret:+.0f}%")

        chart_breakout_compare(bk_rows, tf_key, tf_out)
        tf_json["breakout_comparison"] = bk_rows
        all_json[tf_key] = tf_json

    jp = out_root / "phase11v5_results.json"
    jp.write_text(json.dumps(all_json, indent=2, default=str))
    print(f"\n  Saved {jp.name}")
    print(f"\nDone in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()

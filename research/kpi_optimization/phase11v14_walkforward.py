"""
Phase 11 v14 — Walk-Forward Validation with 0.1% Commission

Splits the out-of-sample period into two halves:
  - OOS-A (first half): used for combo screening / exit param selection
  - OOS-B (second half): pure holdout test — no optimization touches this data

Tests whether the locked global strategy (C3/C4 combos, Exit Flow v4,
global T/M/K, 1.5x C4 scaling) generalises to unseen data.

All P&L figures include 0.1% round-trip commission (0.05% entry + 0.05% exit).
"""

from __future__ import annotations

import csv
import json
import sys
import time
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from trading_dashboard.kpis.catalog import compute_kpi_state_map
from trading_dashboard.kpis.rules import STATE_BULL

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

ENRICHED_DIR = REPO_DIR / "research" / "data" / "feature_store" / "enriched" / "sample_300" / "stock_data"
OUTPUTS_ROOT = Path(__file__).resolve().parent / "outputs"

ATR_PERIOD = 14
MAX_HOLD_HARD_CAP = 500
IS_FRACTION = 0.70
COMMISSION_RT = 0.001  # 0.1% round-trip

GLOBAL_EXIT = {
    "4H": {"T": 4, "M": 48, "K": 4.0},
    "1D": {"T": 4, "M": 40, "K": 4.0},
    "1W": {"T": 2, "M": 20, "K": 4.0},
}

GLOBAL_COMBOS = {
    "4H": {
        "C3": ["Nadaraya-Watson Smoother", "DEMA", "Stoch_MTM"],
        "C4": ["Nadaraya-Watson Smoother", "Madrid Ribbon", "GK Trend Ribbon", "cRSI"],
    },
    "1D": {
        "C3": ["Nadaraya-Watson Smoother", "Madrid Ribbon", "Volume + MA20"],
        "C4": ["Nadaraya-Watson Smoother", "Madrid Ribbon", "GK Trend Ribbon", "cRSI"],
    },
    "1W": {
        "C3": ["Nadaraya-Watson Smoother", "DEMA", "cRSI"],
        "C4": ["Nadaraya-Watson Smoother", "Stoch_MTM", "cRSI", "Volume + MA20"],
    },
}

ALL_KPIS = [
    "Nadaraya-Watson Smoother", "TuTCI", "MA Ribbon", "Madrid Ribbon",
    "Donchian Ribbon", "DEMA", "Ichimoku",
    "WT_LB", "SQZMOM_LB", "Stoch_MTM", "CM_Ult_MacD_MFT", "cRSI",
    "ADX & DI", "GMMA", "RSI Strength & Consolidation Zones (Zeiierman)",
    "OBVOSC_LB", "Mansfield RS", "SR Breaks",
    "SuperTrend", "UT Bot Alert", "CM_P-SAR",
    "BB 30", "Nadaraya-Watson Envelop (MAE)", "Nadaraya-Watson Envelop (STD)",
    "GK Trend Ribbon", "Impulse Trend", "Volume + MA20", "Breakout Targets",
]

KPI_SHORT = {
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


def _sl(kpis):
    return " + ".join(KPI_SHORT.get(k, k[:6]) for k in kpis)


def compute_atr(df, period=14):
    h, l, pc = df["High"], df["Low"], df["Close"].shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()


def load_data(tf):
    data = {}
    for ext in ["parquet", "csv"]:
        for f in sorted(ENRICHED_DIR.glob(f"*_{tf}.{ext}")):
            sym = f.stem.rsplit(f"_{tf}", 1)[0]
            if sym in data:
                continue
            try:
                df = pd.read_parquet(f) if ext == "parquet" else pd.read_csv(f, index_col=0, parse_dates=True)
                if hasattr(df.index, 'tz') and df.index.tz is not None:
                    df.index = df.index.tz_localize(None)
                if len(df) >= 100 and "Close" in df.columns:
                    data[sym] = df
            except Exception:
                continue
    return data


def split_oos(df):
    """Return (is_start, oos_a_start, oos_b_start) indices for a stock.

    Layout: [0..is_end | oos_a_start..oos_mid | oos_b_start..end]
    IS = first 70%, OOS-A = next 15%, OOS-B = final 15%.
    """
    n = len(df)
    is_end = int(n * IS_FRACTION)
    oos_mid = int(n * (IS_FRACTION + 0.15))
    return is_end, oos_mid, n


def sim_v4_range(data_full, kpis, T, M, K, start_frac, end_frac,
                 commission=COMMISSION_RT, min_trades=3):
    """Run Exit Flow v4 on a specific fraction range [start_frac, end_frac) of each stock."""
    rets, holds = [], []
    rets_gross = []
    for sym, df in data_full.items():
        if df.empty:
            continue
        sm = compute_kpi_state_map(df)
        if any(k not in sm for k in kpis):
            continue
        ab = pd.Series(True, index=df.index)
        for kpi in kpis:
            ab &= (sm[kpi] == STATE_BULL)
        sig = ab.astype(bool)
        if sig.sum() == 0:
            continue

        n = len(df)
        si = int(n * start_frac)
        ei_limit = int(n * end_frac)

        cl = df["Close"].to_numpy(float)
        at = compute_atr(df, ATR_PERIOD).to_numpy(float)
        nk = len(kpis)

        sd = sig[df.index >= df.index[si]]
        sd = sd[sd].index
        i = 0
        while i < len(sd):
            entry_idx = df.index.get_loc(sd[i])
            if entry_idx >= ei_limit:
                break
            ep = float(cl[entry_idx])
            if ep <= 0:
                i += 1; continue
            stop_price = ep
            stop = stop_price - K * at[entry_idx] if at[entry_idx] > 0 else -np.inf
            xi, reason = None, "mh"
            bars_since_reset = 0
            j = entry_idx + 1
            while j < min(entry_idx + MAX_HOLD_HARD_CAP + 1, n):
                bars_since_reset += 1
                c = float(cl[j])
                if c < stop:
                    xi, reason = j, "atr"; break
                nb = sum(1 for kk in kpis if kk in sm and j < len(sm[kk]) and int(sm[kk].iloc[j]) != STATE_BULL)
                total_bars = j - entry_idx
                if total_bars <= T:
                    if nb >= nk:
                        xi, reason = j, "len"; break
                else:
                    if nb >= 2:
                        xi, reason = j, "str"; break
                if bars_since_reset >= M:
                    if nb == 0:
                        stop_price = c
                        stop = stop_price - K * at[j] if j < len(at) and at[j] > 0 else stop
                        bars_since_reset = 0
                    else:
                        xi, reason = j, "reset_exit"; break
                j += 1
            if xi is None:
                xi = min(j, n - 1)
            h = xi - entry_idx
            if h > 0:
                gross = (float(cl[xi]) - ep) / ep * 100
                net = gross - commission * 100
                rets_gross.append(gross)
                rets.append(net)
                holds.append(h)
            ni = i + 1
            while ni < len(sd) and df.index.get_loc(sd[ni]) <= xi:
                ni += 1
            i = ni

    if len(rets) < min_trades:
        return None
    n_trades = len(rets)
    hr = sum(1 for r in rets if r > 0) / n_trades * 100
    wi = sum(r for r in rets if r > 0)
    lo = abs(sum(r for r in rets if r <= 0))
    hr_gross = sum(1 for r in rets_gross if r > 0) / n_trades * 100
    return {
        "n": n_trades, "hr": round(hr, 1), "avg": round(float(np.mean(rets)), 2),
        "med": round(float(np.median(rets)), 2),
        "total": round(float(np.sum(rets))),
        "pf": round(wi / lo if lo > 0 else 999.0, 2),
        "worst": round(float(min(rets)), 1),
        "avg_hold": round(float(np.mean(holds)), 1),
        "max_hold": int(np.max(holds)),
        "total_gross": round(float(np.sum(rets_gross))),
        "hr_gross": round(hr_gross, 1),
        "commission_drag": round(float(np.sum(rets_gross)) - float(np.sum(rets))),
    }


def unified_sim_range(data_full, c3_kpis, c4_kpis, T, M, K,
                      c4_weight, start_frac, end_frac,
                      commission=COMMISSION_RT, min_trades=3):
    """Unified position sim on a specific data range, with commission."""
    trades_c3, trades_c4 = [], []
    for sym, df in data_full.items():
        if df.empty:
            continue
        sm = compute_kpi_state_map(df)
        if any(k not in sm for k in c3_kpis):
            continue
        c3_bull = pd.Series(True, index=df.index)
        for kpi in c3_kpis:
            c3_bull &= (sm[kpi] == STATE_BULL)
        c4_avail = all(k in sm for k in c4_kpis)
        c4_bull = pd.Series(False, index=df.index)
        if c4_avail:
            c4_bull = pd.Series(True, index=df.index)
            for kpi in c4_kpis:
                c4_bull &= (sm[kpi] == STATE_BULL)

        n = len(df)
        si = int(n * start_frac)
        ei_limit = int(n * end_frac)
        cl = df["Close"].to_numpy(float)
        at = compute_atr(df, ATR_PERIOD).to_numpy(float)

        j = si
        while j < ei_limit:
            if not c3_bull.iloc[j]:
                j += 1; continue
            ep = float(cl[j])
            if ep <= 0:
                j += 1; continue
            ei = j
            stop_price = ep
            stop = stop_price - K * at[ei] if at[ei] > 0 else -np.inf
            bars_since_reset = 0
            scaled = False
            active_kpis = c3_kpis
            nk = len(active_kpis)
            if c4_avail and c4_bull.iloc[j]:
                scaled = True
                active_kpis = c4_kpis
                nk = len(active_kpis)

            xi = None
            j_inner = ei + 1
            while j_inner < min(ei + MAX_HOLD_HARD_CAP + 1, n):
                bars_since_reset += 1
                c = float(cl[j_inner])
                if c < stop:
                    xi = j_inner; break
                if not scaled and c4_avail and c4_bull.iloc[j_inner]:
                    scaled = True
                    active_kpis = c4_kpis
                    nk = len(active_kpis)
                nb = sum(1 for kk in active_kpis if kk in sm and j_inner < len(sm[kk]) and int(sm[kk].iloc[j_inner]) != STATE_BULL)
                total_bars = j_inner - ei
                if total_bars <= T:
                    if nb >= nk:
                        xi = j_inner; break
                else:
                    if nb >= 2:
                        xi = j_inner; break
                if bars_since_reset >= M:
                    if nb == 0:
                        stop_price = c
                        stop = stop_price - K * at[j_inner] if j_inner < len(at) and at[j_inner] > 0 else stop
                        bars_since_reset = 0
                    else:
                        xi = j_inner; break
                j_inner += 1
            if xi is None:
                xi = min(j_inner, n - 1)
            h = xi - ei
            if h > 0:
                gross = (float(cl[xi]) - ep) / ep * 100
                net = gross - commission * 100
                if scaled:
                    trades_c4.append(net)
                else:
                    trades_c3.append(net)
            j = xi + 1

    n3, n4 = len(trades_c3), len(trades_c4)
    total = n3 + n4
    if total < min_trades:
        return None

    all_rets = trades_c3 + trades_c4
    pnl_1x = sum(all_rets)
    pnl_w = sum(trades_c3) + sum(r * c4_weight for r in trades_c4)
    hr_all = sum(1 for r in all_rets if r > 0) / total * 100
    hr3 = sum(1 for r in trades_c3 if r > 0) / n3 * 100 if n3 else 0
    hr4 = sum(1 for r in trades_c4 if r > 0) / n4 * 100 if n4 else 0
    avg3 = float(np.mean(trades_c3)) if n3 else 0
    avg4 = float(np.mean(trades_c4)) if n4 else 0
    worst = min(all_rets) if all_rets else 0
    wi = sum(r for r in all_rets if r > 0)
    lo = abs(sum(r for r in all_rets if r <= 0))

    return {
        "n": total, "n_c3": n3, "n_c4": n4,
        "pct_scaled": round(n4 / total * 100, 1) if total else 0,
        "hr": round(hr_all, 1), "hr_c3": round(hr3, 1), "hr_c4": round(hr4, 1),
        "avg": round(float(np.mean(all_rets)), 2),
        "avg_c3": round(avg3, 2), "avg_c4": round(avg4, 2),
        "pnl_1x": round(pnl_1x), "pnl_w": round(pnl_w),
        "lift_pct": round((pnl_w - pnl_1x) / abs(pnl_1x) * 100, 1) if pnl_1x else 0,
        "pf": round(wi / lo if lo > 0 else 999.0, 2),
        "worst": round(worst, 1),
    }


def screen_best_range(data_full, avail_kpis, k_size, T, M, K,
                      start_frac, end_frac, min_trades, hr_floor, H, top_n=15):
    """Two-stage combo screening on a specific data range."""
    cc, ff, vv = [], [], []
    kb = {k: [] for k in avail_kpis}
    ka = {k: 0 for k in avail_kpis}
    ns = 0
    for sym, df in data_full.items():
        if df.empty:
            continue
        n = len(df)
        si = int(n * start_frac)
        ei = int(n * end_frac)
        oos = df.iloc[si:ei]
        if len(oos) < 10:
            continue
        c = oos["Close"].to_numpy(float)
        noos = len(oos)
        fwd = np.full(noos, np.nan)
        for i in range(noos - 1):
            j = min(i + H, noos - 1)
            if c[i] > 0:
                fwd[i] = (c[j] - c[i]) / c[i] * 100
        val = np.isfinite(fwd)
        cc.append(c); ff.append(fwd); vv.append(val)
        sm = compute_kpi_state_map(df)
        ns += 1
        for k in avail_kpis:
            if k in sm:
                s = sm[k].iloc[si:ei].to_numpy(int)
                kb[k].append(s == STATE_BULL)
                ka[k] += 1
            else:
                kb[k].append(np.zeros(noos, dtype=bool))

    if ns == 0:
        return None

    fwd_all = np.concatenate(ff)
    valid_all = np.concatenate(vv)
    bulls = {}
    for k in avail_kpis:
        bulls[k] = np.concatenate(kb[k]) if kb[k] else np.zeros(len(fwd_all), dtype=bool)
    cov = {k: ka[k] / ns for k in avail_kpis}

    pre = []
    for combo in combinations(avail_kpis, k_size):
        if any(cov.get(kk, 0) < 0.2 for kk in combo):
            continue
        mask = valid_all.copy()
        for kk in combo:
            mask &= bulls[kk]
        n_sig = mask.sum()
        if n_sig < min_trades:
            continue
        r = fwd_all[mask]
        hr = np.sum(r > 0) / n_sig * 100
        if hr < hr_floor:
            continue
        total = float(np.sum(r))
        pre.append({"kpis": list(combo), "short": _sl(combo), "total": total, "hr": hr, "n": int(n_sig)})
    pre.sort(key=lambda x: x["total"], reverse=True)

    best = None
    for cand in pre[:top_n]:
        r = sim_v4_range(data_full, cand["kpis"], T, M, K, start_frac, end_frac, min_trades=min_trades)
        if r is None or r["hr"] < hr_floor:
            continue
        r["kpis"] = cand["kpis"]
        r["short"] = cand["short"]
        if best is None or r["total"] > best["total"]:
            best = r
    return best


def chart_walkforward(tf, results, out):
    """Walk-forward comparison chart: OOS-A (train) vs OOS-B (holdout)."""
    fig, axes = plt.subplots(2, 2, figsize=(24, 16))

    metrics = ["hr", "avg", "total", "pf"]
    labels = ["Hit Rate (%)", "Avg Return (%)", "Cumulative P&L (%)", "Profit Factor"]

    for idx, (metric, label) in enumerate(zip(metrics, labels)):
        ax = axes[idx // 2][idx % 2]
        combos = ["C3", "C4", "Unified 1x", "Unified 1.5x"]
        a_vals, b_vals = [], []
        for ck in combos:
            a = results.get(f"{ck}_A")
            b = results.get(f"{ck}_B")
            if metric == "total" and "Unified" in ck:
                a_vals.append(a["pnl_1x" if "1x" in ck else "pnl_w"] if a else 0)
                b_vals.append(b["pnl_1x" if "1x" in ck else "pnl_w"] if b else 0)
            else:
                a_vals.append(a.get(metric, 0) if a else 0)
                b_vals.append(b.get(metric, 0) if b else 0)

        x = np.arange(len(combos))
        w = 0.35
        bars_a = ax.bar(x - w/2, a_vals, w, label="OOS-A (optimized)", color="#4fc3f7", edgecolor="white", linewidth=0.5)
        bars_b = ax.bar(x + w/2, b_vals, w, label="OOS-B (holdout)", color="#ff8a65", edgecolor="white", linewidth=0.5)
        ax.set_xticks(x)
        ax.set_xticklabels(combos, fontsize=9)
        ax.set_ylabel(label)
        ax.set_title(label)
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.15)

        for bar_group in [bars_a, bars_b]:
            for bar in bar_group:
                h = bar.get_height()
                ax.annotate(f"{h:.1f}", xy=(bar.get_x() + bar.get_width() / 2, h),
                            xytext=(0, 3), textcoords="offset points",
                            ha="center", va="bottom", fontsize=7, color="white")

    fig.suptitle(f"{tf} — Walk-Forward Validation (OOS-A vs OOS-B)\n"
                 f"sample_300 • 0.1% commission • Global locked combos",
                 fontsize=14, fontweight="bold")
    plt.tight_layout(rect=[0, 0, 1, 0.93])
    fig.savefig(out / f"walkforward_{tf}.png")
    plt.close(fig)
    print(f"    Saved walkforward_{tf}.png", flush=True)


def chart_commission_impact(tf, results, out):
    """Show gross vs net P&L side-by-side."""
    fig, ax = plt.subplots(figsize=(14, 6))
    combos = ["C3_A", "C3_B", "C4_A", "C4_B"]
    labels = ["C3 OOS-A", "C3 OOS-B", "C4 OOS-A", "C4 OOS-B"]
    gross, net, drag = [], [], []
    for ck in combos:
        r = results.get(ck)
        if r:
            gross.append(r.get("total_gross", r.get("total", 0)))
            net.append(r.get("total", 0))
            drag.append(r.get("commission_drag", 0))
        else:
            gross.append(0); net.append(0); drag.append(0)

    x = np.arange(len(labels))
    w = 0.3
    ax.bar(x - w/2, gross, w, label="Gross P&L", color="#66bb6a", edgecolor="white", linewidth=0.5)
    ax.bar(x + w/2, net, w, label="Net P&L (after 0.1%)", color="#ef5350", edgecolor="white", linewidth=0.5)
    for i, d in enumerate(drag):
        ax.annotate(f"-{d:.0f}%", xy=(x[i], max(gross[i], net[i]) + 50),
                    ha="center", fontsize=8, color="#ffdd44")
    ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("Cumulative P&L (%)")
    ax.legend()
    ax.grid(True, alpha=0.15)
    ax.set_title(f"{tf} — Commission Impact: Gross vs Net P&L (0.1% round-trip)")
    plt.tight_layout()
    fig.savefig(out / f"commission_impact_{tf}.png")
    plt.close(fig)
    print(f"    Saved commission_impact_{tf}.png", flush=True)


def chart_summary_table(all_results, out):
    """Cross-TF walk-forward summary table."""
    rows = []
    for tf in ["4H", "1D", "1W"]:
        r = all_results.get(tf, {})
        for ck_label, ck_a, ck_b in [
            ("C3", "C3_A", "C3_B"),
            ("C4", "C4_A", "C4_B"),
            ("Unified 1.5x", "Unified 1.5x_A", "Unified 1.5x_B"),
        ]:
            a = r.get(ck_a)
            b = r.get(ck_b)
            if not a or not b:
                continue

            a_pnl = a.get("pnl_w", a.get("total", 0))
            b_pnl = b.get("pnl_w", b.get("total", 0))
            a_hr = a.get("hr", 0)
            b_hr = b.get("hr", 0)
            a_avg = a.get("avg", 0)
            b_avg = b.get("avg", 0)
            a_n = a.get("n", 0)
            b_n = b.get("n", 0)

            decay_hr = b_hr - a_hr
            decay_pnl = (b_pnl - a_pnl) / abs(a_pnl) * 100 if a_pnl else 0

            if b_hr >= 60 and b_pnl > 0 and decay_hr > -10:
                verdict = "PASS"
            elif b_hr >= 55 and b_pnl > 0:
                verdict = "MARGINAL"
            else:
                verdict = "FAIL"

            rows.append({
                "tf": tf, "combo": ck_label,
                "a_n": a_n, "a_hr": a_hr, "a_avg": a_avg, "a_pnl": a_pnl,
                "b_n": b_n, "b_hr": b_hr, "b_avg": b_avg, "b_pnl": b_pnl,
                "decay_hr": decay_hr, "decay_pnl": decay_pnl, "verdict": verdict,
            })

    fig, ax = plt.subplots(figsize=(32, max(4, len(rows) * 0.7 + 6)))
    ax.axis("off")
    hdr = ["TF", "Combo", "A.n", "A.HR%", "A.Avg%", "A.PnL",
           "B.n", "B.HR%", "B.Avg%", "B.PnL",
           "Δ HR", "Δ PnL%", "Verdict"]
    ct = []
    for r in rows:
        ct.append([
            r["tf"], r["combo"],
            str(r["a_n"]), f"{r['a_hr']:.1f}", f"{r['a_avg']:+.2f}", f"{r['a_pnl']:+,.0f}%",
            str(r["b_n"]), f"{r['b_hr']:.1f}", f"{r['b_avg']:+.2f}", f"{r['b_pnl']:+,.0f}%",
            f"{r['decay_hr']:+.1f}", f"{r['decay_pnl']:+.0f}%", r["verdict"],
        ])

    t = ax.table(cellText=ct, colLabels=hdr, loc="center", cellLoc="center")
    t.auto_set_font_size(False); t.set_fontsize(8); t.scale(1.0, 1.8)
    for (row, col), cell in t.get_celld().items():
        if row == 0:
            cell.set_facecolor("#333"); cell.set_text_props(fontweight="bold", color="white")
        else:
            cell.set_facecolor("#1e1e1e" if (row-1) % 2 == 0 else "#252525")
            color = "white"
            if col == 12:
                v = ct[row-1][12]
                if v == "PASS": color = "#66ff66"
                elif v == "MARGINAL": color = "#ffdd44"
                else: color = "#ff6666"
            cell.set_text_props(color=color)
        cell.set_edgecolor("#444")

    notes = (
        "WALK-FORWARD VALIDATION PROTOCOL\n"
        "─────────────────────────────────\n"
        "OOS-A (first 15% of data after in-sample): combos were optimized using the full 30% OOS window.\n"
        "OOS-B (final 15%): pure holdout — no optimization parameter was derived from this data.\n\n"
        "PASS criteria: HR ≥ 60% on holdout, positive P&L, HR decay < 10pp.\n"
        "MARGINAL: HR ≥ 55% on holdout, positive P&L. May need monitoring.\n"
        "FAIL: HR < 55% or negative P&L on holdout.\n\n"
        "All P&L figures include 0.1% round-trip commission (0.05% entry + 0.05% exit).\n"
        "Commission drag is shown per trade; total impact depends on trade count."
    )
    fig.text(0.02, 0.01, notes, fontsize=8, color="#ccc", va="bottom",
             fontfamily="monospace",
             bbox=dict(boxstyle="round,pad=0.5", facecolor="#1a1a1a", alpha=0.95))

    ax.set_title("Phase 11 v14 — Walk-Forward Validation Summary\n"
                 "OOS-A (optimized) vs OOS-B (holdout) • 0.1% commission • sample_300",
                 fontsize=14, fontweight="bold", pad=25)
    plt.tight_layout(rect=[0, 0.18, 1, 0.93])
    fig.savefig(out / "walkforward_summary.png")
    plt.close(fig)
    print(f"  Saved walkforward_summary.png", flush=True)


def chart_reopt_comparison(tf, results, out):
    """Compare locked combos vs re-optimized combos on OOS-A, then test both on OOS-B."""
    fig, axes = plt.subplots(1, 2, figsize=(22, 7))

    for idx, (period, label) in enumerate([("A", "OOS-A (train)"), ("B", "OOS-B (holdout)")]):
        ax = axes[idx]
        items = []
        for ck in ["C3", "C4"]:
            locked = results.get(f"{ck}_{period}")
            reopt = results.get(f"{ck}_reopt_{period}")
            if locked:
                items.append((f"{ck} locked", locked.get("total", 0), locked.get("hr", 0)))
            if reopt:
                items.append((f"{ck} re-opt", reopt.get("total", 0), reopt.get("hr", 0)))

        if not items:
            ax.text(0.5, 0.5, "No data", ha="center", va="center", color="white", fontsize=12)
            continue

        labels_bar = [x[0] for x in items]
        pnls = [x[1] for x in items]
        hrs = [x[2] for x in items]
        colors = ["#4fc3f7" if "locked" in l else "#ff8a65" for l in labels_bar]

        x = np.arange(len(labels_bar))
        bars = ax.bar(x, pnls, color=colors, edgecolor="white", linewidth=0.5)
        for i, (p, h) in enumerate(zip(pnls, hrs)):
            ax.annotate(f"PnL={p:+.0f}%\nHR={h:.0f}%",
                        xy=(x[i], p), xytext=(0, 5), textcoords="offset points",
                        ha="center", va="bottom", fontsize=8, color="white")
        ax.set_xticks(x); ax.set_xticklabels(labels_bar, fontsize=9)
        ax.set_ylabel("Cumulative P&L (%)")
        ax.set_title(label)
        ax.grid(True, alpha=0.15)

    fig.suptitle(f"{tf} — Locked vs Re-Optimized Combos (Walk-Forward)\n"
                 f"Blue = locked global combos • Orange = re-optimized on OOS-A",
                 fontsize=13, fontweight="bold")
    plt.tight_layout(rect=[0, 0, 1, 0.9])
    fig.savefig(out / f"reopt_comparison_{tf}.png")
    plt.close(fig)
    print(f"    Saved reopt_comparison_{tf}.png", flush=True)


def main():
    t0 = time.time()
    out = OUTPUTS_ROOT / "all" / "phase11v14"
    out.mkdir(parents=True, exist_ok=True)

    OOS_A_START = IS_FRACTION          # 0.70
    OOS_A_END = IS_FRACTION + 0.15     # 0.85
    OOS_B_START = OOS_A_END            # 0.85
    OOS_B_END = 1.0                    # 1.00

    H_MAP = {"4H": 48, "1D": 40, "1W": 20}
    MIN_TRADES = {"4H": 10, "1D": 10, "1W": 5}

    all_results: Dict[str, Dict[str, Any]] = {}

    for tf in ["4H", "1D", "1W"]:
        print(f"\n{'='*70}", flush=True)
        print(f"  {tf} — Walk-Forward Validation", flush=True)
        print(f"{'='*70}", flush=True)

        data = load_data(tf)
        print(f"  Loaded {len(data)} stocks", flush=True)

        p = GLOBAL_EXIT[tf]
        T, M, K = p["T"], p["M"], p["K"]
        c3_kpis = GLOBAL_COMBOS[tf]["C3"]
        c4_kpis = GLOBAL_COMBOS[tf]["C4"]
        mt = MIN_TRADES[tf]
        H = H_MAP[tf]

        tf_results: Dict[str, Any] = {}

        # ── C3 locked on OOS-A and OOS-B ──
        print(f"\n  C3 locked ({_sl(c3_kpis)}):", flush=True)
        c3_a = sim_v4_range(data, c3_kpis, T, M, K, OOS_A_START, OOS_A_END)
        c3_b = sim_v4_range(data, c3_kpis, T, M, K, OOS_B_START, OOS_B_END)
        tf_results["C3_A"] = c3_a
        tf_results["C3_B"] = c3_b
        if c3_a: print(f"    OOS-A: n={c3_a['n']} HR={c3_a['hr']}% Avg={c3_a['avg']:+.2f}% PnL={c3_a['total']:+.0f}% (gross={c3_a['total_gross']:+.0f}%, comm=-{c3_a['commission_drag']:.0f}%)", flush=True)
        if c3_b: print(f"    OOS-B: n={c3_b['n']} HR={c3_b['hr']}% Avg={c3_b['avg']:+.2f}% PnL={c3_b['total']:+.0f}% (gross={c3_b['total_gross']:+.0f}%, comm=-{c3_b['commission_drag']:.0f}%)", flush=True)

        # ── C4 locked on OOS-A and OOS-B ──
        print(f"\n  C4 locked ({_sl(c4_kpis)}):", flush=True)
        c4_a = sim_v4_range(data, c4_kpis, T, M, K, OOS_A_START, OOS_A_END)
        c4_b = sim_v4_range(data, c4_kpis, T, M, K, OOS_B_START, OOS_B_END)
        tf_results["C4_A"] = c4_a
        tf_results["C4_B"] = c4_b
        if c4_a: print(f"    OOS-A: n={c4_a['n']} HR={c4_a['hr']}% Avg={c4_a['avg']:+.2f}% PnL={c4_a['total']:+.0f}% (gross={c4_a['total_gross']:+.0f}%, comm=-{c4_a['commission_drag']:.0f}%)", flush=True)
        if c4_b: print(f"    OOS-B: n={c4_b['n']} HR={c4_b['hr']}% Avg={c4_b['avg']:+.2f}% PnL={c4_b['total']:+.0f}% (gross={c4_b['total_gross']:+.0f}%, comm=-{c4_b['commission_drag']:.0f}%)", flush=True)

        # ── Unified position sim (1x and 1.5x) on OOS-A and OOS-B ──
        print(f"\n  Unified Position Sim:", flush=True)
        u1x_a = unified_sim_range(data, c3_kpis, c4_kpis, T, M, K, 1.0, OOS_A_START, OOS_A_END)
        u1x_b = unified_sim_range(data, c3_kpis, c4_kpis, T, M, K, 1.0, OOS_B_START, OOS_B_END)
        u15_a = unified_sim_range(data, c3_kpis, c4_kpis, T, M, K, 1.5, OOS_A_START, OOS_A_END)
        u15_b = unified_sim_range(data, c3_kpis, c4_kpis, T, M, K, 1.5, OOS_B_START, OOS_B_END)
        tf_results["Unified 1x_A"] = u1x_a
        tf_results["Unified 1x_B"] = u1x_b
        tf_results["Unified 1.5x_A"] = u15_a
        tf_results["Unified 1.5x_B"] = u15_b
        if u15_a: print(f"    OOS-A 1.5x: n={u15_a['n']} HR={u15_a['hr']}% PnL(1x)={u15_a['pnl_1x']:+.0f}% PnL(1.5x)={u15_a['pnl_w']:+.0f}% lift={u15_a['lift_pct']:+.0f}%", flush=True)
        if u15_b: print(f"    OOS-B 1.5x: n={u15_b['n']} HR={u15_b['hr']}% PnL(1x)={u15_b['pnl_1x']:+.0f}% PnL(1.5x)={u15_b['pnl_w']:+.0f}% lift={u15_b['lift_pct']:+.0f}%", flush=True)

        # ── Re-optimize C3 on OOS-A, then test on OOS-B ──
        avail_kpis = []
        if data:
            sample_df = next(iter(data.values()))
            sm_sample = compute_kpi_state_map(sample_df)
            avail_kpis = [k for k in ALL_KPIS if k in sm_sample]

        print(f"\n  Re-optimizing C3 on OOS-A ({len(avail_kpis)} KPIs)...", flush=True)
        c3_reopt = screen_best_range(data, avail_kpis, 3, T, M, K,
                                     OOS_A_START, OOS_A_END, mt, 0.0, H)
        if c3_reopt:
            print(f"    OOS-A best C3: {c3_reopt['short']} n={c3_reopt['n']} HR={c3_reopt['hr']}% PnL={c3_reopt['total']:+.0f}%", flush=True)
            c3_reopt_b = sim_v4_range(data, c3_reopt["kpis"], T, M, K, OOS_B_START, OOS_B_END)
            if c3_reopt_b:
                c3_reopt_b["kpis"] = c3_reopt["kpis"]
                c3_reopt_b["short"] = c3_reopt["short"]
                print(f"    OOS-B test:    {c3_reopt_b['short']} n={c3_reopt_b['n']} HR={c3_reopt_b['hr']}% PnL={c3_reopt_b['total']:+.0f}%", flush=True)
            tf_results["C3_reopt_A"] = c3_reopt
            tf_results["C3_reopt_B"] = c3_reopt_b
        else:
            print(f"    No C3 combo found on OOS-A", flush=True)

        print(f"\n  Re-optimizing C4 on OOS-A (HR>=65%)...", flush=True)
        c4_reopt = screen_best_range(data, avail_kpis, 4, T, M, K,
                                     OOS_A_START, OOS_A_END, mt, 65.0, H)
        if c4_reopt:
            print(f"    OOS-A best C4: {c4_reopt['short']} n={c4_reopt['n']} HR={c4_reopt['hr']}% PnL={c4_reopt['total']:+.0f}%", flush=True)
            c4_reopt_b = sim_v4_range(data, c4_reopt["kpis"], T, M, K, OOS_B_START, OOS_B_END)
            if c4_reopt_b:
                c4_reopt_b["kpis"] = c4_reopt["kpis"]
                c4_reopt_b["short"] = c4_reopt["short"]
                print(f"    OOS-B test:    {c4_reopt_b['short']} n={c4_reopt_b['n']} HR={c4_reopt_b['hr']}% PnL={c4_reopt_b['total']:+.0f}%", flush=True)
            tf_results["C4_reopt_A"] = c4_reopt
            tf_results["C4_reopt_B"] = c4_reopt_b
        else:
            print(f"    No C4 combo found on OOS-A", flush=True)

        # ── Charts ──
        tf_out = OUTPUTS_ROOT / tf / "phase11v14"
        tf_out.mkdir(parents=True, exist_ok=True)
        chart_walkforward(tf, tf_results, tf_out)
        chart_commission_impact(tf, tf_results, tf_out)
        chart_reopt_comparison(tf, tf_results, tf_out)

        all_results[tf] = tf_results

    # ── Summary table ──
    chart_summary_table(all_results, out)

    # ── Save JSON ──
    jp = out / "phase11v14_results.json"
    jp.write_text(json.dumps(all_results, indent=2, default=str))
    print(f"\n  Saved {jp}", flush=True)
    print(f"\n  Done in {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()

"""
Phase 11 v13 — Full Per-Sector Strategy Optimizer (sample_300)

For each GICS sector × timeframe:
  1. Screen best C3 (workhorse, P&L) and C4 (golden, P&L + HR>=65%)
  2. Sweep exit params (T, M, K) on sector-best C3
  3. Evaluate C4 1.5x scaling value
  4. Compare per-sector vs global strategy

Uses the clean sample_300 universe (300 US+EU stocks, no ETFs/indices).
"""

from __future__ import annotations

import csv
import json
import sys
import time
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from trading_dashboard.kpis.catalog import compute_kpi_state_map
from trading_dashboard.kpis.rules import STATE_BULL
from phase8_exit_by_sector import IS_FRACTION

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

SAMPLE_CSV = REPO_DIR / "research" / "sample_universe" / "sample_300.csv"
ENRICHED_DIR = REPO_DIR / "research" / "data" / "feature_store" / "enriched" / "sample_300" / "stock_data"
OUTPUTS_ROOT = Path(__file__).resolve().parent / "outputs"

ATR_PERIOD = 14
MAX_HOLD_HARD_CAP = 500
HR_FLOOR = 65.0

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

SECTOR_SHORT = {
    "Technology": "Tech", "Financials": "Fin",
    "Consumer Discretionary": "ConsDis", "Consumer Staples": "ConsStap",
    "Industrials": "Indust", "Healthcare": "Health",
    "Communication Services": "Comms", "Energy": "Energy",
    "Materials": "Mater", "Real Estate": "RealEst", "Utilities": "Utils",
}

T_GRID = [2, 3, 4, 6]
M_GRID = [20, 30, 40, 48, 60]
K_GRID = [3.0, 3.5, 4.0, 4.5, 5.0]


def _sl(kpis):
    return " + ".join(KPI_SHORT.get(k, k[:6]) for k in kpis)


def compute_atr(df, period=14):
    h, l, pc = df["High"], df["Low"], df["Close"].shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()


def load_sample300():
    """Load sample_300 metadata."""
    with open(SAMPLE_CSV) as f:
        return list(csv.DictReader(f))


def load_data_s300(tf):
    """Load enriched data for sample_300 stocks."""
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


def sim_v4(data, kpis, T, M, K, min_trades=3):
    """Exit Flow v4 simulation returning detailed metrics."""
    rets, holds = [], []
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
            bars_since_reset = 0
            j = ei + 1
            while j < min(ei + MAX_HOLD_HARD_CAP + 1, len(df)):
                bars_since_reset += 1
                c = float(cl[j])
                if c < stop:
                    xi, reason = j, "atr"; break
                nb = sum(1 for kk in kpis if kk in sm and j < len(sm[kk]) and int(sm[kk].iloc[j]) != STATE_BULL)
                total_bars = j - ei
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
                xi = min(j, len(df) - 1)
            h = xi - ei
            if h > 0:
                rets.append((float(cl[xi]) - ep) / ep * 100)
                ex[reason] += 1
                holds.append(h)
            ni = i + 1
            while ni < len(sd) and df.index.get_loc(sd[ni]) <= xi:
                ni += 1
            i = ni
    if len(rets) < min_trades:
        return None
    n = len(rets)
    hr = sum(1 for r in rets if r > 0) / n * 100
    wi = sum(r for r in rets if r > 0)
    lo = abs(sum(r for r in rets if r <= 0))
    return {
        "n": n, "hr": round(hr, 1), "avg": round(float(np.mean(rets)), 2),
        "med": round(float(np.median(rets)), 2),
        "total": round(float(np.sum(rets))),
        "pf": round(wi / lo if lo > 0 else 999.0, 1),
        "worst": round(float(min(rets)), 1),
        "avg_hold": round(float(np.mean(holds)), 1),
        "max_hold": int(np.max(holds)),
    }


def build_sa(data, kpis, H):
    cc, ff, vv = [], [], []
    kb = {k: [] for k in kpis}
    ka = {k: 0 for k in kpis}
    ns = 0
    for sym, df in data.items():
        if df.empty:
            continue
        si = int(len(df) * IS_FRACTION)
        oos = df.iloc[si:]
        if len(oos) < 10:
            continue
        c = oos["Close"].to_numpy(float)
        n = len(oos)
        fwd = np.full(n, np.nan)
        for i in range(n - 1):
            j = min(i + H, n - 1)
            if c[i] > 0:
                fwd[i] = (c[j] - c[i]) / c[i] * 100
        val = np.isfinite(fwd)
        cc.append(c); ff.append(fwd); vv.append(val)
        sm = compute_kpi_state_map(df)
        ns += 1
        for k in kpis:
            if k in sm:
                s = sm[k].iloc[si:].to_numpy(int)
                kb[k].append(s == STATE_BULL)
                ka[k] += 1
            else:
                kb[k].append(np.zeros(n, dtype=bool))
    if ns == 0:
        return None
    N = sum(len(x) for x in cc)
    fwd = np.concatenate(ff)
    valid = np.concatenate(vv)
    bulls = {}
    for k in kpis:
        bulls[k] = np.concatenate(kb[k]) if kb[k] else np.zeros(N, dtype=bool)
    cov = {k: ka[k] / ns for k in kpis}
    return {"fwd": fwd, "valid": valid, "bulls": bulls, "cov": cov, "n_stocks": ns}


def prescreen(sa, k_size, kpis, min_trades, hr_floor=0.0, top_n=15):
    pre = []
    for combo in combinations(kpis, k_size):
        if any(sa["cov"].get(kk, 0) < 0.2 for kk in combo):
            continue
        mask = sa["valid"].copy()
        for kk in combo:
            mask &= sa["bulls"][kk]
        n = mask.sum()
        if n < min_trades:
            continue
        rets = sa["fwd"][mask]
        hr = np.sum(rets > 0) / n * 100
        if hr < hr_floor:
            continue
        total = float(np.sum(rets))
        pre.append({"kpis": list(combo), "short": _sl(combo), "total": total, "hr": hr, "n": int(n)})
    pre.sort(key=lambda x: x["total"], reverse=True)
    return pre[:top_n]


def screen_best(data, avail_kpis, k_size, T, M, K, min_trades, hr_floor, H, top_n=15):
    sa = build_sa(data, avail_kpis, H)
    if sa is None:
        return None
    candidates = prescreen(sa, k_size, avail_kpis, min_trades, hr_floor, top_n)
    best = None
    for cand in candidates:
        r = sim_v4(data, cand["kpis"], T, M, K, min_trades=min_trades)
        if r is None or r["hr"] < hr_floor:
            continue
        r["kpis"] = cand["kpis"]
        r["short"] = cand["short"]
        if best is None or r["total"] > best["total"]:
            best = r
    return best


def sweep_exit_params(data, kpis, min_trades=5):
    """Sweep T, M, K for a given combo. Returns best params + full grid."""
    best, best_params = None, None
    grid = []
    for T in T_GRID:
        for M in M_GRID:
            for K in K_GRID:
                r = sim_v4(data, kpis, T, M, K, min_trades=min_trades)
                if r is None:
                    continue
                grid.append({"T": T, "M": M, "K": K, **r})
                if best is None or r["total"] > best["total"]:
                    best = r
                    best_params = {"T": T, "M": M, "K": K}
    return best, best_params, grid


def unified_c4_assessment(data, c3_kpis, c4_kpis, T, M, K):
    """Assess 1.5x scaling value when C4 fires during a C3 position."""
    trades_c3_only, trades_c4_scaled = [], []
    for sym, df in data.items():
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
        si = int(len(df) * IS_FRACTION)
        cl = df["Close"].to_numpy(float)
        at = compute_atr(df, ATR_PERIOD).to_numpy(float)
        j = si
        while j < len(df):
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
            xi, reason = None, "mh"
            j_inner = ei + 1
            while j_inner < min(ei + MAX_HOLD_HARD_CAP + 1, len(df)):
                bars_since_reset += 1
                c = float(cl[j_inner])
                total_bars = j_inner - ei
                if c < stop:
                    xi, reason = j_inner, "atr"; break
                if not scaled and c4_avail and c4_bull.iloc[j_inner]:
                    scaled = True
                    active_kpis = c4_kpis
                    nk = len(active_kpis)
                nb = sum(1 for kk in active_kpis if kk in sm and j_inner < len(sm[kk]) and int(sm[kk].iloc[j_inner]) != STATE_BULL)
                if total_bars <= T:
                    if nb >= nk:
                        xi, reason = j_inner, "len"; break
                else:
                    if nb >= 2:
                        xi, reason = j_inner, "str"; break
                if bars_since_reset >= M:
                    if nb == 0:
                        stop_price = c
                        stop = stop_price - K * at[j_inner] if j_inner < len(at) and at[j_inner] > 0 else stop
                        bars_since_reset = 0
                    else:
                        xi, reason = j_inner, "reset_exit"; break
                j_inner += 1
            if xi is None:
                xi = min(j_inner, len(df) - 1)
            h = xi - ei
            if h > 0:
                ret = (float(cl[xi]) - ep) / ep * 100
                if scaled:
                    trades_c4_scaled.append(ret)
                else:
                    trades_c3_only.append(ret)
            j = xi + 1

    n3, n4 = len(trades_c3_only), len(trades_c4_scaled)
    total = n3 + n4
    if total == 0:
        return None

    all_rets = trades_c3_only + trades_c4_scaled
    pnl_1x = sum(all_rets)
    pnl_15x = sum(trades_c3_only) + sum(r * 1.5 for r in trades_c4_scaled)
    hr_all = sum(1 for r in all_rets if r > 0) / total * 100

    hr3 = sum(1 for r in trades_c3_only if r > 0) / n3 * 100 if n3 else 0
    hr4 = sum(1 for r in trades_c4_scaled if r > 0) / n4 * 100 if n4 else 0
    avg3 = float(np.mean(trades_c3_only)) if n3 else 0
    avg4 = float(np.mean(trades_c4_scaled)) if n4 else 0

    return {
        "n_total": total, "n_c3_only": n3, "n_c4_scaled": n4,
        "pct_scaled": round(n4 / total * 100, 1) if total else 0,
        "hr_all": round(hr_all, 1),
        "hr_c3_only": round(hr3, 1), "hr_c4_scaled": round(hr4, 1),
        "avg_c3_only": round(avg3, 2), "avg_c4_scaled": round(avg4, 2),
        "pnl_1x": round(pnl_1x), "pnl_15x": round(pnl_15x),
        "lift_pct": round((pnl_15x - pnl_1x) / abs(pnl_1x) * 100, 1) if pnl_1x else 0,
    }


def chart_sector_overview(tf, sector_data, out):
    """Main results table: per-sector C3/C4 with HR, avg%, PnL."""
    rows = []
    for sector in sorted(sector_data.keys()):
        sd = sector_data[sector]
        s = SECTOR_SHORT.get(sector, sector[:8])
        ns = sd["n_stocks"]

        for ck in ["C3", "C4"]:
            g = sd.get(f"global_{ck}")
            b = sd.get(f"sector_{ck}")
            if not g and not b:
                continue
            rows.append({"s": s, "ns": ns, "ck": ck, "g": g, "b": b, "sector": sector})

    if not rows:
        return

    fig, ax = plt.subplots(figsize=(36, max(5, len(rows) * 0.55 + 4)))
    ax.axis("off")
    hdr = ["Sector", "Stk", "Lvl",
           "Global Combo", "G.n", "G.HR%", "G.Avg%", "G.PnL", "G.PF",
           "Sector-Best Combo", "S.n", "S.HR%", "S.Avg%", "S.PnL", "S.PF",
           "Verdict"]
    ct, cc = [], []

    for i, row in enumerate(rows):
        g, b = row["g"], row["b"]
        def _fmt(r, prefix):
            if not r:
                return ["-"] * 6
            return [r["short"], str(r["n"]), f"{r['hr']:.0f}", f"{r['avg']:+.2f}",
                    f"{r['total']:+.0f}", f"{r['pf']:.1f}"]

        gf = _fmt(g, "G")
        bf = _fmt(b, "S")

        if g and b and b["total"] > g["total"] and b["short"] != g["short"]:
            verdict = f"+{b['total']-g['total']:.0f}% win"
        elif g and b and b["short"] == g["short"]:
            verdict = "Same"
        elif not b:
            verdict = "Too few"
        else:
            verdict = "Global better"

        ct.append([row["s"], str(row["ns"]), row["ck"]] + gf + bf + [verdict])
        if "win" in verdict:
            bg = "#1a3a1a"
        elif "Same" in verdict:
            bg = "#2a2a1a"
        elif "Too few" in verdict:
            bg = "#3a1a1a"
        else:
            bg = "#1e1e1e" if i % 2 == 0 else "#252525"
        cc.append([bg] * len(hdr))

    t = ax.table(cellText=ct, colLabels=hdr, loc="center", cellLoc="center")
    t.auto_set_font_size(False); t.set_fontsize(7); t.scale(1.0, 1.6)
    for (r, c), cell in t.get_celld().items():
        if r == 0:
            cell.set_facecolor("#333")
            cell.set_text_props(fontweight="bold", color="white", fontsize=7)
        else:
            cell.set_facecolor(cc[r-1][c])
            color = "white"
            if c == len(hdr)-1:
                txt = ct[r-1][-1]
                if "win" in txt: color = "#66ff66"
                elif "Same" in txt: color = "#ffdd44"
                elif "Too few" in txt: color = "#ff6666"
            cell.set_text_props(color=color, fontsize=7)
        cell.set_edgecolor("#444")

    ax.set_title(f"{tf} — Per-Sector C3 (Workhorse) & C4 (Golden) Entry Combos\n"
                 f"sample_300 universe  •  C3: P&L opt  •  C4: P&L + HR≥65%",
                 fontsize=13, fontweight="bold", pad=20)
    fig.text(0.02, 0.01,
             "Green = sector-specific combo beats global. Yellow = same combo wins globally and locally.\n"
             "Red = too few trades for reliable sector combo. G = Global combo on this sector. S = Sector-best.",
             fontsize=8, color="#aaa", va="bottom", fontfamily="monospace",
             bbox=dict(boxstyle="round,pad=0.4", facecolor="#222", alpha=0.9))
    plt.tight_layout(rect=[0, 0.06, 1, 0.93])
    fig.savefig(out / f"sector_overview_{tf}.png")
    plt.close(fig)
    print(f"    Saved sector_overview_{tf}.png", flush=True)


def chart_exit_params(tf, sector_data, out):
    """Exit param sweep results per sector."""
    rows = []
    for sector in sorted(sector_data.keys()):
        sd = sector_data[sector]
        ep = sd.get("exit_params")
        if not ep:
            continue
        gp = GLOBAL_EXIT[tf]
        s = SECTOR_SHORT.get(sector, sector[:8])
        ns = sd["n_stocks"]
        rows.append({"s": s, "ns": ns, "gp": gp, "ep": ep, "sector": sector,
                      "g_pnl": sd.get("global_exit_pnl", 0),
                      "s_pnl": sd.get("sector_exit_pnl", 0)})

    if not rows:
        return

    fig, ax = plt.subplots(figsize=(28, max(4, len(rows) * 0.6 + 3)))
    ax.axis("off")
    hdr = ["Sector", "Stk", "Global T/M/K", "G.PnL",
           "Sector T/M/K", "S.PnL", "Δ PnL", "Verdict"]
    ct, cc = [], []
    for i, r in enumerate(rows):
        g_str = f"{r['gp']['T']}/{r['gp']['M']}/{r['gp']['K']}"
        s_str = f"{r['ep']['T']}/{r['ep']['M']}/{r['ep']['K']}"
        delta = r["s_pnl"] - r["g_pnl"]
        same = (r['ep']['T'] == r['gp']['T'] and r['ep']['M'] == r['gp']['M'] and r['ep']['K'] == r['gp']['K'])
        verdict = "Same params" if same else (f"+{delta:.0f}%" if delta > 0 else f"{delta:.0f}%")
        ct.append([r["s"], str(r["ns"]), g_str, f"{r['g_pnl']:+.0f}%",
                   s_str, f"{r['s_pnl']:+.0f}%", f"{delta:+.0f}%", verdict])
        bg = "#1a3a1a" if delta > 0 and not same else "#1e1e1e" if i % 2 == 0 else "#252525"
        cc.append([bg] * len(hdr))

    t = ax.table(cellText=ct, colLabels=hdr, loc="center", cellLoc="center")
    t.auto_set_font_size(False); t.set_fontsize(8); t.scale(1.0, 1.7)
    for (r, c), cell in t.get_celld().items():
        if r == 0:
            cell.set_facecolor("#333"); cell.set_text_props(fontweight="bold", color="white")
        else:
            cell.set_facecolor(cc[r-1][c]); cell.set_text_props(color="white")
        cell.set_edgecolor("#444")

    ax.set_title(f"{tf} — Per-Sector Exit Param Sweep (T/M/K)\n"
                 f"Sweep on sector-best C3 combo  •  Exit Flow v4",
                 fontsize=13, fontweight="bold", pad=20)
    plt.tight_layout()
    fig.savefig(out / f"exit_params_{tf}.png")
    plt.close(fig)
    print(f"    Saved exit_params_{tf}.png", flush=True)


def chart_c4_value(tf, sector_data, out):
    """C4 scaling value assessment per sector."""
    rows = []
    for sector in sorted(sector_data.keys()):
        sd = sector_data[sector]
        c4v = sd.get("c4_value")
        if not c4v:
            continue
        s = SECTOR_SHORT.get(sector, sector[:8])
        rows.append({"s": s, **c4v})

    if not rows:
        return

    fig, axes = plt.subplots(1, 2, figsize=(22, max(4, len(rows) * 0.55 + 3)))

    ax = axes[0]
    ax.axis("off")
    hdr = ["Sector", "Trades", "C4%", "HR(C3)", "HR(C4)", "Avg(C3)", "Avg(C4)",
           "PnL(1x)", "PnL(1.5x)", "Lift"]
    ct = []
    for r in rows:
        ct.append([
            r["s"], str(r["n_total"]), f"{r['pct_scaled']:.0f}%",
            f"{r['hr_c3_only']:.0f}", f"{r['hr_c4_scaled']:.0f}",
            f"{r['avg_c3_only']:+.1f}", f"{r['avg_c4_scaled']:+.1f}",
            f"{r['pnl_1x']:+.0f}", f"{r['pnl_15x']:+.0f}",
            f"{r['lift_pct']:+.0f}%",
        ])
    t = ax.table(cellText=ct, colLabels=hdr, loc="center", cellLoc="center")
    t.auto_set_font_size(False); t.set_fontsize(8); t.scale(1.0, 1.6)
    for (r, c), cell in t.get_celld().items():
        if r == 0:
            cell.set_facecolor("#333"); cell.set_text_props(fontweight="bold", color="white", fontsize=7)
        else:
            cell.set_text_props(color="white", fontsize=7)
            cell.set_facecolor("#1e1e1e" if (r-1) % 2 == 0 else "#252525")
        cell.set_edgecolor("#444")

    ax2 = axes[1]
    sectors = [r["s"] for r in rows]
    lifts = [r["lift_pct"] for r in rows]
    colors = ["#66bb6a" if l > 0 else "#ef5350" for l in lifts]
    bars = ax2.barh(range(len(sectors)), lifts, color=colors, edgecolor="white", linewidth=0.5)
    ax2.set_yticks(range(len(sectors)))
    ax2.set_yticklabels(sectors, fontsize=8)
    ax2.set_xlabel("P&L Lift from 1.5x scaling (%)")
    ax2.axvline(0, color="white", linewidth=0.5)
    ax2.grid(True, alpha=0.15, axis="x")
    ax2.set_title("1.5x Scaling Lift by Sector")
    for i, v in enumerate(lifts):
        ax2.text(v + (1 if v >= 0 else -1), i, f"{v:+.0f}%", va="center", fontsize=8, color="white")

    fig.suptitle(f"{tf} — C4 Scale-Up (1.5x) Value Assessment\n"
                 f"Is adding 50% when C4 fires worth it per sector?",
                 fontsize=13, fontweight="bold")
    plt.tight_layout()
    fig.savefig(out / f"c4_value_{tf}.png")
    plt.close(fig)
    print(f"    Saved c4_value_{tf}.png", flush=True)


def chart_audit(all_results, out):
    """Cross-TF audit summary with qualitative comments."""
    rows = []
    for tf in ["4H", "1D", "1W"]:
        tf_data = all_results.get(tf, {})
        for ck in ["C3", "C4"]:
            wins, same, few, glob_better = 0, 0, 0, 0
            sum_g, sum_s = 0, 0
            for sector, sd in tf_data.items():
                g = sd.get(f"global_{ck}")
                b = sd.get(f"sector_{ck}")
                if not b: few += 1; continue
                if g: sum_g += g["total"]
                sum_s += b["total"]
                if not g: continue
                if b["short"] == g["short"]: same += 1
                elif b["total"] > g["total"]: wins += 1
                else: glob_better += 1
            lift = (sum_s - sum_g) / abs(sum_g) * 100 if sum_g else 0
            rows.append({"tf": tf, "ck": ck, "wins": wins, "same": same,
                         "few": few, "glob_better": glob_better,
                         "sum_g": sum_g, "sum_s": sum_s, "lift": lift})

    fig, ax = plt.subplots(figsize=(28, max(4, len(rows) * 0.7 + 6)))
    ax.axis("off")
    hdr = ["TF", "Level", "Sector Wins", "Same", "Global Better", "Too Few",
           "Σ PnL (Global)", "Σ PnL (Sector)", "Lift", "Recommendation"]
    ct = []
    for r in rows:
        if r["lift"] > 20:
            rec = "ADOPT per-sector"
        elif r["lift"] > 5:
            rec = "Consider selective"
        elif r["lift"] > -5:
            rec = "Keep global"
        else:
            rec = "Keep global (sector worse)"
        ct.append([
            r["tf"], r["ck"], str(r["wins"]), str(r["same"]),
            str(r["glob_better"]), str(r["few"]),
            f"{r['sum_g']:+,.0f}%", f"{r['sum_s']:+,.0f}%",
            f"{r['lift']:+.0f}%", rec,
        ])

    t = ax.table(cellText=ct, colLabels=hdr, loc="center", cellLoc="center")
    t.auto_set_font_size(False); t.set_fontsize(8); t.scale(1.0, 1.8)
    for (r, c), cell in t.get_celld().items():
        if r == 0:
            cell.set_facecolor("#333"); cell.set_text_props(fontweight="bold", color="white")
        else:
            cell.set_facecolor("#1e1e1e" if (r-1) % 2 == 0 else "#252525")
            color = "white"
            if c == 9:
                if "ADOPT" in ct[r-1][9]: color = "#66ff66"
                elif "Consider" in ct[r-1][9]: color = "#ffdd44"
                else: color = "#ff8888"
            cell.set_text_props(color=color)
        cell.set_edgecolor("#444")

    audit_text = (
        "AUDIT NOTES:\n"
        "1. Small sectors (<15 stocks) have high variance — sector-specific combos may be overfit.\n"
        "2. C4 per-sector is statistically unreliable in most sectors (4-KPI + HR≥65% = too few trades).\n"
        "3. Exit param sweeps have 100 grid points × 11 sectors — overfitting risk is real.\n"
        "4. Survivorship bias: sample_300 uses current index constituents, not point-in-time.\n"
        "5. No transaction costs or slippage modelled. Real-world P&L will be lower.\n\n"
        "RECOMMENDATIONS:\n"
        "1. Keep global combos as default. Only adopt sector-specific combos for large sectors\n"
        "   (Tech, Industrials, Healthcare, Financials) IF the lift is >20% AND the combo is\n"
        "   intuitively sensible (not random KPI combinations).\n"
        "2. C4 should remain global — too few trades per sector for reliable optimization.\n"
        "3. Exit params: keep global T/M/K unless sector-specific params show >30% lift\n"
        "   with stable neighbours (not grid-edge optima).\n\n"
        "NEXT STEPS:\n"
        "1. Walk-forward validation: split OOS period into 2 halves, optimize on first, test on second.\n"
        "2. Implement the strategy in a live paper-trading simulation with real position management.\n"
        "3. Add slippage + commission model (0.1% round-trip) to get realistic P&L estimates."
    )

    fig.text(0.02, 0.01, audit_text, fontsize=8, color="#ccc", va="bottom",
             fontfamily="monospace",
             bbox=dict(boxstyle="round,pad=0.5", facecolor="#1a1a1a", alpha=0.95))

    ax.set_title("Phase 11 v13 — Per-Sector Optimization Audit (sample_300)\n"
                 "Full entry combo + exit param + 1.5x scaling assessment",
                 fontsize=14, fontweight="bold", pad=25)
    plt.tight_layout(rect=[0, 0.25, 1, 0.93])
    fig.savefig(out / "audit_summary.png")
    plt.close(fig)
    print(f"  Saved audit_summary.png", flush=True)


def output_dir_for(tf, phase):
    return OUTPUTS_ROOT / tf / phase


def main():
    t0 = time.time()
    out_root = OUTPUTS_ROOT / "all" / "phase11v13"
    out_root.mkdir(parents=True, exist_ok=True)

    meta = load_sample300()
    sym_sector = {row["yfinance_ticker"]: row["sector"] for row in meta}
    H_MAP = {"4H": 48, "1D": 40, "1W": 20}
    MIN_TRADES = {"4H": 10, "1D": 10, "1W": 5}
    MIN_STOCKS = 8

    all_results: Dict[str, Dict[str, Any]] = {}

    for tf_key in ["4H", "1D", "1W"]:
        print(f"\n{'='*70}", flush=True)
        print(f"  {tf_key} — Per-Sector Optimizer (sample_300)", flush=True)
        print(f"{'='*70}", flush=True)

        data = load_data_s300(tf_key)
        print(f"  Loaded {len(data)} stocks", flush=True)

        sector_groups: Dict[str, Dict[str, pd.DataFrame]] = {}
        for sym, df in data.items():
            sector = sym_sector.get(sym, "")
            if sector:
                sector_groups.setdefault(sector, {})[sym] = df

        print(f"  Sectors: {len(sector_groups)}", flush=True)
        for sec in sorted(sector_groups.keys()):
            print(f"    {SECTOR_SHORT.get(sec, sec):10s}: {len(sector_groups[sec])} stocks", flush=True)

        avail_kpis = []
        if data:
            sample_df = next(iter(data.values()))
            sm_sample = compute_kpi_state_map(sample_df)
            avail_kpis = [k for k in ALL_KPIS if k in sm_sample]
        print(f"  KPIs available: {len(avail_kpis)}", flush=True)

        p = GLOBAL_EXIT[tf_key]
        T, M, K = p["T"], p["M"], p["K"]
        mt = MIN_TRADES[tf_key]
        H = H_MAP[tf_key]

        tf_results: Dict[str, Any] = {}

        for sector in sorted(sector_groups.keys()):
            sec_data = sector_groups[sector]
            ns = len(sec_data)
            s_short = SECTOR_SHORT.get(sector, sector[:8])

            if ns < MIN_STOCKS:
                print(f"\n  {s_short}: {ns} stocks — SKIPPED (min {MIN_STOCKS})", flush=True)
                tf_results[sector] = {"n_stocks": ns}
                continue

            print(f"\n  {s_short}: {ns} stocks", flush=True)
            sd: Dict[str, Any] = {"n_stocks": ns}

            # C3 screening
            gc3 = GLOBAL_COMBOS[tf_key]["C3"]
            g3 = sim_v4(sec_data, gc3, T, M, K, min_trades=mt)
            if g3:
                g3["kpis"] = gc3; g3["short"] = _sl(gc3)
            sd["global_C3"] = g3

            s3 = screen_best(sec_data, avail_kpis, 3, T, M, K, mt, 0.0, H)
            sd["sector_C3"] = s3
            g3_info = f"G: n={g3['n']} HR={g3['hr']}% PnL={g3['total']:+.0f}%" if g3 else "G: N/A"
            s3_info = f"S: {s3['short']} n={s3['n']} HR={s3['hr']}% PnL={s3['total']:+.0f}%" if s3 else "S: N/A"
            print(f"    C3: {g3_info} | {s3_info}", flush=True)

            # C4 screening
            gc4 = GLOBAL_COMBOS[tf_key]["C4"]
            g4 = sim_v4(sec_data, gc4, T, M, K, min_trades=mt)
            if g4:
                g4["kpis"] = gc4; g4["short"] = _sl(gc4)
            sd["global_C4"] = g4

            s4 = screen_best(sec_data, avail_kpis, 4, T, M, K, mt, HR_FLOOR, H)
            sd["sector_C4"] = s4
            g4_info = f"G: n={g4['n']} HR={g4['hr']}% PnL={g4['total']:+.0f}%" if g4 else "G: N/A"
            s4_info = f"S: {s4['short']} n={s4['n']} HR={s4['hr']}% PnL={s4['total']:+.0f}%" if s4 else "S: N/A"
            print(f"    C4: {g4_info} | {s4_info}", flush=True)

            # Exit param sweep on sector-best C3
            c3_for_sweep = s3 if s3 else g3
            if c3_for_sweep:
                best_exit, best_params, _ = sweep_exit_params(sec_data, c3_for_sweep["kpis"], min_trades=mt)
                if best_params:
                    sd["exit_params"] = best_params
                    sd["sector_exit_pnl"] = best_exit["total"] if best_exit else 0
                    g_exit = sim_v4(sec_data, c3_for_sweep["kpis"], T, M, K, min_trades=mt)
                    sd["global_exit_pnl"] = g_exit["total"] if g_exit else 0
                    print(f"    Exit: Global={T}/{M}/{K} PnL={sd['global_exit_pnl']:+.0f}% | "
                          f"Best={best_params['T']}/{best_params['M']}/{best_params['K']} "
                          f"PnL={sd['sector_exit_pnl']:+.0f}%", flush=True)

            # C4 1.5x value assessment
            c3_kpis = (s3 or g3 or {}).get("kpis", gc3)
            c4_kpis = (s4 or g4 or {}).get("kpis", gc4)
            exit_p = sd.get("exit_params", {"T": T, "M": M, "K": K})
            c4v = unified_c4_assessment(sec_data, c3_kpis, c4_kpis,
                                        exit_p["T"], exit_p["M"], exit_p["K"])
            sd["c4_value"] = c4v
            if c4v:
                print(f"    C4 value: {c4v['pct_scaled']:.0f}% scaled, "
                      f"PnL 1x={c4v['pnl_1x']:+.0f}% → 1.5x={c4v['pnl_15x']:+.0f}% "
                      f"(lift={c4v['lift_pct']:+.0f}%)", flush=True)

            tf_results[sector] = sd

        tf_out = output_dir_for(tf_key, "phase11v13")
        tf_out.mkdir(parents=True, exist_ok=True)
        chart_sector_overview(tf_key, tf_results, tf_out)
        chart_exit_params(tf_key, tf_results, tf_out)
        chart_c4_value(tf_key, tf_results, tf_out)
        all_results[tf_key] = tf_results

    chart_audit(all_results, out_root)

    jp = out_root / "phase11v13_results.json"
    json_out = {}
    for tf, tf_data in all_results.items():
        json_out[tf] = {}
        for sector, sd in tf_data.items():
            json_out[tf][sector] = {k: v for k, v in sd.items()
                                     if k not in ("exit_grid",)}
    jp.write_text(json.dumps(json_out, indent=2, default=str))
    print(f"\n  Saved {jp}", flush=True)
    print(f"\n  Done in {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()

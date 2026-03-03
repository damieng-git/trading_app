"""
Phase 11 v10 — Breakout KPI Combo Screening

Screens all C3 and C4 combos that include at least one breakout-category KPI.
Uses Exit Flow v4 with locked params. HR >= 65% constraint.
Compares against the current locked C3/C4 combos.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "legacy"))

from trading_dashboard.kpis.catalog import compute_kpi_state_map
from trading_dashboard.kpis.rules import STATE_BULL, STATE_NA
from tf_config import ENRICHED_DIR, output_dir_for
from phase8_exit_by_sector import load_data, IS_FRACTION

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

ATR_PERIOD = 14
MAX_HOLD_HARD_CAP = 500
HR_FLOOR = 65.0
MIN_TRADES = {"4H": 30, "1D": 30, "1W": 8}

EXIT_PARAMS = {
    "4H": {"T": 4, "M": 48, "K": 4.0},
    "1D": {"T": 4, "M": 40, "K": 4.0},
    "1W": {"T": 2, "M": 20, "K": 4.0},
}

BREAKOUT_KPIS = [
    "BB 30",
    "Nadaraya-Watson Envelop (MAE)",
    "Nadaraya-Watson Envelop (STD)",
    "Donchian Ribbon",
    "Breakout Targets",
    "SR Breaks",
]

ALL_KPIS = [
    "Nadaraya-Watson Smoother", "TuTCI", "MA Ribbon", "Madrid Ribbon",
    "Donchian Ribbon", "DEMA", "Ichimoku",
    "WT_LB", "SQZMOM_LB", "Stoch_MTM", "CM_Ult_MacD_MFT", "cRSI",
    "ADX & DI", "GMMA", "RSI Strength & Consolidation Zones (Zeiierman)",
    "OBVOSC_LB",
    "Mansfield RS", "SR Breaks",
    "SuperTrend", "UT Bot Alert", "CM_P-SAR",
    "BB 30", "Nadaraya-Watson Envelop (MAE)", "Nadaraya-Watson Envelop (STD)",
    "GK Trend Ribbon", "Impulse Trend", "Volume + MA20",
    "Breakout Targets",
]

LOCKED_COMBOS = {
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


@dataclass
class SA:
    close: np.ndarray
    atr: np.ndarray
    fwd: np.ndarray
    valid: np.ndarray
    bulls: Dict[str, np.ndarray]
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
        a = a_full.iloc[si:].to_numpy(float)
        fwd = np.full(n, np.nan)
        for i in range(n - 1):
            j = min(i + H, n - 1)
            if c[i] > 0:
                fwd[i] = (c[j] - c[i]) / c[i] * 100
        val = np.isfinite(fwd)
        cc.append(c); aa.append(a); ff.append(fwd); vv.append(val)
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
    close = np.concatenate(cc)
    atr = np.concatenate(aa)
    fwd = np.concatenate(ff)
    valid = np.concatenate(vv)
    bulls = {}
    for k in kpis:
        bulls[k] = np.concatenate(kb[k]) if kb[k] else np.zeros(N, dtype=bool)
    cov = {k: ka[k] / ns for k in kpis}
    return SA(close, atr, fwd, valid, bulls, ns, cov)


def screen_combos(sa, k, kpis, min_trades, hr_floor, must_include_breakout=True):
    results = []
    for combo in combinations(kpis, k):
        if must_include_breakout:
            if not any(kpi in BREAKOUT_KPIS for kpi in combo):
                continue
        if any(sa.kpi_cov.get(kk, 0) < 0.3 for kk in combo):
            continue
        mask = sa.valid.copy()
        for kk in combo:
            mask &= sa.bulls[kk]
        n = mask.sum()
        if n < min_trades:
            continue
        rets = sa.fwd[mask]
        hr = np.sum(rets > 0) / n * 100
        if hr < hr_floor:
            continue
        avg = float(np.mean(rets))
        total = float(np.sum(rets))
        med = float(np.median(rets))
        wi = float(np.sum(rets[rets > 0]))
        lo = float(np.abs(np.sum(rets[rets <= 0])))
        pf = wi / lo if lo > 0 else 999.0
        brk_kpis = [kk for kk in combo if kk in BREAKOUT_KPIS]
        results.append({
            "kpis": list(combo), "short": _sl(combo),
            "n": int(n), "hr": round(hr, 1), "avg": round(avg, 2),
            "med": round(med, 2), "total": round(total), "pf": round(pf, 1),
            "breakout_kpis": [KPI_SHORT.get(b, b) for b in brk_kpis],
        })
    results.sort(key=lambda x: x["total"], reverse=True)
    return results


def sim_v4(data, kpis, T, M, K):
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
    if len(rets) < 3:
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
        "avg_hold": round(float(np.mean(holds)), 1),
        "max_hold": int(np.max(holds)),
        "exit_breakdown": {k: v for k, v in ex.items() if v > 0},
    }


def chart_results(all_results, out):
    for tf_key, tf_data in all_results.items():
        rows = []
        for ck in ["C3", "C4"]:
            lock = tf_data.get(f"locked_{ck}")
            if lock:
                rows.append(("LOCKED " + ck, lock))
            for i, r in enumerate(tf_data.get(f"breakout_{ck}", [])[:8]):
                rows.append((f"BRK {ck} #{i+1}", r))
        if not rows:
            continue

        fig, ax = plt.subplots(figsize=(28, max(4, len(rows) * 0.6 + 3)))
        ax.axis("off")
        hdr = ["", "KPIs", "Breakout", "Trades", "HR%", "Avg%", "Med%",
               "PnL", "PF", "AvgHold", "MaxHold"]
        ct, cc_colors = [], []
        for label, r in rows:
            brk = ", ".join(r.get("breakout_kpis", [])) if "breakout_kpis" in r else "-"
            ct.append([
                label, r.get("short", _sl(r.get("kpis", []))), brk,
                str(r["n"]), f"{r['hr']:.0f}", f"{r['avg']:+.2f}",
                f"{r['med']:+.2f}", f"{r['total']:+.0f}",
                f"{r['pf']:.1f}",
                str(r.get("avg_hold", "-")), str(r.get("max_hold", "-")),
            ])
            if "LOCKED" in label:
                cc_colors.append(["#2a3a2a"] * len(hdr))
            else:
                cc_colors.append(["#1e1e1e" if len(cc_colors) % 2 == 0 else "#252525"] * len(hdr))

        t = ax.table(cellText=ct, colLabels=hdr, loc="center", cellLoc="center")
        t.auto_set_font_size(False); t.set_fontsize(8); t.scale(1.0, 1.6)
        for (r, c), cell in t.get_celld().items():
            if r == 0:
                cell.set_facecolor("#333")
                cell.set_text_props(fontweight="bold", color="white")
            else:
                cell.set_facecolor(cc_colors[r - 1][c])
                txt = cell.get_text().get_text()
                if "LOCKED" in ct[r-1][0]:
                    cell.set_text_props(color="#66ff66", fontweight="bold")
                else:
                    cell.set_text_props(color="white")
            cell.set_edgecolor("#444")

        ax.set_title(f"{tf_key} — Breakout Combos vs Locked (Exit Flow v4, HR≥65%)\n"
                     f"Green = current locked combo. Others = best breakout alternatives.",
                     fontsize=13, fontweight="bold", pad=20)
        fig.text(0.02, 0.01,
                 f"Breakout KPIs: BB30, NWE-MAE, NWE-STD, Donch, BrkTgt, SRBrk\n"
                 f"Screening: vectorised fwd-return + full sim_v4 on top candidates. "
                 f"PnL = sum of trade returns (Exit Flow v4, K=4.0).",
                 fontsize=8, color="#aaa", verticalalignment="bottom", fontfamily="monospace",
                 bbox=dict(boxstyle="round,pad=0.4", facecolor="#222", alpha=0.9))
        plt.tight_layout(rect=[0, 0.06, 1, 0.94])
        fig.savefig(out / f"breakout_combos_{tf_key}.png")
        plt.close(fig)
        print(f"    Saved breakout_combos_{tf_key}.png")


def main():
    t0 = time.time()
    out = output_dir_for("all", "phase11v10")
    out.mkdir(parents=True, exist_ok=True)
    all_results = {}

    H_MAP = {"4H": 48, "1D": 40, "1W": 20}

    for tf_key in ["4H", "1D", "1W"]:
        print(f"\n{'='*70}")
        print(f"  {tf_key}")
        print(f"{'='*70}")

        data = load_data(ENRICHED_DIR, tf_key)
        print(f"  Loaded {len(data)} stocks")

        avail_kpis = []
        sample_df = next(iter(data.values()))
        sm_sample = compute_kpi_state_map(sample_df)
        for k in ALL_KPIS:
            if k in sm_sample:
                avail_kpis.append(k)
        brk_avail = [k for k in BREAKOUT_KPIS if k in avail_kpis]
        print(f"  Available KPIs: {len(avail_kpis)}")
        print(f"  Available breakout KPIs: {', '.join(_sl([b]) for b in brk_avail)}")

        sa = build_sa(data, avail_kpis, H_MAP[tf_key])
        if sa is None:
            print(f"  No data!"); continue
        print(f"  {sa.n_stocks} stocks in arrays")

        brk_cov = {k: sa.kpi_cov.get(k, 0) for k in BREAKOUT_KPIS}
        print(f"  Breakout coverage: { {KPI_SHORT.get(k,k): f'{v:.0%}' for k,v in brk_cov.items() if v > 0} }")

        p = EXIT_PARAMS[tf_key]
        T, M, K = p["T"], p["M"], p["K"]
        mt = MIN_TRADES[tf_key]
        tf_results = {}

        for ck, k_size in [("C3", 3), ("C4", 4)]:
            print(f"\n  --- {ck} (k={k_size}) ---")
            t1 = time.time()
            screened = screen_combos(sa, k_size, avail_kpis, mt, HR_FLOOR, must_include_breakout=True)
            print(f"  Screen: {len(screened)} breakout combos passed HR>={HR_FLOOR}% ({time.time()-t1:.1f}s)")

            locked_kpis = LOCKED_COMBOS[tf_key].get(ck)
            locked_sim = None
            if locked_kpis:
                locked_sim = sim_v4(data, locked_kpis, T, M, K)
                if locked_sim:
                    locked_sim["kpis"] = locked_kpis
                    locked_sim["short"] = _sl(locked_kpis)
                    locked_sim["breakout_kpis"] = [KPI_SHORT.get(b, b) for b in locked_kpis if b in BREAKOUT_KPIS]
                    print(f"  Locked {ck}: {_sl(locked_kpis)}")
                    print(f"    n={locked_sim['n']} HR={locked_sim['hr']}% "
                          f"Avg={locked_sim['avg']:+.2f}% PnL={locked_sim['total']:+.0f}% "
                          f"PF={locked_sim['pf']}")

            top = screened[:15]
            sim_results = []
            for cand in top:
                sr = sim_v4(data, cand["kpis"], T, M, K)
                if sr and sr["hr"] >= HR_FLOOR:
                    sr["kpis"] = cand["kpis"]
                    sr["short"] = cand["short"]
                    sr["breakout_kpis"] = cand["breakout_kpis"]
                    sim_results.append(sr)
                    if len(sim_results) <= 5:
                        brk_label = ", ".join(sr["breakout_kpis"])
                        print(f"    #{len(sim_results)} {sr['short']} [{brk_label}]")
                        print(f"       n={sr['n']} HR={sr['hr']}% Avg={sr['avg']:+.2f}% "
                              f"PnL={sr['total']:+.0f}% PF={sr['pf']} "
                              f"Hold={sr['avg_hold']}/{sr['max_hold']}")

            sim_results.sort(key=lambda x: x["total"], reverse=True)
            tf_results[f"locked_{ck}"] = locked_sim
            tf_results[f"breakout_{ck}"] = sim_results

        all_results[tf_key] = tf_results

    chart_results(all_results, out)

    jp = out / "phase11v10_breakout_combos.json"
    summary = {}
    for tf_key, tf_data in all_results.items():
        summary[tf_key] = {}
        for ck in ["C3", "C4"]:
            lock = tf_data.get(f"locked_{ck}")
            brks = tf_data.get(f"breakout_{ck}", [])[:5]
            summary[tf_key][ck] = {
                "locked": lock,
                "top_breakout": brks,
                "breakout_beats_locked": any(
                    b["total"] > (lock["total"] if lock else 0) for b in brks
                ) if brks else False,
            }
    jp.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\n  Saved results to {jp}")

    print(f"\n{'='*70}")
    print(f"  SUMMARY")
    print(f"{'='*70}")
    for tf_key, tf_data in all_results.items():
        print(f"\n  {tf_key}:")
        for ck in ["C3", "C4"]:
            lock = tf_data.get(f"locked_{ck}")
            brks = tf_data.get(f"breakout_{ck}", [])[:3]
            if lock:
                print(f"    Locked {ck}: {lock['short']} → PnL={lock['total']:+.0f}% HR={lock['hr']}%")
            for i, b in enumerate(brks):
                marker = ">>>" if lock and b["total"] > lock["total"] else "   "
                print(f"    {marker} Brk {ck} #{i+1}: {b['short']} → PnL={b['total']:+.0f}% HR={b['hr']}% [{', '.join(b['breakout_kpis'])}]")

    print(f"\n  Done in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

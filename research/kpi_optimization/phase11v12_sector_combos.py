"""
Phase 11 v12 — Per-Sector Entry Combo Analysis

For each sector, finds the best C3 (workhorse, P&L-optimized) and C4
(golden combo, P&L with HR >= 65%) using Exit Flow v4 with locked params.

Compares per-sector winners against the global locked combos.
"""

from __future__ import annotations

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
sys.path.insert(0, str(Path(__file__).resolve().parent / "legacy"))

from trading_dashboard.kpis.catalog import compute_kpi_state_map
from trading_dashboard.kpis.rules import STATE_BULL
from tf_config import ENRICHED_DIR, output_dir_for
from phase8_exit_by_sector import load_data, IS_FRACTION
from apps.dashboard.sector_map import load_sector_map

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

EXIT_PARAMS = {
    "4H": {"T": 4, "M": 48, "K": 4.0},
    "1D": {"T": 4, "M": 40, "K": 4.0},
    "1W": {"T": 2, "M": 20, "K": 4.0},
}

MIN_TRADES_SECTOR = {"4H": 10, "1D": 10, "1W": 5}
MIN_STOCKS_SECTOR = 5

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

SECTOR_SHORT = {
    "Technology": "Tech", "Financial Services": "Financ",
    "Consumer Cyclical": "ConsCyc", "Consumer Defensive": "ConsDef",
    "Industrials": "Indust", "Healthcare": "Health",
    "Communication Services": "Comms", "Energy": "Energy",
    "Basic Materials": "BasMat", "Real Estate": "RealEst",
    "Utilities": "Utils",
}


def _sl(kpis):
    return " + ".join(KPI_SHORT.get(k, k[:6]) for k in kpis)


def compute_atr(df, period=14):
    h, l, pc = df["High"], df["Low"], df["Close"].shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()


def sim_v4(data, kpis, T, M, K, min_trades=3):
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
    """Vectorised screening arrays for fast pre-filter."""
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


def fast_screen(data, avail_kpis, k_size, T, M, K, min_trades, hr_floor=0.0, H=40, top_n=15):
    """Two-stage: vectorised pre-screen then full sim_v4 on top candidates."""
    sa = build_sa(data, avail_kpis, H)
    if sa is None:
        return []

    pre = []
    for combo in combinations(avail_kpis, k_size):
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

    results = []
    for cand in pre[:top_n]:
        r = sim_v4(data, cand["kpis"], T, M, K, min_trades=min_trades)
        if r is None:
            continue
        if r["hr"] < hr_floor:
            continue
        r["kpis"] = cand["kpis"]
        r["short"] = cand["short"]
        results.append(r)

    results.sort(key=lambda x: x["total"], reverse=True)
    return results


def chart_sector_table(tf_key, sector_results, out):
    """Big table: one row per sector, showing global vs sector-best C3 and C4."""
    rows = []
    for sector in sorted(sector_results.keys()):
        sr = sector_results[sector]
        n_stocks = sr["n_stocks"]
        s_short = SECTOR_SHORT.get(sector, sector[:8])

        for ck in ["C3", "C4"]:
            glob = sr.get(f"global_{ck}")
            best = sr.get(f"best_{ck}")
            if not glob and not best:
                continue
            rows.append({
                "sector": s_short, "n_stocks": n_stocks, "ck": ck,
                "global": glob, "best": best,
            })

    if not rows:
        return

    fig, ax = plt.subplots(figsize=(34, max(5, len(rows) * 0.55 + 4)))
    ax.axis("off")
    hdr = ["Sector", "Stk", "Lvl",
           "Global Combo", "G.Trades", "G.HR%", "G.PnL", "G.PF",
           "Sector-Best Combo", "S.Trades", "S.HR%", "S.PnL", "S.PF",
           "Verdict"]
    ct, cc = [], []

    for i, row in enumerate(rows):
        g = row["global"]
        b = row["best"]

        g_short = g["short"] if g else "-"
        g_n = str(g["n"]) if g else "-"
        g_hr = f"{g['hr']:.0f}" if g else "-"
        g_pnl = f"{g['total']:+.0f}" if g else "-"
        g_pf = f"{g['pf']:.1f}" if g else "-"

        b_short = b["short"] if b else "-"
        b_n = str(b["n"]) if b else "-"
        b_hr = f"{b['hr']:.0f}" if b else "-"
        b_pnl = f"{b['total']:+.0f}" if b else "-"
        b_pf = f"{b['pf']:.1f}" if b else "-"

        if g and b and b["total"] > g["total"] and b["short"] != g["short"]:
            lift = b["total"] - g["total"]
            verdict = f"+{lift:.0f}% sector win"
        elif g and b and b["short"] == g["short"]:
            verdict = "Same combo"
        elif g and not b:
            verdict = "Too few trades"
        elif not g and b:
            verdict = "Global N/A"
        else:
            verdict = "Global better"

        ct.append([
            row["sector"], str(row["n_stocks"]), row["ck"],
            g_short, g_n, g_hr, g_pnl, g_pf,
            b_short, b_n, b_hr, b_pnl, b_pf,
            verdict,
        ])

        if "sector win" in verdict:
            bg = "#1a3a1a"
        elif "Same" in verdict:
            bg = "#2a2a1a"
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
            cell.set_facecolor(cc[r - 1][c])
            color = "white"
            if c == 13:
                if "sector win" in ct[r-1][13]:
                    color = "#66ff66"
                elif "Same" in ct[r-1][13]:
                    color = "#ffdd44"
                elif "Too few" in ct[r-1][13]:
                    color = "#ff6666"
            cell.set_text_props(color=color, fontsize=7)
        cell.set_edgecolor("#444")

    ax.set_title(f"{tf_key} — Per-Sector Entry Combos: Global vs Sector-Best\n"
                 f"C3 = workhorse (P&L opt), C4 = golden (P&L + HR≥65%)",
                 fontsize=13, fontweight="bold", pad=20)
    fig.text(0.02, 0.01,
             "Green = sector-specific combo beats global. Yellow = same combo wins. "
             "Red = too few trades for reliable sector combo.\n"
             "G. = Global combo applied to this sector only. S. = Best combo found within this sector.",
             fontsize=8, color="#aaa", verticalalignment="bottom", fontfamily="monospace",
             bbox=dict(boxstyle="round,pad=0.4", facecolor="#222", alpha=0.9))
    plt.tight_layout(rect=[0, 0.06, 1, 0.93])
    fig.savefig(out / f"sector_combos_{tf_key}.png")
    plt.close(fig)
    print(f"    Saved sector_combos_{tf_key}.png")


def chart_summary(all_results, out):
    """Cross-TF summary: how many sectors benefit from sector-specific combos."""
    rows = []
    for tf in ["4H", "1D", "1W"]:
        tf_data = all_results.get(tf, {})
        for ck in ["C3", "C4"]:
            total_sectors = 0
            sector_wins = 0
            same_combo = 0
            too_few = 0
            total_pnl_global = 0
            total_pnl_sector = 0
            for sector, sr in tf_data.items():
                total_sectors += 1
                g = sr.get(f"global_{ck}")
                b = sr.get(f"best_{ck}")
                if not b:
                    too_few += 1
                    continue
                if g and b["short"] == g["short"]:
                    same_combo += 1
                elif g and b["total"] > g["total"]:
                    sector_wins += 1
                if g:
                    total_pnl_global += g["total"]
                total_pnl_sector += b["total"]
            rows.append({
                "tf": tf, "ck": ck, "sectors": total_sectors,
                "wins": sector_wins, "same": same_combo, "few": too_few,
                "pnl_g": total_pnl_global, "pnl_s": total_pnl_sector,
            })

    fig, ax = plt.subplots(figsize=(24, max(4, len(rows) * 0.7 + 3)))
    ax.axis("off")
    hdr = ["TF", "Level", "Sectors", "Sector Wins", "Same Combo", "Too Few",
           "Σ PnL (Global)", "Σ PnL (Sector-Best)", "Lift"]
    ct, cc_colors = [], []
    for i, r in enumerate(rows):
        lift = (r["pnl_s"] - r["pnl_g"]) / abs(r["pnl_g"]) * 100 if r["pnl_g"] else 0
        ct.append([
            r["tf"], r["ck"], str(r["sectors"]),
            str(r["wins"]), str(r["same"]), str(r["few"]),
            f"{r['pnl_g']:+,.0f}%", f"{r['pnl_s']:+,.0f}%",
            f"{lift:+.0f}%",
        ])
        cc_colors.append(["#1e1e1e" if i % 2 == 0 else "#252525"] * len(hdr))

    t = ax.table(cellText=ct, colLabels=hdr, loc="center", cellLoc="center")
    t.auto_set_font_size(False); t.set_fontsize(9); t.scale(1.0, 1.8)
    for (r, c), cell in t.get_celld().items():
        if r == 0:
            cell.set_facecolor("#333")
            cell.set_text_props(fontweight="bold", color="white")
        else:
            cell.set_facecolor(cc_colors[r - 1][c])
            cell.set_text_props(color="white")
            if c == 8:
                val = ct[r-1][8]
                if val.startswith("+"):
                    cell.set_text_props(color="#66ff66", fontweight="bold")
                elif val.startswith("-"):
                    cell.set_text_props(color="#ff6666", fontweight="bold")
        cell.set_edgecolor("#444")

    ax.set_title("Phase 11 v12 — Sector Optimization: Is It Worth It?\n"
                 "Sum of per-sector P&L using global combos vs per-sector best combos",
                 fontsize=14, fontweight="bold", pad=25)
    fig.text(0.02, 0.01,
             "Sector Wins = # sectors where a different combo beats the global one.\n"
             "Same Combo = global combo is already the best for that sector.\n"
             "Too Few = sector has insufficient trades for reliable combo selection.\n"
             "Lift = total P&L improvement if each sector used its own best combo.",
             fontsize=8.5, color="#aaa", verticalalignment="bottom", fontfamily="monospace",
             bbox=dict(boxstyle="round,pad=0.4", facecolor="#222", alpha=0.9))
    plt.tight_layout(rect=[0, 0.08, 1, 0.93])
    fig.savefig(out / "sector_summary.png")
    plt.close(fig)
    print(f"  Saved sector_summary.png")


def main():
    t0 = time.time()
    out_root = output_dir_for("all", "phase11v12")
    out_root.mkdir(parents=True, exist_ok=True)

    sm = load_sector_map()
    all_results: Dict[str, Dict[str, Any]] = {}

    for tf_key in ["4H", "1D", "1W"]:
        print(f"\n{'='*70}")
        print(f"  {tf_key} — Per-Sector Combo Screening")
        print(f"{'='*70}")

        data = load_data(ENRICHED_DIR, tf_key)
        print(f"  Loaded {len(data)} stocks total")

        sector_groups: Dict[str, Dict[str, pd.DataFrame]] = {}
        for sym, df in data.items():
            entry = sm.get(sym, {})
            sector = entry.get("sector", "")
            if not sector:
                continue
            sector_groups.setdefault(sector, {})[sym] = df

        print(f"  Sectors with data: {len(sector_groups)}")
        for sec in sorted(sector_groups.keys()):
            print(f"    {SECTOR_SHORT.get(sec, sec):10s}: {len(sector_groups[sec])} stocks")

        p = EXIT_PARAMS[tf_key]
        T, M, K = p["T"], p["M"], p["K"]
        mt = MIN_TRADES_SECTOR[tf_key]

        avail_kpis = []
        sample_df = next(iter(data.values()))
        sm_sample = compute_kpi_state_map(sample_df)
        for k in ALL_KPIS:
            if k in sm_sample:
                avail_kpis.append(k)

        tf_results: Dict[str, Any] = {}

        for sector in sorted(sector_groups.keys()):
            sec_data = sector_groups[sector]
            n_stocks = len(sec_data)
            s_short = SECTOR_SHORT.get(sector, sector[:8])

            if n_stocks < MIN_STOCKS_SECTOR:
                print(f"\n  {s_short}: {n_stocks} stocks — SKIPPED (min {MIN_STOCKS_SECTOR})")
                tf_results[sector] = {"n_stocks": n_stocks}
                continue

            print(f"\n  {s_short}: {n_stocks} stocks")
            sr: Dict[str, Any] = {"n_stocks": n_stocks}

            for ck, k_size, hr_floor in [("C3", 3, 0.0), ("C4", 4, HR_FLOOR)]:
                t1 = time.time()

                # Global combo on this sector's data
                locked_kpis = LOCKED_COMBOS[tf_key][ck]
                glob_r = sim_v4(sec_data, locked_kpis, T, M, K, min_trades=mt)
                if glob_r:
                    glob_r["kpis"] = locked_kpis
                    glob_r["short"] = _sl(locked_kpis)
                sr[f"global_{ck}"] = glob_r

                # Screen all combos on this sector's data
                H_MAP = {"4H": 48, "1D": 40, "1W": 20}
                screened = fast_screen(sec_data, avail_kpis, k_size, T, M, K,
                                       min_trades=mt, hr_floor=hr_floor,
                                       H=H_MAP[tf_key])
                best = screened[0] if screened else None
                sr[f"best_{ck}"] = best
                sr[f"top3_{ck}"] = screened[:3]

                elapsed = time.time() - t1
                g_info = f"Global: n={glob_r['n']} PnL={glob_r['total']:+.0f}%" if glob_r else "Global: N/A"
                b_info = f"Best: {best['short']} n={best['n']} PnL={best['total']:+.0f}%" if best else "Best: N/A"
                print(f"    {ck} ({elapsed:.0f}s): {g_info} | {b_info}")

            tf_results[sector] = sr

        tf_out = output_dir_for(tf_key, "phase11v12")
        tf_out.mkdir(parents=True, exist_ok=True)
        chart_sector_table(tf_key, tf_results, tf_out)
        all_results[tf_key] = tf_results

    chart_summary(all_results, out_root)

    # Save JSON
    json_out = {}
    for tf, tf_data in all_results.items():
        json_out[tf] = {}
        for sector, sr in tf_data.items():
            entry = {"n_stocks": sr["n_stocks"]}
            for ck in ["C3", "C4"]:
                g = sr.get(f"global_{ck}")
                b = sr.get(f"best_{ck}")
                entry[f"global_{ck}"] = g
                entry[f"best_{ck}"] = b
            json_out[tf][sector] = entry

    jp = out_root / "phase11v12_sector_combos.json"
    jp.write_text(json.dumps(json_out, indent=2, default=str))
    print(f"\n  Saved {jp}")

    # Final verdict
    print(f"\n{'='*70}")
    print(f"  VERDICT")
    print(f"{'='*70}")
    for tf in ["4H", "1D", "1W"]:
        print(f"\n  {tf}:")
        tf_data = all_results.get(tf, {})
        for ck in ["C3", "C4"]:
            wins, same, few = 0, 0, 0
            for sector, sr in tf_data.items():
                g = sr.get(f"global_{ck}")
                b = sr.get(f"best_{ck}")
                if not b:
                    few += 1
                elif g and b["short"] == g["short"]:
                    same += 1
                elif g and b["total"] > g["total"]:
                    wins += 1
            print(f"    {ck}: {wins} sector wins, {same} same combo, {few} too few")

    print(f"\n  Done in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

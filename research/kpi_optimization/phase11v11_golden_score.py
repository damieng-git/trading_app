"""
Phase 11 v11 — Golden Score C4 Optimization

Re-screens ALL C4 combos ranked by golden_score instead of raw P&L.

    golden_score = HR × PF / |worst_loss|

This favours combos that are:
  - Right more often (high HR)
  - Asymmetric winners (high PF)
  - Shallow downside (small worst loss)

Uses Exit Flow v4 with locked params. HR >= 65% floor.
Compares golden-score winners against the current P&L-optimized locked C4s.
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

LOCKED_C4 = {
    "4H": ["Nadaraya-Watson Smoother", "Madrid Ribbon", "GK Trend Ribbon", "cRSI"],
    "1D": ["Nadaraya-Watson Smoother", "Madrid Ribbon", "GK Trend Ribbon", "cRSI"],
    "1W": ["Nadaraya-Watson Smoother", "Stoch_MTM", "cRSI", "Volume + MA20"],
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


def sim_v4(data, kpis, T, M, K):
    """Full bar-by-bar Exit Flow v4 simulation. Returns detailed metrics."""
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
    pf = wi / lo if lo > 0 else 999.0
    worst = min(rets)
    avg_win = float(np.mean([r for r in rets if r > 0])) if any(r > 0 for r in rets) else 0
    avg_loss = float(np.mean([r for r in rets if r <= 0])) if any(r <= 0 for r in rets) else 0
    gs = hr * pf / abs(worst) if worst != 0 else hr * pf
    return {
        "n": n, "hr": round(hr, 1), "avg": round(float(np.mean(rets)), 2),
        "med": round(float(np.median(rets)), 2),
        "total": round(float(np.sum(rets))),
        "pf": round(pf, 1), "worst": round(worst, 1),
        "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
        "golden_score": round(gs, 1),
        "avg_hold": round(float(np.mean(holds)), 1),
        "max_hold": int(np.max(holds)),
        "exit_breakdown": {k: v for k, v in ex.items() if v > 0},
    }


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


def prescreen_c4(sa, kpis, min_trades, hr_floor):
    """Fast vectorised pre-screen: returns combos passing HR floor, ranked by golden_score proxy."""
    results = []
    for combo in combinations(kpis, 4):
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
        worst = float(np.min(rets))
        wi = float(np.sum(rets[rets > 0]))
        lo = float(np.abs(np.sum(rets[rets <= 0])))
        pf = wi / lo if lo > 0 else 999.0
        gs = hr * pf / abs(worst) if worst != 0 else hr * pf
        results.append({
            "kpis": list(combo), "short": _sl(combo),
            "n": int(n), "hr": round(hr, 1), "avg": round(avg, 2),
            "total": round(total), "pf": round(pf, 1),
            "worst": round(worst, 1), "golden_score": round(gs, 1),
        })
    results.sort(key=lambda x: x["golden_score"], reverse=True)
    return results


def chart_comparison(tf_key, locked_sim, top_golden, out):
    """Side-by-side table: locked C4 vs top golden-score C4 candidates."""
    rows = []
    if locked_sim:
        rows.append(("LOCKED C4 (P&L opt)", locked_sim))
    for i, r in enumerate(top_golden[:10]):
        rows.append((f"Golden #{i+1}", r))
    if not rows:
        return

    fig, ax = plt.subplots(figsize=(30, max(4, len(rows) * 0.65 + 3)))
    ax.axis("off")
    hdr = ["", "KPIs", "Trades", "HR%", "Avg%", "AvgWin", "AvgLoss",
           "Worst", "PnL", "PF", "GoldenScore", "AvgHold"]
    ct, cc_colors = [], []
    for label, r in rows:
        ct.append([
            label, r.get("short", _sl(r.get("kpis", []))),
            str(r["n"]), f"{r['hr']:.0f}", f"{r['avg']:+.2f}",
            f"{r.get('avg_win', '-'):+.2f}" if isinstance(r.get('avg_win'), (int, float)) else "-",
            f"{r.get('avg_loss', '-'):+.2f}" if isinstance(r.get('avg_loss'), (int, float)) else "-",
            f"{r['worst']:+.1f}",
            f"{r['total']:+.0f}", f"{r['pf']:.1f}",
            f"{r['golden_score']:.0f}",
            str(r.get("avg_hold", "-")),
        ])
        if "LOCKED" in label:
            cc_colors.append(["#2a3a2a"] * len(hdr))
        else:
            cc_colors.append(["#1e1e1e" if len(cc_colors) % 2 == 0 else "#252525"] * len(hdr))

    t = ax.table(cellText=ct, colLabels=hdr, loc="center", cellLoc="center")
    t.auto_set_font_size(False); t.set_fontsize(8); t.scale(1.0, 1.7)
    for (r, c), cell in t.get_celld().items():
        if r == 0:
            cell.set_facecolor("#333")
            cell.set_text_props(fontweight="bold", color="white")
        else:
            cell.set_facecolor(cc_colors[r - 1][c])
            if "LOCKED" in ct[r-1][0]:
                cell.set_text_props(color="#66ff66", fontweight="bold")
            else:
                cell.set_text_props(color="white")
            # Highlight golden score column
            if c == 10:
                try:
                    gs_val = float(ct[r-1][10])
                    best_gs = max(float(row[1]["golden_score"]) for row in rows)
                    if gs_val >= best_gs * 0.95:
                        cell.set_text_props(color="#ffdd44", fontweight="bold")
                except (ValueError, KeyError):
                    pass
        cell.set_edgecolor("#444")

    ax.set_title(f"{tf_key} — C4 Golden Score Ranking\n"
                 f"golden_score = HR × PF / |worst_loss|  •  HR ≥ 65%  •  Exit Flow v4",
                 fontsize=13, fontweight="bold", pad=20)
    fig.text(0.02, 0.01,
             "Green row = current locked C4 (optimized for P&L).\n"
             "GoldenScore rewards high hit rate, strong win/loss asymmetry, and shallow worst loss.\n"
             "Higher golden score = better scale-up signal (more confidence per trade).",
             fontsize=8.5, color="#aaa", verticalalignment="bottom", fontfamily="monospace",
             bbox=dict(boxstyle="round,pad=0.4", facecolor="#222", alpha=0.9))
    plt.tight_layout(rect=[0, 0.07, 1, 0.93])
    fig.savefig(out / f"golden_score_{tf_key}.png")
    plt.close(fig)
    print(f"    Saved golden_score_{tf_key}.png")


def chart_scatter(tf_key, locked_sim, top_golden, out):
    """Scatter: HR vs PF, sized by trades, colored by golden score."""
    fig, ax = plt.subplots(figsize=(12, 8))
    all_pts = list(top_golden[:30])
    if locked_sim:
        all_pts.append(locked_sim)

    hrs = [p["hr"] for p in all_pts]
    pfs = [p["pf"] for p in all_pts]
    ns = [p["n"] for p in all_pts]
    gs = [p["golden_score"] for p in all_pts]
    worsts = [abs(p["worst"]) for p in all_pts]

    sc = ax.scatter(hrs[:-1] if locked_sim else hrs,
                    pfs[:-1] if locked_sim else pfs,
                    s=[max(20, n/5) for n in (ns[:-1] if locked_sim else ns)],
                    c=gs[:-1] if locked_sim else gs,
                    cmap="YlOrRd", alpha=0.7, edgecolors="white", linewidth=0.5)
    plt.colorbar(sc, label="Golden Score", ax=ax)

    if locked_sim:
        ax.scatter([hrs[-1]], [pfs[-1]], s=200, c="#66ff66", marker="*",
                   edgecolors="white", linewidth=1.5, zorder=10, label="Locked C4")
        ax.annotate("LOCKED", (hrs[-1], pfs[-1]), fontsize=9, color="#66ff66",
                    fontweight="bold", xytext=(8, 8), textcoords="offset points")

    if top_golden:
        best = top_golden[0]
        ax.scatter([best["hr"]], [best["pf"]], s=200, c="#ffdd44", marker="D",
                   edgecolors="white", linewidth=1.5, zorder=10, label="Best Golden")
        ax.annotate(best["short"], (best["hr"], best["pf"]), fontsize=8,
                    color="#ffdd44", xytext=(8, -12), textcoords="offset points")

    ax.set_xlabel("Hit Rate (%)")
    ax.set_ylabel("Profit Factor")
    ax.set_title(f"{tf_key} — C4 Combos: HR vs PF (size=trades, color=golden score)")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.15)
    plt.tight_layout()
    fig.savefig(out / f"golden_scatter_{tf_key}.png")
    plt.close(fig)
    print(f"    Saved golden_scatter_{tf_key}.png")


def chart_summary(all_data, out):
    """Cross-TF summary table."""
    rows = []
    for tf in ["4H", "1D", "1W"]:
        d = all_data.get(tf, {})
        lock = d.get("locked")
        best = d.get("best_golden")
        if lock:
            rows.append((tf, "LOCKED (P&L)", lock))
        if best:
            rows.append((tf, "GOLDEN #1", best))

    if not rows:
        return

    fig, ax = plt.subplots(figsize=(30, max(4, len(rows) * 0.7 + 3)))
    ax.axis("off")
    hdr = ["TF", "Type", "KPIs", "Trades", "HR%", "Avg%", "AvgWin", "AvgLoss",
           "Worst", "PnL", "PF", "GScore", "Verdict"]
    ct, cc_colors = [], []
    for tf, typ, r in rows:
        if typ == "LOCKED (P&L)":
            verdict = "Current"
        else:
            lock = all_data[tf].get("locked")
            if lock:
                gs_lift = (r["golden_score"] - lock["golden_score"]) / lock["golden_score"] * 100 if lock["golden_score"] else 0
                pnl_change = (r["total"] - lock["total"]) / abs(lock["total"]) * 100 if lock["total"] else 0
                verdict = f"GS {gs_lift:+.0f}%, PnL {pnl_change:+.0f}%"
            else:
                verdict = "N/A"
        ct.append([
            tf, typ, r.get("short", _sl(r.get("kpis", []))),
            str(r["n"]), f"{r['hr']:.0f}", f"{r['avg']:+.2f}",
            f"{r.get('avg_win', 0):+.2f}", f"{r.get('avg_loss', 0):+.2f}",
            f"{r['worst']:+.1f}",
            f"{r['total']:+.0f}", f"{r['pf']:.1f}",
            f"{r['golden_score']:.0f}", verdict,
        ])
        bg = "#2a3a2a" if "LOCKED" in typ else "#1e2e3e"
        cc_colors.append([bg] * len(hdr))

    t = ax.table(cellText=ct, colLabels=hdr, loc="center", cellLoc="center")
    t.auto_set_font_size(False); t.set_fontsize(8); t.scale(1.0, 1.8)
    for (r, c), cell in t.get_celld().items():
        if r == 0:
            cell.set_facecolor("#333")
            cell.set_text_props(fontweight="bold", color="white")
        else:
            cell.set_facecolor(cc_colors[r - 1][c])
            if "LOCKED" in ct[r-1][1]:
                cell.set_text_props(color="#66ff66", fontweight="bold")
            elif "GOLDEN" in ct[r-1][1]:
                cell.set_text_props(color="#ffdd44", fontweight="bold")
            else:
                cell.set_text_props(color="white")
        cell.set_edgecolor("#444")

    ax.set_title("Phase 11 v11 — Golden Score C4: Locked vs Best Alternative\n"
                 "golden_score = HR × PF / |worst|  •  Higher = more confidence to scale up",
                 fontsize=14, fontweight="bold", pad=25)
    fig.text(0.02, 0.01,
             "Green = current locked C4 (P&L optimized). Yellow = best golden-score C4.\n"
             "Verdict shows golden score lift and P&L trade-off vs locked.\n"
             "A golden C4 with higher GScore but lower PnL is still better for scale-up confidence.",
             fontsize=8.5, color="#aaa", verticalalignment="bottom", fontfamily="monospace",
             bbox=dict(boxstyle="round,pad=0.4", facecolor="#222", alpha=0.9))
    plt.tight_layout(rect=[0, 0.07, 1, 0.93])
    fig.savefig(out / "golden_summary.png")
    plt.close(fig)
    print(f"  Saved golden_summary.png")


def main():
    t0 = time.time()
    out = output_dir_for("all", "phase11v11")
    out.mkdir(parents=True, exist_ok=True)
    all_data = {}
    H_MAP = {"4H": 48, "1D": 40, "1W": 20}

    for tf_key in ["4H", "1D", "1W"]:
        print(f"\n{'='*70}")
        print(f"  {tf_key} — C4 Golden Score Screening")
        print(f"{'='*70}")

        data = load_data(ENRICHED_DIR, tf_key)
        print(f"  Loaded {len(data)} stocks")

        avail_kpis = []
        sample_df = next(iter(data.values()))
        sm_sample = compute_kpi_state_map(sample_df)
        for k in ALL_KPIS:
            if k in sm_sample:
                avail_kpis.append(k)
        print(f"  Available KPIs: {len(avail_kpis)}")

        sa = build_sa(data, avail_kpis, H_MAP[tf_key])
        if sa is None:
            print(f"  No data!"); continue
        print(f"  {sa.n_stocks} stocks in arrays")

        p = EXIT_PARAMS[tf_key]
        T, M, K = p["T"], p["M"], p["K"]
        mt = MIN_TRADES[tf_key]

        # Pre-screen all C4 combos by golden score
        t1 = time.time()
        prescreened = prescreen_c4(sa, avail_kpis, mt, HR_FLOOR)
        print(f"  Pre-screen: {len(prescreened)} C4 combos passed HR>={HR_FLOOR}% ({time.time()-t1:.1f}s)")

        # Full sim on top 25 by golden score
        print(f"  Running full sim_v4 on top 25 golden candidates...")
        top_golden = []
        for cand in prescreened[:25]:
            sr = sim_v4(data, cand["kpis"], T, M, K)
            if sr and sr["hr"] >= HR_FLOOR:
                sr["kpis"] = cand["kpis"]
                sr["short"] = cand["short"]
                top_golden.append(sr)

        top_golden.sort(key=lambda x: x["golden_score"], reverse=True)

        # Sim the locked C4
        locked_kpis = LOCKED_C4[tf_key]
        locked_sim = sim_v4(data, locked_kpis, T, M, K)
        if locked_sim:
            locked_sim["kpis"] = locked_kpis
            locked_sim["short"] = _sl(locked_kpis)

        # Print results
        print(f"\n  Locked C4: {_sl(locked_kpis)}")
        if locked_sim:
            print(f"    n={locked_sim['n']} HR={locked_sim['hr']}% Avg={locked_sim['avg']:+.2f}% "
                  f"PnL={locked_sim['total']:+.0f}% PF={locked_sim['pf']} "
                  f"Worst={locked_sim['worst']:+.1f}% "
                  f"GoldenScore={locked_sim['golden_score']:.0f}")

        print(f"\n  Top 10 Golden Score C4:")
        for i, r in enumerate(top_golden[:10]):
            marker = ">>>" if locked_sim and r["golden_score"] > locked_sim["golden_score"] else "   "
            print(f"  {marker} #{i+1} {r['short']}")
            print(f"       n={r['n']} HR={r['hr']}% Avg={r['avg']:+.2f}% "
                  f"PnL={r['total']:+.0f}% PF={r['pf']} "
                  f"Worst={r['worst']:+.1f}% AvgW={r['avg_win']:+.2f}% AvgL={r['avg_loss']:+.2f}% "
                  f"GScore={r['golden_score']:.0f}")

        tf_out = output_dir_for(tf_key, "phase11v11")
        tf_out.mkdir(parents=True, exist_ok=True)
        chart_comparison(tf_key, locked_sim, top_golden, tf_out)
        chart_scatter(tf_key, locked_sim, top_golden, tf_out)

        all_data[tf_key] = {
            "locked": locked_sim,
            "best_golden": top_golden[0] if top_golden else None,
            "top_5": top_golden[:5],
        }

    chart_summary(all_data, out)

    jp = out / "phase11v11_golden_results.json"
    jp.write_text(json.dumps(all_data, indent=2, default=str))
    print(f"\n  Saved {jp}")

    # Final verdict
    print(f"\n{'='*70}")
    print(f"  VERDICT")
    print(f"{'='*70}")
    for tf in ["4H", "1D", "1W"]:
        d = all_data.get(tf, {})
        lock = d.get("locked")
        best = d.get("best_golden")
        if lock and best:
            gs_lift = (best["golden_score"] - lock["golden_score"]) / lock["golden_score"] * 100 if lock["golden_score"] else 0
            pnl_diff = best["total"] - lock["total"]
            print(f"\n  {tf}:")
            print(f"    Locked:  {lock['short']} → GS={lock['golden_score']:.0f} PnL={lock['total']:+.0f}%")
            print(f"    Golden:  {best['short']} → GS={best['golden_score']:.0f} PnL={best['total']:+.0f}%")
            print(f"    Δ GScore: {gs_lift:+.0f}%  Δ PnL: {pnl_diff:+.0f}%")
            if gs_lift > 20 and pnl_diff > -2000:
                print(f"    → RECOMMEND: Switch to golden combo")
            elif gs_lift > 0:
                print(f"    → CONSIDER: Modest golden score improvement")
            else:
                print(f"    → KEEP: Locked combo is already strong")

    print(f"\n  Done in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

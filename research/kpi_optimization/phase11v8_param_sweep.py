"""
Phase 11 v8 — T / M / K Parameter Sweep for Exit Flow v4

Tests all combinations of:
  T (lenient period):  [2, 4, 6, 8]
  M (checkpoint interval): TF-dependent grids
  K (ATR multiplier): [2.0, 2.5, 3.0, 3.5, 4.0]

on the best HR≥65% combos from v7, using Exit Flow v4 (checkpoint + ATR reset).
Generates heatmaps, sensitivity analysis, and documented recommendations.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from itertools import product
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
from tf_config import ENRICHED_DIR, TIMEFRAME_CONFIGS, output_dir_for
from phase8_exit_by_sector import load_data, IS_FRACTION

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
from matplotlib.cm import ScalarMappable

plt.style.use("dark_background")
plt.rcParams.update({
    "font.family": "sans-serif", "font.size": 10,
    "axes.titlesize": 12, "axes.titleweight": "bold",
    "figure.facecolor": "#181818", "axes.facecolor": "#1e1e1e",
    "savefig.facecolor": "#181818", "savefig.dpi": 180,
    "savefig.bbox": "tight", "savefig.pad_inches": 0.3,
})

ATR_PERIOD = 14
MAX_HOLD_HARD_CAP = 500
HR_FLOOR = 65.0

T_GRID = [2, 4, 6, 8]
M_GRIDS = {
    "4H": [12, 24, 36, 48, 60],
    "1D": [10, 20, 30, 40, 50],
    "1W": [5, 10, 15, 20, 30],
}
K_GRID = [2.0, 2.5, 3.0, 3.5, 4.0]

COMBOS_TO_TEST = {
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

V7_BASELINE = {
    "4H": {"T": 4, "M": 48, "K": 3.5},
    "1D": {"T": 4, "M": 40, "K": 3.5},
    "1W": {"T": 2, "M": 20, "K": 2.0},
}

KPI_SHORT = {
    "Nadaraya-Watson Smoother": "NWSm", "cRSI": "cRSI", "OBVOSC_LB": "OBVOsc",
    "Madrid Ribbon": "Madrid", "GK Trend Ribbon": "GKTr", "Volume + MA20": "Vol>MA",
    "DEMA": "DEMA", "Donchian Ribbon": "Donch",
}


def _sl(kpis):
    return " + ".join(KPI_SHORT.get(k, k[:6]) for k in kpis)


def compute_atr(df, period=14):
    h, l, pc = df["High"], df["Low"], df["Close"].shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()


@dataclass
class SweepResult:
    T: int
    M: int
    K: float
    n: int
    hr: float
    avg_ret: float
    med_ret: float
    pf: float
    total_ret: float
    avg_hold: float
    max_hold: int
    resets_per_trade: float
    atr_pct: float
    strict_pct: float
    reset_exit_pct: float


def sim_v4(data, kpis, T, M, K, vol=False):
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
                ex[reason] += 1; holds.append(h); resets_list.append(n_resets)
            ni = i + 1
            while ni < len(sd) and df.index.get_loc(sd[ni]) <= xi:
                ni += 1
            i = ni
    n = len(rets)
    if n < 5:
        return None
    te = sum(ex.values())
    wi = sum(r for r in rets if r > 0)
    lo = abs(sum(r for r in rets if r <= 0))
    return SweepResult(
        T, M, K, n,
        sum(1 for r in rets if r > 0) / n * 100,
        float(np.mean(rets)), float(np.median(rets)),
        wi / lo if lo > 0 else 999.0, float(np.sum(rets)),
        float(np.mean(holds)), int(np.max(holds)),
        float(np.mean(resets_list)),
        ex["atr"] / te * 100 if te else 0,
        ex["str"] / te * 100 if te else 0,
        ex["reset_exit"] / te * 100 if te else 0,
    )


# ── Charts ───────────────────────────────────────────────────────────────

def chart_heatmaps(results: List[SweepResult], baseline: dict,
                   combo_name: str, ck: str, tf: str, out: Path):
    """3×2 heatmap grid: M vs K for each T value, coloured by total P&L."""
    ts = sorted(set(r.T for r in results))
    ms = sorted(set(r.M for r in results))
    ks = sorted(set(r.K for r in results))

    all_pnl = [r.total_ret for r in results if r.hr >= HR_FLOOR]
    if not all_pnl:
        return
    vmin, vmax = min(all_pnl), max(all_pnl)

    fig, axes = plt.subplots(2, 2, figsize=(20, 16))
    axes = axes.flatten()

    best_overall = max((r for r in results if r.hr >= HR_FLOOR),
                       key=lambda r: r.total_ret, default=None)
    baseline_r = next((r for r in results
                       if r.T == baseline["T"] and r.M == baseline["M"]
                       and abs(r.K - baseline["K"]) < 0.01), None)

    for ti, t_val in enumerate(ts):
        if ti >= 4:
            break
        ax = axes[ti]
        grid = np.full((len(ms), len(ks)), np.nan)
        hr_grid = np.full((len(ms), len(ks)), np.nan)
        for r in results:
            if r.T != t_val:
                continue
            mi = ms.index(r.M)
            ki = ks.index(r.K)
            if r.hr >= HR_FLOOR:
                grid[mi, ki] = r.total_ret
            hr_grid[mi, ki] = r.hr

        im = ax.imshow(grid, aspect="auto", origin="lower",
                       vmin=vmin, vmax=vmax, cmap="RdYlGn",
                       extent=[-0.5, len(ks) - 0.5, -0.5, len(ms) - 0.5])

        for mi in range(len(ms)):
            for ki in range(len(ks)):
                val = grid[mi, ki]
                hr_val = hr_grid[mi, ki]
                if np.isnan(val):
                    ax.text(ki, mi, f"HR={hr_val:.0f}%\n<65%", ha="center", va="center",
                            fontsize=6.5, color="#888", style="italic")
                else:
                    color = "black" if val > (vmin + vmax) / 2 else "white"
                    ax.text(ki, mi, f"{val:+.0f}%\nHR={hr_val:.0f}%", ha="center", va="center",
                            fontsize=7, color=color, fontweight="bold")
                is_baseline = (t_val == baseline["T"] and ms[mi] == baseline["M"]
                               and abs(ks[ki] - baseline["K"]) < 0.01)
                if is_baseline:
                    rect = plt.Rectangle((ki - 0.45, mi - 0.45), 0.9, 0.9,
                                         linewidth=3, edgecolor="cyan", facecolor="none")
                    ax.add_patch(rect)
                if best_overall and t_val == best_overall.T and ms[mi] == best_overall.M and abs(ks[ki] - best_overall.K) < 0.01:
                    rect = plt.Rectangle((ki - 0.45, mi - 0.45), 0.9, 0.9,
                                         linewidth=3, edgecolor="gold", facecolor="none", linestyle="--")
                    ax.add_patch(rect)

        ax.set_xticks(range(len(ks)))
        ax.set_xticklabels([f"{k:.1f}" for k in ks])
        ax.set_yticks(range(len(ms)))
        ax.set_yticklabels([str(m) for m in ms])
        ax.set_xlabel("K (ATR multiplier)")
        ax.set_ylabel("M (checkpoint interval)")
        ax.set_title(f"T = {t_val} bars")

    fig.colorbar(im, ax=axes, label="Total P&L (%)", shrink=0.6, pad=0.02)

    bl_str = f"T={baseline['T']}, M={baseline['M']}, K={baseline['K']}"
    bl_pnl = f"PnL={baseline_r.total_ret:+.0f}%" if baseline_r and baseline_r.hr >= HR_FLOOR else "HR<65%"
    best_str = ""
    if best_overall:
        best_str = (f"Best: T={best_overall.T}, M={best_overall.M}, K={best_overall.K:.1f} → "
                    f"PnL={best_overall.total_ret:+.0f}% (n={best_overall.n}, HR={best_overall.hr:.0f}%, "
                    f"PF={best_overall.pf:.1f}, AvgH={best_overall.avg_hold:.0f})")
    lift = ""
    if best_overall and baseline_r and baseline_r.hr >= HR_FLOOR:
        pct = (best_overall.total_ret - baseline_r.total_ret) / abs(baseline_r.total_ret) * 100
        lift = f" | Lift vs baseline: {pct:+.0f}%"

    fig.suptitle(f"{tf} {ck}: {combo_name} — T/M/K Sweep (Exit Flow v4)\n"
                 f"Cyan=v7 baseline ({bl_str}, {bl_pnl}) | Gold=best{lift}",
                 fontsize=13, fontweight="bold")
    plt.tight_layout(rect=[0, 0, 0.92, 0.93])
    fig.savefig(out / f"sweep_{ck}_heatmap.png")
    plt.close(fig)
    print(f"      Saved sweep_{ck}_heatmap.png")


def chart_sensitivity(results: List[SweepResult], baseline: dict,
                      combo_name: str, ck: str, tf: str, out: Path):
    """1D sensitivity: how each parameter affects P&L when others are fixed at best."""
    valid = [r for r in results if r.hr >= HR_FLOOR]
    if not valid:
        return
    best = max(valid, key=lambda r: r.total_ret)

    fig, axes = plt.subplots(1, 3, figsize=(22, 7))
    params = [
        ("T", T_GRID, best.M, best.K, axes[0]),
        ("M", sorted(set(r.M for r in results)), best.T, best.K, axes[1]),
        ("K", K_GRID, best.T, best.M, axes[2]),
    ]
    for pname, pvals, fix1, fix2, ax in params:
        pnls, hrs, ns = [], [], []
        for pv in pvals:
            if pname == "T":
                match = [r for r in results if r.T == pv and r.M == fix1 and abs(r.K - fix2) < 0.01]
            elif pname == "M":
                match = [r for r in results if r.M == pv and r.T == fix1 and abs(r.K - fix2) < 0.01]
            else:
                match = [r for r in results if abs(r.K - pv) < 0.01 and r.T == fix1 and r.M == fix2]
            if match:
                r = match[0]
                pnls.append(r.total_ret if r.hr >= HR_FLOOR else 0)
                hrs.append(r.hr)
                ns.append(r.n)
            else:
                pnls.append(0); hrs.append(0); ns.append(0)

        ax2 = ax.twinx()
        bars = ax.bar(range(len(pvals)), pnls, color="#66bb6a", alpha=0.7, edgecolor="white", linewidth=0.5)
        ax2.plot(range(len(pvals)), hrs, "o-", color="#42a5f5", linewidth=2, markersize=6)
        ax2.axhline(HR_FLOOR, color="#ef5350", linestyle="--", alpha=0.5, label="HR floor")

        for i, (p, h, n) in enumerate(zip(pnls, hrs, ns)):
            if p > 0:
                ax.text(i, p, f"n={n}", ha="center", va="bottom", fontsize=7, color="#aaa")

        ax.set_xticks(range(len(pvals)))
        ax.set_xticklabels([str(v) for v in pvals])
        ax.set_xlabel(f"{pname} value")
        ax.set_ylabel("Total P&L (%)", color="#66bb6a")
        ax2.set_ylabel("Hit Rate (%)", color="#42a5f5")
        ax2.legend(fontsize=8, loc="lower right")

        if pname == "T":
            fix_str = f"M={fix1}, K={fix2}"
        elif pname == "M":
            fix_str = f"T={fix1}, K={fix2}"
        else:
            fix_str = f"T={fix1}, M={fix2}"
        ax.set_title(f"{pname} sensitivity (others at best: {fix_str})")

    fig.suptitle(f"{tf} {ck}: {combo_name} — Parameter Sensitivity",
                 fontsize=13, fontweight="bold")
    plt.tight_layout()
    fig.savefig(out / f"sweep_{ck}_sensitivity.png")
    plt.close(fig)
    print(f"      Saved sweep_{ck}_sensitivity.png")


def chart_audit(all_bests: Dict[str, Dict[str, Dict]], out: Path):
    """Critical audit chart: overfitting risk, stability, recommendations."""
    rows = []
    for tf in ["4H", "1D", "1W"]:
        for ck in ["C3", "C4"]:
            d = all_bests.get(tf, {}).get(ck)
            if d:
                rows.append(d)
    if not rows:
        return

    fig, ax = plt.subplots(figsize=(28, max(8, len(rows) * 1.2 + 6)))
    ax.axis("off")

    hdr = ["TF", "Combo", "Param", "v7 Base", "Best", "PnL Lift",
           "n", "HR%", "PF", "AvgH", "MaxH", "Resets/t",
           "Stability", "Overfit Risk", "Recommendation"]
    ct, cc = [], []
    for i, r in enumerate(rows):
        stability = r.get("stability", "?")
        overfit = r.get("overfit_risk", "?")
        rec = r.get("recommendation", "")
        ct.append([
            r["tf"], r["ck"],
            f"T={r['best_T']}, M={r['best_M']}, K={r['best_K']:.1f}",
            f"T={r['base_T']}, M={r['base_M']}, K={r['base_K']:.1f}",
            f"PnL: {r['base_pnl']:+.0f}% → {r['best_pnl']:+.0f}%",
            f"{r['lift_pct']:+.0f}%",
            str(r["best_n"]), f"{r['best_hr']:.0f}",
            f"{r['best_pf']:.1f}", f"{r['best_avg_hold']:.0f}",
            str(r["best_max_hold"]), f"{r['best_resets']:.1f}",
            stability, overfit, rec,
        ])
        if "HIGH" in overfit.upper():
            bg = "#3a1a1a"
        elif "MEDIUM" in overfit.upper():
            bg = "#3a3a1a"
        else:
            bg = "#1a3a1a" if i % 2 == 0 else "#1e3e1e"
        cc.append([bg] * len(hdr))

    t = ax.table(cellText=ct, colLabels=hdr, loc="center", cellLoc="center")
    t.auto_set_font_size(False); t.set_fontsize(7); t.scale(1.0, 2.0)
    for (r, c), cell in t.get_celld().items():
        if r == 0:
            cell.set_facecolor("#333")
            cell.set_text_props(fontweight="bold", color="white")
        else:
            cell.set_facecolor(cc[r - 1][c])
            cell.set_text_props(color="white")
        cell.set_edgecolor("#444")

    audit_notes = [
        "AUDIT NOTES:",
        "• Stability = how flat the P&L surface is near the optimum. 'Flat' = robust, 'Peaked' = fragile.",
        "• Overfit Risk = does the best param set look like an outlier or part of a stable region?",
        "• If best is at grid edge (min/max T, M, or K), the true optimum may lie outside the tested range.",
        "• If P&L lift > 50%, be sceptical — large lifts from param changes often signal overfitting.",
        "• Prefer parameter sets where nearby cells also show strong P&L (robust plateau).",
    ]
    fig.text(0.02, 0.02, "\n".join(audit_notes), fontsize=8.5, color="#aaa",
             verticalalignment="bottom", fontfamily="monospace",
             bbox=dict(boxstyle="round,pad=0.5", facecolor="#222", alpha=0.9))

    fig.suptitle("Phase 11 v8 — T/M/K Parameter Sweep: Audit & Recommendations\n"
                 "(320 stocks, Exit Flow v4, HR≥65%)",
                 fontsize=14, fontweight="bold", y=0.98)
    plt.tight_layout(rect=[0, 0.12, 1, 0.95])
    fig.savefig(out / "audit_recommendations.png")
    plt.close(fig)
    print(f"  Saved audit_recommendations.png")


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    t0 = time.time()
    out_root = output_dir_for("all", "phase11v8")
    out_root.mkdir(parents=True, exist_ok=True)
    all_json: Dict[str, Any] = {}
    all_bests: Dict[str, Dict[str, Dict]] = {}

    for tf_key in ["1W", "1D", "4H"]:
        tf_cfg = TIMEFRAME_CONFIGS[tf_key]
        print(f"\n{'=' * 70}")
        print(f"  TIMEFRAME: {tf_key}")
        print(f"{'=' * 70}")

        data = load_data(ENRICHED_DIR, tf_key)
        print(f"  Loaded {len(data)} stocks")

        tf_out = output_dir_for(tf_key, "phase11v8")
        tf_out.mkdir(parents=True, exist_ok=True)

        m_grid = M_GRIDS[tf_key]
        baseline = V7_BASELINE[tf_key]
        combos = COMBOS_TO_TEST[tf_key]
        tf_json = {}
        all_bests[tf_key] = {}

        for ck, kpis in combos.items():
            combo_name = _sl(kpis)
            param_grid = list(product(T_GRID, m_grid, K_GRID))
            print(f"\n    {ck}: {combo_name}")
            print(f"    Testing {len(param_grid)} parameter combinations...")

            results: List[SweepResult] = []
            t1 = time.time()
            for gi, (t_val, m_val, k_val) in enumerate(param_grid):
                sr = sim_v4(data, kpis, t_val, m_val, k_val)
                if sr:
                    results.append(sr)
                if (gi + 1) % 20 == 0:
                    elapsed = time.time() - t1
                    print(f"      {gi+1}/{len(param_grid)} ({elapsed:.0f}s)")

            print(f"    {len(results)} valid results ({time.time()-t1:.0f}s)")

            valid = [r for r in results if r.hr >= HR_FLOOR]
            baseline_r = next((r for r in results
                               if r.T == baseline["T"] and r.M == baseline["M"]
                               and abs(r.K - baseline["K"]) < 0.01), None)

            if valid:
                best = max(valid, key=lambda r: r.total_ret)
                bl_pnl = baseline_r.total_ret if baseline_r and baseline_r.hr >= HR_FLOOR else 0

                print(f"    Baseline (T={baseline['T']}, M={baseline['M']}, K={baseline['K']}): ", end="")
                if baseline_r and baseline_r.hr >= HR_FLOOR:
                    print(f"n={baseline_r.n} HR={baseline_r.hr:.0f}% PnL={baseline_r.total_ret:+.0f}%")
                else:
                    print(f"HR<65% — not viable")

                print(f"    Best: T={best.T}, M={best.M}, K={best.K:.1f} → "
                      f"n={best.n} HR={best.hr:.0f}% Avg={best.avg_ret:+.2f}% "
                      f"PnL={best.total_ret:+.0f}% PF={best.pf:.1f} "
                      f"AvgH={best.avg_hold:.0f} MaxH={best.max_hold} "
                      f"Resets/t={best.resets_per_trade:.1f}")

                if bl_pnl > 0:
                    lift = (best.total_ret - bl_pnl) / abs(bl_pnl) * 100
                    print(f"    Lift vs baseline: {lift:+.0f}%")
                else:
                    lift = 999

                # Stability: check neighbors of best
                neighbors = []
                for r in valid:
                    dt = abs(r.T - best.T)
                    dm = abs(r.M - best.M) / max(m_grid)
                    dk = abs(r.K - best.K) / 2.0
                    if 0 < dt + dm + dk <= 0.6:
                        neighbors.append(r.total_ret)
                if neighbors:
                    neighbor_avg = np.mean(neighbors)
                    ratio = neighbor_avg / best.total_ret if best.total_ret > 0 else 0
                    stability = "Flat/Robust" if ratio > 0.85 else ("Moderate" if ratio > 0.7 else "Peaked/Fragile")
                else:
                    stability = "No neighbors"
                    ratio = 0

                at_edge = (best.T in [min(T_GRID), max(T_GRID)] or
                           best.M in [min(m_grid), max(m_grid)] or
                           best.K in [min(K_GRID), max(K_GRID)])
                overfit_risk = "LOW"
                if lift > 50:
                    overfit_risk = "HIGH — large lift"
                elif at_edge:
                    overfit_risk = "MEDIUM — at grid edge"
                elif stability == "Peaked/Fragile":
                    overfit_risk = "MEDIUM — fragile peak"

                if overfit_risk.startswith("HIGH"):
                    recommendation = "Use with caution; prefer robust neighbor"
                elif overfit_risk.startswith("MEDIUM"):
                    recommendation = "Acceptable; verify on OOS"
                else:
                    recommendation = "Adopt — stable and better than baseline"

                # Check if baseline is already near-optimal
                if bl_pnl > 0 and abs(lift) < 10:
                    recommendation = "Keep baseline — marginal improvement"

                all_bests[tf_key][ck] = {
                    "tf": tf_key, "ck": ck, "combo": combo_name,
                    "best_T": best.T, "best_M": best.M, "best_K": best.K,
                    "base_T": baseline["T"], "base_M": baseline["M"], "base_K": baseline["K"],
                    "best_pnl": best.total_ret, "base_pnl": bl_pnl,
                    "lift_pct": lift, "best_n": best.n, "best_hr": best.hr,
                    "best_pf": best.pf, "best_avg_hold": best.avg_hold,
                    "best_max_hold": best.max_hold, "best_resets": best.resets_per_trade,
                    "stability": stability, "overfit_risk": overfit_risk,
                    "recommendation": recommendation,
                    "neighbor_ratio": ratio,
                }

            chart_heatmaps(results, baseline, combo_name, ck, tf_key, tf_out)
            chart_sensitivity(results, baseline, combo_name, ck, tf_key, tf_out)

            tf_json[ck] = {
                "combo": kpis, "n_params_tested": len(param_grid),
                "n_valid": len(valid) if valid else 0,
                "baseline": baseline,
                "best": {"T": best.T, "M": best.M, "K": best.K,
                         "n": best.n, "hr": round(best.hr, 1),
                         "avg_ret": round(best.avg_ret, 2),
                         "total_pnl": round(best.total_ret),
                         "pf": round(best.pf, 1),
                         "avg_hold": round(best.avg_hold, 1),
                         "max_hold": best.max_hold,
                         "resets": round(best.resets_per_trade, 2),
                         } if valid else {},
            }

        all_json[tf_key] = tf_json

    chart_audit(all_bests, out_root)

    jp = out_root / "phase11v8_results.json"
    jp.write_text(json.dumps(all_json, indent=2, default=str))
    (out_root / "phase11v8_audit.json").write_text(
        json.dumps(all_bests, indent=2, default=str))

    print(f"\n{'=' * 70}")
    print(f"  FINAL AUDIT SUMMARY")
    print(f"{'=' * 70}")
    for tf in ["4H", "1D", "1W"]:
        print(f"\n  {tf}:")
        for ck in ["C3", "C4"]:
            d = all_bests.get(tf, {}).get(ck)
            if d:
                print(f"    {ck} {d['combo']}:")
                print(f"      Baseline: T={d['base_T']}, M={d['base_M']}, K={d['base_K']:.1f} → PnL={d['base_pnl']:+.0f}%")
                print(f"      Best:     T={d['best_T']}, M={d['best_M']}, K={d['best_K']:.1f} → PnL={d['best_pnl']:+.0f}% ({d['lift_pct']:+.0f}%)")
                print(f"      Stability: {d['stability']} | Overfit: {d['overfit_risk']}")
                print(f"      → {d['recommendation']}")

    print(f"\n  Done in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

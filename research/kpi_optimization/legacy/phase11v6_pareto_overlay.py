"""
Phase 11 v6 — Pareto Frontier + Quality Overlay (Full Dataset ~320 stocks)

Goals:
  1. Screen ALL C3/C4/C5 combos across full dataset (CSV + parquet)
  2. Plot Pareto frontier: Total P&L vs Avg Return per trade
  3. Full v3 exit simulation on Pareto-optimal + top candidates
  4. Quality overlay: test BB30 / NWE MAE as position-sizing signals
  5. Recommend final combo structure (single-track vs dual-track)
"""

from __future__ import annotations

import json
import sys
import time
from collections import namedtuple
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

COLORS = {
    "screen": "#555555", "pareto": "#ff7043", "sim": "#42a5f5",
    "current": "#00e5ff", "bb30_yes": "#66bb6a", "bb30_no": "#ef5350",
    "nwe_yes": "#ab47bc", "nwe_no": "#ff7043",
}

# ── Config ────────────────────────────────────────────────────────────────

V3_PARAMS = {
    "4H": {"T": 4, "M": 48, "K": 3.5},
    "1D": {"T": 4, "M": 40, "K": 3.5},
    "1W": {"T": 2, "M": 20, "K": 2.0},
}

ATR_PERIOD = 14
MIN_KPI_COVERAGE = 0.30
MIN_TRADES = {"4H": 15, "1D": 15, "1W": 5}
SIM_CANDIDATES_PER_CK = 60

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

OVERLAY_KPIS = ["BB 30", "Nadaraya-Watson Envelop (MAE)"]

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


# ── Screen arrays ─────────────────────────────────────────────────────────

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


# ── Screen ALL combos ─────────────────────────────────────────────────────

SR = namedtuple("SR", "kpis vol n avg_ret hr total_pnl atr_pct")


def screen_all(sa: SA, k: int, kpis: List[str], K: float, mt: int) -> List[SR]:
    avail = [kpi for kpi in kpis if sa.kpi_cov.get(kpi, 0) >= MIN_KPI_COVERAGE]
    if len(avail) < k:
        return []
    hs = sa.close - K * sa.atr
    ah = sa.low_min_fwd < hs
    results: List[SR] = []
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
            ar = float(np.mean(rets))
            tr = float(np.sum(rets))
            hr = float(np.sum(rets > 0) / n) * 100
            ap = float(np.sum(ah[mask]) / n) * 100
            results.append(SR(list(combo), use_vol, n, ar, hr, tr, ap))
    return results


# ── Pareto frontier ───────────────────────────────────────────────────────

def find_pareto(results: List[SR]) -> List[int]:
    """Find Pareto-optimal indices maximising both total_pnl and avg_ret."""
    if not results:
        return []
    pts = np.array([(r.total_pnl, r.avg_ret) for r in results])
    order = np.argsort(-pts[:, 0])
    pareto = []
    max_y = -np.inf
    for i in order:
        if pts[i, 1] > max_y:
            pareto.append(int(i))
            max_y = pts[i, 1]
    return pareto


def select_candidates(results: List[SR], pareto_idx: List[int],
                      max_total: int = SIM_CANDIDATES_PER_CK) -> List[int]:
    """Select diverse set: Pareto + top PnL + top avg_ret."""
    chosen = set(pareto_idx)
    by_pnl = sorted(range(len(results)), key=lambda i: results[i].total_pnl, reverse=True)
    by_avg = sorted(range(len(results)), key=lambda i: results[i].avg_ret, reverse=True)
    for lst in [by_pnl, by_avg]:
        for i in lst:
            if len(chosen) >= max_total:
                break
            chosen.add(i)
    return sorted(chosen)


# ── Full v3 simulation ───────────────────────────────────────────────────

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
           T: int, M: int, K: float, vol: bool = False, mn: int = 3,
           overlay_kpis: Optional[List[str]] = None
           ) -> Tuple[Optional[FR], Optional[Dict[str, FR]]]:
    """Full v3 exit simulation. If overlay_kpis given, also returns split results."""
    rets: List[float] = []
    ex: Dict[str, int] = {"atr": 0, "len": 0, "str": 0, "mh": 0}
    holds: List[int] = []

    overlay_rets: Dict[str, Dict[str, List[float]]] = {}
    if overlay_kpis:
        for ok in overlay_kpis:
            overlay_rets[ok] = {"yes": [], "no": []}

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

        overlay_states_at: Dict[str, pd.Series] = {}
        if overlay_kpis:
            for ok in overlay_kpis:
                if ok in sm:
                    overlay_states_at[ok] = (sm[ok] == STATE_BULL)
                else:
                    overlay_states_at[ok] = pd.Series(False, index=df.index)

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
                rets.append(ret)
                ex[reason] += 1
                holds.append(h)
                if overlay_kpis:
                    for ok in overlay_kpis:
                        bull = bool(overlay_states_at[ok].iloc[ei]) if ei < len(overlay_states_at[ok]) else False
                        bucket = "yes" if bull else "no"
                        overlay_rets[ok][bucket].append(ret)
            ni = i + 1
            while ni < len(sd) and df.index.get_loc(sd[ni]) <= xi:
                ni += 1
            i = ni

    n = len(rets)
    if n < mn:
        return None, None
    hr = sum(1 for r in rets if r > 0) / n * 100
    ar = float(np.mean(rets))
    mr = float(np.median(rets))
    w = min(rets)
    wi = sum(r for r in rets if r > 0)
    lo = abs(sum(r for r in rets if r <= 0))
    pf = wi / lo if lo > 0 else 999.0
    te = sum(ex.values())
    main = FR(kpis, vol, n, hr, ar, mr, w, pf,
              ex["atr"] / te * 100 if te else 0,
              ex["str"] / te * 100 if te else 0,
              ex["mh"] / te * 100 if te else 0,
              float(np.mean(holds)) if holds else 0,
              float(np.sum(rets)))

    overlay_fr: Optional[Dict[str, FR]] = None
    if overlay_kpis:
        overlay_fr = {}
        for ok in overlay_kpis:
            for bucket in ["yes", "no"]:
                brets = overlay_rets[ok][bucket]
                bn = len(brets)
                key = f"{_s(ok)}_{bucket}"
                if bn < 3:
                    overlay_fr[key] = FR(kpis, vol, bn, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
                    continue
                bhr = sum(1 for r in brets if r > 0) / bn * 100
                bar = float(np.mean(brets))
                bmr = float(np.median(brets))
                bw = min(brets)
                bwi = sum(r for r in brets if r > 0)
                blo = abs(sum(r for r in brets if r <= 0))
                bpf = bwi / blo if blo > 0 else 999.0
                overlay_fr[key] = FR(kpis, vol, bn, bhr, bar, bmr, bw, bpf,
                                     0, 0, 0, 0, float(np.sum(brets)))
    return main, overlay_fr


# ── Charts ───────────────────────────────────────────────────────────────

def chart_pareto(all_sr: List[SR], pareto_idx: List[int],
                 sim_results: List[Tuple[int, FR]], current: Optional[FR],
                 k: int, tf: str, out: Path):
    if len(all_sr) < 10:
        return
    fig, ax = plt.subplots(figsize=(16, 11))

    xs = np.array([r.total_pnl for r in all_sr])
    ys = np.array([r.avg_ret for r in all_sr])
    hrs = np.array([r.hr for r in all_sr])
    ax.scatter(xs, ys, s=4, alpha=0.12, c="#888", zorder=1, label=f"All screened ({len(all_sr):,})")

    if pareto_idx:
        px = xs[pareto_idx]
        py = ys[pareto_idx]
        order = np.argsort(px)
        ax.plot(px[order], py[order], "-", color=COLORS["pareto"], alpha=0.6, linewidth=2, zorder=4)
        ax.scatter(px, py, s=40, c=COLORS["pareto"], edgecolors="white",
                   linewidth=0.5, zorder=5, label=f"Pareto frontier ({len(pareto_idx)})")
        for i in pareto_idx[:8]:
            r = all_sr[i]
            vl = "+v" if r.vol else ""
            ax.annotate(f"{_sl(r.kpis, ',')}{vl}\n{r.n}t PnL={r.total_pnl:+.0f}%",
                        (r.total_pnl, r.avg_ret), fontsize=5.5, color="#ffa",
                        xytext=(6, 4), textcoords="offset points",
                        bbox=dict(boxstyle="round,pad=0.15", facecolor="#333", alpha=0.75))

    if sim_results:
        sx = [fr.total_ret for _, fr in sim_results]
        sy = [fr.avg_ret for _, fr in sim_results]
        ax.scatter(sx, sy, s=60, marker="D", c=COLORS["sim"], edgecolors="white",
                   linewidth=0.6, zorder=6, alpha=0.8, label=f"Simulated ({len(sim_results)})")
        top5 = sorted(sim_results, key=lambda x: x[1].total_ret, reverse=True)[:5]
        for idx, fr in top5:
            vl = "+v" if fr.vol else ""
            ax.annotate(f"{_sl(fr.kpis, ',')}{vl}\nn={fr.n} HR={fr.hr:.0f}%\nPnL={fr.total_ret:+.0f}%",
                        (fr.total_ret, fr.avg_ret), fontsize=6, color="white",
                        xytext=(8, -15), textcoords="offset points",
                        arrowprops=dict(arrowstyle="-", color="white", alpha=0.4),
                        bbox=dict(boxstyle="round,pad=0.2", facecolor="#1a3a1a", alpha=0.85))

    if current:
        ax.scatter([current.total_ret], [current.avg_ret], s=300, marker="*",
                   c=COLORS["current"], edgecolors="white", linewidth=1.5, zorder=10,
                   label=f"Current (n={current.n}, PnL={current.total_ret:+.0f}%)")

    ax.set_xlabel("Total Cumulative P&L (%)", fontsize=11)
    ax.set_ylabel("Avg Return per Trade (%)", fontsize=11)
    ax.set_title(f"{tf} C{k} — Pareto Frontier: Total P&L vs Avg Return\n"
                 f"({len(all_sr):,} combos screened, {len(pareto_idx)} Pareto-optimal)", fontsize=13)
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(True, alpha=0.15)
    plt.tight_layout()
    fig.savefig(out / f"pareto_C{k}.png")
    plt.close(fig)
    print(f"    Saved pareto_C{k}.png")


def chart_overlay(overlay_data: List[Dict], tf: str, out: Path):
    """Bar chart showing impact of BB30/NWE overlay on top PnL combos."""
    if not overlay_data:
        return
    fig, axes = plt.subplots(2, 2, figsize=(22, 14))
    metrics = [
        ("total_ret", "Total P&L (%)", axes[0, 0]),
        ("n", "Trade Count", axes[0, 1]),
        ("avg_ret", "Avg Return (%)", axes[1, 0]),
        ("hr", "Hit Rate (%)", axes[1, 1]),
    ]
    labels = sorted(set(d["combo_label"] for d in overlay_data))
    overlays = ["All", "BB30=Y", "BB30=N", "NWE=Y", "NWE=N"]
    colors_ov = ["#42a5f5", "#66bb6a", "#ef5350", "#ab47bc", "#ff7043"]
    x = np.arange(len(labels))
    w = 0.8 / len(overlays)

    for attr, ylabel, ax in metrics:
        for j, ov in enumerate(overlays):
            vals = []
            for label in labels:
                match = [d for d in overlay_data if d["combo_label"] == label and d["overlay"] == ov]
                vals.append(match[0][attr] if match else 0)
            ax.bar(x + j * w - (len(overlays) - 1) * w / 2, vals, w,
                   color=colors_ov[j], label=ov, edgecolor="white", linewidth=0.3)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=7, rotation=20, ha="right")
        ax.set_ylabel(ylabel, fontsize=10)
        ax.legend(fontsize=7, ncol=len(overlays))
        ax.grid(True, alpha=0.15, axis="y")

    fig.suptitle(f"{tf} — Quality Overlay: BB30 & NWE as Sizing Signal on P&L Combos",
                 fontsize=14, fontweight="bold")
    plt.tight_layout()
    fig.savefig(out / "overlay_impact.png")
    plt.close(fig)
    print(f"    Saved overlay_impact.png")


def chart_summary_table(all_recs: Dict[str, Dict[str, Dict]], out: Path):
    """Summary table across all TFs and C-levels."""
    rows = []
    for tf in ["4H", "1D", "1W"]:
        for ck in ["C3", "C4", "C5"]:
            rec = all_recs.get(tf, {}).get(ck)
            if not rec:
                continue
            rows.append(rec)
    if not rows:
        return
    fig, ax = plt.subplots(figsize=(26, max(4, len(rows) * 0.7 + 2.5)))
    ax.axis("off")
    hdr = ["TF", "Combo", "KPIs", "Vol", "n", "HR%", "Avg%", "PnL%",
           "PF", "ATR%", "BB30 lift", "Strategy"]
    ct = []
    cc = []
    for i, r in enumerate(rows):
        ct.append([
            r.get("tf", ""), r.get("ck", ""), r.get("kpis_short", ""),
            "Y" if r.get("vol") else "N", str(r.get("n", 0)),
            f"{r.get('hr', 0):.0f}", f"{r.get('avg_ret', 0):+.2f}",
            f"{r.get('total_ret', 0):+.0f}", f"{r.get('pf', 0):.1f}",
            f"{r.get('atr_pct', 0):.1f}", r.get("bb30_lift", "n/a"),
            r.get("strategy", ""),
        ])
        bg = "#1e1e1e" if i % 2 == 0 else "#252525"
        cc.append([bg] * len(hdr))
    t = ax.table(cellText=ct, colLabels=hdr, loc="center", cellLoc="center")
    t.auto_set_font_size(False)
    t.set_fontsize(8)
    t.scale(1.0, 1.6)
    for (r, c), cell in t.get_celld().items():
        if r == 0:
            cell.set_facecolor("#333")
            cell.set_text_props(fontweight="bold", color="white")
        else:
            cell.set_facecolor(cc[r - 1][c])
            cell.set_text_props(color="white")
        cell.set_edgecolor("#444")
    ax.set_title("Phase 11 v6 — P&L-Optimised Entry Combos with Quality Overlay\n(320 stocks, Exit Flow v3)",
                 fontsize=14, fontweight="bold", pad=25)
    plt.tight_layout()
    fig.savefig(out / "summary_table.png")
    plt.close(fig)
    print(f"  Saved summary_table.png")


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    t0 = time.time()
    out_root = output_dir_for("all", "phase11v6")
    out_root.mkdir(parents=True, exist_ok=True)
    all_json: Dict[str, Any] = {}
    all_recs: Dict[str, Dict[str, Dict]] = {}

    for tf_key, tf_cfg in TIMEFRAME_CONFIGS.items():
        print(f"\n{'=' * 70}")
        print(f"  TIMEFRAME: {tf_key}")
        print(f"{'=' * 70}")

        data = load_data(ENRICHED_DIR, tf_key)
        print(f"  Loaded {len(data)} stocks")
        p = V3_PARAMS.get(tf_key, V3_PARAMS["1D"])
        T, M, K = p["T"], p["M"], p["K"]
        H = tf_cfg.default_horizon
        mt = MIN_TRADES.get(tf_key, 15)

        tf_out = output_dir_for(tf_key, "phase11v6")
        tf_out.mkdir(parents=True, exist_ok=True)

        print(f"  Building screen arrays (H={H}, M={M})...")
        sa = build_sa(data, ALL_KPIS, H, M)
        print(f"  {sa.n_stocks} stocks in arrays")

        current_combos = COMBO_DEFINITIONS.get(tf_key, COMBO_DEFINITIONS["1W"])
        tf_json: Dict[str, Any] = {}
        all_recs[tf_key] = {}
        overlay_chart_data: List[Dict] = []

        for k in [3, 4, 5]:
            ck = f"C{k}"
            ck_def = f"combo_{k}"
            cur_kpis = current_combos.get(ck_def, [])

            print(f"\n  {ck}: Screening ALL combos (min {mt} trades)...")
            t1 = time.time()
            all_sr = screen_all(sa, k, ALL_KPIS, K, mt)
            print(f"    {len(all_sr):,} combos passed threshold ({time.time()-t1:.1f}s)")

            if not all_sr:
                print("    No combos found, skipping")
                continue

            by_pnl = sorted(all_sr, key=lambda r: r.total_pnl, reverse=True)
            by_avg = sorted(all_sr, key=lambda r: r.avg_ret, reverse=True)
            print(f"    Top PnL (screen): {_sl(by_pnl[0].kpis)} n={by_pnl[0].n} PnL={by_pnl[0].total_pnl:+.0f}%")
            print(f"    Top Avg (screen): {_sl(by_avg[0].kpis)} n={by_avg[0].n} Avg={by_avg[0].avg_ret:+.2f}%")

            pareto_idx = find_pareto(all_sr)
            print(f"    Pareto frontier: {len(pareto_idx)} combos")

            cand_idx = select_candidates(all_sr, pareto_idx, SIM_CANDIDATES_PER_CK)
            print(f"    Simulating {len(cand_idx)} candidates with Exit Flow v3...")

            t2 = time.time()
            sim_results: List[Tuple[int, FR]] = []
            for ci, idx in enumerate(cand_idx):
                sr = all_sr[idx]
                fr, _ = sim_v3(data, sr.kpis, T, M, K, vol=sr.vol, mn=mt)
                if fr:
                    sim_results.append((idx, fr))
                if (ci + 1) % 15 == 0:
                    print(f"      {ci+1}/{len(cand_idx)} ({time.time()-t2:.0f}s)")
            sim_results.sort(key=lambda x: x[1].total_ret, reverse=True)
            print(f"    {len(sim_results)} simulated ({time.time()-t2:.0f}s)")

            cur_fr = None
            if cur_kpis:
                cur_fr, _ = sim_v3(data, cur_kpis, T, M, K, vol=False, mn=3)
                if cur_fr:
                    print(f"    Current: n={cur_fr.n} HR={cur_fr.hr:.0f}% Avg={cur_fr.avg_ret:+.2f}% PnL={cur_fr.total_ret:+.0f}%")

            if sim_results:
                best = sim_results[0][1]
                vl = " +vol" if best.vol else ""
                print(f"    Best sim: {_sl(best.kpis)}{vl} n={best.n} HR={best.hr:.0f}% Avg={best.avg_ret:+.2f}% PnL={best.total_ret:+.0f}%")

            chart_pareto(all_sr, pareto_idx, sim_results, cur_fr, k, tf_key, tf_out)

            # ── Quality overlay on top 5 PnL combos ──────────────
            print(f"    Overlay analysis (BB30 + NWE MAE) on top 5 P&L combos...")
            top5 = sim_results[:5]
            for rank, (idx, fr_base) in enumerate(top5):
                sr = all_sr[idx]
                _, overlay = sim_v3(data, sr.kpis, T, M, K, vol=sr.vol, mn=3,
                                    overlay_kpis=OVERLAY_KPIS)
                label = f"{ck} #{rank+1}"
                overlay_chart_data.append({
                    "combo_label": label, "overlay": "All",
                    "total_ret": fr_base.total_ret, "n": fr_base.n,
                    "avg_ret": fr_base.avg_ret, "hr": fr_base.hr,
                })
                if overlay:
                    for ok_name in OVERLAY_KPIS:
                        short = _s(ok_name)
                        for bucket, suffix in [("yes", "Y"), ("no", "N")]:
                            key = f"{short}_{bucket}"
                            if key in overlay:
                                ofr = overlay[key]
                                overlay_chart_data.append({
                                    "combo_label": label,
                                    "overlay": f"{'BB30' if 'BB30' in short else 'NWE'}={suffix}",
                                    "total_ret": ofr.total_ret, "n": ofr.n,
                                    "avg_ret": ofr.avg_ret, "hr": ofr.hr,
                                })
                    bb30_y = overlay.get("BB30_yes")
                    bb30_n = overlay.get("BB30_no")
                    if bb30_y and bb30_n and bb30_y.n >= 3 and bb30_n.n >= 3:
                        lift = bb30_y.avg_ret - bb30_n.avg_ret
                        print(f"      #{rank+1} {_sl(sr.kpis)}: BB30 lift = {lift:+.2f}% "
                              f"(Y: {bb30_y.n}t/{bb30_y.hr:.0f}%HR/{bb30_y.avg_ret:+.2f}% "
                              f"vs N: {bb30_n.n}t/{bb30_n.hr:.0f}%HR/{bb30_n.avg_ret:+.2f}%)")

            # ── Record best for summary ──
            if sim_results:
                best = sim_results[0][1]
                bb30_lift_str = "n/a"
                for d in overlay_chart_data:
                    if d["combo_label"] == f"{ck} #1" and d["overlay"] == "BB30=Y":
                        bb30_y_avg = d["avg_ret"]
                        for d2 in overlay_chart_data:
                            if d2["combo_label"] == f"{ck} #1" and d2["overlay"] == "BB30=N":
                                bb30_lift_str = f"{bb30_y_avg - d2['avg_ret']:+.1f}%"
                                break
                        break
                all_recs[tf_key][ck] = {
                    "tf": tf_key, "ck": ck, "kpis_short": _sl(best.kpis),
                    "vol": best.vol, "n": best.n, "hr": best.hr,
                    "avg_ret": best.avg_ret, "total_ret": best.total_ret,
                    "pf": best.pf, "atr_pct": best.atr_pct,
                    "bb30_lift": bb30_lift_str,
                    "strategy": "Base" if best.avg_ret < 4 else "Quality",
                    "kpis": best.kpis,
                }

            tf_json[ck] = {
                "screened": len(all_sr),
                "pareto": len(pareto_idx),
                "simulated": len(sim_results),
                "best_kpis": sim_results[0][1].kpis if sim_results else [],
                "best_vol": sim_results[0][1].vol if sim_results else False,
                "best_n": sim_results[0][1].n if sim_results else 0,
                "best_hr": round(sim_results[0][1].hr, 1) if sim_results else 0,
                "best_avg_ret": round(sim_results[0][1].avg_ret, 2) if sim_results else 0,
                "best_total_pnl": round(sim_results[0][1].total_ret, 0) if sim_results else 0,
                "current_n": cur_fr.n if cur_fr else 0,
                "current_pnl": round(cur_fr.total_ret, 0) if cur_fr else 0,
            }

        chart_overlay(overlay_chart_data, tf_key, tf_out)
        all_json[tf_key] = tf_json

    chart_summary_table(all_recs, out_root)

    jp = out_root / "phase11v6_results.json"
    jp.write_text(json.dumps(all_json, indent=2, default=str))
    all_recs_ser = {}
    for tf, cks in all_recs.items():
        all_recs_ser[tf] = {}
        for ck, rec in cks.items():
            all_recs_ser[tf][ck] = {k: v for k, v in rec.items()}
    (out_root / "phase11v6_recommendations.json").write_text(
        json.dumps(all_recs_ser, indent=2, default=str))

    print(f"\n{'=' * 70}")
    print(f"  SUMMARY")
    print(f"{'=' * 70}")
    for tf in ["4H", "1D", "1W"]:
        print(f"\n  {tf}:")
        for ck in ["C3", "C4", "C5"]:
            rec = all_recs.get(tf, {}).get(ck)
            if rec:
                vl = " +vol" if rec["vol"] else ""
                print(f"    {ck}: {rec['kpis_short']}{vl} — "
                      f"n={rec['n']} HR={rec['hr']:.0f}% Avg={rec['avg_ret']:+.2f}% "
                      f"PnL={rec['total_ret']:+.0f}% BB30-lift={rec['bb30_lift']}")

    print(f"\n  Saved results to {out_root}")
    print(f"  Done in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

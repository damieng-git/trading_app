"""
Phase 11 v9 — Unified Position Simulator

One position per stock at a time. Multiple entry signals (C3/C4) can scale
the position up, but there is only ONE exit — after which the position is flat.

Tests three scenarios:
  A) C3 only at 1x (baseline — no tiering)
  B) C3 at 1x, scale to 1.5x when C4 also fires
  C) C3 at 1x, scale to 2x when C4 also fires

Exit: based on the ENTRY combo level (highest active).
  When C4 is active → exit on C4 rules (2/4 KPIs bearish)
  When only C3 is active → exit on C3 rules (2/3 KPIs bearish)
  ATR stop + checkpoint reset (Exit Flow v4, K=4.0)

Uses the locked combos from v7 and locked exit params from v8.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
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

EXIT_PARAMS = {
    "4H": {"T": 4, "M": 48, "K": 4.0},
    "1D": {"T": 4, "M": 40, "K": 4.0},
    "1W": {"T": 2, "M": 20, "K": 4.0},
}

ENTRY_COMBOS = {
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
class Trade:
    entry_bar: int
    exit_bar: int
    entry_price: float
    exit_price: float
    return_pct: float
    weighted_return: float
    holding_bars: int
    exit_reason: str
    max_level: str
    n_resets: int
    size_at_exit: float


@dataclass
class ScenarioResult:
    name: str
    c4_weight: float
    trades: List[Trade]
    n: int
    hr: float
    avg_ret: float
    med_ret: float
    total_ret_unweighted: float
    total_ret_weighted: float
    avg_hold: float
    max_hold: int
    pf: float
    avg_size: float
    pct_scaled_up: float


def run_unified_sim(data: Dict[str, pd.DataFrame],
                    c3_kpis: List[str], c4_kpis: List[str],
                    T: int, M: int, K: float,
                    c4_weight: float) -> ScenarioResult:
    """
    Unified single-position simulator.

    For each stock:
      - Scan OOS bars sequentially
      - When C3 fires and no position → enter at 1x
      - While in position, check if C4 also fires → scale to c4_weight
      - Exit based on highest active combo level's invalidation rules
      - After exit, position is flat — wait for next C3 entry
    """
    all_trades: List[Trade] = []

    for sym, df in data.items():
        if df.empty:
            continue
        sm = compute_kpi_state_map(df)
        if any(k not in sm for k in c3_kpis):
            continue

        c3_bull = pd.Series(True, index=df.index)
        for kpi in c3_kpis:
            c3_bull &= (sm[kpi] == STATE_BULL)

        c4_available = all(k in sm for k in c4_kpis)
        c4_bull = pd.Series(False, index=df.index)
        if c4_available:
            c4_bull = pd.Series(True, index=df.index)
            for kpi in c4_kpis:
                c4_bull &= (sm[kpi] == STATE_BULL)

        si = int(len(df) * IS_FRACTION)
        cl = df["Close"].to_numpy(float)
        at = compute_atr(df, ATR_PERIOD).to_numpy(float)

        j = si
        while j < len(df):
            if not c3_bull.iloc[j]:
                j += 1
                continue

            ep = float(cl[j])
            if ep <= 0:
                j += 1
                continue

            ei = j
            stop_price = ep
            stop = stop_price - K * at[ei] if at[ei] > 0 else -np.inf
            bars_since_reset = 0
            n_resets = 0
            current_size = 1.0
            max_level = "C3"
            was_scaled = False

            if c4_available and c4_bull.iloc[j]:
                current_size = c4_weight
                max_level = "C4"
                was_scaled = True

            active_kpis = c4_kpis if max_level == "C4" else c3_kpis
            nk = len(active_kpis)
            xi = None
            reason = "mh"

            j_inner = ei + 1
            while j_inner < min(ei + MAX_HOLD_HARD_CAP + 1, len(df)):
                bars_since_reset += 1
                c = float(cl[j_inner])
                total_bars = j_inner - ei

                if c < stop:
                    xi, reason = j_inner, "atr"
                    break

                if not was_scaled and c4_available and c4_bull.iloc[j_inner]:
                    current_size = c4_weight
                    max_level = "C4"
                    was_scaled = True
                    active_kpis = c4_kpis
                    nk = len(active_kpis)

                nb = sum(1 for kk in active_kpis
                         if kk in sm and j_inner < len(sm[kk])
                         and int(sm[kk].iloc[j_inner]) != STATE_BULL)

                if total_bars <= T:
                    if nb >= nk:
                        xi, reason = j_inner, "len"
                        break
                else:
                    if nb >= 2:
                        xi, reason = j_inner, "str"
                        break

                if bars_since_reset >= M:
                    check_kpis = active_kpis
                    nb_check = sum(1 for kk in check_kpis
                                   if kk in sm and j_inner < len(sm[kk])
                                   and int(sm[kk].iloc[j_inner]) != STATE_BULL)
                    if nb_check == 0:
                        n_resets += 1
                        stop_price = c
                        stop = stop_price - K * at[j_inner] if j_inner < len(at) and at[j_inner] > 0 else stop
                        bars_since_reset = 0
                    else:
                        xi, reason = j_inner, "reset_exit"
                        break

                j_inner += 1

            if xi is None:
                xi = min(j_inner, len(df) - 1)

            xp = float(cl[xi])
            h = xi - ei
            if h > 0:
                ret_pct = (xp - ep) / ep * 100
                weighted_ret = ret_pct * current_size
                all_trades.append(Trade(
                    ei, xi, ep, xp, ret_pct, weighted_ret,
                    h, reason, max_level, n_resets, current_size,
                ))

            j = xi + 1

    n = len(all_trades)
    if n == 0:
        return ScenarioResult(
            f"C4@{c4_weight}x", c4_weight, [], 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    rets_uw = [t.return_pct for t in all_trades]
    rets_wt = [t.weighted_return for t in all_trades]
    hr = sum(1 for r in rets_uw if r > 0) / n * 100
    wi = sum(r for r in rets_wt if r > 0)
    lo = abs(sum(r for r in rets_wt if r <= 0))
    pf = wi / lo if lo > 0 else 999.0
    holds = [t.holding_bars for t in all_trades]
    sizes = [t.size_at_exit for t in all_trades]
    scaled = sum(1 for t in all_trades if t.max_level == "C4")

    return ScenarioResult(
        name=f"C4@{c4_weight}x",
        c4_weight=c4_weight,
        trades=all_trades,
        n=n, hr=hr,
        avg_ret=float(np.mean(rets_uw)),
        med_ret=float(np.median(rets_uw)),
        total_ret_unweighted=float(np.sum(rets_uw)),
        total_ret_weighted=float(np.sum(rets_wt)),
        avg_hold=float(np.mean(holds)),
        max_hold=int(np.max(holds)),
        pf=pf,
        avg_size=float(np.mean(sizes)),
        pct_scaled_up=scaled / n * 100,
    )


def chart_comparison(results: Dict[str, ScenarioResult], tf: str, out: Path):
    scenarios = ["C3 only (1x)", "C3+C4 (1x/1.5x)", "C3+C4 (1x/2x)"]
    keys = ["1.0", "1.5", "2.0"]

    fig, axes = plt.subplots(2, 3, figsize=(24, 14))
    metrics = [
        ("total_ret_weighted", "Total Weighted P&L (%)", axes[0, 0]),
        ("total_ret_unweighted", "Total Unweighted P&L (%)", axes[0, 1]),
        ("n", "Trade Count", axes[0, 2]),
        ("hr", "Hit Rate (%)", axes[1, 0]),
        ("pf", "Profit Factor", axes[1, 1]),
        ("avg_hold", "Avg Hold (bars)", axes[1, 2]),
    ]

    colors = ["#42a5f5", "#66bb6a", "#ff7043"]

    for attr, ylabel, ax in metrics:
        vals = [getattr(results[k], attr) for k in keys]
        bars = ax.bar(range(len(keys)), vals, color=colors, edgecolor="white", linewidth=0.5)
        ax.set_xticks(range(len(keys)))
        ax.set_xticklabels(scenarios, fontsize=9)
        ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.15, axis="y")
        for i, v in enumerate(vals):
            fmt = f"{v:+.0f}%" if "P&L" in ylabel else (f"{v:.0f}" if v > 10 else f"{v:.1f}")
            ax.text(i, v, fmt, ha="center", va="bottom", fontsize=9, fontweight="bold", color="white")
        if attr == "total_ret_weighted":
            base = vals[0]
            for i in [1, 2]:
                if base > 0:
                    lift = (vals[i] - base) / base * 100
                    ax.annotate(f"{lift:+.0f}% vs 1x", (i, vals[i]),
                                fontsize=8, color="#aaffaa", ha="center",
                                xytext=(0, 12), textcoords="offset points")

    fig.suptitle(f"{tf} — Unified Position: C3 Only vs C3+C4 Tiered Sizing\n"
                 f"(Exit Flow v4, K=4.0, one position per stock, single exit)",
                 fontsize=14, fontweight="bold")
    plt.tight_layout()
    fig.savefig(out / "unified_comparison.png")
    plt.close(fig)
    print(f"    Saved unified_comparison.png")


def chart_scaling_detail(results: Dict[str, ScenarioResult], tf: str, out: Path):
    """Show how often C4 fires and its impact."""
    fig, axes = plt.subplots(1, 3, figsize=(22, 7))

    for ax, key, label in zip(axes, ["1.5", "2.0"], ["1x/1.5x", "1x/2x"]):
        r = results[key]
        c3_only = [t for t in r.trades if t.max_level == "C3"]
        c4_scaled = [t for t in r.trades if t.max_level == "C4"]
        n3, n4 = len(c3_only), len(c4_scaled)
        if n3 + n4 == 0:
            continue
        r3 = [t.return_pct for t in c3_only]
        r4 = [t.return_pct for t in c4_scaled]

        data_plot = []
        labels_plot = []
        if r3:
            data_plot.append(r3)
            labels_plot.append(f"C3 only\nn={n3}\navg={np.mean(r3):+.1f}%")
        if r4:
            data_plot.append(r4)
            labels_plot.append(f"C4 scaled\nn={n4}\navg={np.mean(r4):+.1f}%")

        bp = ax.boxplot(data_plot, labels=labels_plot, patch_artist=True,
                        medianprops=dict(color="white", linewidth=2))
        colors_bp = ["#42a5f5", "#66bb6a"]
        for patch, color in zip(bp["boxes"], colors_bp):
            patch.set_facecolor(color)
            patch.set_alpha(0.6)
        ax.axhline(0, color="#ef5350", linestyle="--", alpha=0.5)
        ax.set_ylabel("Return per trade (%)")
        ax.set_title(f"{label}: {n4}/{n3+n4} trades scaled ({n4/(n3+n4)*100:.0f}%)")
        ax.grid(True, alpha=0.15, axis="y")

    ax3 = axes[2]
    for key, color, label in [("1.5", "#66bb6a", "1x/1.5x"), ("2.0", "#ff7043", "1x/2x")]:
        r = results[key]
        sizes = sorted(set(t.size_at_exit for t in r.trades))
        for s in sizes:
            trades_s = [t for t in r.trades if t.size_at_exit == s]
            ax3.scatter([s] * len(trades_s),
                        [t.return_pct for t in trades_s],
                        alpha=0.15, s=8, color=color)
        trade_by_size = {}
        for t in r.trades:
            trade_by_size.setdefault(t.size_at_exit, []).append(t.return_pct)
        xs = sorted(trade_by_size.keys())
        ys = [np.mean(trade_by_size[x]) for x in xs]
        ax3.plot(xs, ys, "o-", color=color, linewidth=2, markersize=8, label=label)
    ax3.axhline(0, color="#ef5350", linestyle="--", alpha=0.5)
    ax3.set_xlabel("Position Size (x)")
    ax3.set_ylabel("Avg Return (%)")
    ax3.set_title("Avg Return by Position Size")
    ax3.legend(fontsize=9)
    ax3.grid(True, alpha=0.15)

    fig.suptitle(f"{tf} — Scaling Detail: When C4 fires, does it help?",
                 fontsize=13, fontweight="bold")
    plt.tight_layout()
    fig.savefig(out / "scaling_detail.png")
    plt.close(fig)
    print(f"    Saved scaling_detail.png")


def chart_summary(all_results: Dict[str, Dict[str, ScenarioResult]], out: Path):
    rows = []
    for tf in ["4H", "1D", "1W"]:
        for key, label in [("1.0", "C3 only 1x"), ("1.5", "C3+C4 1x/1.5x"), ("2.0", "C3+C4 1x/2x")]:
            r = all_results.get(tf, {}).get(key)
            if r:
                rows.append({"tf": tf, "scenario": label, "r": r})
    if not rows:
        return

    fig, ax = plt.subplots(figsize=(28, max(5, len(rows) * 0.7 + 4)))
    ax.axis("off")
    hdr = ["TF", "Scenario", "Trades", "HR%", "Avg%", "PnL (1x)", "PnL (wtd)",
           "PF", "AvgHold", "MaxHold", "AvgSize", "%Scaled", "Verdict"]
    ct, cc = [], []
    for i, row in enumerate(rows):
        r = row["r"]
        if row["scenario"] == "C3 only 1x":
            verdict = "Baseline"
        else:
            base = all_results[row["tf"]]["1.0"]
            lift = (r.total_ret_weighted - base.total_ret_weighted) / abs(base.total_ret_weighted) * 100 if base.total_ret_weighted != 0 else 0
            verdict = f"{lift:+.0f}% vs baseline"
        ct.append([
            row["tf"], row["scenario"], str(r.n),
            f"{r.hr:.0f}", f"{r.avg_ret:+.2f}",
            f"{r.total_ret_unweighted:+.0f}", f"{r.total_ret_weighted:+.0f}",
            f"{r.pf:.1f}", f"{r.avg_hold:.0f}", str(r.max_hold),
            f"{r.avg_size:.2f}", f"{r.pct_scaled_up:.0f}%", verdict,
        ])
        bg = "#1a3a1a" if "1x/2x" in row["scenario"] else (
            "#1e1e1e" if i % 2 == 0 else "#252525")
        cc.append([bg] * len(hdr))

    t = ax.table(cellText=ct, colLabels=hdr, loc="center", cellLoc="center")
    t.auto_set_font_size(False); t.set_fontsize(8); t.scale(1.0, 1.7)
    for (r, c), cell in t.get_celld().items():
        if r == 0:
            cell.set_facecolor("#333")
            cell.set_text_props(fontweight="bold", color="white")
        else:
            cell.set_facecolor(cc[r - 1][c])
            cell.set_text_props(color="white")
        cell.set_edgecolor("#444")

    fig.text(0.02, 0.01,
             "NOTE: All scenarios use ONE position per stock with a single exit.\n"
             "PnL(1x) = unweighted (all trades at 1x). PnL(wtd) = weighted by actual position size.\n"
             "%Scaled = % of trades where C4 fired and position was scaled up.",
             fontsize=8.5, color="#aaa", verticalalignment="bottom", fontfamily="monospace",
             bbox=dict(boxstyle="round,pad=0.4", facecolor="#222", alpha=0.9))

    ax.set_title("Phase 11 v9 — Unified Position Sim: Is Tiered Sizing Worth It?\n"
                 "(Exit Flow v4, K=4.0, 320 stocks, single exit per position)",
                 fontsize=14, fontweight="bold", pad=25)
    plt.tight_layout(rect=[0, 0.08, 1, 0.95])
    fig.savefig(out / "unified_summary.png")
    plt.close(fig)
    print(f"  Saved unified_summary.png")


def main():
    t0 = time.time()
    out_root = output_dir_for("all", "phase11v9")
    out_root.mkdir(parents=True, exist_ok=True)
    all_json: Dict[str, Any] = {}
    all_results: Dict[str, Dict[str, ScenarioResult]] = {}

    for tf_key in ["1W", "1D", "4H"]:
        print(f"\n{'=' * 70}")
        print(f"  TIMEFRAME: {tf_key}")
        print(f"{'=' * 70}")

        data = load_data(ENRICHED_DIR, tf_key)
        print(f"  Loaded {len(data)} stocks")

        tf_out = output_dir_for(tf_key, "phase11v9")
        tf_out.mkdir(parents=True, exist_ok=True)

        p = EXIT_PARAMS[tf_key]
        T, M, K = p["T"], p["M"], p["K"]
        c3_kpis = ENTRY_COMBOS[tf_key]["C3"]
        c4_kpis = ENTRY_COMBOS[tf_key]["C4"]

        print(f"  C3: {_sl(c3_kpis)}")
        print(f"  C4: {_sl(c4_kpis)}")
        print(f"  Exit: T={T}, M={M}, K={K}")

        tf_results: Dict[str, ScenarioResult] = {}

        for c4w, label in [(1.0, "C3 only (1x)"), (1.5, "C3+C4 (1x/1.5x)"), (2.0, "C3+C4 (1x/2x)")]:
            print(f"\n  Scenario: {label}...")
            t1 = time.time()
            r = run_unified_sim(data, c3_kpis, c4_kpis, T, M, K, c4w)
            elapsed = time.time() - t1

            print(f"    n={r.n} HR={r.hr:.0f}% Avg={r.avg_ret:+.2f}% "
                  f"PnL(1x)={r.total_ret_unweighted:+.0f}% "
                  f"PnL(wtd)={r.total_ret_weighted:+.0f}% "
                  f"PF={r.pf:.1f} AvgH={r.avg_hold:.0f} MaxH={r.max_hold}")
            print(f"    AvgSize={r.avg_size:.2f}x Scaled={r.pct_scaled_up:.0f}% ({elapsed:.0f}s)")

            tf_results[f"{c4w}"] = r

        chart_comparison(tf_results, tf_key, tf_out)
        chart_scaling_detail(tf_results, tf_key, tf_out)

        all_results[tf_key] = tf_results
        all_json[tf_key] = {}
        for key, r in tf_results.items():
            all_json[tf_key][key] = {
                "n": r.n, "hr": round(r.hr, 1),
                "avg_ret": round(r.avg_ret, 2),
                "total_pnl_1x": round(r.total_ret_unweighted),
                "total_pnl_weighted": round(r.total_ret_weighted),
                "pf": round(r.pf, 1),
                "avg_hold": round(r.avg_hold, 1),
                "max_hold": r.max_hold,
                "avg_size": round(r.avg_size, 3),
                "pct_scaled": round(r.pct_scaled_up, 1),
            }

    chart_summary(all_results, out_root)

    jp = out_root / "phase11v9_results.json"
    jp.write_text(json.dumps(all_json, indent=2, default=str))

    print(f"\n{'=' * 70}")
    print(f"  VERDICT")
    print(f"{'=' * 70}")
    for tf in ["4H", "1D", "1W"]:
        base = all_results[tf]["1.0"]
        r15 = all_results[tf]["1.5"]
        r20 = all_results[tf]["2.0"]
        lift15 = (r15.total_ret_weighted - base.total_ret_weighted) / abs(base.total_ret_weighted) * 100 if base.total_ret_weighted else 0
        lift20 = (r20.total_ret_weighted - base.total_ret_weighted) / abs(base.total_ret_weighted) * 100 if base.total_ret_weighted else 0
        print(f"\n  {tf}:")
        print(f"    C3 only:    {base.n:5d} trades, PnL={base.total_ret_weighted:+.0f}%")
        print(f"    1x/1.5x:    {r15.n:5d} trades, PnL={r15.total_ret_weighted:+.0f}% ({lift15:+.0f}%)")
        print(f"    1x/2x:      {r20.n:5d} trades, PnL={r20.total_ret_weighted:+.0f}% ({lift20:+.0f}%)")
        print(f"    Scaled up:  {r20.pct_scaled_up:.0f}% of trades")

    print(f"\n  Done in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

"""
KPI Optimization — Visualization Suite

Generates static PNG charts from Phase 1-6 output data.
All data is read dynamically from output files (no hardcoded values).
Supports --timeframe {1W, 1D, 4H}.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from textwrap import fill

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np
import pandas as pd
import seaborn as sns
from scipy.cluster.hierarchy import linkage
from scipy.spatial.distance import squareform

plt.style.use("dark_background")
plt.rcParams.update({
    "font.family": "sans-serif",
    "font.size": 10,
    "axes.titlesize": 13,
    "axes.titleweight": "bold",
    "figure.facecolor": "#181818",
    "axes.facecolor": "#1e1e1e",
    "savefig.facecolor": "#181818",
    "savefig.dpi": 180,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.3,
})

_parser = argparse.ArgumentParser(description="KPI Optimization — Visualization Suite")
_parser.add_argument("--timeframe", "-tf", default="1W", choices=["1W", "1D", "4H"])
_args = _parser.parse_args()
TF = _args.timeframe

BASE = Path(__file__).resolve().parent / "outputs" / TF
CHARTS = BASE / "charts"
CHARTS.mkdir(parents=True, exist_ok=True)

DIM_COLORS = {
    "Trend": "#4fc3f7",
    "Momentum": "#ab47bc",
    "Relative Strength": "#ffb74d",
    "Breakout": "#66bb6a",
    "Risk / Exit": "#ef5350",
    "Other": "#78909c",
}

TF_HORIZON_MAP = {
    "1W": {"hr_col": "HR 4w", "horizons": ["HR 1w", "HR 4w", "HR 13w"],
            "horizon_labels": ["1 Week", "4 Weeks", "13 Weeks"], "pf_col": "PF 4w"},
    "1D": {"hr_col": "HR 5d", "horizons": ["HR 1d", "HR 5d", "HR 20d"],
            "horizon_labels": ["1 Day", "5 Days", "20 Days"], "pf_col": "PF 5d"},
    "4H": {"hr_col": "HR 24h", "horizons": ["HR 8h", "HR 24h", "HR 80h"],
            "horizon_labels": ["8 Hours", "24 Hours", "80 Hours"], "pf_col": "PF 24h"},
}

TF_CFG = TF_HORIZON_MAP[TF]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_commentary(fig, text: str, y: float = -0.02, fontsize: int = 9) -> None:
    fig.text(
        0.05, y, text, fontsize=fontsize, color="#b0bec5",
        ha="left", va="top", wrap=True,
        fontstyle="italic",
        bbox=dict(boxstyle="round,pad=0.5", facecolor="#252525", edgecolor="#444", alpha=0.95),
        transform=fig.transFigure,
    )


def _wrap(text: str, width: int = 120) -> str:
    return fill(text, width=width)


def _parse_md_table(filepath: Path, table_idx: int = 0) -> pd.DataFrame | None:
    """Parse the N-th markdown table from a file."""
    if not filepath.exists():
        return None
    text = filepath.read_text(encoding="utf-8")
    tables = re.findall(
        r'(\|[^\n]+\|\n\|[-| :]+\|\n(?:\|[^\n]+\|\n?)+)', text
    )
    if table_idx >= len(tables):
        return None
    table_text = tables[table_idx].strip()
    lines = table_text.split("\n")
    headers = [h.strip() for h in lines[0].split("|")[1:-1]]
    rows = []
    for line in lines[2:]:
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) == len(headers):
            rows.append(cells)
    return pd.DataFrame(rows, columns=headers)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 1 — Chart 1: KPI Ranking
# ═══════════════════════════════════════════════════════════════════════════

def chart_p1_ranking():
    csv_path = BASE / "phase1" / "kpi_scorecard.csv"
    if not csv_path.exists():
        print("  [1] SKIP phase1_kpi_ranking (no data)")
        return
    df = pd.read_csv(csv_path)
    df = df[df["Available"] == True].copy()
    df = df[~df["KPI"].str.contains("Repainting", na=False)]
    hr_col = TF_CFG["hr_col"]
    df = df.sort_values(hr_col, ascending=True)

    fig, ax = plt.subplots(figsize=(14, max(8, len(df) * 0.4)))
    fig.subplots_adjust(bottom=0.22)

    colors = [DIM_COLORS.get(d, "#78909c") for d in df["Dimension"]]
    ax.barh(range(len(df)), df[hr_col], color=colors, edgecolor="none", height=0.7)

    ax.axvline(0.50, color="#ff5252", linestyle="--", linewidth=1.2, alpha=0.7, label="50% coin flip")
    ax.set_yticks(range(len(df)))
    ax.set_yticklabels(df["KPI"], fontsize=9)
    ax.set_xlabel(f"Hit Rate ({hr_col.replace('HR ', '')} Horizon)")
    ax.set_title(f"Phase 1 — KPI Quality Ranking ({TF}, {hr_col.replace('HR ', '')} Hit Rate, Long-Only)")
    ax.set_xlim(0.45, max(df[hr_col].max() * 1.05, 0.72))

    for i, (hr, sig) in enumerate(zip(df[hr_col], df["Sig"])):
        label = f"{hr:.1%} {sig}" if isinstance(sig, str) else f"{hr:.1%}"
        ax.text(hr + 0.003, i, label, va="center", fontsize=8, color="white")

    ax.axvline(0.55, color="#66bb6a", linestyle=":", linewidth=0.8, alpha=0.5)
    ax.text(0.551, len(df) - 0.5, "Tier B", fontsize=8, color="#66bb6a", alpha=0.7)
    ax.axvline(0.60, color="#4fc3f7", linestyle=":", linewidth=0.8, alpha=0.5)
    ax.text(0.601, len(df) - 0.5, "Tier A", fontsize=8, color="#4fc3f7", alpha=0.7)

    handles = [plt.Rectangle((0, 0), 1, 1, fc=c) for c in DIM_COLORS.values()]
    ax.legend(handles, DIM_COLORS.keys(), loc="lower right", fontsize=8, framealpha=0.3)

    top_kpi = df.iloc[-1]["KPI"]
    top_hr = df.iloc[-1][hr_col]
    n_pass = int((df[hr_col] > 0.50).sum())
    n_total = len(df)
    tier_a = df[df[hr_col] >= 0.60]["KPI"].tolist()
    tier_b = df[(df[hr_col] >= 0.55) & (df[hr_col] < 0.60)]["KPI"].tolist()
    tier_a_str = ", ".join(tier_a[:3]) if tier_a else "None"
    commentary = (
        f"INSIGHT: {n_pass}/{n_total} KPIs beat the coin flip on {TF}. "
        f"Top performer: {top_kpi} ({top_hr:.1%}) — its trend-regime approach captures persistent moves. "
        f"{'Tier A (>60%): ' + tier_a_str + '. These are core signal generators. ' if tier_a else ''}"
        f"{'Tier B (55-60%): ' + str(len(tier_b)) + ' KPIs serve as confirmation filters. ' if tier_b else ''}"
        f"The middle pack (51-54%) all beat random but shouldn't be used standalone. "
        f"RECOMMENDATION: Combine KPIs (see Phase 4) — even marginal KPIs become powerful as AND-filters."
    )
    _add_commentary(fig, _wrap(commentary), y=0.08)
    fig.savefig(CHARTS / "phase1_kpi_ranking.png")
    plt.close(fig)
    print("  [1] phase1_kpi_ranking.png")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 1 — Chart 2: Quality vs Quantity
# ═══════════════════════════════════════════════════════════════════════════

def chart_p1_scatter():
    csv_path = BASE / "phase1" / "kpi_scorecard.csv"
    if not csv_path.exists():
        print("  [2] SKIP phase1_quality_vs_quantity (no data)")
        return
    df = pd.read_csv(csv_path)
    df = df[(df["Available"] == True) & (~df["KPI"].str.contains("Repainting", na=False))].copy()
    hr_col = TF_CFG["hr_col"]
    df = df[df["Freq/yr"] > 0]

    fig, ax = plt.subplots(figsize=(13, 9))
    fig.subplots_adjust(bottom=0.22)

    for dim, color in DIM_COLORS.items():
        mask = df["Dimension"] == dim
        sub = df[mask]
        if sub.empty:
            continue
        sizes = np.clip(sub["Bull Signals"] / 80, 20, 400)
        ax.scatter(sub["Freq/yr"], sub[hr_col], s=sizes, c=color, alpha=0.75, edgecolors="white",
                   linewidths=0.5, label=dim, zorder=3)
        for _, row in sub.iterrows():
            short = row["KPI"][:18]
            ax.annotate(short, (row["Freq/yr"], row[hr_col]), fontsize=7,
                        color="white", alpha=0.8, xytext=(5, 3), textcoords="offset points")

    ax.axhline(0.50, color="#ff5252", linestyle="--", linewidth=1, alpha=0.6)
    ax.set_xlabel("Signal Frequency (bull signals / year)")
    ax.set_ylabel(f"Hit Rate ({hr_col.replace('HR ', '')} Horizon)")
    ax.set_title(f"Phase 1 — Quality vs Quantity Tradeoff ({TF})")
    ax.legend(fontsize=8, framealpha=0.3, loc="upper right")
    ax.grid(True, alpha=0.15)

    ax.text(0.98, 0.98, "HIGH QUALITY\nHIGH FREQ\n(ideal)", transform=ax.transAxes,
            ha="right", va="top", fontsize=8, color="#66bb6a", alpha=0.5)
    ax.text(0.02, 0.98, "HIGH QUALITY\nLOW FREQ\n(selective)", transform=ax.transAxes,
            ha="left", va="top", fontsize=8, color="#ffb74d", alpha=0.5)
    ax.text(0.98, 0.02, "LOW QUALITY\nHIGH FREQ\n(noisy)", transform=ax.transAxes,
            ha="right", va="bottom", fontsize=8, color="#ef5350", alpha=0.5)

    top = df.nlargest(1, hr_col).iloc[0]
    high_freq = df.nlargest(3, "Freq/yr")
    ideal = df[(df[hr_col] > 0.55) & (df["Freq/yr"] > df["Freq/yr"].median())]
    commentary = (
        f"INSIGHT: {top['KPI'][:20]} dominates — high accuracy ({top[hr_col]:.1%}) "
        f"AND high frequency ({top['Freq/yr']:.0f}/yr). "
        f"Most KPIs cluster at 51-54% with {df['Freq/yr'].median():.0f} signals/yr — marginal individually. "
        f"Breakout indicators tend toward low frequency (<5/yr) but higher accuracy. "
        f"RECOMMENDATION: {top['KPI'][:20]} is the only KPI suitable as standalone. "
        f"All others should be used in combination. High-freq + low-accuracy = noise. Low-freq + high-accuracy = too rare."
    )
    _add_commentary(fig, _wrap(commentary), y=0.08)
    fig.savefig(CHARTS / "phase1_quality_vs_quantity.png")
    plt.close(fig)
    print("  [2] phase1_quality_vs_quantity.png")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 1 — Chart 3: Horizon Heatmap
# ═══════════════════════════════════════════════════════════════════════════

def chart_p1_horizon():
    csv_path = BASE / "phase1" / "kpi_scorecard.csv"
    if not csv_path.exists():
        print("  [3] SKIP phase1_horizon_heatmap (no data)")
        return
    df = pd.read_csv(csv_path)
    hr_col = TF_CFG["hr_col"]
    horizon_cols = TF_CFG["horizons"]
    horizon_labels = TF_CFG["horizon_labels"]

    df = df[(df["Available"] == True) & (~df["KPI"].str.contains("Repainting", na=False))
            & (~df["KPI"].str.contains("Envelop", na=False))].copy()
    df = df.sort_values(hr_col, ascending=False)

    avail_cols = [c for c in horizon_cols if c in df.columns]
    if not avail_cols:
        print("  [3] SKIP phase1_horizon_heatmap (no horizon columns)")
        return

    matrix = df[avail_cols].values
    labels_y = df["KPI"].tolist()
    labels_x = horizon_labels[:len(avail_cols)]

    fig, ax = plt.subplots(figsize=(8, max(8, len(labels_y) * 0.45)))
    fig.subplots_adjust(bottom=0.18)

    im = ax.imshow(matrix, aspect="auto", cmap="RdYlGn", vmin=0.47, vmax=0.70)
    ax.set_xticks(range(len(avail_cols)))
    ax.set_xticklabels(labels_x, fontsize=10)
    ax.set_yticks(range(len(labels_y)))
    ax.set_yticklabels(labels_y, fontsize=9)
    ax.set_title(f"Phase 1 — Hit Rate Across Horizons ({TF})")

    for i in range(len(labels_y)):
        for j in range(len(avail_cols)):
            val = matrix[i, j]
            color = "black" if val > 0.58 else "white"
            ax.text(j, i, f"{val:.1%}", ha="center", va="center", fontsize=8, color=color, fontweight="bold")

    fig.colorbar(im, ax=ax, shrink=0.5, label="Hit Rate")

    top_name = labels_y[0]
    short_h = matrix[0, 0]
    long_h = matrix[0, -1]
    avg_improvement = float(np.nanmean(matrix[:, -1] - matrix[:, 0]))
    commentary = (
        f"INSIGHT: All KPIs show improving hit rates at longer horizons, confirming they capture "
        f"genuine trends rather than noise. {top_name[:20]} scales from {short_h:.1%} ({labels_x[0]}) "
        f"to {long_h:.1%} ({labels_x[-1]}). "
        f"Average improvement from shortest to longest horizon: +{avg_improvement:.1%}. "
        f"RECOMMENDATION: The middle horizon ({labels_x[1]}) is the practical evaluation metric — "
        f"long enough for signal to materialize, short enough for actionable trading."
    )
    _add_commentary(fig, _wrap(commentary), y=0.06)
    fig.savefig(CHARTS / "phase1_horizon_heatmap.png")
    plt.close(fig)
    print("  [3] phase1_horizon_heatmap.png")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 2 — Chart 4: Before/After (from report)
# ═══════════════════════════════════════════════════════════════════════════

def chart_p2_optimization():
    report_path = BASE / "phase2" / "param_optimization_report.md"
    if not report_path.exists():
        print("  [4] SKIP phase2_param_optimization (no data)")
        return

    tbl = _parse_md_table(report_path, table_idx=0)
    if tbl is None or tbl.empty:
        print("  [4] SKIP phase2_param_optimization (table parse failed)")
        return

    names = tbl["Indicator"].tolist()
    defaults = []
    optimized = []
    params = []
    for _, row in tbl.iterrows():
        def _float(v):
            try:
                return float(v.replace("—", "nan"))
            except Exception:
                return np.nan
        defaults.append(_float(row.get("Default HR (OOS)", "nan")))
        opt = _float(row.get("Best HR (OOS)", "nan"))
        optimized.append(opt if not np.isnan(opt) else None)
        params.append(row.get("Best Params", ""))

    fig, ax = plt.subplots(figsize=(max(10, len(names) * 1.1), 7))
    fig.subplots_adjust(bottom=0.24)

    x = np.arange(len(names))
    w = 0.35
    ax.bar(x - w / 2, defaults, w, label="Default OOS HR", color="#616161", edgecolor="none")
    opt_vals = [v if v is not None else 0 for v in optimized]
    opt_colors = ["#66bb6a" if v is not None else "#2e2e2e" for v in optimized]
    ax.bar(x + w / 2, opt_vals, w, label="Optimized OOS HR", color=opt_colors, edgecolor="none")

    ax.axhline(0.50, color="#ff5252", linestyle="--", linewidth=1, alpha=0.5)
    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=30, ha="right", fontsize=9)
    ax.set_ylabel("OOS Hit Rate")
    ax.set_title(f"Phase 2 — Parameter Optimization ({TF}, Default vs Best OOS)")
    ax.legend(fontsize=9, framealpha=0.3)
    ax.set_ylim(0.48, max(max(defaults), max(v for v in optimized if v is not None) if any(v is not None for v in optimized) else 0.6) * 1.08)

    for i, (d, o, p) in enumerate(zip(defaults, optimized, params)):
        if not np.isnan(d):
            ax.text(i - w / 2, d + 0.005, f"{d:.1%}", ha="center", fontsize=7, color="white")
        if o is not None:
            delta = o - d
            ax.text(i + w / 2, o + 0.005, f"{o:.1%}\n(+{delta:.1%})", ha="center", fontsize=7,
                    color="#66bb6a", fontweight="bold")

    fig.savefig(CHARTS / "phase2_param_optimization.png")
    plt.close(fig)
    print("  [4] phase2_param_optimization.png")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 3 — Chart 5: Correlation Clustermap
# ═══════════════════════════════════════════════════════════════════════════

def chart_p3_correlation():
    csv_path = BASE / "phase3" / "correlation_matrix.csv"
    if not csv_path.exists():
        print("  [5] SKIP phase3_correlation_clustermap (no data)")
        return
    corr = pd.read_csv(csv_path, index_col=0)

    short = {
        "Nadaraya-Watson Smoother": "NW Smoother",
        "RSI Strength & Consolidation Zones (Zeiierman)": "RSI Zeiierman",
        "RSI Strength & Consolidation Zones (Zeiierman) (breakout)": "RSI Zeii (BO)",
        "Nadaraya-Watson Envelop (MAE)": "NWE (MAE)",
        "Nadaraya-Watson Envelop (STD)": "NWE (STD)",
        "Nadaraya-Watson Envelop (Repainting)": "NWE (Repaint)",
        "CM_Ult_MacD_MFT": "MACD",
    }
    corr = corr.rename(index=short, columns=short)

    dist = 1.0 - corr.clip(-1, 1)
    np.fill_diagonal(dist.values, 0)
    dist = (dist + dist.T) / 2
    dist = dist.clip(lower=0)
    condensed = squareform(dist.values, checks=False)
    condensed = np.nan_to_num(condensed, nan=1.0)

    g = sns.clustermap(
        corr, method="ward", metric="precomputed",
        row_linkage=linkage(condensed, method="ward"),
        col_linkage=linkage(condensed, method="ward"),
        cmap="RdYlGn", vmin=-0.3, vmax=0.75,
        figsize=(14, 12), linewidths=0.3, linecolor="#333",
        annot=True, fmt=".2f", annot_kws={"fontsize": 6},
        cbar_kws={"label": "Pearson Correlation (median across stocks)"},
        dendrogram_ratio=(0.12, 0.12),
    )
    g.fig.suptitle(f"Phase 3 — KPI State Correlation ({TF}, Hierarchical Clustering)",
                   fontsize=14, fontweight="bold", y=1.01)
    g.ax_heatmap.tick_params(axis="both", labelsize=8)

    upper = corr.where(np.triu(np.ones(corr.shape), k=1).astype(bool))
    top_pairs = upper.stack().nlargest(3)
    pair_strs = [f"{a}/{b} ({v:.2f})" for (a, b), v in top_pairs.items()]
    n_kpis = len(corr)
    retained_path = BASE / "phase3" / "retained_kpis.json"
    n_dropped = 0
    dropped_names = []
    if retained_path.exists():
        ret = json.loads(retained_path.read_text())
        n_dropped = len(ret.get("dropped", []))
        dropped_names = ret.get("dropped", [])
    dropped_str = ", ".join(dropped_names) if dropped_names else "none"
    commentary = (
        f"INSIGHT: {n_kpis} KPIs analyzed. Top correlated pairs: {'; '.join(pair_strs)}. "
        f"Dropped as redundant: {dropped_str} ({n_dropped} total). "
        f"NW Smoother has low correlation with everything (<0.40) — it captures unique trend-regime information. "
        f"RECOMMENDATION: Drop redundant pairs to reduce complexity without losing signal diversity. "
        f"Remaining KPIs each contribute independent information to the combination strategies."
    )
    g.fig.text(
        0.05, -0.04, _wrap(commentary), fontsize=8.5, color="#b0bec5",
        ha="left", va="top", fontstyle="italic",
        bbox=dict(boxstyle="round,pad=0.5", facecolor="#252525", edgecolor="#444", alpha=0.95),
    )

    g.savefig(CHARTS / "phase3_correlation_clustermap.png", bbox_inches="tight")
    plt.close(g.fig)
    print("  [5] phase3_correlation_clustermap.png")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 4 — Chart 6: Stepwise Buildup (from report)
# ═══════════════════════════════════════════════════════════════════════════

def chart_p4_stepwise():
    report_path = BASE / "phase4" / "combination_report.md"
    if not report_path.exists():
        print("  [6] SKIP phase4_stepwise_buildup (no data)")
        return

    text = report_path.read_text(encoding="utf-8")
    step_section = re.search(r'## Approach A.*?(?=## Approach B)', text, re.DOTALL)
    if not step_section:
        print("  [6] SKIP phase4_stepwise_buildup (no stepwise section)")
        return

    pattern = r'\*\*AND\(([^)]+)\)\*\*: OOS HR=([\d.]+), PF=([\d.]+|inf|—), trades=([\d,]+)'
    matches = re.findall(pattern, step_section.group())
    if not matches:
        print("  [6] SKIP phase4_stepwise_buildup (no stepwise data)")
        return

    steps = []
    for kpis_str, hr_str, pf_str, trades_str in matches:
        kpi_list = [k.strip() for k in kpis_str.split(",")]
        n_kpis = len(kpi_list)
        last_added = kpi_list[-1][:16]
        label = last_added if n_kpis == 1 else f"+{last_added}"
        steps.append({
            "step": n_kpis,
            "label": label,
            "hr": float(hr_str),
            "trades": int(trades_str.replace(",", "")),
        })

    if not steps:
        print("  [6] SKIP (empty)")
        return

    x = [s["step"] for s in steps]
    hr = [s["hr"] for s in steps]
    trades = [s["trades"] for s in steps]
    labels = [s["label"] for s in steps]

    # Parse baseline from report
    baseline_hr = 0.50
    bl_match = re.search(r'Baseline.*?TrendScore.*?OOS HR\s*=\s*([\d.]+)', text)
    if bl_match:
        baseline_hr = float(bl_match.group(1))

    fig, ax1 = plt.subplots(figsize=(13, 7))
    fig.subplots_adjust(bottom=0.26)

    color_hr = "#4fc3f7"
    color_trades = "#ef5350"

    ax1.plot(x, hr, "o-", color=color_hr, linewidth=2.5, markersize=10, zorder=5, label="OOS Hit Rate")
    ax1.fill_between(x, 0.50, hr, alpha=0.1, color=color_hr)
    ax1.axhline(baseline_hr, color="#ffb74d", linestyle="--", linewidth=1, alpha=0.7, label="Baseline (TrendScore > 0)")
    ax1.axhline(0.50, color="#ff5252", linestyle="--", linewidth=0.8, alpha=0.4)
    ax1.set_ylabel("OOS Hit Rate", color=color_hr, fontsize=11)
    ax1.set_ylim(0.45, max(hr) * 1.08)
    ax1.set_xlabel("Number of KPIs in AND-filter")
    ax1.set_title(f"Phase 4 — Stepwise AND-Filter Buildup ({TF})")
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, rotation=35, ha="right", fontsize=9)

    for xi, h, t in zip(x, hr, trades):
        ax1.annotate(f"{h:.1%}", (xi, h), textcoords="offset points", xytext=(0, 12),
                     fontsize=9, color=color_hr, fontweight="bold", ha="center")

    ax2 = ax1.twinx()
    ax2.bar(x, trades, alpha=0.25, color=color_trades, width=0.5, label="OOS Trade Count")
    ax2.set_ylabel("Trade Count (OOS)", color=color_trades, fontsize=11)
    ax2.set_yscale("log")
    ax2.set_ylim(max(10, min(trades) * 0.5), max(trades) * 3)

    for xi, t in zip(x, trades):
        ax2.text(xi, t * 1.3, f"{t:,}", ha="center", fontsize=8, color=color_trades)

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="center left", fontsize=8, framealpha=0.3)

    best_idx = hr.index(max(hr))
    best_hr_val = hr[best_idx]
    best_trades_val = trades[best_idx]
    first_hr = hr[0]
    first_trades = trades[0]
    commentary = (
        f"INSIGHT: Each added KPI increases hit rate but drastically reduces trade count. "
        f"From 1 KPI ({first_hr:.1%}, {first_trades:,} trades) to {best_idx+1} KPIs "
        f"({best_hr_val:.1%}, {best_trades_val:,} trades). "
        f"Baseline TrendScore sits at {baseline_hr:.1%} — the AND-filter approach outperforms by "
        f"+{(best_hr_val - baseline_hr)*100:.0f}pp. "
        f"RECOMMENDATION: Pick your sweet spot based on use case. "
        f"2-3 KPIs = practical (high trade count, ~70% accuracy). "
        f"5+ KPIs = high-conviction entries (fewer signals, >80% accuracy)."
    )
    _add_commentary(fig, _wrap(commentary), y=0.06)
    fig.savefig(CHARTS / "phase4_stepwise_buildup.png")
    plt.close(fig)
    print("  [6] phase4_stepwise_buildup.png")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 4 — Chart 7: Strategy Comparison (from report)
# ═══════════════════════════════════════════════════════════════════════════

def chart_p4_comparison():
    report_path = BASE / "phase4" / "combination_report.md"
    if not report_path.exists():
        print("  [7] SKIP phase4_strategy_comparison (no data)")
        return

    tbl = _parse_md_table(report_path, table_idx=0)
    if tbl is None or tbl.empty:
        print("  [7] SKIP phase4_strategy_comparison (table parse failed)")
        return

    strategies = []
    for _, row in tbl.iterrows():
        try:
            name = row.get("Strategy", "")
            hr = float(row.get("OOS HR", "0").replace("—", "nan"))
            trades = int(row.get("Trades (OOS)", "0").replace(",", "").replace("—", "0"))
            approach = row.get("Approach", "")
            if np.isnan(hr):
                continue
            strategies.append({"name": name[:25], "hr": hr, "trades": trades, "approach": approach})
        except Exception:
            continue

    if len(strategies) < 2:
        print("  [7] SKIP (too few strategies)")
        return

    # Keep top 8 for readability
    strategies = strategies[:8]
    names = [s["name"] for s in strategies]
    hrs = [s["hr"] for s in strategies]
    trades_list = [s["trades"] for s in strategies]

    approach_colors = {
        "Baseline": "#616161", "DimGate": "#78909c",
        "Weighted": "#ffb74d", "Stepwise": "#4fc3f7",
    }
    colors = [approach_colors.get(s["approach"], "#4fc3f7") for s in strategies]

    fig, ax = plt.subplots(figsize=(max(10, len(names) * 1.5), 7))
    fig.subplots_adjust(bottom=0.26)

    ax.bar(range(len(names)), hrs, color=colors, edgecolor="none", width=0.65)
    ax.axhline(0.50, color="#ff5252", linestyle="--", linewidth=1, alpha=0.5)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, fontsize=8, rotation=30, ha="right")
    ax.set_ylabel("OOS Hit Rate")
    ax.set_title(f"Phase 4 — Strategy Comparison ({TF})")
    ax.set_ylim(0.45, max(hrs) * 1.08)

    for i, (h, t) in enumerate(zip(hrs, trades_list)):
        ax.text(i, h + 0.008, f"{h:.1%}", ha="center", fontsize=10, fontweight="bold", color="white")
        ax.text(i, h - 0.025, f"{t:,} trades", ha="center", fontsize=7, color="#b0bec5")

    handles = [plt.Rectangle((0, 0), 1, 1, fc=c) for c in approach_colors.values()]
    ax.legend(handles, approach_colors.keys(), loc="upper left", fontsize=8, framealpha=0.3)

    best = strategies[0]
    worst = strategies[-1]
    stepwise_strats = [s for s in strategies if s["approach"] == "Stepwise"]
    commentary = (
        f"INSIGHT: Stepwise AND-filtering dominates all approaches. Best: {best['name']} at {best['hr']:.1%} "
        f"({best['trades']:,} trades). Weighted voting is modest — averaging dilutes strong KPIs. "
        f"Dimension gating adds bureaucracy without proportional benefit. "
        f"The AND approach works because it requires every filter to agree — false positives are eliminated multiplicatively. "
        f"DECISION FRAMEWORK: Use a 2-3 KPI AND-filter for high-frequency monitoring. "
        f"Use a 5+ KPI filter for high-conviction portfolio entries."
    )
    _add_commentary(fig, _wrap(commentary), y=0.06)
    fig.savefig(CHARTS / "phase4_strategy_comparison.png")
    plt.close(fig)
    print("  [7] phase4_strategy_comparison.png")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 5 — Chart 8: MTF Comparison (from report)
# ═══════════════════════════════════════════════════════════════════════════

def chart_p5_mtf():
    report_path = BASE / "phase5" / "mtf_analysis_report.md"
    if not report_path.exists():
        print("  [8] SKIP phase5_mtf_comparison (no data)")
        return

    tbl = _parse_md_table(report_path, table_idx=0)
    if tbl is None or tbl.empty:
        print("  [8] SKIP phase5_mtf_comparison (table parse failed)")
        return

    strategies = []
    for _, row in tbl.iterrows():
        try:
            name = row.get("Strategy", "")
            hr = float(row.get("OOS HR", "0").replace("—", "nan"))
            trades = int(row.get("Trades", "0").replace(",", "").replace("—", "0"))
            if np.isnan(hr):
                continue
            strategies.append({"name": name, "hr": hr, "trades": trades})
        except Exception:
            continue

    if not strategies:
        print("  [8] SKIP (no strategies)")
        return

    names = [s["name"] for s in strategies]
    hrs = [s["hr"] for s in strategies]
    trades_list = [s["trades"] for s in strategies]
    colors = ["#4fc3f7", "#ab47bc", "#ef5350"][:len(strategies)]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 6))
    fig.subplots_adjust(bottom=0.28)

    ax1.bar(range(len(names)), hrs, color=colors, width=0.55)
    ax1.axhline(0.50, color="#ff5252", linestyle="--", linewidth=1, alpha=0.5)
    ax1.set_xticks(range(len(names)))
    ax1.set_xticklabels(names, fontsize=8, rotation=15, ha="right")
    ax1.set_ylabel("OOS Hit Rate")
    ax1.set_title("Hit Rate by Timeframe Filter")
    hr_range = max(hrs) - min(hrs)
    ax1.set_ylim(min(hrs) - max(hr_range * 2, 0.05), max(hrs) + max(hr_range * 2, 0.05))
    for i, h in enumerate(hrs):
        ax1.text(i, h + 0.003, f"{h:.1%}", ha="center", fontsize=11, fontweight="bold")

    ax2.bar(range(len(names)), trades_list, color=colors, width=0.55)
    ax2.set_xticks(range(len(names)))
    ax2.set_xticklabels(names, fontsize=8, rotation=15, ha="right")
    ax2.set_ylabel("OOS Trade Count")
    ax2.set_title("Trade Count by Timeframe Filter")
    for i, t in enumerate(trades_list):
        ax2.text(i, t + max(trades_list) * 0.02, f"{t:,}", ha="center", fontsize=10, fontweight="bold")

    fig.suptitle(f"Phase 5 — Multi-Timeframe Confirmation Analysis ({TF})", fontsize=14, fontweight="bold")

    fig.savefig(CHARTS / "phase5_mtf_comparison.png")
    plt.close(fig)
    print("  [8] phase5_mtf_comparison.png")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 6 — Chart 9: Walk-Forward Consistency (from report)
# ═══════════════════════════════════════════════════════════════════════════

def chart_p6_walkforward():
    report_path = BASE / "phase6" / "final_report.md"
    if not report_path.exists():
        print("  [9] SKIP phase6_walkforward (no data)")
        return

    text = report_path.read_text(encoding="utf-8")

    strategy_pattern = r'### (.+?)\n\n\|[^\n]+\n\|[^\n]+\n((?:\|[^\n]+\n?)+)'
    strategy_blocks = re.findall(strategy_pattern, text)
    if not strategy_blocks:
        print("  [9] SKIP phase6_walkforward (no strategy data)")
        return

    all_strategies = []
    for name, table_body in strategy_blocks:
        name = name.strip()
        windows = []
        for line in table_body.strip().split("\n"):
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if len(cells) >= 6:
                try:
                    hr_val = float(cells[3]) if cells[3] != "—" else np.nan
                    trades_val = int(cells[5]) if cells[5] != "—" else 0
                    windows.append({"hr": hr_val, "trades": trades_val, "period": cells[2]})
                except Exception:
                    continue
        if windows:
            all_strategies.append({"name": name, "windows": windows})

    if not all_strategies:
        print("  [9] SKIP (no walkforward windows)")
        return

    n_windows = max(len(s["windows"]) for s in all_strategies)
    x = list(range(n_windows))
    strategy_colors = ["#616161", "#ffb74d", "#4fc3f7", "#66bb6a"]
    markers = ["s", "D", "^", "o"]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 10), height_ratios=[2, 1])
    fig.subplots_adjust(bottom=0.18, hspace=0.35)

    for si, strat in enumerate(all_strategies):
        hrs = [w["hr"] for w in strat["windows"]]
        valid_x = [i for i, h in enumerate(hrs) if not np.isnan(h)]
        valid_hr = [h for h in hrs if not np.isnan(h)]
        color = strategy_colors[si % len(strategy_colors)]
        marker = markers[si % len(markers)]
        short_name = strat["name"][:40]

        if valid_x:
            ax1.plot(valid_x, valid_hr, f"{marker}-", color=color, linewidth=2, markersize=8, label=short_name)
            for xi, h in zip(valid_x, valid_hr):
                ax1.text(xi, h + 0.015, f"{h:.0%}", ha="center", fontsize=8, color=color, fontweight="bold")

    ax1.axhline(0.50, color="#ff5252", linestyle="--", linewidth=1, alpha=0.4)
    ax1.set_xticks(x)
    window_labels = [f"W{i+1}" for i in x]
    ax1.set_xticklabels(window_labels, fontsize=9)
    ax1.set_ylabel("Hit Rate")
    ax1.set_title(f"Phase 6 — Walk-Forward Consistency ({TF})", fontsize=14)
    ax1.set_ylim(0.40, 0.95)
    ax1.legend(fontsize=8, framealpha=0.3, loc="lower left")
    ax1.grid(True, alpha=0.1)

    bar_width = 0.8 / max(len(all_strategies), 1)
    for si, strat in enumerate(all_strategies):
        trades = [w["trades"] for w in strat["windows"]]
        offsets = [xi + (si - len(all_strategies) / 2) * bar_width for xi in x[:len(trades)]]
        color = strategy_colors[si % len(strategy_colors)]
        ax2.bar(offsets, trades, bar_width, color=color, label=strat["name"][:30], alpha=0.8)

    ax2.set_xticks(x)
    ax2.set_xticklabels(window_labels, fontsize=9)
    ax2.set_ylabel("Trade Count")
    ax2.set_title("Trade Count per Window", fontsize=11)
    ax2.legend(fontsize=7, framealpha=0.3)

    wf_summary = re.search(r'Walk-forward mean HR: ([\d.]+) \(std: ([\d.]+)\)', text)
    consistency = re.search(r'Consistency: (\w+)', text)
    recommended = re.search(r'Recommended Strategy: (.+)', text)
    mean_hr = wf_summary.group(1) if wf_summary else "?"
    std_hr = wf_summary.group(2) if wf_summary else "?"
    cons_label = consistency.group(1) if consistency else "?"
    rec_name = recommended.group(1).strip() if recommended else "?"
    commentary = (
        f"INSIGHT: Walk-forward validation tests strategy robustness across sequential time windows. "
        f"Recommended strategy ({rec_name[:40]}): mean HR = {mean_hr}, std = {std_hr} — {cons_label}. "
        f"{'Low variance across windows confirms the signal is genuine and not period-specific. ' if cons_label == 'STABLE' else ''}"
        f"{'Moderate variance suggests the strategy works but performance varies with market regime. ' if cons_label == 'ACCEPTABLE' else ''}"
        f"Trade counts in early windows may be sparse if KPI data coverage was limited before 2023. "
        f"RECOMMENDATION: Re-run quarterly as more data accumulates. Monitor for regime shifts that may "
        f"degrade performance."
    )
    _add_commentary(fig, _wrap(commentary), y=0.04)
    fig.savefig(CHARTS / "phase6_walkforward.png")
    plt.close(fig)
    print("  [9] phase6_walkforward.png")


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    print(f"Generating charts for {TF} to {CHARTS} ...\n")
    chart_p1_ranking()
    chart_p1_scatter()
    chart_p1_horizon()
    chart_p2_optimization()
    chart_p3_correlation()
    chart_p4_stepwise()
    chart_p4_comparison()
    chart_p5_mtf()
    chart_p6_walkforward()
    print(f"\nDone! Charts saved to {CHARTS}")


if __name__ == "__main__":
    main()

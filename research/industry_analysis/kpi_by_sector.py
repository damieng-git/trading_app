"""
KPI & Combo Hit Rate by Sector / Industry (Weekly timeframe)

Evaluates whether different sectors benefit from different KPIs or combos.
For each sector with enough stocks, computes:
  - Individual KPI hit rates at 4w horizon
  - Combo (C3/C4/C5) hit rates
  - Best KPIs per sector
  - Sector-level recommendations

Outputs:
  - PNG heatmaps (KPI HR by sector, Combo HR by sector)
  - Markdown report with qualitative recommendations
  - CSV raw data
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from trading_dashboard.kpis.catalog import compute_kpi_state_map, KPI_TREND_ORDER, KPI_BREAKOUT_ORDER
from trading_dashboard.kpis.rules import STATE_BULL
from apps.dashboard.sector_map import load_sector_map, SECTOR_MAP_PATH

ENRICHED_DIR = REPO_DIR / "data" / "feature_store" / "enriched" / "sample_100" / "stock_data"
OUTPUT_DIR = Path(__file__).parent / "outputs"

TIMEFRAME = "1W"
HORIZON = 4
MIN_STOCKS_PER_SECTOR = 5
MIN_TRADES_PER_KPI = 20

COMBO_DEFINITIONS = {
    "C3": ["Nadaraya-Watson Smoother", "cRSI", "SR Breaks"],
    "C4": ["Nadaraya-Watson Smoother", "cRSI", "SR Breaks", "Stoch_MTM"],
    "C5": ["Nadaraya-Watson Smoother", "cRSI", "SR Breaks", "Stoch_MTM", "CM_P-SAR"],
}

CORE_KPIS = [
    "Nadaraya-Watson Smoother", "cRSI", "SR Breaks", "Stoch_MTM", "CM_P-SAR",
    "WT_LB", "ADX & DI", "SQZMOM_LB", "CM_Ult_MacD_MFT", "SuperTrend",
    "UT Bot Alert", "Ichimoku", "MA Ribbon", "Madrid Ribbon", "Donchian Ribbon",
    "DEMA", "TuTCI", "GMMA", "Mansfield RS", "OBVOSC_LB",
    "BB 30", "Nadaraya-Watson Envelop (MAE)",
]


def load_data() -> Dict[str, pd.DataFrame]:
    data = {}
    for f in sorted(ENRICHED_DIR.glob(f"*_{TIMEFRAME}.csv")):
        symbol = f.stem.rsplit(f"_{TIMEFRAME}", 1)[0]
        try:
            df = pd.read_csv(f, index_col=0, parse_dates=[0])
            df.index = pd.to_datetime(df.index, errors="coerce").tz_localize(None)
            df = df.sort_index()
            if len(df) >= 52 and "Close" in df.columns:
                data[symbol] = df
        except Exception:
            continue
    return data


def compute_kpi_hr(
    state_series: pd.Series,
    fwd_ret: pd.Series,
) -> Tuple[float, int]:
    """Hit rate and trade count for a single KPI bull signal."""
    bull = state_series == STATE_BULL
    valid = bull & fwd_ret.notna()
    n = int(valid.sum())
    if n < 5:
        return float("nan"), n
    wins = int((fwd_ret[valid] > 0).sum())
    return wins / n, n


def compute_combo_hr(
    state_map: Dict[str, pd.Series],
    kpi_list: List[str],
    fwd_ret: pd.Series,
) -> Tuple[float, int]:
    """Hit rate for an AND-combo of KPIs."""
    avail = [k for k in kpi_list if k in state_map]
    if len(avail) < len(kpi_list):
        return float("nan"), 0
    combo_bull = pd.Series(True, index=fwd_ret.index)
    for k in avail:
        combo_bull = combo_bull & (state_map[k] == STATE_BULL)
    valid = combo_bull & fwd_ret.notna()
    n = int(valid.sum())
    if n < 3:
        return float("nan"), n
    wins = int((fwd_ret[valid] > 0).sum())
    return wins / n, n


def run_analysis():
    t0 = time.time()

    print(f"Loading sector map...")
    sector_map = load_sector_map()

    print(f"Loading {TIMEFRAME} data from {ENRICHED_DIR}...")
    all_data = load_data()
    print(f"Loaded {len(all_data)} stocks")

    # Group stocks by sector
    sector_stocks: Dict[str, List[str]] = defaultdict(list)
    for sym in all_data:
        sm = sector_map.get(sym, {})
        sector = sm.get("sector", "")
        if sector:
            sector_stocks[sector].append(sym)

    # Filter sectors with enough stocks
    valid_sectors = {s: syms for s, syms in sector_stocks.items() if len(syms) >= MIN_STOCKS_PER_SECTOR}
    print(f"Sectors with >= {MIN_STOCKS_PER_SECTOR} stocks: {len(valid_sectors)}")
    for s, syms in sorted(valid_sectors.items(), key=lambda x: -len(x[1])):
        print(f"  {s:30s} {len(syms)} stocks")

    # ── Per-sector KPI hit rates ─────────────────────────────────────────
    kpi_hr_by_sector: Dict[str, Dict[str, float]] = {}
    kpi_trades_by_sector: Dict[str, Dict[str, int]] = {}
    combo_hr_by_sector: Dict[str, Dict[str, float]] = {}
    combo_trades_by_sector: Dict[str, Dict[str, int]] = {}

    for sector, syms in sorted(valid_sectors.items()):
        print(f"\n  Analyzing: {sector} ({len(syms)} stocks)...")
        kpi_hr_by_sector[sector] = {}
        kpi_trades_by_sector[sector] = {}
        combo_hr_by_sector[sector] = {}
        combo_trades_by_sector[sector] = {}

        # Aggregate hit rates across all stocks in the sector
        kpi_wins: Dict[str, int] = defaultdict(int)
        kpi_total: Dict[str, int] = defaultdict(int)
        combo_wins: Dict[str, int] = defaultdict(int)
        combo_total: Dict[str, int] = defaultdict(int)

        for sym in syms:
            df = all_data[sym]
            fwd_ret = df["Close"].pct_change(HORIZON).shift(-HORIZON)
            state_map = compute_kpi_state_map(df)

            for kpi in CORE_KPIS:
                if kpi not in state_map:
                    continue
                bull = state_map[kpi] == STATE_BULL
                valid = bull & fwd_ret.notna()
                n = int(valid.sum())
                w = int((fwd_ret[valid] > 0).sum())
                kpi_wins[kpi] += w
                kpi_total[kpi] += n

            for combo_name, combo_kpis in COMBO_DEFINITIONS.items():
                avail = [k for k in combo_kpis if k in state_map]
                if len(avail) < len(combo_kpis):
                    continue
                combo_bull = pd.Series(True, index=df.index)
                for k in avail:
                    combo_bull = combo_bull & (state_map[k] == STATE_BULL)
                valid = combo_bull & fwd_ret.notna()
                n = int(valid.sum())
                w = int((fwd_ret[valid] > 0).sum())
                combo_wins[combo_name] += w
                combo_total[combo_name] += n

        for kpi in CORE_KPIS:
            total = kpi_total[kpi]
            kpi_trades_by_sector[sector][kpi] = total
            if total >= MIN_TRADES_PER_KPI:
                kpi_hr_by_sector[sector][kpi] = kpi_wins[kpi] / total
            else:
                kpi_hr_by_sector[sector][kpi] = float("nan")

        for combo_name in COMBO_DEFINITIONS:
            total = combo_total[combo_name]
            combo_trades_by_sector[sector][combo_name] = total
            if total >= 5:
                combo_hr_by_sector[sector][combo_name] = combo_wins[combo_name] / total
            else:
                combo_hr_by_sector[sector][combo_name] = float("nan")

    # ── Also compute "ALL" baseline ──────────────────────────────────────
    print(f"\n  Computing ALL-sector baseline...")
    all_kpi_wins: Dict[str, int] = defaultdict(int)
    all_kpi_total: Dict[str, int] = defaultdict(int)
    all_combo_wins: Dict[str, int] = defaultdict(int)
    all_combo_total: Dict[str, int] = defaultdict(int)

    for sym, df in all_data.items():
        fwd_ret = df["Close"].pct_change(HORIZON).shift(-HORIZON)
        state_map = compute_kpi_state_map(df)
        for kpi in CORE_KPIS:
            if kpi not in state_map:
                continue
            bull = state_map[kpi] == STATE_BULL
            valid = bull & fwd_ret.notna()
            n = int(valid.sum())
            w = int((fwd_ret[valid] > 0).sum())
            all_kpi_wins[kpi] += w
            all_kpi_total[kpi] += n
        for combo_name, combo_kpis in COMBO_DEFINITIONS.items():
            avail = [k for k in combo_kpis if k in state_map]
            if len(avail) < len(combo_kpis):
                continue
            combo_bull = pd.Series(True, index=df.index)
            for k in avail:
                combo_bull = combo_bull & (state_map[k] == STATE_BULL)
            valid = combo_bull & fwd_ret.notna()
            n = int(valid.sum())
            w = int((fwd_ret[valid] > 0).sum())
            all_combo_wins[combo_name] += w
            all_combo_total[combo_name] += n

    kpi_hr_by_sector["ALL"] = {}
    combo_hr_by_sector["ALL"] = {}
    for kpi in CORE_KPIS:
        t = all_kpi_total[kpi]
        kpi_hr_by_sector["ALL"][kpi] = all_kpi_wins[kpi] / t if t >= MIN_TRADES_PER_KPI else float("nan")
    for combo_name in COMBO_DEFINITIONS:
        t = all_combo_total[combo_name]
        combo_hr_by_sector["ALL"][combo_name] = all_combo_wins[combo_name] / t if t >= 5 else float("nan")

    # ── Generate outputs ─────────────────────────────────────────────────
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    generate_heatmaps(kpi_hr_by_sector, combo_hr_by_sector, OUTPUT_DIR)
    generate_csv(kpi_hr_by_sector, kpi_trades_by_sector, combo_hr_by_sector, combo_trades_by_sector, OUTPUT_DIR)
    generate_report(kpi_hr_by_sector, combo_hr_by_sector, valid_sectors, OUTPUT_DIR)

    print(f"\nAnalysis complete in {time.time() - t0:.0f}s")
    print(f"Outputs saved to {OUTPUT_DIR}")


# ── Visualization ────────────────────────────────────────────────────────

def generate_heatmaps(
    kpi_hr: Dict[str, Dict[str, float]],
    combo_hr: Dict[str, Dict[str, float]],
    out_dir: Path,
) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    sectors = [s for s in sorted(kpi_hr.keys()) if s != "ALL"]
    sectors_with_all = ["ALL"] + sectors

    # ── KPI heatmap ──────────────────────────────────────────────────────
    kpis_to_show = [k for k in CORE_KPIS if any(not np.isnan(kpi_hr.get(s, {}).get(k, float("nan"))) for s in sectors_with_all)]
    data = np.full((len(sectors_with_all), len(kpis_to_show)), np.nan)
    for i, sector in enumerate(sectors_with_all):
        for j, kpi in enumerate(kpis_to_show):
            data[i, j] = kpi_hr.get(sector, {}).get(kpi, float("nan"))

    fig, ax = plt.subplots(figsize=(max(16, len(kpis_to_show) * 0.8), max(6, len(sectors_with_all) * 0.55)))
    masked = np.ma.masked_invalid(data)
    im = ax.pcolormesh(masked, cmap="RdYlGn", vmin=0.40, vmax=0.75, edgecolors="white", linewidth=0.5)
    ax.set_xticks(np.arange(len(kpis_to_show)) + 0.5)
    ax.set_xticklabels(kpis_to_show, rotation=55, ha="right", fontsize=8)
    ax.set_yticks(np.arange(len(sectors_with_all)) + 0.5)
    ax.set_yticklabels(sectors_with_all, fontsize=9)
    ax.invert_yaxis()

    for i in range(len(sectors_with_all)):
        for j in range(len(kpis_to_show)):
            v = data[i, j]
            if not np.isnan(v):
                ax.text(j + 0.5, i + 0.5, f"{v:.0%}", ha="center", va="center",
                        fontsize=7, fontweight="bold" if i == 0 else "normal",
                        color="white" if v < 0.45 or v > 0.70 else "black")

    plt.colorbar(im, ax=ax, label="Hit Rate (4w)", shrink=0.8)
    ax.set_title("KPI Hit Rate by Sector (Weekly, 4w horizon)", fontsize=13, fontweight="bold", pad=12)

    # Bold "ALL" row
    for label in ax.get_yticklabels():
        if label.get_text() == "ALL":
            label.set_fontweight("bold")

    fig.tight_layout()
    fig.savefig(out_dir / "kpi_hr_by_sector.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved kpi_hr_by_sector.png")

    # ── Combo heatmap ────────────────────────────────────────────────────
    combos = list(COMBO_DEFINITIONS.keys())
    data_c = np.full((len(sectors_with_all), len(combos)), np.nan)
    for i, sector in enumerate(sectors_with_all):
        for j, combo in enumerate(combos):
            data_c[i, j] = combo_hr.get(sector, {}).get(combo, float("nan"))

    fig2, ax2 = plt.subplots(figsize=(6, max(5, len(sectors_with_all) * 0.5)))
    masked_c = np.ma.masked_invalid(data_c)
    im2 = ax2.pcolormesh(masked_c, cmap="RdYlGn", vmin=0.45, vmax=0.85, edgecolors="white", linewidth=1)
    ax2.set_xticks(np.arange(len(combos)) + 0.5)
    ax2.set_xticklabels(combos, fontsize=10)
    ax2.set_yticks(np.arange(len(sectors_with_all)) + 0.5)
    ax2.set_yticklabels(sectors_with_all, fontsize=9)
    ax2.invert_yaxis()

    for i in range(len(sectors_with_all)):
        for j in range(len(combos)):
            v = data_c[i, j]
            if not np.isnan(v):
                ax2.text(j + 0.5, i + 0.5, f"{v:.0%}", ha="center", va="center",
                        fontsize=10, fontweight="bold" if i == 0 else "normal",
                        color="white" if v < 0.50 or v > 0.78 else "black")

    plt.colorbar(im2, ax=ax2, label="Hit Rate (4w)", shrink=0.8)
    ax2.set_title("Combo Hit Rate by Sector (Weekly, 4w horizon)", fontsize=13, fontweight="bold", pad=12)

    for label in ax2.get_yticklabels():
        if label.get_text() == "ALL":
            label.set_fontweight("bold")

    fig2.tight_layout()
    fig2.savefig(out_dir / "combo_hr_by_sector.png", dpi=150, bbox_inches="tight")
    plt.close(fig2)
    print(f"  Saved combo_hr_by_sector.png")

    # ── Top KPIs per sector (bar chart) ──────────────────────────────────
    fig3, axes = plt.subplots(2, 3, figsize=(18, 10))
    axes = axes.flatten()
    plot_sectors = sectors[:6]

    for idx, sector in enumerate(plot_sectors):
        ax3 = axes[idx]
        hrs = kpi_hr.get(sector, {})
        valid = {k: v for k, v in hrs.items() if not np.isnan(v)}
        if not valid:
            ax3.set_title(sector, fontsize=10)
            ax3.text(0.5, 0.5, "No data", ha="center", va="center")
            continue
        sorted_kpis = sorted(valid.items(), key=lambda x: -x[1])[:10]
        names = [k for k, _ in sorted_kpis]
        vals = [v for _, v in sorted_kpis]
        colors = ["#22c55e" if v >= 0.55 else "#eab308" if v >= 0.50 else "#ef4444" for v in vals]
        bars = ax3.barh(range(len(names)), vals, color=colors, edgecolor="white", height=0.7)
        ax3.set_yticks(range(len(names)))
        ax3.set_yticklabels(names, fontsize=7)
        ax3.set_xlim(0.35, 0.80)
        ax3.axvline(0.50, color="gray", linestyle="--", linewidth=0.8, alpha=0.7)
        ax3.set_title(sector, fontsize=10, fontweight="bold")
        ax3.invert_yaxis()
        for bar, val in zip(bars, vals):
            ax3.text(bar.get_width() + 0.005, bar.get_y() + bar.get_height() / 2,
                     f"{val:.0%}", va="center", fontsize=7)

    for idx in range(len(plot_sectors), len(axes)):
        axes[idx].set_visible(False)

    fig3.suptitle("Top 10 KPIs by Sector (Weekly, 4w HR)", fontsize=14, fontweight="bold", y=1.01)
    fig3.tight_layout()
    fig3.savefig(out_dir / "top_kpis_per_sector.png", dpi=150, bbox_inches="tight")
    plt.close(fig3)
    print(f"  Saved top_kpis_per_sector.png")


# ── CSV export ───────────────────────────────────────────────────────────

def generate_csv(
    kpi_hr: Dict[str, Dict[str, float]],
    kpi_trades: Dict[str, Dict[str, int]],
    combo_hr: Dict[str, Dict[str, float]],
    combo_trades: Dict[str, Dict[str, int]],
    out_dir: Path,
) -> None:
    rows = []
    for sector in sorted(kpi_hr.keys()):
        for kpi in CORE_KPIS:
            hr = kpi_hr.get(sector, {}).get(kpi, float("nan"))
            trades = kpi_trades.get(sector, {}).get(kpi, 0)
            rows.append({"sector": sector, "indicator": kpi, "type": "KPI", "hit_rate": hr, "trades": trades})
        for combo in COMBO_DEFINITIONS:
            hr = combo_hr.get(sector, {}).get(combo, float("nan"))
            trades = combo_trades.get(sector, {}).get(combo, 0)
            rows.append({"sector": sector, "indicator": combo, "type": "Combo", "hit_rate": hr, "trades": trades})
    pd.DataFrame(rows).to_csv(out_dir / "kpi_by_sector_raw.csv", index=False)
    print(f"  Saved kpi_by_sector_raw.csv")


# ── Report ───────────────────────────────────────────────────────────────

def generate_report(
    kpi_hr: Dict[str, Dict[str, float]],
    combo_hr: Dict[str, Dict[str, float]],
    valid_sectors: Dict[str, List[str]],
    out_dir: Path,
) -> None:
    lines = [
        "# KPI & Combo Performance by Sector",
        "",
        f"**Timeframe:** {TIMEFRAME}  ",
        f"**Horizon:** {HORIZON} bars (4 weeks)  ",
        f"**Minimum stocks per sector:** {MIN_STOCKS_PER_SECTOR}  ",
        "",
        "## Key Findings",
        "",
    ]

    # Find sectors where combos significantly outperform / underperform ALL
    all_c3 = combo_hr.get("ALL", {}).get("C3", 0.5)
    all_c5 = combo_hr.get("ALL", {}).get("C5", 0.5)

    outperformers = []
    underperformers = []

    for sector in sorted(valid_sectors.keys()):
        c3 = combo_hr.get(sector, {}).get("C3", float("nan"))
        c5 = combo_hr.get(sector, {}).get("C5", float("nan"))
        if not np.isnan(c3) and c3 > all_c3 + 0.05:
            outperformers.append((sector, "C3", c3))
        if not np.isnan(c5) and c5 > all_c5 + 0.05:
            outperformers.append((sector, "C5", c5))
        if not np.isnan(c3) and c3 < all_c3 - 0.05:
            underperformers.append((sector, "C3", c3))
        if not np.isnan(c5) and c5 < all_c5 - 0.05:
            underperformers.append((sector, "C5", c5))

    if outperformers:
        lines.append("**Sectors where combos work best:**")
        for sector, combo, hr in sorted(outperformers, key=lambda x: -x[2]):
            lines.append(f"- **{sector}**: {combo} = {hr:.1%} (vs {all_c3:.1%} ALL)")
        lines.append("")

    if underperformers:
        lines.append("**Sectors where combos underperform:**")
        for sector, combo, hr in sorted(underperformers, key=lambda x: x[2]):
            baseline = all_c3 if combo == "C3" else all_c5
            lines.append(f"- **{sector}**: {combo} = {hr:.1%} (vs {baseline:.1%} ALL)")
        lines.append("")

    # Per-sector best KPIs
    lines.extend(["", "## Best KPIs per Sector", ""])
    lines.append("| Sector | #Stocks | Best KPI | HR | 2nd Best | HR | C3 HR | C5 HR |")
    lines.append("|--------|---------|----------|-----|----------|-----|-------|-------|")

    for sector in sorted(valid_sectors.keys()):
        n = len(valid_sectors[sector])
        hrs = kpi_hr.get(sector, {})
        valid = {k: v for k, v in hrs.items() if not np.isnan(v)}
        top = sorted(valid.items(), key=lambda x: -x[1])[:2]
        best = top[0] if len(top) >= 1 else ("—", 0)
        second = top[1] if len(top) >= 2 else ("—", 0)
        c3 = combo_hr.get(sector, {}).get("C3", float("nan"))
        c5 = combo_hr.get(sector, {}).get("C5", float("nan"))
        c3_str = f"{c3:.0%}" if not np.isnan(c3) else "—"
        c5_str = f"{c5:.0%}" if not np.isnan(c5) else "—"
        lines.append(f"| {sector} | {n} | {best[0]} | {best[1]:.0%} | {second[0]} | {second[1]:.0%} | {c3_str} | {c5_str} |")
    lines.append("")

    # Recommendations
    lines.extend([
        "## Recommendations",
        "",
        "### General",
        "",
        "- The current C3/C5 combo definitions use the **same KPIs for all sectors**.",
        "- This analysis reveals whether sector-specific combo tuning could improve results.",
        "",
        "### Sector-Specific Guidance",
        "",
    ])

    for sector in sorted(valid_sectors.keys()):
        hrs = kpi_hr.get(sector, {})
        valid = {k: v for k, v in hrs.items() if not np.isnan(v)}
        if not valid:
            continue
        top3 = sorted(valid.items(), key=lambda x: -x[1])[:3]
        c3 = combo_hr.get(sector, {}).get("C3", float("nan"))
        c5 = combo_hr.get(sector, {}).get("C5", float("nan"))

        lines.append(f"**{sector}** ({len(valid_sectors[sector])} stocks):")
        lines.append(f"- Top KPIs: {', '.join(f'{k} ({v:.0%})' for k, v in top3)}")

        combo_top = [k for k, _ in top3]
        current_c3 = set(COMBO_DEFINITIONS["C3"])
        overlap = current_c3 & set(combo_top)

        if len(overlap) >= 2:
            lines.append(f"- Current C3 combo aligns well with sector ({len(overlap)}/3 overlap)")
        else:
            lines.append(f"- Consider sector-specific combo: {' + '.join(combo_top)}")

        if not np.isnan(c5) and c5 > 0.70:
            lines.append(f"- C5 combo is strong ({c5:.0%}); use with confidence")
        elif not np.isnan(c3) and c3 > 0.60:
            lines.append(f"- C3 combo is reliable ({c3:.0%}); C5 adds marginal value")
        else:
            lines.append(f"- Combos underperform; consider sector-specific filtering")
        lines.append("")

    lines.extend([
        "### Monitoring Approach",
        "",
        "- **High-conviction sectors** (combo HR > 70%): Trade combos directly",
        "- **Average sectors** (combo HR 55-70%): Use combos as filters, add confirmation",
        "- **Low-conviction sectors** (combo HR < 55%): Consider sector-specific KPI combos",
        "  or use sector ETFs as a regime filter before applying stock-level combos",
        "",
    ])

    report = "\n".join(lines)
    (out_dir / "kpi_by_sector_report.md").write_text(report, encoding="utf-8")
    print(f"  Saved kpi_by_sector_report.md")
    print()
    print(report)


if __name__ == "__main__":
    run_analysis()

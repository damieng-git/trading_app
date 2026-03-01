"""
Best Combo Discovery by Sector (Weekly, 4w horizon)

For each sector, exhaustively searches all C(22,k) KPI combinations for k=3,4,5
to find the highest hit-rate AND-combo. Outputs:
  - combo_kpis_by_sector.json (consumable by dashboard)
  - PNG comparison chart
  - Markdown report
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from trading_dashboard.kpis.catalog import compute_kpi_state_map
from trading_dashboard.kpis.rules import STATE_BULL
from apps.dashboard.sector_map import load_sector_map

ENRICHED_DIR = REPO_DIR / "data" / "feature_store" / "enriched" / "sample_100" / "stock_data"
OUTPUT_DIR = Path(__file__).parent / "outputs"

TIMEFRAME = "1W"
HORIZON = 4
MIN_STOCKS_PER_SECTOR = 5
MIN_COMBO_TRADES = 15

CORE_KPIS = [
    "Nadaraya-Watson Smoother", "cRSI", "SR Breaks", "Stoch_MTM", "CM_P-SAR",
    "WT_LB", "ADX & DI", "SQZMOM_LB", "CM_Ult_MacD_MFT", "SuperTrend",
    "UT Bot Alert", "Ichimoku", "MA Ribbon", "Madrid Ribbon", "Donchian Ribbon",
    "DEMA", "TuTCI", "GMMA", "Mansfield RS", "OBVOSC_LB",
    "BB 30", "Nadaraya-Watson Envelop (MAE)",
]

GLOBAL_COMBOS = {
    "combo_3": ["Nadaraya-Watson Smoother", "cRSI", "SR Breaks"],
    "combo_4": ["Nadaraya-Watson Smoother", "cRSI", "SR Breaks", "Stoch_MTM"],
    "combo_5": ["Nadaraya-Watson Smoother", "cRSI", "SR Breaks", "Stoch_MTM", "CM_P-SAR"],
}


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


def precompute_sector_data(
    all_data: Dict[str, pd.DataFrame],
    sector_stocks: Dict[str, List[str]],
) -> Dict[str, Dict[str, Tuple[np.ndarray, np.ndarray]]]:
    """
    For each sector, precompute bull-mask arrays and forward-return arrays per KPI.
    Returns: {sector: {kpi: (bull_mask_flat, fwd_ret_flat)}}
    """
    result: Dict[str, Dict[str, Tuple[np.ndarray, np.ndarray]]] = {}

    for sector, syms in sector_stocks.items():
        kpi_bulls: Dict[str, list] = defaultdict(list)
        kpi_fwds: Dict[str, list] = defaultdict(list)

        for sym in syms:
            df = all_data[sym]
            fwd = df["Close"].pct_change(HORIZON).shift(-HORIZON)
            state_map = compute_kpi_state_map(df)

            fwd_arr = fwd.to_numpy(dtype=float)
            valid_mask = ~np.isnan(fwd_arr)

            for kpi in CORE_KPIS:
                if kpi not in state_map:
                    continue
                bull = (state_map[kpi] == STATE_BULL).to_numpy(dtype=bool)
                kpi_bulls[kpi].append(bull & valid_mask)
                kpi_fwds[kpi].append(np.where(valid_mask, fwd_arr, np.nan))

        sector_data: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
        for kpi in CORE_KPIS:
            if kpi not in kpi_bulls or not kpi_bulls[kpi]:
                continue
            sector_data[kpi] = (
                np.concatenate(kpi_bulls[kpi]),
                np.concatenate(kpi_fwds[kpi]),
            )
        result[sector] = sector_data

    return result


def find_best_combo(
    sector_data: Dict[str, Tuple[np.ndarray, np.ndarray]],
    k: int,
) -> Tuple[List[str], float, int]:
    """Find the k-KPI AND-combo with highest hit rate."""
    available_kpis = [kpi for kpi in CORE_KPIS if kpi in sector_data]
    if len(available_kpis) < k:
        return [], 0.0, 0

    best_kpis: List[str] = []
    best_hr = 0.0
    best_trades = 0

    n_total = len(sector_data[available_kpis[0]][0])

    # Precompute individual bull masks as arrays for speed
    kpi_masks = {kpi: sector_data[kpi][0] for kpi in available_kpis}
    fwd_arr = sector_data[available_kpis[0]][1]

    for combo in combinations(available_kpis, k):
        combined = kpi_masks[combo[0]].copy()
        for kpi in combo[1:]:
            combined &= kpi_masks[kpi]

        n = int(combined.sum())
        if n < MIN_COMBO_TRADES:
            continue

        wins = int((fwd_arr[combined] > 0).sum())
        hr = wins / n

        if hr > best_hr or (hr == best_hr and n > best_trades):
            best_hr = hr
            best_trades = n
            best_kpis = list(combo)

    return best_kpis, best_hr, best_trades


def run_analysis():
    t0 = time.time()

    print("Loading sector map...")
    sector_map = load_sector_map()

    print(f"Loading {TIMEFRAME} data...")
    all_data = load_data()
    print(f"Loaded {len(all_data)} stocks")

    sector_stocks: Dict[str, List[str]] = defaultdict(list)
    for sym in all_data:
        sm = sector_map.get(sym, {})
        sector = sm.get("sector", "")
        if sector:
            sector_stocks[sector].append(sym)

    valid_sectors = {s: syms for s, syms in sector_stocks.items() if len(syms) >= MIN_STOCKS_PER_SECTOR}
    print(f"Sectors: {len(valid_sectors)}")

    # Also compute ALL
    all_syms = list(all_data.keys())
    valid_sectors_with_all = {"ALL": all_syms, **valid_sectors}

    print("Precomputing KPI data per sector...")
    precomputed = precompute_sector_data(all_data, valid_sectors_with_all)

    # Find best combos
    results: Dict[str, Dict[str, dict]] = {}

    for sector in sorted(valid_sectors_with_all.keys()):
        sd = precomputed.get(sector, {})
        if not sd:
            continue
        print(f"\n  {sector} ({len(valid_sectors_with_all[sector])} stocks):")
        results[sector] = {}

        for k, label in [(3, "combo_3"), (4, "combo_4"), (5, "combo_5")]:
            kpis, hr, trades = find_best_combo(sd, k)
            results[sector][label] = {
                "kpis": kpis,
                "hit_rate": round(hr, 4),
                "trades": trades,
            }
            kpi_str = " + ".join(kpis) if kpis else "—"
            print(f"    {label}: HR={hr:.1%} ({trades} trades) — {kpi_str}")

    # Also compute global combo HR per sector for comparison
    global_results: Dict[str, Dict[str, dict]] = {}
    for sector in sorted(valid_sectors_with_all.keys()):
        sd = precomputed.get(sector, {})
        if not sd:
            continue
        global_results[sector] = {}
        fwd_arr = sd[CORE_KPIS[0]][1] if CORE_KPIS[0] in sd else None
        if fwd_arr is None:
            continue

        for label, gkpis in GLOBAL_COMBOS.items():
            avail = [k for k in gkpis if k in sd]
            if len(avail) < len(gkpis):
                global_results[sector][label] = {"hit_rate": None, "trades": 0}
                continue
            combined = sd[avail[0]][0].copy()
            for kpi in avail[1:]:
                combined &= sd[kpi][0]
            n = int(combined.sum())
            if n < 5:
                global_results[sector][label] = {"hit_rate": None, "trades": n}
                continue
            wins = int((fwd_arr[combined] > 0).sum())
            global_results[sector][label] = {"hit_rate": round(wins / n, 4), "trades": n}

    # ── Output JSON for dashboard consumption ────────────────────────────
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    combo_config: Dict[str, Dict[str, List[str]]] = {}
    for sector, combos in results.items():
        if sector == "ALL":
            continue
        combo_config[sector] = {}
        for label in ["combo_3", "combo_4", "combo_5"]:
            kpis = combos.get(label, {}).get("kpis", [])
            if kpis:
                combo_config[sector][label] = kpis

    config_path = OUTPUT_DIR / "combo_kpis_by_sector.json"
    config_path.write_text(json.dumps(combo_config, indent=2), encoding="utf-8")
    print(f"\n  Saved combo_kpis_by_sector.json")

    # ── PNG ───────────────────────────────────────────────────────────────
    generate_comparison_chart(results, global_results, valid_sectors_with_all, OUTPUT_DIR)

    # ── Report ────────────────────────────────────────────────────────────
    generate_report(results, global_results, combo_config, valid_sectors_with_all, OUTPUT_DIR)

    print(f"\nDone in {time.time() - t0:.0f}s")


def generate_comparison_chart(results, global_results, sectors, out_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    sector_names = [s for s in sorted(sectors.keys()) if s != "ALL"]
    sector_names = ["ALL"] + sector_names

    fig, axes = plt.subplots(1, 3, figsize=(18, max(5, len(sector_names) * 0.55)), sharey=True)

    for col_idx, (label, display) in enumerate([("combo_3", "C3"), ("combo_4", "C4"), ("combo_5", "C5")]):
        ax = axes[col_idx]
        global_hrs = []
        sector_hrs = []
        labels = []

        for sector in sector_names:
            g = global_results.get(sector, {}).get(label, {})
            s = results.get(sector, {}).get(label, {})
            ghr = g.get("hit_rate") if g.get("hit_rate") is not None else float("nan")
            shr = s.get("hit_rate", float("nan"))
            global_hrs.append(ghr)
            sector_hrs.append(shr)
            labels.append(sector)

        y = np.arange(len(labels))
        h = 0.35
        bars_g = ax.barh(y + h / 2, global_hrs, h, label="Global combo", color="#94a3b8", edgecolor="white")
        bars_s = ax.barh(y - h / 2, sector_hrs, h, label="Sector-optimized", color="#22c55e", edgecolor="white")

        ax.set_yticks(y)
        ax.set_yticklabels(labels, fontsize=9)
        ax.set_xlim(0.35, 0.95)
        ax.axvline(0.50, color="gray", linestyle="--", linewidth=0.7, alpha=0.5)
        ax.set_title(display, fontsize=13, fontweight="bold")
        ax.invert_yaxis()

        for bar, val in zip(bars_g, global_hrs):
            if not np.isnan(val):
                ax.text(val + 0.005, bar.get_y() + bar.get_height() / 2,
                        f"{val:.0%}", va="center", fontsize=8, color="#475569")
        for bar, val in zip(bars_s, sector_hrs):
            if not np.isnan(val):
                ax.text(val + 0.005, bar.get_y() + bar.get_height() / 2,
                        f"{val:.0%}", va="center", fontsize=8, color="#15803d", fontweight="bold")

        if col_idx == 0:
            ax.legend(loc="lower right", fontsize=8)

        for label_obj in ax.get_yticklabels():
            if label_obj.get_text() == "ALL":
                label_obj.set_fontweight("bold")

    fig.suptitle("Global vs Sector-Optimized Combos (Weekly, 4w HR)", fontsize=14, fontweight="bold", y=1.02)
    fig.tight_layout()
    fig.savefig(out_dir / "sector_vs_global_combos.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved sector_vs_global_combos.png")


def generate_report(results, global_results, combo_config, sectors, out_dir):
    lines = [
        "# Best Combo by Sector — Discovery Report",
        "",
        f"**Timeframe:** {TIMEFRAME} | **Horizon:** {HORIZON} bars (4w) | "
        f"**Min trades:** {MIN_COMBO_TRADES}",
        "",
        "## Summary Table",
        "",
        "| Sector | Level | Global Combo HR | Sector Combo HR | Improvement | Sector KPIs |",
        "|--------|-------|-----------------|-----------------|-------------|-------------|",
    ]

    for sector in sorted(sectors.keys()):
        for label, display in [("combo_3", "C3"), ("combo_4", "C4"), ("combo_5", "C5")]:
            g = global_results.get(sector, {}).get(label, {})
            s = results.get(sector, {}).get(label, {})
            ghr = g.get("hit_rate")
            shr = s.get("hit_rate", 0)
            kpis = s.get("kpis", [])
            ghr_str = f"{ghr:.0%}" if ghr is not None else "—"
            shr_str = f"{shr:.0%}" if shr else "—"
            imp = ""
            if ghr is not None and shr:
                delta = shr - ghr
                imp = f"{delta:+.0%}"
            kpi_str = ", ".join(kpis) if kpis else "—"
            lines.append(f"| {sector} | {display} | {ghr_str} | {shr_str} | {imp} | {kpi_str} |")

    lines.extend([
        "",
        "## Sector-Specific Combo Definitions",
        "",
        "These definitions can be loaded by the dashboard to replace the global combo for each stock:",
        "",
        "```json",
        json.dumps(combo_config, indent=2),
        "```",
        "",
        "## Interpretation",
        "",
        "- **Sector-optimized combos** are found via exhaustive search over all C(22,k) combinations.",
        "- **Improvement** shows the hit-rate gain vs. the global combo applied to that sector's stocks.",
        "- Sectors with large improvement benefit most from sector-specific tuning.",
        "- Sectors where global and sector combos are similar confirm the global combo is robust.",
        "",
    ])

    report = "\n".join(lines)
    (out_dir / "best_combos_by_sector_report.md").write_text(report, encoding="utf-8")
    print(f"  Saved best_combos_by_sector_report.md")
    print()
    print(report)


if __name__ == "__main__":
    run_analysis()

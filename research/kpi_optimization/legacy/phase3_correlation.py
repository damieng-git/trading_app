"""
Phase 3 — Correlation & Redundancy Analysis (Weekly)

1. Compute KPI states for all stocks using default parameters
2. Build pairwise state correlation matrix (median across stocks)
3. Hierarchical clustering to identify redundant groups
4. Within each cluster, keep only the best performer (from Phase 1)

Outputs: correlation matrix CSV, clustering report, reduced KPI set.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage, fcluster
from scipy.spatial.distance import squareform

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from trading_dashboard.kpis.catalog import compute_kpi_state_map, KPI_TREND_ORDER, KPI_BREAKOUT_ORDER
from trading_dashboard.kpis.rules import STATE_BULL, STATE_BEAR, STATE_NA
from trading_dashboard.indicators.registry import get_dimension_for_kpi, DIMENSIONS
from tf_config import parse_timeframe_arg, output_dir_for, phase1_csv_for, ENRICHED_DIR


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REDUNDANCY_THRESHOLD = 0.75

SCORING_KPIS = KPI_TREND_ORDER + KPI_BREAKOUT_ORDER


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_weekly_data(enriched_dir: Path, timeframe: str, min_bars: int) -> Dict[str, pd.DataFrame]:
    data: Dict[str, pd.DataFrame] = {}
    for f in sorted(enriched_dir.glob(f"*_{timeframe}.csv")):
        symbol = f.stem.rsplit(f"_{timeframe}", 1)[0]
        try:
            df = pd.read_csv(f, index_col=0, parse_dates=[0])
            df.index = pd.to_datetime(df.index, errors="coerce").tz_localize(None)
            df = df.sort_index()
            if len(df) >= min_bars and "Close" in df.columns:
                data[symbol] = df
        except Exception:
            continue
    return data


# ---------------------------------------------------------------------------
# Correlation computation
# ---------------------------------------------------------------------------

def compute_state_matrix(all_data: Dict[str, pd.DataFrame], kpi_names: List[str]) -> Dict[str, pd.DataFrame]:
    """For each stock, compute a DataFrame of KPI states (columns = KPIs, rows = bars)."""
    stock_matrices: Dict[str, pd.DataFrame] = {}

    for symbol, df in all_data.items():
        state_map = compute_kpi_state_map(df)
        cols = {}
        for kpi in kpi_names:
            s = state_map.get(kpi)
            if s is not None:
                numeric = s.replace(STATE_NA, np.nan).astype(float)
                cols[kpi] = numeric
        if cols:
            stock_matrices[symbol] = pd.DataFrame(cols, index=df.index)

    return stock_matrices


def compute_pairwise_correlation(
    stock_matrices: Dict[str, pd.DataFrame],
    kpi_names: List[str],
) -> pd.DataFrame:
    """Compute median pairwise Pearson correlation across all stocks."""
    n = len(kpi_names)
    pair_corrs: Dict[Tuple[str, str], List[float]] = {}

    for i in range(n):
        for j in range(i + 1, n):
            pair_corrs[(kpi_names[i], kpi_names[j])] = []

    for symbol, mat in stock_matrices.items():
        for i in range(n):
            for j in range(i + 1, n):
                ki, kj = kpi_names[i], kpi_names[j]
                if ki not in mat.columns or kj not in mat.columns:
                    continue
                si = mat[ki].dropna()
                sj = mat[kj].dropna()
                common = si.index.intersection(sj.index)
                if len(common) < 50:
                    continue
                c = si.loc[common].corr(sj.loc[common])
                if not np.isnan(c):
                    pair_corrs[(ki, kj)].append(c)

    corr_matrix = pd.DataFrame(1.0, index=kpi_names, columns=kpi_names)
    for (ki, kj), vals in pair_corrs.items():
        if vals:
            med = float(np.median(vals))
            corr_matrix.loc[ki, kj] = med
            corr_matrix.loc[kj, ki] = med

    return corr_matrix


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------

def cluster_kpis(
    corr_matrix: pd.DataFrame,
    threshold: float = REDUNDANCY_THRESHOLD,
) -> Dict[int, List[str]]:
    """Hierarchical clustering on 1-corr distance. Returns cluster_id -> KPI names."""
    dist = 1.0 - corr_matrix.clip(-1, 1)
    np.fill_diagonal(dist.values, 0)
    dist = (dist + dist.T) / 2
    dist = dist.clip(lower=0)

    condensed = squareform(dist.values, checks=False)
    condensed = np.nan_to_num(condensed, nan=1.0)

    Z = linkage(condensed, method="ward")
    cut_dist = 1.0 - threshold
    labels = fcluster(Z, t=cut_dist, criterion="distance")

    clusters: Dict[int, List[str]] = {}
    for kpi, label in zip(corr_matrix.index, labels):
        clusters.setdefault(int(label), []).append(kpi)

    return clusters


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def generate_report(
    corr_matrix: pd.DataFrame,
    clusters: Dict[int, List[str]],
    phase1_scores: pd.DataFrame,
    output_dir: Path,
    timeframe: str,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    corr_matrix.to_csv(output_dir / "correlation_matrix.csv")

    hr_map = {}
    if phase1_scores is not None and "KPI" in phase1_scores.columns:
        for _, row in phase1_scores.iterrows():
            hr_map[row["KPI"]] = row.get("HR 4w", np.nan)

    lines = [
        f"# Phase 3 — Correlation & Redundancy Analysis ({timeframe})",
        "",
        f"**KPIs analyzed:** {len(corr_matrix)} "
        f"| **Redundancy threshold:** r >= {REDUNDANCY_THRESHOLD}",
        "",
        "## Clusters",
        "",
    ]

    kept: List[str] = []
    dropped: List[Tuple[str, str, str]] = []

    for cid in sorted(clusters.keys()):
        members = clusters[cid]
        if len(members) == 1:
            kept.append(members[0])
            dim = DIMENSIONS.get(get_dimension_for_kpi(members[0]) or "", "?")
            hr = hr_map.get(members[0], np.nan)
            hr_s = f"{hr:.3f}" if not np.isnan(hr) else "—"
            lines.append(f"**Cluster {cid}** (unique): {members[0]} ({dim}, HR 4w={hr_s})")
            lines.append("")
            continue

        dims = [DIMENSIONS.get(get_dimension_for_kpi(m) or "", "?") for m in members]
        lines.append(f"**Cluster {cid}** ({len(members)} KPIs):")
        lines.append("")

        best_kpi = None
        best_hr = -1.0
        for m in members:
            hr = hr_map.get(m, np.nan)
            hr_s = f"{hr:.3f}" if not np.isnan(hr) else "—"
            dim = DIMENSIONS.get(get_dimension_for_kpi(m) or "", "?")
            lines.append(f"  - {m} ({dim}, HR 4w={hr_s})")
            if not np.isnan(hr) and hr > best_hr:
                best_hr = hr
                best_kpi = m

        if best_kpi is None:
            best_kpi = members[0]

        kept.append(best_kpi)
        for m in members:
            if m != best_kpi:
                reason = f"redundant with {best_kpi}"
                dropped.append((m, reason, f"cluster {cid}"))

        lines.append(f"  **Keep:** {best_kpi}")

        sub = corr_matrix.loc[members, members]
        pairs = []
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                pairs.append((members[i], members[j], sub.iloc[i, j]))
        if pairs:
            lines.append("")
            lines.append("  Pairwise correlations:")
            for ki, kj, r in sorted(pairs, key=lambda x: -x[2]):
                lines.append(f"    {ki} <-> {kj}: r = {r:.3f}")

        lines.append("")

    lines.extend([
        "## Summary",
        "",
        f"- **Original KPIs:** {len(corr_matrix)}",
        f"- **Clusters found:** {len(clusters)}",
        f"- **Kept (non-redundant):** {len(kept)}",
        f"- **Dropped (redundant):** {len(dropped)}",
        "",
        "### Retained KPI Set",
        "",
    ])

    for k in kept:
        dim = DIMENSIONS.get(get_dimension_for_kpi(k) or "", "?")
        hr = hr_map.get(k, np.nan)
        hr_s = f"{hr:.3f}" if not np.isnan(hr) else "—"
        lines.append(f"- {k} ({dim}, HR 4w={hr_s})")

    lines.append("")

    if dropped:
        lines.append("### Dropped KPIs")
        lines.append("")
        for name, reason, cluster in dropped:
            dim = DIMENSIONS.get(get_dimension_for_kpi(name) or "", "?")
            lines.append(f"- ~~{name}~~ ({dim}) — {reason}")
        lines.append("")

    highest_pairs = []
    for i in range(len(corr_matrix)):
        for j in range(i + 1, len(corr_matrix)):
            ki = corr_matrix.index[i]
            kj = corr_matrix.columns[j]
            r = corr_matrix.iloc[i, j]
            highest_pairs.append((ki, kj, r))
    highest_pairs.sort(key=lambda x: -x[2])

    lines.append("## Top 10 Most Correlated Pairs")
    lines.append("")
    lines.append("| KPI A | KPI B | Correlation |")
    lines.append("|-------|-------|-------------|")
    for ki, kj, r in highest_pairs[:10]:
        lines.append(f"| {ki} | {kj} | {r:.3f} |")
    lines.append("")

    report = "\n".join(lines)
    (output_dir / "correlation_report.md").write_text(report, encoding="utf-8")
    print(report)

    import json
    (output_dir / "retained_kpis.json").write_text(
        json.dumps({"kept": kept, "dropped": [d[0] for d in dropped]}, indent=2) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    t0 = time.time()
    tf = parse_timeframe_arg("Phase 3 — Correlation & Redundancy")

    OUTPUT_DIR = output_dir_for(tf.timeframe, "phase3")
    PHASE1_CSV = phase1_csv_for(tf.timeframe)

    print(f"Loading data from {ENRICHED_DIR} ...")
    all_data = load_weekly_data(ENRICHED_DIR, tf.timeframe, tf.min_bars)
    print(f"Loaded {len(all_data)} stocks in {time.time() - t0:.1f}s")

    available_kpis = []
    sample_df = next(iter(all_data.values()))
    sample_states = compute_kpi_state_map(sample_df)
    for kpi in SCORING_KPIS:
        s = sample_states.get(kpi)
        if s is not None and (s != -2).any():
            available_kpis.append(kpi)
    print(f"Available KPIs: {len(available_kpis)}")

    print("Computing state matrices ...")
    stock_matrices = compute_state_matrix(all_data, available_kpis)
    print(f"State matrices for {len(stock_matrices)} stocks")

    print("Computing pairwise correlations ...")
    corr_matrix = compute_pairwise_correlation(stock_matrices, available_kpis)

    print("Clustering ...")
    clusters = cluster_kpis(corr_matrix, REDUNDANCY_THRESHOLD)

    phase1_scores = None
    if PHASE1_CSV.exists():
        phase1_scores = pd.read_csv(PHASE1_CSV)

    print(f"\nGenerating report to {OUTPUT_DIR} ...")
    generate_report(corr_matrix, clusters, phase1_scores, OUTPUT_DIR, tf.timeframe)
    print(f"\nPhase 3 complete in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

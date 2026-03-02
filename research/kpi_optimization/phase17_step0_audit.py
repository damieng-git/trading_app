"""
Phase 17 — Step 0: Pre-Flight Audit

Runs before the main combo search to validate data quality, KPI coverage,
correlation structure, and individual KPI signal quality.

Sub-steps:
  0b  Data quality audit (missing bars, column checks, date ranges)
  0c  KPI state coverage (NA%, always-bull%, signal rarity per KPI × TF)
  0d  Correlation analysis (pairwise Spearman + coincidence → prune/exclude)
  0e  Individual KPI scorecard (standalone HR/return per polarity)
  0f  Search space estimation

Dataset: sample_300 enriched parquets.
Output:  research/kpi_optimization/outputs/all/phase17/step0/
"""
from __future__ import annotations

import csv
import json
import sys
import time
from collections import defaultdict
from itertools import combinations
from math import comb as _comb
from pathlib import Path

import numpy as np
import pandas as pd

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from trading_dashboard.kpis.catalog import compute_kpi_state_map
from trading_dashboard.kpis.rules import STATE_BULL, STATE_BEAR, STATE_NEUTRAL, STATE_NA

ENRICHED_DIR = REPO_DIR / "research" / "data" / "feature_store" / "enriched" / "sample_300" / "stock_data"
SAMPLE_CSV = REPO_DIR / "research" / "sample_universe" / "sample_300.csv"
OUTPUTS_DIR = Path(__file__).resolve().parent / "outputs" / "all" / "phase17" / "step0"

ALL_TFS = ["4H", "1D", "1W", "2W", "1M"]

V6_KPIS = [
    "Nadaraya-Watson Smoother", "TuTCI", "MA Ribbon", "Madrid Ribbon",
    "Donchian Ribbon", "DEMA", "Ichimoku", "GK Trend Ribbon", "Impulse Trend",
    "WT_LB", "SQZMOM_LB", "Stoch_MTM", "CM_Ult_MacD_MFT", "cRSI",
    "ADX & DI", "GMMA", "RSI Strength & Consolidation Zones (Zeiierman)",
    "OBVOSC_LB", "Mansfield RS", "SR Breaks",
    "BB 30", "Nadaraya-Watson Envelop (MAE)", "Nadaraya-Watson Envelop (STD)",
    "Nadaraya-Watson Envelop (Repainting)",
    "SuperTrend", "UT Bot Alert", "CM_P-SAR", "Volume + MA20",
]

STOOF_KPIS = [
    "MACD_BL", "WT_LB_BL", "OBVOSC_BL", "CCI_Chop_BB_v1", "ADX_DI_BL",
    "LuxAlgo_Norm_v1", "Risk_Indicator", "LuxAlgo_Norm_v2", "CCI_Chop_BB_v2", "PAI",
]

ALL_KPIS = V6_KPIS + STOOF_KPIS

KPI_DIMENSION = {
    "Nadaraya-Watson Smoother": "trend", "TuTCI": "trend", "MA Ribbon": "trend",
    "Madrid Ribbon": "trend", "Donchian Ribbon": "trend", "DEMA": "trend",
    "Ichimoku": "trend", "GK Trend Ribbon": "trend", "Impulse Trend": "trend",
    "ADX & DI": "momentum", "ADX_DI_BL": "trend",
    "WT_LB": "momentum", "SQZMOM_LB": "momentum", "Stoch_MTM": "momentum",
    "CM_Ult_MacD_MFT": "momentum", "cRSI": "momentum", "GMMA": "momentum",
    "RSI Strength & Consolidation Zones (Zeiierman)": "momentum",
    "OBVOSC_LB": "momentum", "Volume + MA20": "momentum",
    "MACD_BL": "momentum", "PAI": "momentum",
    "Mansfield RS": "relative_strength", "SR Breaks": "relative_strength",
    "BB 30": "breakout",
    "Nadaraya-Watson Envelop (MAE)": "breakout",
    "Nadaraya-Watson Envelop (STD)": "breakout",
    "Nadaraya-Watson Envelop (Repainting)": "breakout",
    "SuperTrend": "risk_exit", "UT Bot Alert": "risk_exit",
    "CM_P-SAR": "risk_exit", "Risk_Indicator": "risk_exit",
    "WT_LB_BL": "mean_reversion", "OBVOSC_BL": "mean_reversion",
    "CCI_Chop_BB_v1": "mean_reversion", "LuxAlgo_Norm_v1": "mean_reversion",
    "LuxAlgo_Norm_v2": "mean_reversion", "CCI_Chop_BB_v2": "mean_reversion",
}

STRATEGY_POOLS = {
    "A_trend": {
        "anchor_dim": ["trend"],
        "pool_dim": ["trend", "momentum", "relative_strength"],
        "polarity": "bull_only",
    },
    "B_dip": {
        "anchor_dim": ["trend"],
        "pool_dim": ["trend", "mean_reversion", "breakout", "momentum"],
        "polarity": "mixed",
    },
    "C_breakout": {
        "anchor_dim": ["breakout"],
        "pool_dim": ["breakout", "momentum", "relative_strength"],
        "polarity": "bull_only",
    },
    "D_risk": {
        "anchor_dim": ["trend"],
        "pool_dim": ["trend", "risk_exit", "momentum"],
        "polarity": "bull_only",
    },
    "E_mixed": {
        "anchor_dim": [],
        "pool_dim": list(set(KPI_DIMENSION.values())),
        "polarity": "mixed",
    },
}


def _load_symbols():
    with open(SAMPLE_CSV) as f:
        return [row["yfinance_ticker"] for row in csv.DictReader(f)]


def _load_enriched(sym, tf):
    p = ENRICHED_DIR / f"{sym}_{tf}.parquet"
    if p.exists():
        return pd.read_parquet(p)
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Step 0b: Data Quality Audit
# ══════════════════════════════════════════════════════════════════════════════

def step_0b(symbols):
    print("\n" + "=" * 80)
    print("  STEP 0b: DATA QUALITY AUDIT")
    print("=" * 80)

    results = {}
    for tf in ALL_TFS:
        available = []
        missing = []
        bar_counts = []
        has_stoof = 0
        date_ranges = []

        for sym in symbols:
            df = _load_enriched(sym, tf)
            if df is None or df.empty:
                missing.append(sym)
                continue
            available.append(sym)
            bar_counts.append(len(df))
            date_ranges.append((str(df.index[0])[:10], str(df.index[-1])[:10]))
            if "MACD_BL" in df.columns:
                has_stoof += 1

        results[tf] = {
            "available": len(available),
            "missing": len(missing),
            "has_stoof": has_stoof,
            "bar_stats": {
                "min": int(np.min(bar_counts)) if bar_counts else 0,
                "median": int(np.median(bar_counts)) if bar_counts else 0,
                "max": int(np.max(bar_counts)) if bar_counts else 0,
            },
            "symbols": available,
            "missing_symbols": missing,
        }

        print(f"\n  {tf}: {len(available)}/{len(symbols)} symbols available")
        print(f"    Stoof columns: {'YES' if has_stoof > 0 else 'MISSING'} ({has_stoof}/{len(available)})")
        if bar_counts:
            print(f"    Bars: min={np.min(bar_counts)}, median={np.median(bar_counts):.0f}, max={np.max(bar_counts)}")
        if missing:
            print(f"    Missing ({len(missing)}): {', '.join(missing[:10])}{'...' if len(missing) > 10 else ''}")

    return results


# ══════════════════════════════════════════════════════════════════════════════
# Step 0c: KPI State Coverage
# ══════════════════════════════════════════════════════════════════════════════

def step_0c(symbols, quality):
    print("\n" + "=" * 80)
    print("  STEP 0c: KPI STATE COVERAGE")
    print("=" * 80)

    coverage = {}  # {tf: {kpi: {bull%, bear%, neutral%, na%, total_bars}}}

    for tf in ALL_TFS:
        avail = quality.get(tf, {}).get("symbols", [])
        if not avail:
            print(f"\n  {tf}: SKIPPED (no data)")
            continue

        kpi_accum = defaultdict(lambda: {"bull": 0, "bear": 0, "neutral": 0, "na": 0, "total": 0})

        sample = avail[:50] if len(avail) > 50 else avail
        for sym in sample:
            df = _load_enriched(sym, tf)
            if df is None or df.empty:
                continue
            states = compute_kpi_state_map(df)
            for kpi_name, series in states.items():
                if kpi_name not in ALL_KPIS:
                    continue
                n = len(series)
                kpi_accum[kpi_name]["bull"] += int((series == STATE_BULL).sum())
                kpi_accum[kpi_name]["bear"] += int((series == STATE_BEAR).sum())
                kpi_accum[kpi_name]["neutral"] += int((series == STATE_NEUTRAL).sum())
                kpi_accum[kpi_name]["na"] += int((series == STATE_NA).sum())
                kpi_accum[kpi_name]["total"] += n

        tf_cov = {}
        print(f"\n  {tf} (sampled {len(sample)} stocks):")
        print(f"    {'KPI':<45} {'Bull%':>6} {'Bear%':>6} {'Neut%':>6} {'NA%':>6} {'Flag'}")
        print(f"    {'-'*85}")

        for kpi in ALL_KPIS:
            acc = kpi_accum.get(kpi)
            if acc is None or acc["total"] == 0:
                tf_cov[kpi] = {"bull_pct": 0, "bear_pct": 0, "neutral_pct": 0, "na_pct": 100, "flag": "NO_DATA"}
                print(f"    {kpi:<45} {'—':>6} {'—':>6} {'—':>6} {'100':>6} NO_DATA")
                continue

            t = acc["total"]
            bp = acc["bull"] / t * 100
            brp = acc["bear"] / t * 100
            np_ = acc["neutral"] / t * 100
            nap = acc["na"] / t * 100

            flag = ""
            if nap > 30:
                flag = "HIGH_NA"
            elif bp > 90:
                flag = "ALWAYS_BULL"
            elif bp < 5:
                flag = "RARE_BULL"

            tf_cov[kpi] = {"bull_pct": round(bp, 1), "bear_pct": round(brp, 1),
                           "neutral_pct": round(np_, 1), "na_pct": round(nap, 1), "flag": flag}
            print(f"    {kpi:<45} {bp:>6.1f} {brp:>6.1f} {np_:>6.1f} {nap:>6.1f} {flag}")

        coverage[tf] = tf_cov

    return coverage


# ══════════════════════════════════════════════════════════════════════════════
# Step 0d: Correlation Analysis
# ══════════════════════════════════════════════════════════════════════════════

def step_0d(symbols, quality, coverage):
    print("\n" + "=" * 80)
    print("  STEP 0d: CORRELATION ANALYSIS")
    print("=" * 80)

    correlations = {}
    drop_kpis = {}
    exclusion_pairs = {}

    for tf in ALL_TFS:
        avail = quality.get(tf, {}).get("symbols", [])
        tf_cov = coverage.get(tf, {})
        if not avail or not tf_cov:
            print(f"\n  {tf}: SKIPPED (no data)")
            continue

        eligible_kpis = [k for k in ALL_KPIS if tf_cov.get(k, {}).get("flag", "NO_DATA") not in ("NO_DATA", "HIGH_NA")]
        if len(eligible_kpis) < 3:
            print(f"\n  {tf}: SKIPPED (only {len(eligible_kpis)} eligible KPIs)")
            continue

        all_states = []
        sample = avail[:50] if len(avail) > 50 else avail
        for sym in sample:
            df = _load_enriched(sym, tf)
            if df is None or df.empty:
                continue
            states = compute_kpi_state_map(df)
            row_data = {}
            for kpi in eligible_kpis:
                if kpi in states:
                    s = states[kpi].replace(STATE_NA, np.nan)
                    row_data[kpi] = s
            if row_data:
                combined = pd.DataFrame(row_data)
                all_states.append(combined)

        if not all_states:
            continue

        big_df = pd.concat(all_states, ignore_index=True)
        corr_matrix = big_df[eligible_kpis].corr(method="spearman")

        tf_drops = set()
        tf_excl = []

        print(f"\n  {tf}: {len(eligible_kpis)} eligible KPIs, {len(big_df)} total bars")
        print(f"    High correlations (r > 0.70):")
        print(f"    {'KPI A':<40} {'KPI B':<40} {'r':>6} {'Action'}")
        print(f"    {'-'*95}")

        pairs_checked = []
        for i, kpi_a in enumerate(eligible_kpis):
            for j, kpi_b in enumerate(eligible_kpis):
                if j <= i:
                    continue
                r = corr_matrix.loc[kpi_a, kpi_b]
                if np.isnan(r):
                    continue
                if abs(r) > 0.70:
                    action = ""
                    if abs(r) > 0.90:
                        action = "DROP one"
                        tf_drops.add((kpi_a, kpi_b, r))
                    else:
                        action = "EXCLUDE pair"
                        tf_excl.append((kpi_a, kpi_b, r))
                    print(f"    {kpi_a:<40} {kpi_b:<40} {r:>6.3f} {action}")
                    pairs_checked.append({"a": kpi_a, "b": kpi_b, "r": round(r, 4), "action": action})

        to_drop = set()
        for a, b, r in tf_drops:
            cov_a = tf_cov.get(a, {}).get("bull_pct", 0)
            cov_b = tf_cov.get(b, {}).get("bull_pct", 0)
            drop = b if cov_a >= cov_b else a
            to_drop.add(drop)
            print(f"    → Drop '{drop}' (less signal diversity)")

        drop_kpis[tf] = list(to_drop)
        exclusion_pairs[tf] = [(a, b) for a, b, _ in tf_excl]
        correlations[tf] = pairs_checked

        pruned = [k for k in eligible_kpis if k not in to_drop]
        print(f"    Pruned: {len(eligible_kpis)} → {len(pruned)} KPIs")
        print(f"    Exclusion pairs: {len(tf_excl)}")

    return correlations, drop_kpis, exclusion_pairs


# ══════════════════════════════════════════════════════════════════════════════
# Step 0e: Individual KPI Scorecard
# ══════════════════════════════════════════════════════════════════════════════

def step_0e(symbols, quality, coverage):
    print("\n" + "=" * 80)
    print("  STEP 0e: INDIVIDUAL KPI SCORECARD")
    print("=" * 80)

    scorecard = []

    for tf in ALL_TFS:
        avail = quality.get(tf, {}).get("symbols", [])
        tf_cov = coverage.get(tf, {})
        if not avail or not tf_cov:
            continue

        eligible_kpis = [k for k in ALL_KPIS if tf_cov.get(k, {}).get("flag", "NO_DATA") not in ("NO_DATA",)]

        horizon_map = {"4H": [2, 6, 20], "1D": [1, 5, 20], "1W": [1, 4, 13],
                       "2W": [1, 2, 6], "1M": [1, 2, 4]}
        horizons = horizon_map.get(tf, [1, 4, 8])

        sample = avail[:30] if len(avail) > 30 else avail
        print(f"\n  {tf} (sampled {len(sample)} stocks, horizons={horizons}):")

        for kpi in eligible_kpis:
            for polarity in [+1, -1]:
                pol_label = "+1" if polarity == 1 else "-1"
                target_state = STATE_BULL if polarity == 1 else STATE_BEAR
                all_returns = {h: [] for h in horizons}
                trade_count = 0

                for sym in sample:
                    df = _load_enriched(sym, tf)
                    if df is None or df.empty:
                        continue
                    states = compute_kpi_state_map(df)
                    if kpi not in states:
                        continue
                    s = states[kpi]
                    close = df["Close"] if "Close" in df.columns else None
                    if close is None:
                        continue

                    onset = (s == target_state) & (s.shift(1) != target_state)
                    onset_idx = onset[onset].index

                    for idx in onset_idx:
                        pos = close.index.get_loc(idx)
                        for h in horizons:
                            if pos + h < len(close):
                                ret = (close.iloc[pos + h] / close.iloc[pos] - 1) * 100
                                all_returns[h].append(ret)
                        trade_count += 1

                if trade_count < 10:
                    continue

                row = {"tf": tf, "kpi": kpi, "polarity": pol_label,
                       "dimension": KPI_DIMENSION.get(kpi, "?"), "trades": trade_count}
                for h in horizons:
                    rets = all_returns[h]
                    if rets:
                        arr = np.array(rets)
                        row[f"hr_h{h}"] = round(float((arr > 0).mean() * 100), 1)
                        row[f"avg_h{h}"] = round(float(arr.mean()), 3)
                        row[f"worst_h{h}"] = round(float(arr.min()), 1)
                    else:
                        row[f"hr_h{h}"] = None
                        row[f"avg_h{h}"] = None
                        row[f"worst_h{h}"] = None

                scorecard.append(row)

        print(f"    {'KPI':<45} {'Pol':>3} {'Trades':>6} ", end="")
        for h in horizons:
            print(f"  {'HR@'+str(h):>7} {'Avg@'+str(h):>8}", end="")
        print()
        print(f"    {'-'*120}")

        tf_rows = [r for r in scorecard if r["tf"] == tf]
        tf_rows.sort(key=lambda r: r.get(f"hr_h{horizons[1]}", 0) or 0, reverse=True)
        for r in tf_rows[:40]:
            print(f"    {r['kpi']:<45} {r['polarity']:>3} {r['trades']:>6} ", end="")
            for h in horizons:
                hr = r.get(f"hr_h{h}")
                avg = r.get(f"avg_h{h}")
                hr_s = f"{hr:.1f}%" if hr is not None else "—"
                avg_s = f"{avg:+.3f}" if avg is not None else "—"
                print(f"  {hr_s:>7} {avg_s:>8}", end="")
            print()

    return scorecard


# ══════════════════════════════════════════════════════════════════════════════
# Step 0f: Search Space Estimation
# ══════════════════════════════════════════════════════════════════════════════

def step_0f(coverage, drop_kpis):
    print("\n" + "=" * 80)
    print("  STEP 0f: SEARCH SPACE ESTIMATION")
    print("=" * 80)

    estimates = {}

    for tf in ALL_TFS:
        tf_cov = coverage.get(tf, {})
        if not tf_cov:
            continue

        drops = set(drop_kpis.get(tf, []))
        eligible = [k for k in ALL_KPIS
                    if tf_cov.get(k, {}).get("flag", "NO_DATA") not in ("NO_DATA", "HIGH_NA")
                    and k not in drops]
        n = len(eligible)

        print(f"\n  {tf}: {n} eligible KPIs (after pruning)")

        tf_est = {}
        total = 0
        for size_label, size in [("C3", 3), ("C4", 4), ("C5", 5), ("C6", 6)]:
            if n < size:
                combos = 0
            else:
                combos = _comb(n, size)
            tf_est[size_label] = combos
            total += combos
            print(f"    {size_label}: C({n},{size}) = {combos:,} combos")

        tf_est["total"] = total
        estimates[tf] = tf_est
        print(f"    Total: {total:,} combos")

        for strat, cfg in STRATEGY_POOLS.items():
            pool = [k for k in eligible if KPI_DIMENSION.get(k) in cfg["pool_dim"]]
            n_pool = len(pool)
            strat_total = sum(_comb(n_pool, s) for s in range(3, 7) if n_pool >= s)
            if cfg["polarity"] == "mixed":
                strat_total *= 4
            print(f"      {strat}: pool={n_pool} → ~{strat_total:,} combos")

    return estimates


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    t0 = time.time()
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    symbols = _load_symbols()
    print(f"\nPhase 17 — Step 0: Pre-Flight Audit")
    print(f"Universe: {len(symbols)} symbols")
    print(f"Timeframes: {ALL_TFS}")
    print(f"KPIs: {len(V6_KPIS)} v6 + {len(STOOF_KPIS)} Stoof = {len(ALL_KPIS)} total")

    # 0b: Data Quality
    quality = step_0b(symbols)
    with open(OUTPUTS_DIR / "quality_report.json", "w") as f:
        safe_q = {tf: {k: v for k, v in d.items() if k != "symbols"} for tf, d in quality.items()}
        json.dump(safe_q, f, indent=2)

    # 0c: KPI State Coverage
    coverage = step_0c(symbols, quality)
    with open(OUTPUTS_DIR / "kpi_coverage.json", "w") as f:
        json.dump(coverage, f, indent=2)

    # 0d: Correlation Analysis
    correlations, drop_kpis, exclusion_pairs = step_0d(symbols, quality, coverage)
    with open(OUTPUTS_DIR / "correlation_pairs.json", "w") as f:
        json.dump(correlations, f, indent=2)
    with open(OUTPUTS_DIR / "drop_kpis.json", "w") as f:
        json.dump(drop_kpis, f, indent=2)
    with open(OUTPUTS_DIR / "exclusion_pairs.json", "w") as f:
        json.dump({tf: [(a, b) for a, b in pairs] for tf, pairs in exclusion_pairs.items()}, f, indent=2)

    # 0e: KPI Scorecard
    scorecard = step_0e(symbols, quality, coverage)
    if scorecard:
        csv_path = OUTPUTS_DIR / "kpi_scorecard.csv"
        all_keys = set()
        for row in scorecard:
            all_keys.update(row.keys())
        base = ["tf", "kpi", "polarity", "dimension", "trades"]
        extra = sorted(k for k in all_keys if k not in base)
        fieldnames = base + extra
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(scorecard)
        print(f"\n  Scorecard saved: {csv_path}")

    # 0f: Search Space
    estimates = step_0f(coverage, drop_kpis)
    with open(OUTPUTS_DIR / "search_space.json", "w") as f:
        json.dump(estimates, f, indent=2)

    # ── Recommendations ──────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  RECOMMENDATIONS")
    print("=" * 80)

    recs = []

    # Check Stoof availability
    any_stoof = any(quality.get(tf, {}).get("has_stoof", 0) > 0 for tf in ALL_TFS)
    if not any_stoof:
        recs.append("CRITICAL: No Stoof columns found in any parquet. Run `fetch_sample300.py --force` to re-enrich with corrected Stoof indicators before proceeding to Stage 1.")

    # Check 2W/1M availability
    for tf in ["2W", "1M"]:
        if quality.get(tf, {}).get("available", 0) == 0:
            recs.append(f"MISSING: {tf} data not available. Run `fetch_sample300.py --force` to generate.")

    # Correlation drops
    for tf in ALL_TFS:
        drops = drop_kpis.get(tf, [])
        if drops:
            recs.append(f"{tf}: Drop {len(drops)} redundant KPIs: {', '.join(drops)}")

    # Degenerate KPIs
    for tf in ALL_TFS:
        tf_cov = coverage.get(tf, {})
        for kpi, info in tf_cov.items():
            if info.get("flag") == "ALWAYS_BULL":
                recs.append(f"{tf}: '{kpi}' is always bullish (>90%) — exclude from combos on this TF")
            elif info.get("flag") == "RARE_BULL":
                recs.append(f"{tf}: '{kpi}' is rarely bullish (<5%) — combos will have too few trades")

    for i, rec in enumerate(recs, 1):
        print(f"  {i}. {rec}")

    rec_path = OUTPUTS_DIR / "recommendations.txt"
    with open(rec_path, "w") as f:
        f.write("Phase 17 — Step 0 Recommendations\n")
        f.write(f"Generated: {time.strftime('%Y-%m-%d %H:%M')}\n\n")
        for i, rec in enumerate(recs, 1):
            f.write(f"{i}. {rec}\n")

    elapsed = time.time() - t0
    print(f"\nTotal runtime: {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"Outputs saved to: {OUTPUTS_DIR}")


if __name__ == "__main__":
    main()

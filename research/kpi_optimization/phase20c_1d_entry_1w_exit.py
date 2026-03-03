"""
Phase 20C — 1D Entry + 1W Exit Swing Optimization

Tests 1D entry combos (single KPI, 2-KPI bi-polarity, P18 top combos)
with exit governed by locked 1W strategies.

Entry: 1D combo onset → buy next day open
Exit:  1W KPI invalidation + ATR trailing stop on weekly bars
"""
from __future__ import annotations

import gc
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from research.kpi_optimization.phase18_master import (
    KPI_DIM, KPI_SHORT, EXIT_PARAMS, COST_PCT,
    load_data, precompute,
    _get_exit_kpi_indices, _sl,
)

T0 = time.time()
OUT_DIR = Path(__file__).parent / "outputs" / "all" / "phase20c"


def log(msg):
    elapsed = (time.time() - T0) / 60
    print(f"[{time.strftime('%H:%M:%S')}] [{elapsed:5.1f}m] {msg}", flush=True)


# ─── Locked 1W strategies ────────────────────────────────────────────────────

LOCKED_1W = {
    "dip_buy": {
        "label": "NWSm(+)+ADX(-)+Stoch(+)",
        "kpis": ["Nadaraya-Watson Smoother", "ADX & DI", "Stoch_MTM"],
        "pols": [1, -1, 1],
    },
    "swing": {
        "label": "NWSm(+)+Stoch(+)+cRSI(+)",
        "kpis": ["Nadaraya-Watson Smoother", "Stoch_MTM", "cRSI"],
        "pols": [1, 1, 1],
    },
    "trend": {
        "label": "NWSm(+)+DEMA(+)+cRSI(+)",
        "kpis": ["Nadaraya-Watson Smoother", "DEMA", "cRSI"],
        "pols": [1, 1, 1],
    },
}


# ─── Build 1D entry candidate pools ──────────────────────────────────────────

def build_entry_candidates():
    """Build the 3 pools of 1D entry combos to test."""
    all_kpis = list(KPI_DIM.keys())
    candidates = []
    seen = set()

    def _add(kpis, pols, pool):
        key = (tuple(kpis), tuple(pols))
        if key not in seen:
            seen.add(key)
            candidates.append({
                "kpis": kpis, "pols": pols,
                "label": _sl(kpis, pols),
                "pool": pool,
            })

    # Pool 1: P18 top 1D combos
    p18_path = Path(__file__).parent / "outputs" / "all" / "phase18" / "phase18_1_combos.json"
    with open(p18_path) as f:
        p18_all = json.load(f)
    p18_1d = sorted(
        [c for c in p18_all if c.get("tf") == "1D" and c.get("hr", 0) >= 70],
        key=lambda c: -c.get("trades", 0))[:20]
    for c in p18_1d:
        _add(c["kpis"], c["pols"], "p18")
    log(f"  Pool 1 (P18 top 1D): {len(p18_1d)} combos")

    # Pool 2: Single-KPI sweep (both polarities)
    for k in all_kpis:
        _add([k], [1], "single")
        _add([k], [-1], "single")
    log(f"  Pool 2 (single KPI ±1): {len(all_kpis)*2} combos")

    # Pool 3: 2-KPI bi-polarity pairs
    # Focus on cross-dimension pairs (trend×momentum, trend×mean_reversion, etc.)
    trend_kpis = [k for k, d in KPI_DIM.items() if d == "trend"]
    momentum_kpis = [k for k, d in KPI_DIM.items() if d == "momentum"]
    mr_kpis = [k for k, d in KPI_DIM.items() if d == "mean_reversion"]

    # Trend(+) × Momentum(-) — classic dip-buy
    for tk in trend_kpis:
        for mk in momentum_kpis:
            _add([tk, mk], [1, -1], "2kpi_dip")

    # Trend(+) × Momentum(+) — trend confirmation
    for tk in trend_kpis:
        for mk in momentum_kpis[:6]:  # top momentum KPIs only
            _add([tk, mk], [1, 1], "2kpi_trend")

    # Momentum(-) × Momentum(+) — divergence/reversal
    for i, mk1 in enumerate(momentum_kpis):
        for mk2 in momentum_kpis[i+1:]:
            _add([mk1, mk2], [-1, 1], "2kpi_div")
            _add([mk1, mk2], [1, -1], "2kpi_div")

    # Mean-reversion(+) × Trend(+) — recovery entry
    for mrk in mr_kpis:
        for tk in trend_kpis[:5]:
            _add([mrk, tk], [1, 1], "2kpi_recov")
            _add([mrk, tk], [-1, 1], "2kpi_recov")

    log(f"  Pool 3 (2-KPI pairs): {len(candidates) - len(p18_1d) - len(all_kpis)*2} combos")
    log(f"  Total unique candidates: {len(candidates)}")
    return candidates


# ─── Precompute 1W exit state per symbol ──────────────────────────────────────

def precompute_1w_exit(data_1w, all_pc_1w, exit_def):
    """For each symbol, compute weekly exit arrays: exit_active mask + ATR + close.
    Returns {sym: {dates, exit_active, cl, atr, n}}"""
    kpis = exit_def["kpis"]
    pols = exit_def["pols"]
    exit_kpi_idx = _get_exit_kpi_indices(kpis, pols, "standard")
    result = {}

    for sym, pc in all_pc_1w.items():
        if sym not in data_1w:
            continue
        n = pc["n"]
        bulls, bears = pc["bulls"], pc["bears"]
        if any(k not in bulls for k in kpis):
            continue

        exit_active = np.ones(n, dtype=bool)
        for ei in exit_kpi_idx:
            k, p = kpis[ei], pols[ei]
            if p == 1:
                exit_active &= bulls[k]
            else:
                exit_active &= bears[k]

        idx = data_1w[sym].index[:n]
        result[sym] = {
            "dates": pd.to_datetime(idx).tz_localize(None),
            "exit_active": exit_active,
            "cl": pc["cl"],
            "atr": pc["atr"],
            "n": n,
        }
    return result


# ─── Main simulation: 1D entry + 1W exit ─────────────────────────────────────

def sim_1d_entry_1w_exit(all_pc_1d, data_1d, entry_kpis, entry_pols,
                         exit_data_1w, K_1w=4.0, M_1w=20):
    """Simulate: enter on 1D combo onset, exit on 1W KPI invalidation / ATR stop."""
    trades = []

    for sym, pc in all_pc_1d.items():
        if sym not in exit_data_1w or sym not in data_1d:
            continue

        bulls, bears = pc["bulls"], pc["bears"]
        if any(k not in bulls for k in entry_kpis):
            continue

        n_1d = pc["n"]
        cl_1d = pc["cl"]
        op_1d = pc["op"]
        sma20 = pc["sma20"]
        sma200 = pc["sma200"]
        vol_spike_ok = pc["vol_spike_ok"]
        overext_ok = pc["overext_ok"]
        dates_1d = pd.to_datetime(data_1d[sym].index[:n_1d]).tz_localize(None)

        ew = exit_data_1w[sym]
        dates_1w = ew["dates"]
        exit_active_1w = ew["exit_active"]
        cl_1w = ew["cl"]
        atr_1w = ew["atr"]
        n_1w = ew["n"]

        # Build 1D combo active mask
        combo_active = np.ones(n_1d, dtype=bool)
        for k, p in zip(entry_kpis, entry_pols):
            if p == 1:
                combo_active &= bulls[k]
            else:
                combo_active &= bears[k]

        i = 1
        while i < n_1d - 2:
            if not combo_active[i] or (i > 0 and combo_active[i - 1]):
                i += 1
                continue

            # v5 gates
            if not np.isnan(sma20[i]) and not np.isnan(sma200[i]):
                if sma20[i] <= sma200[i]:
                    i += 1
                    continue
            else:
                i += 1
                continue
            if not vol_spike_ok[i] or not overext_ok[i]:
                i += 1
                continue

            entry_price = op_1d[i + 1] if i + 1 < n_1d else cl_1d[i]
            if entry_price <= 0 or np.isnan(entry_price):
                i += 1
                continue

            entry_date = dates_1d[i]

            # Find the first 1W bar on or after entry date
            w_start = dates_1w.searchsorted(entry_date)
            if w_start >= n_1w:
                i += 1
                continue

            # Weekly exit loop
            best_price = entry_price
            sl = entry_price - K_1w * atr_1w[w_start] if (
                w_start < n_1w and not np.isnan(atr_1w[w_start])) else entry_price * 0.85
            exit_price = None
            exit_week = None

            for w in range(w_start + 1, min(w_start + M_1w, n_1w)):
                wk_cl = cl_1w[w]
                if np.isnan(wk_cl):
                    continue

                if wk_cl > best_price:
                    best_price = wk_cl
                trail = best_price - K_1w * atr_1w[w] if not np.isnan(atr_1w[w]) else best_price * 0.85

                # ATR trailing stop
                if wk_cl <= max(sl, trail):
                    exit_price = wk_cl
                    exit_week = w
                    break

                # KPI invalidation (exit KPIs no longer aligned)
                if not exit_active_1w[w] and w >= w_start + 2:
                    exit_price = wk_cl
                    exit_week = w
                    break

            if exit_price is None:
                exit_week = min(w_start + M_1w, n_1w - 1)
                exit_price = cl_1w[exit_week]

            if exit_price <= 0 or np.isnan(exit_price):
                i += 2
                continue

            ret = (exit_price - entry_price) / entry_price * 100 - COST_PCT
            hold_weeks = exit_week - w_start
            hold_days = hold_weeks * 5  # approximate

            # Find next 1D bar after exit week for skip
            exit_date = dates_1w[exit_week] if exit_week < n_1w else dates_1d[-1]
            next_i = dates_1d.searchsorted(exit_date, side="right")

            trades.append({"ret": ret, "hold_d": hold_days, "hold_w": hold_weeks})
            i = max(i + 2, next_i)

    if len(trades) < 5:
        return None

    rets = [t["ret"] for t in trades]
    holds_d = [t["hold_d"] for t in trades]
    holds_w = [t["hold_w"] for t in trades]
    wins = sum(1 for r in rets if r > 0)
    gross_w = sum(r for r in rets if r > 0)
    gross_l = abs(sum(r for r in rets if r <= 0))
    return {
        "trades": len(rets),
        "hr": round(wins / len(rets) * 100, 1),
        "pf": round(gross_w / gross_l, 2) if gross_l > 0 else 999,
        "avg_ret": round(float(np.mean(rets)), 3),
        "avg_hold_d": round(float(np.mean(holds_d)), 1),
        "avg_hold_w": round(float(np.mean(holds_w)), 1),
        "worst": round(min(rets), 1),
        "best": round(max(rets), 1),
        "total_ret": round(sum(rets), 1),
    }


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_kpis = list(KPI_DIM.keys())

    log("Phase 20C — 1D Entry + 1W Exit Swing Optimization")
    log("=" * 90)

    # Build entry candidates
    log("\nBuilding 1D entry candidates...")
    candidates = build_entry_candidates()

    # Load data
    log("\nLoading 1W data...")
    data_1w = load_data("1W")
    all_pc_1w = precompute(data_1w, "1W", all_kpis)
    log(f"  1W: {len(all_pc_1w)} stocks")

    log("Loading 1D data...")
    data_1d = load_data("1D")
    all_pc_1d = precompute(data_1d, "1D", all_kpis)
    log(f"  1D: {len(all_pc_1d)} stocks")

    # Precompute 1W exit states for each locked strategy
    exit_data = {}
    for stype, sdef in LOCKED_1W.items():
        exit_data[stype] = precompute_1w_exit(data_1w, all_pc_1w, sdef)
        log(f"  1W exit '{stype}': {len(exit_data[stype])} stocks with exit data")

    del all_pc_1w
    gc.collect()

    K_1w = EXIT_PARAMS["1W"]["K"]
    M_1w = EXIT_PARAMS["1W"]["M"]

    # Run simulations
    all_results = []
    n_total = len(candidates) * len(LOCKED_1W)
    log(f"\nRunning {n_total} simulations ({len(candidates)} entries × "
        f"{len(LOCKED_1W)} exit strategies)...")
    log(f"\n{'#':>4} {'Entry':<45} {'Pool':<10} {'Exit':<10} │ "
        f"{'Tr':>5} {'HR':>6} {'PF':>7} {'Ret':>7} {'HoldD':>6} {'Worst':>6} {'Tot':>8}")
    log(f"{'':─>4} {'':─<45} {'':─<10} {'':─<10} ┼ {'':─>5} {'':─>6} {'':─>7} {'':─>7} {'':─>6} {'':─>6} {'':─>8}")

    sim_count = 0
    for ci, cand in enumerate(candidates):
        for stype, sdef in LOCKED_1W.items():
            sim_count += 1
            r = sim_1d_entry_1w_exit(
                all_pc_1d, data_1d,
                cand["kpis"], cand["pols"],
                exit_data[stype],
                K_1w=K_1w, M_1w=M_1w)

            entry = {
                "entry_label": cand["label"],
                "entry_kpis": cand["kpis"],
                "entry_pols": cand["pols"],
                "pool": cand["pool"],
                "exit_type": stype,
                "exit_label": sdef["label"],
                "result": r,
            }
            all_results.append(entry)

            if r and r["trades"] >= 30:
                log(f"{sim_count:4} {cand['label']:<45} {cand['pool']:<10} {stype:<10} │ "
                    f"{r['trades']:5} {r['hr']:5.1f}% {r['pf']:7.2f} {r['avg_ret']:6.2f}% "
                    f"{r['avg_hold_d']:5.0f}d {r['worst']:5.1f}% {r['total_ret']:7.0f}%")

        if (ci + 1) % 50 == 0:
            log(f"  ... {ci+1}/{len(candidates)} entries done ({sim_count} sims)")

    # ─── Results ──────────────────────────────────────────────────────────────
    log(f"\n\n{'='*90}")
    log("RESULTS — Best 1D entries per 1W exit strategy")
    log(f"{'='*90}")

    viable = [r for r in all_results if r["result"] and r["result"]["trades"] >= 50]
    log(f"\nViable combos (≥50 trades): {len(viable)} out of {len(all_results)}")

    best_per_exit = {}
    for stype in LOCKED_1W:
        stype_results = sorted(
            [r for r in viable if r["exit_type"] == stype],
            key=lambda r: -(r["result"]["hr"] * r["result"]["avg_ret"]))
        best_per_exit[stype] = stype_results[:10]

        log(f"\n  ── 1W EXIT: {stype.upper()} ({LOCKED_1W[stype]['label']}) ──")
        log(f"  {'#':>2} {'Entry Combo':<45} {'Pool':<10} │ "
            f"{'Tr':>5} {'HR':>6} {'PF':>7} {'Ret':>7} {'HoldD':>6} {'HoldW':>6} {'Worst':>6} {'TotRet':>8}")
        log(f"  {'':─>2} {'':─<45} {'':─<10} ┼ {'':─>5} {'':─>6} {'':─>7} {'':─>7} {'':─>6} {'':─>6} {'':─>6} {'':─>8}")

        for i, r in enumerate(stype_results[:10], 1):
            m = r["result"]
            log(f"  {i:2} {r['entry_label']:<45} {r['pool']:<10} │ "
                f"{m['trades']:5} {m['hr']:5.1f}% {m['pf']:7.2f} {m['avg_ret']:6.2f}% "
                f"{m['avg_hold_d']:5.0f}d {m['avg_hold_w']:5.1f}w "
                f"{m['worst']:5.1f}% {m['total_ret']:7.0f}%")

    # Cross-strategy summary
    log(f"\n\n{'='*90}")
    log("OVERALL BEST — Top 15 across all exit strategies")
    log(f"{'='*90}")
    overall = sorted(viable, key=lambda r: -(r["result"]["hr"] * r["result"]["avg_ret"]))
    for i, r in enumerate(overall[:15], 1):
        m = r["result"]
        log(f"  {i:2} {r['entry_label']:<40} exit={r['exit_type']:<10} "
            f"tr={m['trades']:>4} HR={m['hr']:>5.1f}% PF={m['pf']:>6.2f} "
            f"ret={m['avg_ret']:>6.2f}% hold={m['avg_hold_d']:.0f}d "
            f"worst={m['worst']:.1f}% total={m['total_ret']:.0f}%")

    # Save
    with open(OUT_DIR / "phase20c_results.json", "w") as f:
        json.dump(all_results, f, indent=2, default=str)

    best_flat = []
    for stype, items in best_per_exit.items():
        for r in items:
            best_flat.append(r)
    with open(OUT_DIR / "phase20c_best.json", "w") as f:
        json.dump(best_flat, f, indent=2, default=str)

    elapsed = (time.time() - T0) / 60
    log(f"\n\nPhase 20C complete — {elapsed:.1f} min")
    log(f"Output: {OUT_DIR}")


if __name__ == "__main__":
    main()

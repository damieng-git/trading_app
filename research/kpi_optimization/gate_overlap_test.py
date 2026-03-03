"""
1W Gate Overlap Test

Tests how P18 top combos for 1D and 4H perform when gated by 
validated 1W strategies (HOLD or ENTRY state).

For each 1W gate × 1D/4H combo:
  - Ungated: run combo on full data
  - Gated:   only allow entries when 1W combo is active (HOLD/ENTRY)
  - Report:  trade count, HR, PF, avg_ret for both
"""
from __future__ import annotations

import gc
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from research.kpi_optimization.phase18_master import (
    KPI_DIM, ENRICHED_DIR, EXIT_PARAMS, ATR_PERIOD, MAX_HOLD,
    COMMISSION, SLIPPAGE, COST_PCT,
    load_data, precompute, compute_atr,
    _get_exit_kpi_indices,
)
from trading_dashboard.kpis.catalog import compute_kpi_state_map
from trading_dashboard.kpis.rules import STATE_BULL, STATE_BEAR

T0 = time.time()

def log(msg):
    elapsed = (time.time() - T0) / 60
    print(f"[{time.strftime('%H:%M:%S')}] [{elapsed:5.1f}m] {msg}", flush=True)


# ─── Validated 1W gates ──────────────────────────────────────────────────────

GATES_1W = {
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


# ─── Compute 1W gate state per symbol ────────────────────────────────────────

def compute_gate_states(data_1w, all_pc_1w, gate_def):
    """Returns {sym: pd.Series(bool, index=DatetimeIndex)} — True when 1W strategy is in HOLD.
    
    Simulates the 1W strategy: on onset (combo transitions to active), enter trade.
    Trade stays open until exit condition (ATR trailing stop or KPI invalidation).
    The HOLD mask covers the full trade duration, not just the combo-active bars.
    """
    kpis = gate_def["kpis"]
    pols = gate_def["pols"]
    exit_kpi_idx = _get_exit_kpi_indices(kpis, pols, "standard")
    T = EXIT_PARAMS["1W"]["T"]
    M = EXIT_PARAMS["1W"]["M"]
    K = EXIT_PARAMS["1W"]["K"]
    states = {}

    for sym, pc in all_pc_1w.items():
        n = pc["n"]
        bulls, bears = pc["bulls"], pc["bears"]
        if any(k not in bulls for k in kpis):
            continue

        cl, op, at = pc["cl"], pc["op"], pc["atr"]
        sma20, sma200 = pc["sma20"], pc["sma200"]
        overext_ok, vol_spike_ok = pc["overext_ok"], pc["vol_spike_ok"]

        combo_active = np.ones(n, dtype=bool)
        for k, p in zip(kpis, pols):
            if p == 1:
                combo_active &= bulls[k]
            else:
                combo_active &= bears[k]

        exit_active = np.ones(n, dtype=bool)
        for ei in exit_kpi_idx:
            k, p = kpis[ei], pols[ei]
            if p == 1:
                exit_active &= bulls[k]
            else:
                exit_active &= bears[k]

        in_trade = np.zeros(n, dtype=bool)
        i = 1
        while i < n - 2:
            if not combo_active[i] or (i > 0 and combo_active[i - 1]):
                i += 1
                continue
            # Onset check passed — check gates
            if np.isnan(sma20[i]) or np.isnan(sma200[i]) or sma20[i] <= sma200[i]:
                i += 1
                continue
            if not vol_spike_ok[i] or not overext_ok[i]:
                i += 1
                continue

            entry_price = op[i + 1] if i + 1 < n else cl[i]
            if entry_price <= 0 or np.isnan(entry_price):
                i += 1
                continue

            # Mark entry bar as in-trade
            in_trade[i] = True

            # Find exit
            sl = entry_price - K * at[i] if not np.isnan(at[i]) else entry_price * 0.9
            best = entry_price
            exit_bar = min(i + 2 + M, n - 1)
            for j in range(i + 2, min(i + 2 + M, n)):
                if cl[j] > best:
                    best = cl[j]
                trail = best - K * at[j] if not np.isnan(at[j]) else best * 0.9
                if cl[j] <= max(sl, trail):
                    exit_bar = j
                    break
                if not exit_active[j] and j >= i + T:
                    exit_bar = j
                    break

            # Mark all bars from entry to exit as in-trade (HOLD state)
            in_trade[i:exit_bar + 1] = True
            i = exit_bar + 1

        if sym in data_1w:
            idx = data_1w[sym].index[:n]
            states[sym] = pd.Series(in_trade, index=idx)
    return states


def align_gate_to_tf(gate_states_1w, data_tf):
    """Align 1W gate states to a faster TF using forward-fill merge_asof."""
    aligned = {}
    for sym, df_tf in data_tf.items():
        if sym not in gate_states_1w:
            continue
        gs = gate_states_1w[sym]
        gate_df = pd.DataFrame({"gate": gs.values.astype(float)}, index=gs.index)
        gate_df.index = pd.to_datetime(gate_df.index).tz_localize(None)
        tf_idx = pd.to_datetime(df_tf.index).tz_localize(None)
        tf_df = pd.DataFrame({"_tmp": 0}, index=tf_idx)
        merged = pd.merge_asof(
            tf_df, gate_df,
            left_index=True, right_index=True,
            direction="backward"
        )
        aligned[sym] = merged["gate"].fillna(0).to_numpy().astype(bool)
    return aligned


# ─── Gated simulation ────────────────────────────────────────────────────────

def sim_combo_gated(all_pc, combo_kpis, combo_pols, tf, gate_mask_by_sym,
                    exit_mode="standard", delay=1):
    """Run sim_combo logic with an additional gate mask. Returns (ungated_result, gated_result)."""
    T = EXIT_PARAMS[tf]["T"]
    M = EXIT_PARAMS[tf]["M"]
    K = EXIT_PARAMS[tf]["K"]

    exit_kpi_idx = _get_exit_kpi_indices(combo_kpis, combo_pols, exit_mode)

    trades_ungated = []
    trades_gated = []

    for sym, pc in all_pc.items():
        bulls, bears = pc["bulls"], pc["bears"]
        if any(k not in bulls for k in combo_kpis):
            continue

        cl, op, at, n = pc["cl"], pc["op"], pc["atr"], pc["n"]
        sma20, sma200 = pc["sma20"], pc["sma200"]
        overext_ok = pc["overext_ok"]
        vol_spike_ok = pc["vol_spike_ok"]

        gate = gate_mask_by_sym.get(sym)
        has_gate = gate is not None and len(gate) >= n

        # Combo active mask
        combo_active = np.ones(n, dtype=bool)
        for k, p in zip(combo_kpis, combo_pols):
            if p == 1:
                combo_active &= bulls[k]
            else:
                combo_active &= bears[k]

        # Build exit mask
        exit_active = np.ones(n, dtype=bool)
        for ei in exit_kpi_idx:
            k, p = combo_kpis[ei], combo_pols[ei]
            if p == 1:
                exit_active &= bulls[k]
            else:
                exit_active &= bears[k]

        i = delay
        while i < n - 2:
            if not combo_active[i]:
                i += 1
                continue
            # Onset check
            if i > 0 and combo_active[i - 1]:
                i += 1
                continue

            entry_price = op[i + 1] if i + 1 < n else cl[i]
            if entry_price <= 0 or np.isnan(entry_price):
                i += 1
                continue

            # SMA gate (1D/1W)
            if tf in ("1D", "1W", "2W", "1M"):
                if np.isnan(sma20[i]) or np.isnan(sma200[i]) or sma20[i] <= sma200[i]:
                    i += 1
                    continue
            if not vol_spike_ok[i]:
                i += 1
                continue
            if not overext_ok[i]:
                i += 1
                continue

            # Find exit
            sl = entry_price - K * at[i] if not np.isnan(at[i]) else entry_price * 0.9
            best = entry_price
            exit_price = None
            exit_bar = None

            for j in range(i + 2, min(i + 2 + M, n)):
                if cl[j] > best:
                    best = cl[j]
                trail = best - K * at[j] if not np.isnan(at[j]) else best * 0.9
                if cl[j] <= max(sl, trail):
                    exit_price = cl[j]
                    exit_bar = j
                    break
                if not exit_active[j] and j >= i + T:
                    exit_price = op[j + 1] if j + 1 < n else cl[j]
                    exit_bar = j
                    break
            if exit_price is None:
                exit_bar = min(i + 2 + M, n - 1)
                exit_price = cl[exit_bar]

            if exit_price <= 0 or np.isnan(exit_price):
                i = exit_bar + 1
                continue

            ret = (exit_price - entry_price) / entry_price * 100 - COST_PCT
            hold = exit_bar - i

            trades_ungated.append({"ret": ret, "hold": hold})

            if has_gate and gate[i]:
                trades_gated.append({"ret": ret, "hold": hold})

            i = exit_bar + 1

    def _stats(trades):
        if not trades or len(trades) < 5:
            return None
        rets = [t["ret"] for t in trades]
        holds = [t["hold"] for t in trades]
        wins = sum(1 for r in rets if r > 0)
        losses = sum(1 for r in rets if r <= 0)
        hr = wins / len(rets) * 100
        gross_w = sum(r for r in rets if r > 0)
        gross_l = abs(sum(r for r in rets if r <= 0))
        pf = round(gross_w / gross_l, 2) if gross_l > 0 else 999
        return {
            "trades": len(rets),
            "hr": round(hr, 1),
            "pf": pf,
            "avg_ret": round(float(np.mean(rets)), 3),
            "avg_hold": round(float(np.mean(holds)), 1),
            "worst": round(min(rets), 1),
        }

    return _stats(trades_ungated), _stats(trades_gated)


# ─── Load P18 top combos ─────────────────────────────────────────────────────

def load_p18_combos(tf, top_n=15):
    p18_path = Path(__file__).parent / "outputs" / "all" / "phase18" / "phase18_1_combos.json"
    with open(p18_path) as f:
        all_combos = json.load(f)
    tf_combos = [c for c in all_combos if c.get("tf") == tf and c.get("hr", 0) >= 75]
    return sorted(tf_combos, key=lambda c: -c.get("trades", 0))[:top_n]


def classify_combo(combo):
    kpis = combo["kpis"]
    pols = combo["pols"]
    dims = [KPI_DIM.get(k, "unknown") for k in kpis]
    has_bear_momentum = any(p == -1 and d in ("momentum", "mean_reversion") for d, p in zip(dims, pols))
    has_bull_trend = any(p == 1 and d == "trend" for d, p in zip(dims, pols))
    all_bull = all(p == 1 for p in pols)
    if has_bear_momentum and has_bull_trend:
        return "dip_buy"
    if all_bull:
        return "trend"
    return "swing"


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    log("1W GATE OVERLAP TEST")
    log("=" * 90)

    all_kpis = list(KPI_DIM.keys())

    # Load 1W
    log("Loading 1W data...")
    data_1w = load_data("1W")
    all_pc_1w = precompute(data_1w, "1W", all_kpis)
    log(f"  1W: {len(data_1w)} stocks, {len(all_pc_1w)} precomputed")

    # Compute gate states for all 3 validated 1W strategies
    gate_states = {}
    for gname, gdef in GATES_1W.items():
        gs = compute_gate_states(data_1w, all_pc_1w, gdef)
        gate_states[gname] = gs
        active_pcts = []
        for sym, s in gs.items():
            active_pcts.append(s.mean() * 100)
        log(f"  Gate '{gname}' ({gdef['label']}): avg active {np.mean(active_pcts):.1f}% of bars")

    results = []

    for tf in ["1D", "4H"]:
        log(f"\n{'='*90}")
        log(f"  Loading {tf} data...")
        data_tf = load_data(tf)
        all_pc_tf = precompute(data_tf, tf, all_kpis)
        log(f"  {tf}: {len(data_tf)} stocks, {len(all_pc_tf)} precomputed")

        combos = load_p18_combos(tf, top_n=15)
        log(f"  Top {len(combos)} P18 combos for {tf}")

        # Align all gates to this TF
        aligned_gates = {}
        for gname, gs in gate_states.items():
            aligned_gates[gname] = align_gate_to_tf(gs, data_tf)
            n_aligned = sum(1 for v in aligned_gates[gname].values() if v.any())
            log(f"  Gate '{gname}' aligned to {tf}: {len(aligned_gates[gname])} stocks, "
                f"{n_aligned} with active gate")

        log(f"\n  {'Combo':<50} {'Type':<10} │ {'Ungated':>40} │ Gate │ {'Gated':>40}")
        log(f"  {'':─<50} {'':─<10} ┼ {'':─>40} ┼ {'':─<5} ┼ {'':─>40}")

        for ci, combo in enumerate(combos):
            label = combo.get("label", "?")[:48]
            ctype = classify_combo(combo)

            for gname, gdef in GATES_1W.items():
                gate_mask = aligned_gates[gname]
                ungated, gated = sim_combo_gated(
                    all_pc_tf, combo["kpis"], combo["pols"], tf,
                    gate_mask, exit_mode="standard", delay=1)

                def _fmt(r):
                    if r is None:
                        return "no trades"
                    return (f"tr={r['trades']:>5} HR={r['hr']:>5.1f}% "
                            f"ret={r['avg_ret']:>6.2f}% PF={r['pf']:>7} "
                            f"hold={r['avg_hold']:>4.1f}")

                ug_str = _fmt(ungated)
                g_str = _fmt(gated)

                # Survival rate
                if ungated and gated:
                    surv = gated["trades"] / ungated["trades"] * 100
                    surv_str = f"{surv:4.0f}%"
                else:
                    surv_str = "  — "

                log(f"  {label:<50} {ctype:<10} │ {ug_str} │ {gname:<10} {surv_str} │ {g_str}")

                results.append({
                    "tf": tf,
                    "combo": label,
                    "combo_kpis": combo["kpis"],
                    "combo_pols": combo["pols"],
                    "combo_type": ctype,
                    "gate": gname,
                    "gate_label": gdef["label"],
                    "ungated": ungated,
                    "gated": gated,
                    "survival_pct": (gated["trades"] / ungated["trades"] * 100
                                     if ungated and gated else 0),
                })

        del data_tf, all_pc_tf
        gc.collect()

    # ─── Summary ──────────────────────────────────────────────────────────────
    log(f"\n\n{'='*90}")
    log("SUMMARY — Best gated combo per TF × trading type × gate")
    log(f"{'='*90}")

    for tf in ["1D", "4H"]:
        log(f"\n  ── {tf} ──")
        tf_results = [r for r in results if r["tf"] == tf and r["gated"] is not None]
        if not tf_results:
            log(f"    No gated results for {tf}")
            continue

        for ctype in ["dip_buy", "swing", "trend"]:
            type_results = [r for r in tf_results if r["combo_type"] == ctype]
            if not type_results:
                continue
            log(f"\n    {ctype.upper()}:")
            # Sort by gated HR * avg_ret (quality score)
            scored = sorted(type_results,
                            key=lambda r: r["gated"]["hr"] * r["gated"]["avg_ret"],
                            reverse=True)
            for r in scored[:5]:
                g = r["gated"]
                u = r["ungated"]
                hr_delta = g["hr"] - u["hr"] if u else 0
                log(f"      {r['combo'][:40]:<40} gate={r['gate']:<10} │ "
                    f"tr={g['trades']:>4} HR={g['hr']:>5.1f}% (Δ{hr_delta:>+5.1f}) "
                    f"ret={g['avg_ret']:>6.2f}% PF={g['pf']:>6} "
                    f"hold={g['avg_hold']:>4.1f} surv={r['survival_pct']:>4.0f}%")

    log(f"\n\nDone in {(time.time()-T0)/60:.1f} min")

    # Save
    out_path = Path(__file__).parent / "outputs" / "all" / "phase20b" / "gate_overlap_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    log(f"Saved: {out_path}")


if __name__ == "__main__":
    main()

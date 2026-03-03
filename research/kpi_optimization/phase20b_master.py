"""
Phase 20B — Expanded Strategy Matrix Validation

Fills the strategy-type × timeframe matrix using:
  20B.0  3-axis candidate extraction (trades / quality / tail-safety)
  20B.1  4-fold walk-forward validation (regime robustness)
  20B.2  2-fold validation (practical viability)
  20B.3  Exit mode re-optimization (READY/CANDIDATE combos only)
  20B.4  Baseline comparison (random entry + buy-and-hold)
  20B.5  Behavior classification + matrix assembly

Processes ONE timeframe at a time to stay within server memory limits.

Inputs:  phase18_1_combos.json, supplement_validated.json,
         phase19_validated.json, sample_300 parquets
Output:  research/kpi_optimization/outputs/all/phase20b/
"""
from __future__ import annotations

import csv
import gc
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import psutil

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from research.kpi_optimization.phase18_master import (
    KPI_DIM, KPI_SHORT, MIXED_ALLOWED_DIMS, BULL_ONLY_DIMS,
    ENRICHED_DIR, EXIT_PARAMS, ATR_PERIOD, MAX_HOLD,
    COMMISSION, SLIPPAGE, COST_PCT, HR_FLOOR, TOP_N,
    ARCHETYPES, ALL_TFS,
    _check_memory, _save_json, compute_atr, load_data, precompute,
    sim_combo, _sl, _get_exit_kpi_indices,
)

# ══════════════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════════════

P18_DIR = Path(__file__).resolve().parent / "outputs" / "all" / "phase18"
P18S_DIR = Path(__file__).resolve().parent / "outputs" / "all" / "phase18_supplement"
P19_DIR = Path(__file__).resolve().parent / "outputs" / "all" / "phase19"
P20B_DIR = Path(__file__).resolve().parent / "outputs" / "all" / "phase20b"

MEM_THRESHOLD = 70

CANDIDATE_TFS = ["4H", "1D", "1W", "2W"]
AXIS_N = {"4H": 20, "1D": 20, "1W": 18, "2W": 15}
CANDIDATE_HR_FLOOR = {"4H": 80.0, "1D": 80.0, "1W": 80.0, "2W": 75.0}

FOLD_SLICE_MIN_BARS = {"4H": 50, "1D": 50, "1W": 20, "2W": 10}
FOLD_MIN_FRAC = {"4H": 0.02, "1D": 0.02, "1W": 0.015, "2W": 0.01}

FOLD_4_WINDOWS = {
    "4H": [
        {"id": "F1", "test_start": "2020-07-01", "test_end": "2021-07-01",
         "regime": "Post-COVID recovery"},
        {"id": "F2", "test_start": "2022-01-01", "test_end": "2023-01-01",
         "regime": "2022 bear market"},
        {"id": "F3", "test_start": "2023-07-01", "test_end": "2024-07-01",
         "regime": "Recovery / AI bull"},
        {"id": "F4", "test_start": "2025-01-01", "test_end": "2025-07-01",
         "regime": "Recent conditions"},
    ],
    "1D": [
        {"id": "F1", "test_start": "2020-07-01", "test_end": "2021-07-01",
         "regime": "Post-COVID recovery"},
        {"id": "F2", "test_start": "2022-01-01", "test_end": "2023-01-01",
         "regime": "2022 bear market"},
        {"id": "F3", "test_start": "2023-07-01", "test_end": "2024-07-01",
         "regime": "Recovery / AI bull"},
        {"id": "F4", "test_start": "2025-01-01", "test_end": "2025-07-01",
         "regime": "Recent conditions"},
    ],
    "1W": [
        {"id": "F1", "test_start": "2020-07-01", "test_end": "2021-07-01",
         "regime": "Post-COVID recovery"},
        {"id": "F2", "test_start": "2022-01-01", "test_end": "2023-01-01",
         "regime": "2022 bear market"},
        {"id": "F3", "test_start": "2023-07-01", "test_end": "2024-07-01",
         "regime": "Recovery / AI bull"},
        {"id": "F4", "test_start": "2024-01-01", "test_end": "2025-07-01",
         "regime": "Recent 18-month window"},
    ],
    "2W": [
        {"id": "F1", "test_start": "2019-07-01", "test_end": "2021-01-01",
         "regime": "Pre/Post-COVID transition"},
        {"id": "F2", "test_start": "2021-01-01", "test_end": "2022-07-01",
         "regime": "Late bull to early bear"},
        {"id": "F3", "test_start": "2022-01-01", "test_end": "2023-07-01",
         "regime": "Bear market stress period"},
        {"id": "F4", "test_start": "2023-07-01", "test_end": "2025-07-01",
         "regime": "Recovery + recent conditions"},
    ],
}

FOLD_2_WINDOWS = [
    {"id": "H1", "test_start_frac": 0.0, "test_end_frac": 0.5},
    {"id": "H2", "test_start_frac": 0.5, "test_end_frac": 1.0},
]

FOLD_THRESHOLDS = {
    "4H": {"min_trades": 40, "min_hr": 65.0, "min_ret": 0.0, "max_worst": -30.0},
    "1D": {"min_trades": 30, "min_hr": 65.0, "min_ret": 0.0, "max_worst": -30.0},
    "1W": {"min_trades": 20, "min_hr": 70.0, "min_ret": 0.0, "max_worst": -25.0},
    "2W": {"min_trades": 10, "min_hr": 70.0, "min_ret": 0.0, "max_worst": -25.0},
}

EXIT_MODES = ["standard", "trend_anchor", "momentum_governed",
              "risk_priority", "adaptive"]
TMK_GRID = [(2, 20, 3.0), (2, 20, 4.0), (4, 40, 4.0),
            (4, 48, 4.0), (6, 48, 4.0)]

HOLD_BANDS = {"4H": (2, 30), "1D": (3, 20), "1W": (2, 10), "2W": (1, 8)}

N_RANDOM_BASELINE = 5


# ══════════════════════════════════════════════════════════════════════════════
# Logging
# ══════════════════════════════════════════════════════════════════════════════

_T0 = None

def log(msg, level="INFO"):
    elapsed = (time.time() - _T0) / 60 if _T0 else 0
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{elapsed:6.1f}m] [{level:5s}] {msg}", flush=True)

def log_phase(phase_id, title):
    log("")
    log("=" * 78)
    log(f"  PHASE {phase_id} — {title}")
    log("=" * 78)

def log_mem(label=""):
    mem = psutil.virtual_memory()
    pct = mem.percent
    log(f"  Memory [{label}]: {pct:.0f}% ({mem.used/1e9:.1f}/{mem.total/1e9:.1f} GB)")
    if pct > MEM_THRESHOLD:
        gc.collect()
        mem2 = psutil.virtual_memory()
        if mem2.percent > MEM_THRESHOLD:
            log(f"  WARNING: Memory still at {mem2.percent:.0f}% after gc", "WARN")
    return pct


# ══════════════════════════════════════════════════════════════════════════════
# Date → fraction helpers
# ══════════════════════════════════════════════════════════════════════════════

def _date_to_frac(dates_index, date_str):
    ts = pd.Timestamp(date_str)
    n = len(dates_index)
    if n == 0:
        return 0.0
    if ts <= dates_index[0]:
        return 0.0
    if ts >= dates_index[-1]:
        return 1.0
    return dates_index.searchsorted(ts) / n


def build_frac_map(data, fold, tf="1D"):
    fracs = {}
    min_f = FOLD_MIN_FRAC.get(tf, 0.02)
    for sym, df in data.items():
        idx = df.index
        s = _date_to_frac(idx, fold["test_start"])
        e = _date_to_frac(idx, fold["test_end"])
        if e - s > min_f:
            fracs[sym] = (s, e)
    return fracs


def _slice_pc_to_fold(all_pc, fold_fracs, min_bars=50):
    sliced = {}
    for sym, pc in all_pc.items():
        if sym not in fold_fracs:
            continue
        sf, ef = fold_fracs[sym]
        n = pc["n"]
        si, ei = int(n * sf), int(n * ef)
        if ei - si < min_bars:
            continue
        sliced[sym] = {
            "bulls": {k: v[si:ei].copy() for k, v in pc["bulls"].items()},
            "bears": {k: v[si:ei].copy() for k, v in pc["bears"].items()},
            "nbull": {k: v[si:ei].copy() for k, v in pc["nbull"].items()},
            "nbear": {k: v[si:ei].copy() for k, v in pc["nbear"].items()},
            "cl": pc["cl"][si:ei].copy(),
            "op": pc["op"][si:ei].copy(),
            "atr": pc["atr"][si:ei].copy(),
            "n": ei - si,
            "sma20": pc["sma20"][si:ei].copy(),
            "sma200": pc["sma200"][si:ei].copy(),
            "overext_ok": pc["overext_ok"][si:ei].copy(),
            "vol_spike_ok": pc["vol_spike_ok"][si:ei].copy(),
        }
    return sliced


def _slice_pc_by_frac(all_pc, start_frac, end_frac, min_bars=50):
    """Slice all_pc using global fractional bounds (for 2-fold)."""
    sliced = {}
    for sym, pc in all_pc.items():
        n = pc["n"]
        si, ei = int(n * start_frac), int(n * end_frac)
        if ei - si < min_bars:
            continue
        sliced[sym] = {
            "bulls": {k: v[si:ei].copy() for k, v in pc["bulls"].items()},
            "bears": {k: v[si:ei].copy() for k, v in pc["bears"].items()},
            "nbull": {k: v[si:ei].copy() for k, v in pc["nbull"].items()},
            "nbear": {k: v[si:ei].copy() for k, v in pc["nbear"].items()},
            "cl": pc["cl"][si:ei].copy(),
            "op": pc["op"][si:ei].copy(),
            "atr": pc["atr"][si:ei].copy(),
            "n": ei - si,
            "sma20": pc["sma20"][si:ei].copy(),
            "sma200": pc["sma200"][si:ei].copy(),
            "overext_ok": pc["overext_ok"][si:ei].copy(),
            "vol_spike_ok": pc["vol_spike_ok"][si:ei].copy(),
        }
    return sliced


# ══════════════════════════════════════════════════════════════════════════════
# Simulation helpers
# ══════════════════════════════════════════════════════════════════════════════

def _run_combo(sliced_pc, combo, tf):
    return sim_combo(
        sliced_pc,
        combo["kpis"], combo["pols"], tf,
        exit_mode=combo.get("exit_mode", "standard"),
        gate=combo.get("gate", "none"),
        delay=combo.get("delay", 1),
        T_override=combo.get("T"),
        M_override=combo.get("M"),
        K_override=combo.get("K"),
        min_trades=5,
        start_frac=0.0, end_frac=1.0,
    )


def _check_fold(r, tf):
    """Check a fold result against TF thresholds. Returns (pass, reasons)."""
    if r is None:
        return False, "no_result"
    th = FOLD_THRESHOLDS[tf]
    reasons = []
    if r["trades"] < th["min_trades"]:
        reasons.append(f"trades={r['trades']}<{th['min_trades']}")
    if r["hr"] < th["min_hr"]:
        reasons.append(f"HR={r['hr']}<{th['min_hr']}")
    if r["avg_ret"] <= th["min_ret"]:
        reasons.append(f"avg_ret={r['avg_ret']}<=0")
    if r["worst"] <= th["max_worst"]:
        reasons.append(f"worst={r['worst']}<={th['max_worst']}")
    return (len(reasons) == 0), "; ".join(reasons) if reasons else ""


def sim_random_entry(all_pc, n_target, avg_hold, start_frac, end_frac, seed=42):
    rng = random.Random(seed)
    trades = []
    syms = list(all_pc.keys())
    if not syms:
        return None
    per_sym = max(1, n_target // len(syms))
    for sym in syms:
        pc = all_pc[sym]
        n = pc["n"]
        si, ei = int(n * start_frac), int(n * end_frac)
        if ei - si < 50:
            continue
        cl, op = pc["cl"], pc["op"]
        hold = max(1, int(avg_hold))
        for _ in range(per_sym):
            if si + 2 >= ei - hold - 2:
                continue
            j = rng.randint(si + 1, ei - hold - 2)
            ep = float(op[j]) if j < len(op) else 0
            if ep <= 0 or np.isnan(ep):
                continue
            xp = float(cl[min(j + hold, ei - 1)])
            if np.isnan(xp):
                continue
            trades.append((xp - ep) / ep * 100 - COST_PCT)
    if not trades:
        return None
    return {
        "trades": len(trades),
        "hr": round(sum(1 for r in trades if r > 0) / len(trades) * 100, 1),
        "avg_ret": round(float(np.mean(trades)), 3),
    }


def sim_buy_and_hold(all_pc, start_frac, end_frac):
    rets = []
    for sym, pc in all_pc.items():
        n = pc["n"]
        si, ei = int(n * start_frac), int(n * end_frac)
        if ei - si < 20:
            continue
        ep, xp = float(pc["cl"][si]), float(pc["cl"][ei - 1])
        if ep <= 0 or np.isnan(ep) or np.isnan(xp):
            continue
        rets.append((xp - ep) / ep * 100 - COST_PCT)
    if not rets:
        return None
    return {
        "trades": len(rets),
        "hr": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
        "avg_ret": round(float(np.mean(rets)), 3),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Behavior classification
# ══════════════════════════════════════════════════════════════════════════════

def classify_behavior(combo):
    kpis = combo["kpis"]
    pols = combo["pols"]
    dims = [KPI_DIM.get(k, "unknown") for k in kpis]
    has_breakout = any(d == "breakout" for d in dims)
    has_bear_momentum = any(
        p == -1 and d in ("momentum", "mean_reversion")
        for d, p in zip(dims, pols))
    has_bull_trend = any(p == 1 and d == "trend" for d, p in zip(dims, pols))
    all_same_pol = len(set(pols)) == 1
    if has_breakout:
        return "breakout"
    if has_bear_momentum and has_bull_trend:
        return "dip_buy"
    if all_same_pol:
        return "trend_entry"
    return "mixed"


def infer_trading_type(behavior, tf, avg_hold):
    """Map behavior + tf to matrix cell trading type."""
    if behavior == "dip_buy":
        return "buy_the_dip"
    if tf in ("4H", "1D") and avg_hold and avg_hold < 15:
        return "swing_trading" if behavior != "trend_entry" else "swing_trading"
    if tf in ("1W", "2W"):
        if avg_hold and avg_hold > 6:
            return "trend_position"
        return "swing_trading"
    return "swing_trading"


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 20B.0 — CANDIDATE EXTRACTION (3-axis)
# ══════════════════════════════════════════════════════════════════════════════

def phase_20b_0():
    log_phase("20B.0", "EXPANDED CANDIDATE EXTRACTION (3-axis)")

    # Load P18 stage-1 combos
    p18_path = P18_DIR / "phase18_1_combos.json"
    with open(p18_path) as f:
        all_p18 = json.load(f)
    log(f"P18 combos loaded: {len(all_p18)}")

    # Load P18 supplement
    p18s_path = P18S_DIR / "supplement_validated.json"
    supp_combos = []
    if p18s_path.exists():
        with open(p18s_path) as f:
            supp_combos = json.load(f)
        log(f"P18 supplement loaded: {len(supp_combos)}")

    # Load P19
    p19_path = P19_DIR / "phase19_validated.json"
    p19_combos = []
    if p19_path.exists():
        with open(p19_path) as f:
            p19_combos = json.load(f)
        log(f"P19 MTF loaded: {len(p19_combos)}")

    candidates = {}
    for tf in CANDIDATE_TFS:
        hr_floor = CANDIDATE_HR_FLOOR[tf]
        axis_n = AXIS_N[tf]

        tf_combos = [c for c in all_p18 if c.get("tf") == tf
                     and c.get("hr", 0) >= hr_floor]

        # 3-axis selection
        by_trades = sorted(tf_combos, key=lambda c: -c.get("trades", 0))[:axis_n]
        by_quality = sorted(tf_combos,
                            key=lambda c: -(c.get("hr", 0) * c.get("avg_ret", 0)))[:axis_n]
        by_safety = sorted(tf_combos,
                           key=lambda c: c.get("worst", -999),
                           reverse=True)[:axis_n]

        merged = {(tuple(c["kpis"]), tuple(c["pols"])): c
                  for axis in [by_trades, by_quality, by_safety]
                  for c in axis}

        # Add supplement combos for this TF
        tf_supp = [c for c in supp_combos if c.get("tf") == tf]
        for c in tf_supp:
            key = (tuple(c["kpis"]), tuple(c["pols"]))
            if key not in merged:
                merged[key] = c

        selected = list(merged.values())
        candidates[tf] = selected

        log(f"  {tf}: p18={len(tf_combos)} (HR>={hr_floor}%) → "
            f"3-axis={len(merged)-len(tf_supp)} + supp={len(tf_supp)} = "
            f"{len(selected)} candidates")
        for i, c in enumerate(sorted(selected, key=lambda x: -x.get("trades", 0))[:5]):
            log(f"    #{i+1}: {c.get('label','?')[:50]} "
                f"tr={c['trades']} HR={c['hr']}% "
                f"ret={c.get('avg_ret',0):.2f}% arch={c.get('archetype','?')}")

    total = sum(len(v) for v in candidates.values())
    log(f"Total candidates: {total}")

    return candidates, p19_combos


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 20B.1 — 4-FOLD VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

def phase_20b_1_tf(tf, combos, data, all_pc):
    """Run 4-fold validation for one timeframe. Returns (results_list, survivors_list)."""
    folds = FOLD_4_WINDOWS[tf]
    min_bars = FOLD_SLICE_MIN_BARS[tf]
    log(f"\n  ── {tf}: {len(combos)} candidates × {len(folds)} folds ──")

    # Pre-build fold slices
    fold_slices = {}
    for fold in folds:
        fid = fold["id"]
        fracs = build_frac_map(data, fold, tf=tf)
        fold_slices[fid] = _slice_pc_to_fold(all_pc, fracs, min_bars=min_bars)
        log(f"  {fid}: {len(fold_slices[fid])} stocks in window")

    results = []
    survivors = []

    for ci, combo in enumerate(combos):
        label = combo.get("label", _sl(combo["kpis"], combo["pols"]))
        fold_metrics = {}
        n_pass = 0

        for fold in folds:
            fid = fold["id"]
            r = _run_combo(fold_slices[fid], combo, tf)
            ok, reason = _check_fold(r, tf)
            fm = {"pass": ok}
            if r:
                fm.update({
                    "trades": r["trades"], "hr": r["hr"],
                    "avg_ret": r["avg_ret"], "pf": r["pf"],
                    "avg_hold": r["avg_hold"], "worst": r["worst"],
                })
            if not ok:
                fm["reason"] = reason
            else:
                n_pass += 1
            fold_metrics[fid] = fm

        # Score
        passing = [fm for fm in fold_metrics.values() if fm.get("pass")]
        if passing:
            hrs = [fm["hr"] for fm in passing]
            rets = [fm["avg_ret"] for fm in passing]
            score_4f = round(min(hrs) * float(np.mean(rets)), 2)
        else:
            score_4f = 0

        entry = {
            **combo, "tf": tf,
            "folds_4": fold_metrics, "n_pass_4": n_pass,
            "score_4f": score_4f,
        }
        results.append(entry)

        if n_pass >= 2:
            survivors.append(entry)

        fold_str = " | ".join(
            f"{fid}:{'✓' if fm.get('pass') else '✗'}"
            f"({fm.get('trades','?')}/{fm.get('hr','?')}%)"
            for fid, fm in fold_metrics.items())
        status = "PASS" if n_pass >= 3 else ("CAND" if n_pass >= 2 else "FAIL")
        log(f"  {status} [{ci+1}/{len(combos)}] {label[:45]:<45} "
            f"({n_pass}/4) {fold_str}")

    del fold_slices
    gc.collect()

    log(f"\n  {tf} 4-fold: {len(survivors)} survivors (≥2/4 pass) "
        f"out of {len(combos)}")
    return results, survivors


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 20B.2 — 2-FOLD VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

def phase_20b_2_tf(tf, combos, all_pc):
    """Run 2-fold (50/50 split) validation. Returns updated combos."""
    min_bars = FOLD_SLICE_MIN_BARS[tf]
    log(f"\n  ── {tf}: {len(combos)} combos × 2 folds ──")

    # Pre-build 2-fold slices
    fold_slices = {}
    for fold in FOLD_2_WINDOWS:
        fid = fold["id"]
        fold_slices[fid] = _slice_pc_by_frac(
            all_pc, fold["test_start_frac"], fold["test_end_frac"],
            min_bars=min_bars)
        log(f"  {fid}: {len(fold_slices[fid])} stocks")

    for ci, combo in enumerate(combos):
        label = combo.get("label", "?")
        fold_metrics = {}
        n_pass = 0

        for fold in FOLD_2_WINDOWS:
            fid = fold["id"]
            r = _run_combo(fold_slices[fid], combo, tf)
            ok, reason = _check_fold(r, tf)
            fm = {"pass": ok}
            if r:
                fm.update({
                    "trades": r["trades"], "hr": r["hr"],
                    "avg_ret": r["avg_ret"], "pf": r["pf"],
                    "avg_hold": r["avg_hold"], "worst": r["worst"],
                })
            if not ok:
                fm["reason"] = reason
            else:
                n_pass += 1
            fold_metrics[fid] = fm

        combo["folds_2"] = fold_metrics
        combo["n_pass_2"] = n_pass

        fold_str = " | ".join(
            f"{fid}:{'✓' if fm.get('pass') else '✗'}"
            f"({fm.get('trades','?')}/{fm.get('hr','?')}%)"
            for fid, fm in fold_metrics.items())
        log(f"  [{ci+1}/{len(combos)}] {label[:45]:<45} ({n_pass}/2) {fold_str}")

    del fold_slices
    gc.collect()

    return combos


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 20B.3 — EXIT MODE RE-OPTIMIZATION
# ══════════════════════════════════════════════════════════════════════════════

def phase_20b_3_tf(tf, combos, data, all_pc):
    """Re-test exit modes for READY/CANDIDATE combos across all 6 folds."""
    hold_lo, hold_hi = HOLD_BANDS.get(tf, (1, 30))
    folds_4 = FOLD_4_WINDOWS[tf]
    min_bars = FOLD_SLICE_MIN_BARS[tf]

    log(f"\n  ── {tf}: {len(combos)} combos × "
        f"{len(EXIT_MODES)} modes × {len(TMK_GRID)} TMK ──")

    # Build fold slices (4-fold)
    fold_slices_4 = {}
    for fold in folds_4:
        fracs = build_frac_map(data, fold, tf=tf)
        fold_slices_4[fold["id"]] = _slice_pc_to_fold(all_pc, fracs, min_bars)

    # Build fold slices (2-fold)
    fold_slices_2 = {}
    for fold in FOLD_2_WINDOWS:
        fold_slices_2[fold["id"]] = _slice_pc_by_frac(
            all_pc, fold["test_start_frac"], fold["test_end_frac"], min_bars)

    all_slices = {**fold_slices_4, **fold_slices_2}

    for ci, combo in enumerate(combos):
        best_exit = None
        best_score = -1

        for em in EXIT_MODES:
            for T_v, M_v, K_v in TMK_GRID:
                fold_rets = []
                fold_holds = []
                fold_worsts = []
                all_ok = True

                for fid, spc in all_slices.items():
                    r = sim_combo(
                        spc, combo["kpis"], combo["pols"], tf,
                        exit_mode=em, gate=combo.get("gate", "none"),
                        delay=combo.get("delay", 1),
                        T_override=T_v, M_override=M_v, K_override=K_v,
                        min_trades=5, start_frac=0.0, end_frac=1.0)
                    if r is None or r["trades"] < 5:
                        all_ok = False
                        break
                    fold_rets.append(r["avg_ret"])
                    fold_holds.append(r["avg_hold"])
                    fold_worsts.append(r["worst"])

                if not all_ok:
                    continue

                mean_hold = float(np.mean(fold_holds))
                worst_ever = min(fold_worsts)
                if mean_hold < hold_lo or mean_hold > hold_hi:
                    continue
                if worst_ever <= -20:
                    continue

                score = float(np.mean(fold_rets))
                if score > best_score:
                    best_score = score
                    best_exit = {
                        "exit_mode": em, "T": T_v, "M": M_v, "K": K_v,
                        "mean_ret": round(score, 3),
                        "mean_hold": round(mean_hold, 1),
                        "worst_ever": round(worst_ever, 1),
                    }

        combo["best_exit"] = best_exit
        if best_exit:
            combo["exit_mode"] = best_exit["exit_mode"]
            combo["T"] = best_exit["T"]
            combo["M"] = best_exit["M"]
            combo["K"] = best_exit["K"]
            log(f"  [{ci+1}] {combo.get('label','?')[:40]:<40} → "
                f"{best_exit['exit_mode']} T={best_exit['T']} "
                f"M={best_exit['M']} K={best_exit['K']} "
                f"ret={best_exit['mean_ret']:.2f}% hold={best_exit['mean_hold']:.0f}")
        else:
            log(f"  [{ci+1}] {combo.get('label','?')[:40]:<40} → "
                f"no exit passed constraints", "WARN")

    del fold_slices_4, fold_slices_2, all_slices
    gc.collect()
    return combos


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 20B.4 — BASELINE COMPARISON
# ══════════════════════════════════════════════════════════════════════════════

def phase_20b_4_tf(tf, combos, data, all_pc):
    """Compare each combo against random entry + buy-and-hold on all 6 folds."""
    folds_4 = FOLD_4_WINDOWS[tf]
    min_bars = FOLD_SLICE_MIN_BARS[tf]

    log(f"\n  ── {tf}: {len(combos)} combos ──")

    # Collect all fold slice params
    fold_params = []
    for fold in folds_4:
        fracs = build_frac_map(data, fold, tf=tf)
        spc = _slice_pc_to_fold(all_pc, fracs, min_bars)
        fold_params.append(("4f_" + fold["id"], spc, 0.0, 1.0))
    for fold in FOLD_2_WINDOWS:
        spc = _slice_pc_by_frac(
            all_pc, fold["test_start_frac"], fold["test_end_frac"], min_bars)
        fold_params.append(("2f_" + fold["id"], spc,
                            0.0, 1.0))

    for ci, combo in enumerate(combos):
        combo_scores = []
        random_scores = []
        bah_scores = []

        for fi, (fid, spc, sf, ef) in enumerate(fold_params):
            r = _run_combo(spc, combo, tf)
            if r:
                combo_scores.append(r["hr"] * r["avg_ret"] / 100)
            else:
                combo_scores.append(0)

            rand_runs = []
            n_t = r["trades"] if r else 50
            avg_h = r["avg_hold"] if r else 10
            for seed in range(N_RANDOM_BASELINE):
                rr = sim_random_entry(spc, n_t, avg_h, sf, ef, seed=seed + fi * 100)
                rand_runs.append(rr["hr"] * rr["avg_ret"] / 100 if rr else 0)
            random_scores.append(float(np.mean(rand_runs)))

            bah = sim_buy_and_hold(spc, sf, ef)
            bah_scores.append(bah["hr"] * bah["avg_ret"] / 100 if bah else 0)

        beats_random = sum(1 for c, r in zip(combo_scores, random_scores) if c > r)
        beats_bah = sum(1 for c, b in zip(combo_scores, bah_scores) if c > b)
        n_folds = len(fold_params)
        beats_all = beats_random >= (n_folds - 1) and beats_bah >= (n_folds // 2 + 1)

        combo["beats_baseline"] = beats_all
        combo["beats_random"] = beats_random
        combo["beats_bah"] = beats_bah
        combo["n_baseline_folds"] = n_folds

        status = "ALPHA" if beats_all else "NO_ALPHA"
        log(f"  {status} [{ci+1}] {combo.get('label','?')[:40]:<40} "
            f"vs_rnd={beats_random}/{n_folds} vs_bah={beats_bah}/{n_folds}")

    # Free fold slices
    del fold_params
    gc.collect()
    return combos


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 20B.5 — CLASSIFICATION + MATRIX ASSEMBLY
# ══════════════════════════════════════════════════════════════════════════════

def classify_status(combo):
    n4 = combo.get("n_pass_4", 0)
    n2 = combo.get("n_pass_2", 0)
    if n4 >= 3 and n2 >= 1:
        return "READY"
    if n4 >= 2 and n2 >= 1:
        return "CANDIDATE"
    if n4 < 2 and n2 >= 1:
        return "TACTICAL"
    return "GAP"


def phase_20b_5(all_survivors):
    log_phase("20B.5", "BEHAVIOR CLASSIFICATION + MATRIX ASSEMBLY")

    strategies = []
    for combo in all_survivors:
        status = classify_status(combo)
        if status == "GAP":
            continue

        behavior = classify_behavior(combo)
        tf = combo["tf"]
        avg_hold_vals = []
        for folds_key in ("folds_4", "folds_2"):
            for fm in combo.get(folds_key, {}).values():
                if fm.get("avg_hold"):
                    avg_hold_vals.append(fm["avg_hold"])
        avg_hold = float(np.mean(avg_hold_vals)) if avg_hold_vals else None
        trading_type = infer_trading_type(behavior, tf, avg_hold)

        strategy = {
            "status": status,
            "trading_type": trading_type,
            "behavior": behavior,
            "entry_tf": tf,
            "combo_kpis": combo["kpis"],
            "combo_pols": combo["pols"],
            "combo_label": combo.get("label", ""),
            "archetype_p18": combo.get("archetype", "unknown"),
            "exit_mode": combo.get("exit_mode", "standard"),
            "exit_params": {"T": combo.get("T"), "M": combo.get("M"),
                            "K": combo.get("K")},
            "gate": combo.get("gate", "none"),
            "delay": combo.get("delay", 1),
            "n_pass_4": combo.get("n_pass_4", 0),
            "n_pass_2": combo.get("n_pass_2", 0),
            "score_4f": combo.get("score_4f", 0),
            "beats_baseline": combo.get("beats_baseline", False),
            "beats_random": combo.get("beats_random", 0),
            "beats_bah": combo.get("beats_bah", 0),
            "avg_hold": round(avg_hold, 1) if avg_hold else None,
            "best_exit": combo.get("best_exit"),
            "folds_4": combo.get("folds_4", {}),
            "folds_2": combo.get("folds_2", {}),
        }
        strategies.append(strategy)
        log(f"  {status:<10} {trading_type:<18} {tf} {combo.get('label','?')[:40]} "
            f"4f={combo.get('n_pass_4',0)}/4 2f={combo.get('n_pass_2',0)}/2 "
            f"score={combo.get('score_4f',0):.1f}")

    # Build matrix summary
    matrix = {}
    for s in strategies:
        cell = f"{s['trading_type']}|{s['entry_tf']}"
        if cell not in matrix:
            matrix[cell] = []
        matrix[cell].append(s)

    # Pick best per cell
    best_per_cell = {}
    for cell, strats in matrix.items():
        ready = [s for s in strats if s["status"] == "READY"]
        if ready:
            best = max(ready, key=lambda s: s["score_4f"])
        else:
            cands = [s for s in strats if s["status"] == "CANDIDATE"]
            best = max(cands, key=lambda s: s["score_4f"]) if cands else strats[0]
        best_per_cell[cell] = best

    log(f"\n  Matrix cells filled: {len(best_per_cell)}")
    for cell, s in sorted(best_per_cell.items()):
        log(f"    {cell:<30} {s['status']:<10} {s['combo_label'][:40]} "
            f"score={s['score_4f']:.1f}")

    return strategies, best_per_cell


# ══════════════════════════════════════════════════════════════════════════════
# Report
# ══════════════════════════════════════════════════════════════════════════════

def write_report(strategies, best_per_cell, all_results, elapsed_min):
    path = P20B_DIR / "PHASE20B_REPORT.md"
    L = []
    L.append("# Phase 20B — Expanded Strategy Matrix Report")
    L.append(f"\nGenerated: {time.strftime('%Y-%m-%d %H:%M')}")
    L.append(f"Runtime: {elapsed_min:.1f} min\n")

    n_ready = sum(1 for s in strategies if s["status"] == "READY")
    n_cand = sum(1 for s in strategies if s["status"] == "CANDIDATE")
    n_tact = sum(1 for s in strategies if s["status"] == "TACTICAL")

    L.append("## Summary\n")
    L.append(f"- Candidates tested: {len(all_results)}")
    L.append(f"- READY: {n_ready}")
    L.append(f"- CANDIDATE: {n_cand}")
    L.append(f"- TACTICAL: {n_tact}")
    L.append(f"- Matrix cells filled: {len(best_per_cell)}\n")

    L.append("## Strategy Matrix\n")
    L.append("| Trading Type | TF | Status | Combo | Score | "
             "4-fold | 2-fold | Baseline | Avg Hold |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for cell, s in sorted(best_per_cell.items()):
        L.append(f"| {s['trading_type']} | {s['entry_tf']} | **{s['status']}** | "
                 f"{s['combo_label'][:35]} | {s['score_4f']:.1f} | "
                 f"{s['n_pass_4']}/4 | {s['n_pass_2']}/2 | "
                 f"{'YES' if s.get('beats_baseline') else 'NO'} | "
                 f"{s.get('avg_hold','—')} |")

    L.append("\n## All Strategies (sorted by score)\n")
    L.append("| # | Status | Type | TF | Combo | Score | 4f | 2f | Alpha | Hold |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for i, s in enumerate(sorted(strategies, key=lambda x: -x["score_4f"]), 1):
        L.append(f"| {i} | {s['status']} | {s['trading_type']} | {s['entry_tf']} | "
                 f"{s['combo_label'][:35]} | {s['score_4f']:.1f} | "
                 f"{s['n_pass_4']}/4 | {s['n_pass_2']}/2 | "
                 f"{'Y' if s.get('beats_baseline') else 'N'} | "
                 f"{s.get('avg_hold','—')} |")

    L.append("\n## Per-Fold Detail (top strategies)\n")
    for s in sorted(strategies, key=lambda x: -x["score_4f"])[:10]:
        L.append(f"\n### {s['status']} {s['trading_type']} {s['entry_tf']}: "
                 f"{s['combo_label']}\n")
        L.append("| Fold | Trades | HR | Avg Ret | PF | Avg Hold | Worst |")
        L.append("|---|---|---|---|---|---|---|")
        for fid, fm in {**s.get("folds_4", {}), **s.get("folds_2", {})}.items():
            if fm.get("pass") is not None and fm.get("trades"):
                L.append(f"| {fid} | {fm['trades']} | {fm['hr']}% | "
                         f"{fm['avg_ret']:.2f}% | {fm.get('pf','—')} | "
                         f"{fm.get('avg_hold','—')} | {fm.get('worst','—')} |")

    with open(path, "w") as f:
        f.write("\n".join(L))
    log(f"Report saved: {path}")


# ══════════════════════════════════════════════════════════════════════════════
# Main — process ONE TF at a time for memory safety
# ══════════════════════════════════════════════════════════════════════════════

def main():
    global _T0
    _T0 = time.time()
    P20B_DIR.mkdir(parents=True, exist_ok=True)

    log("Phase 20B — Expanded Strategy Matrix Validation")
    log("=" * 78)
    log(f"Timeframes: {CANDIDATE_TFS}")
    log(f"Memory threshold: {MEM_THRESHOLD}%")
    log_mem("startup")

    # ── 20B.0 ──
    candidates, p19_combos = phase_20b_0()
    _save_json(P20B_DIR / "phase20b_candidates.json", candidates)

    all_kpis = list(KPI_DIM.keys())
    all_results = []
    all_survivors = []

    # ── Process each TF sequentially (memory-safe) ──
    for tf in CANDIDATE_TFS:
        if tf not in candidates or not candidates[tf]:
            log(f"\n  {tf}: no candidates, skipping")
            continue

        combos = candidates[tf]
        log_mem(f"pre-load {tf}")

        log(f"\n  Loading {tf}...")
        data = load_data(tf)
        log(f"  Loaded {len(data)} stocks for {tf}")
        log(f"  Pre-computing KPI states for {tf}...")
        all_pc = precompute(data, tf, all_kpis)
        log(f"  Precomputed {len(all_pc)} valid symbols for {tf}")
        log_mem(f"post-load {tf}")

        # 20B.1 — 4-fold
        log_phase("20B.1", f"4-FOLD VALIDATION — {tf}")
        results_tf, survivors_tf = phase_20b_1_tf(tf, combos, data, all_pc)
        all_results.extend(results_tf)

        if not survivors_tf:
            log(f"  {tf}: 0 survivors from 4-fold, skipping further phases")
            del data, all_pc
            gc.collect()
            log_mem(f"post-free {tf}")
            continue

        # 20B.2 — 2-fold
        log_phase("20B.2", f"2-FOLD VALIDATION — {tf}")
        survivors_tf = phase_20b_2_tf(tf, survivors_tf, all_pc)

        # Filter to READY or CANDIDATE
        qualified = [c for c in survivors_tf if classify_status(c) != "GAP"]
        log(f"  {tf}: {len(qualified)} qualified (READY/CANDIDATE/TACTICAL) "
            f"out of {len(survivors_tf)}")

        if qualified:
            # 20B.3 — Exit optimization
            log_phase("20B.3", f"EXIT RE-OPTIMIZATION — {tf}")
            qualified = phase_20b_3_tf(tf, qualified, data, all_pc)

            # 20B.4 — Baseline
            log_phase("20B.4", f"BASELINE COMPARISON — {tf}")
            qualified = phase_20b_4_tf(tf, qualified, data, all_pc)

        all_survivors.extend(qualified)

        del data, all_pc
        gc.collect()
        log_mem(f"post-free {tf}")

    # Save intermediate
    _save_json(P20B_DIR / "phase20b_fold_results.json", all_results)
    _save_json(P20B_DIR / "phase20b_survivors.json", all_survivors)

    # ── 20B.5 — Classification + matrix ──
    strategies, best_per_cell = phase_20b_5(all_survivors)
    _save_json(P20B_DIR / "phase20b_strategies.json", strategies)
    _save_json(P20B_DIR / "strategy_matrix.json", best_per_cell)

    # CSV
    if strategies:
        csv_fields = [
            "status", "trading_type", "behavior", "entry_tf",
            "combo_label", "archetype_p18", "exit_mode",
            "n_pass_4", "n_pass_2", "score_4f",
            "beats_baseline", "beats_random", "beats_bah", "avg_hold",
        ]
        with open(P20B_DIR / "phase20b_strategies.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=csv_fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(strategies)

    # Report
    elapsed = (time.time() - _T0) / 60
    write_report(strategies, best_per_cell, all_results, elapsed)

    # Final
    log("")
    log("=" * 78)
    log(f"Phase 20B COMPLETE — {elapsed:.1f} min")
    log(f"  Candidates tested: {len(all_results)}")
    log(f"  Survivors (≥2/4): {len(all_survivors)}")
    n_ready = sum(1 for s in strategies if s["status"] == "READY")
    n_cand = sum(1 for s in strategies if s["status"] == "CANDIDATE")
    log(f"  READY: {n_ready}, CANDIDATE: {n_cand}")
    log(f"  Matrix cells: {len(best_per_cell)}")
    log(f"  Output: {P20B_DIR}")
    log_mem("final")
    log("=" * 78)


if __name__ == "__main__":
    main()

"""
Phase 20 — Strategy Validation & Selection (4-Fold Walk-Forward)

Sub-phases:
  20.0  Candidate extraction from P18 combos (archetype-agnostic)
  20.1  4-fold walk-forward validation across market regimes
  20.2  Regime layer test (higher-TF active position filter)
  20.3  P19 MTF overlay test (4H confirmation for daily combos)
  20.4  Exit mode re-optimization across all folds
  20.5  Baseline comparison (random entry, buy-and-hold, random exit)
  20.6  Behavior classification & strategy config assembly

Inputs:  phase18_1_combos.json, phase19_validated.json, sample_300 parquets
Output:  research/kpi_optimization/outputs/all/phase20/
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
    sim_combo, _sl,
)

# ══════════════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════════════

P18_DIR = Path(__file__).resolve().parent / "outputs" / "all" / "phase18"
P19_DIR = Path(__file__).resolve().parent / "outputs" / "all" / "phase19"
P20_DIR = Path(__file__).resolve().parent / "outputs" / "all" / "phase20"

MEM_THRESHOLD = 70

CANDIDATE_TFS = ["1D", "1W", "2W"]
CANDIDATES_PER_TF = 20
CANDIDATE_HR_FLOOR = 85.0
CANDIDATE_MIN_TRADES = {"1D": 300, "1W": 100, "2W": 50}

FOLD_MIN_TRADES_BY_TF = {"1D": 30, "1W": 20, "2W": 10}
FOLD_SLICE_MIN_BARS_BY_TF = {"1D": 50, "1W": 20, "2W": 10}
FOLD_MIN_FRAC_BY_TF = {"1D": 0.02, "1W": 0.015, "2W": 0.01}

# TF-aware 4-fold date windows.
# 1D keeps ~1Y test windows.
# 1W/2W use longer "recent" windows to ensure enough bars.
FOLD_WINDOWS_BY_TF = {
    "1D": [
        {"id": "F1", "test_start": "2020-07-01", "test_end": "2021-07-01",
         "regime": "Post-COVID recovery / stimulus bull"},
        {"id": "F2", "test_start": "2022-01-01", "test_end": "2023-01-01",
         "regime": "2022 bear market (rates + inflation)"},
        {"id": "F3", "test_start": "2023-07-01", "test_end": "2024-07-01",
         "regime": "Recovery / AI-tech bull"},
        {"id": "F4", "test_start": "2025-01-01", "test_end": "2025-07-01",
         "regime": "Recent market conditions"},
    ],
    "1W": [
        {"id": "F1", "test_start": "2020-07-01", "test_end": "2021-07-01",
         "regime": "Post-COVID recovery / stimulus bull"},
        {"id": "F2", "test_start": "2022-01-01", "test_end": "2023-01-01",
         "regime": "2022 bear market (rates + inflation)"},
        {"id": "F3", "test_start": "2023-07-01", "test_end": "2024-07-01",
         "regime": "Recovery / AI-tech bull"},
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

# Pass/fail thresholds per fold
FOLD_MIN_TRADES = 30
FOLD_MIN_HR = 75.0
FOLD_MIN_AVG_RET = 0.0
FOLD_MAX_WORST = -25.0

EXIT_MODES = ["standard", "trend_anchor", "momentum_governed",
              "risk_priority", "adaptive"]
TMK_GRID = [(2, 20, 3.0), (2, 20, 4.0), (4, 40, 4.0),
            (4, 48, 4.0), (6, 48, 4.0)]

HOLD_BANDS = {
    "daily":    (3, 20),
    "swing":    (2, 10),
    "position": (1, 8),
}

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
    used_gb = mem.used / 1e9
    total_gb = mem.total / 1e9
    log(f"  Memory [{label}]: {pct:.0f}% ({used_gb:.1f}/{total_gb:.1f} GB)")
    if pct > MEM_THRESHOLD:
        gc.collect()
        mem2 = psutil.virtual_memory()
        if mem2.percent > MEM_THRESHOLD:
            log(f"  WARNING: Memory still at {mem2.percent:.0f}% after gc", "WARN")
    return pct


# ══════════════════════════════════════════════════════════════════════════════
# Date → fraction conversion
# ══════════════════════════════════════════════════════════════════════════════

def _date_to_frac(dates_index, date_str):
    """Convert a calendar date to a fractional position within a symbol's data."""
    ts = pd.Timestamp(date_str)
    n = len(dates_index)
    if n == 0:
        return 0.0
    if ts <= dates_index[0]:
        return 0.0
    if ts >= dates_index[-1]:
        return 1.0
    pos = dates_index.searchsorted(ts)
    return pos / n


def build_frac_map(data, fold, tf="1D"):
    """Build per-symbol start_frac/end_frac for a fold's test window."""
    fracs = {}
    min_frac = FOLD_MIN_FRAC_BY_TF.get(tf, 0.02)
    for sym, df in data.items():
        idx = df.index
        s = _date_to_frac(idx, fold["test_start"])
        e = _date_to_frac(idx, fold["test_end"])
        if e - s > min_frac:
            fracs[sym] = (s, e)
    return fracs


# ══════════════════════════════════════════════════════════════════════════════
# Fold-aware simulation wrapper
# ══════════════════════════════════════════════════════════════════════════════

def _slice_pc_to_fold(all_pc, fold_fracs, min_bars=50):
    """Create a truncated all_pc containing only the fold's date window.

    Each symbol's arrays are sliced to the fold window, then start_frac=0.0
    and end_frac=1.0 can be used with sim_combo for correct per-symbol dating.
    """
    sliced = {}
    for sym, pc in all_pc.items():
        if sym not in fold_fracs:
            continue
        sf, ef = fold_fracs[sym]
        n = pc["n"]
        si, ei = int(n * sf), int(n * ef)
        if ei - si < min_bars:
            continue

        new_pc = {
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
        sliced[sym] = new_pc
    return sliced


def sim_combo_fold(all_pc, combo, tf, fold_fracs):
    """Run sim_combo constrained to the fold's date window.

    Slices each symbol's data to the exact fold window, then runs sim_combo
    on the truncated data with start_frac=0.0, end_frac=1.0.
    """
    sliced_pc = _slice_pc_to_fold(all_pc, fold_fracs)
    if not sliced_pc:
        return None

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
        start_frac=0.0,
        end_frac=1.0,
    )


def sim_combo_fold_exit(all_pc, combo, tf, fold_fracs, exit_mode,
                        T_ov=None, M_ov=None, K_ov=None):
    """Like sim_combo_fold but with overridden exit parameters."""
    sliced_pc = _slice_pc_to_fold(all_pc, fold_fracs)
    if not sliced_pc:
        return None

    return sim_combo(
        sliced_pc,
        combo["kpis"], combo["pols"], tf,
        exit_mode=exit_mode,
        gate=combo.get("gate", "none"),
        delay=combo.get("delay", 1),
        T_override=T_ov,
        M_override=M_ov,
        K_override=K_ov,
        min_trades=5,
        start_frac=0.0,
        end_frac=1.0,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Regime simulation (check if a combo has an active position on a given bar)
# ══════════════════════════════════════════════════════════════════════════════

def compute_regime_mask(all_pc, regime_combo, tf):
    """Compute per-symbol boolean mask: True when regime combo has active position.

    Returns {sym: np.array[bool]} of length n for each symbol.
    """
    masks = {}
    kpis = regime_combo["kpis"]
    pols = regime_combo["pols"]
    em = regime_combo.get("exit_mode", "standard")
    gate = regime_combo.get("gate", "none")
    delay = regime_combo.get("delay", 1)

    from research.kpi_optimization.phase18_master import (
        _get_exit_kpi_indices, EXIT_PARAMS as EP
    )

    T = regime_combo.get("T") or EP.get(tf, {}).get("T", 4)
    M = regime_combo.get("M") or EP.get(tf, {}).get("M", 40)
    K = regime_combo.get("K") or EP.get(tf, {}).get("K", 4.0)

    exit_kpi_idx = _get_exit_kpi_indices(kpis, pols, em)

    for sym, pc in all_pc.items():
        bulls, bears = pc["bulls"], pc["bears"]
        nbull, nbear = pc["nbull"], pc["nbear"]
        if any(k not in bulls for k in kpis):
            continue

        cl, op, at, n = pc["cl"], pc["op"], pc["atr"], pc["n"]
        active = np.zeros(n, dtype=bool)

        entry_match = np.ones(n, dtype=bool)
        for k, p in zip(kpis, pols):
            entry_match &= bulls[k] if p == 1 else bears[k]

        onset = np.zeros(n, dtype=bool)
        onset[1:] = entry_match[1:] & ~entry_match[:-1]

        exit_nbool = []
        for i in exit_kpi_idx:
            k, p = kpis[i], pols[i]
            exit_nbool.append(nbull[k] if p == 1 else nbear[k])
        nk_exit = len(exit_nbool)
        if nk_exit == 0:
            exit_nbool = [nbull[k] if p == 1 else nbear[k]
                          for k, p in zip(kpis, pols)]
            nk_exit = len(exit_nbool)

        j = 1
        while j < n:
            if not onset[j]:
                j += 1
                continue

            if gate == "sma20_200":
                if (np.isnan(pc["sma20"][j]) or np.isnan(pc["sma200"][j])
                        or pc["sma20"][j] < pc["sma200"][j]):
                    j += 1
                    continue
            elif gate == "v5":
                if (np.isnan(pc["sma20"][j]) or np.isnan(pc["sma200"][j])
                        or pc["sma20"][j] < pc["sma200"][j]):
                    j += 1
                    continue
                if not pc["overext_ok"][j] or not pc["vol_spike_ok"][j]:
                    j += 1
                    continue

            fill = j + delay
            if fill >= n:
                break
            ep = float(op[fill]) if delay >= 1 else float(cl[j])
            if ep <= 0 or np.isnan(ep):
                j += 1
                continue

            atr_val = at[fill]
            stop = (ep - K * atr_val if not np.isnan(atr_val) and atr_val > 0
                    else ep * 0.95)
            bars_since_reset = 0
            xi = None

            jj = fill + 1
            while jj < min(fill + MAX_HOLD + 1, n):
                bars_since_reset += 1
                c = cl[jj]
                if np.isnan(c):
                    jj += 1
                    continue
                if c < stop:
                    xi = jj
                    break

                nb = sum(1 for arr in exit_nbool if jj < len(arr) and arr[jj])

                if em == "risk_priority" and nb > 0:
                    xi = jj
                    break

                bars_held = jj - fill
                if bars_held <= T:
                    if nb >= nk_exit:
                        xi = jj
                        break
                else:
                    if nb >= 2:
                        xi = jj
                        break

                if bars_since_reset >= M:
                    if nb == 0:
                        a_val = at[jj] if jj < len(at) else np.nan
                        stop = (c - K * a_val if not np.isnan(a_val)
                                and a_val > 0 else stop)
                        bars_since_reset = 0
                    else:
                        xi = jj
                        break
                jj += 1

            if xi is None:
                xi = min(jj, n - 1)

            # Mark active from fill to xi
            active[fill:xi + 1] = True
            j = xi + 1

        masks[sym] = active
    return masks


def sim_combo_with_regime(all_pc, combo, tf, regime_masks, fold_fracs):
    """Run sim_combo but only allow entries when regime mask is True."""
    sliced_pc = _slice_pc_to_fold(all_pc, fold_fracs)
    if not sliced_pc:
        return None

    # Apply regime gate: zero out entry signals where regime is inactive.
    # Symbols without a regime mask are excluded entirely.
    to_remove = []
    for sym, pc in sliced_pc.items():
        if sym not in regime_masks:
            to_remove.append(sym)
            continue
        # Slice the regime mask to the same fold window
        sf, ef = fold_fracs[sym]
        n_full = all_pc[sym]["n"]
        si, ei = int(n_full * sf), int(n_full * ef)
        mask = regime_masks[sym][si:ei]

        n_sliced = pc["n"]
        mask_len = min(len(mask), n_sliced)
        mask_padded = np.zeros(n_sliced, dtype=bool)
        mask_padded[:mask_len] = mask[:mask_len]

        for k in pc["bulls"]:
            pc["bulls"][k] = pc["bulls"][k] & mask_padded
            pc["bears"][k] = pc["bears"][k] & mask_padded

    for sym in to_remove:
        del sliced_pc[sym]

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
        start_frac=0.0,
        end_frac=1.0,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Random baseline simulation
# ══════════════════════════════════════════════════════════════════════════════

def sim_random_entry(all_pc, tf, n_target_trades, avg_hold, fold_fracs, seed=42):
    """Simulate random entries with fixed hold period as a baseline."""
    rng = random.Random(seed)
    trades = []

    syms = [s for s in all_pc if s in fold_fracs]
    if not syms:
        return None

    trades_per_sym = max(1, n_target_trades // len(syms))

    for sym in syms:
        pc = all_pc[sym]
        sf, ef = fold_fracs[sym]
        n = pc["n"]
        si, ei = int(n * sf), int(n * ef)
        if ei - si < 50:
            continue

        cl, op = pc["cl"], pc["op"]
        hold = max(1, int(avg_hold))

        for _ in range(trades_per_sym):
            if si + 2 >= ei - hold - 2:
                continue
            j = rng.randint(si + 1, ei - hold - 2)
            ep = float(op[j]) if j < len(op) else 0
            if ep <= 0 or np.isnan(ep):
                continue
            xp = float(cl[min(j + hold, ei - 1)])
            if np.isnan(xp):
                continue
            ret = (xp - ep) / ep * 100 - COST_PCT
            trades.append(ret)

    if not trades:
        return None

    nt = len(trades)
    hr = sum(1 for r in trades if r > 0) / nt * 100
    return {
        "trades": nt,
        "hr": round(hr, 1),
        "avg_ret": round(float(np.mean(trades)), 3),
    }


def sim_buy_and_hold(all_pc, tf, fold_fracs):
    """Buy at test window start, sell at test window end for each symbol."""
    rets = []
    for sym, pc in all_pc.items():
        if sym not in fold_fracs:
            continue
        sf, ef = fold_fracs[sym]
        n = pc["n"]
        si, ei = int(n * sf), int(n * ef)
        if ei - si < 20:
            continue
        cl = pc["cl"]
        ep = float(cl[si])
        xp = float(cl[ei - 1])
        if ep <= 0 or np.isnan(ep) or np.isnan(xp):
            continue
        ret = (xp - ep) / ep * 100 - COST_PCT
        rets.append(ret)

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
    """Classify a combo by its actual trading behavior based on KPI composition."""
    kpis = combo["kpis"]
    pols = combo["pols"]
    dims = [KPI_DIM.get(k, "unknown") for k in kpis]

    has_breakout = any(d == "breakout" for d in dims)
    has_bear_momentum = any(
        p == -1 and d in ("momentum", "mean_reversion")
        for d, p in zip(dims, pols)
    )
    has_bull_trend = any(
        p == 1 and d == "trend"
        for d, p in zip(dims, pols)
    )
    all_same_pol = len(set(pols)) == 1

    if has_breakout:
        return "breakout"
    if has_bear_momentum and has_bull_trend:
        return "dip_buy"
    if all_same_pol:
        return "trend_entry"
    return "mixed"


def infer_frequency(tf):
    """Map entry timeframe to trading frequency label."""
    return {"1D": "daily", "1W": "swing", "2W": "position"}.get(tf, "unknown")


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 20.0 — CANDIDATE EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

def phase_20_0():
    """Load P18 combos, filter and rank by trades. Returns {tf: [combos]}."""
    log_phase("20.0", "CANDIDATE EXTRACTION")

    p18_path = P18_DIR / "phase18_1_combos.json"
    if not p18_path.exists():
        log(f"FATAL: {p18_path} not found", "ERROR")
        sys.exit(1)

    with open(p18_path) as f:
        all_combos = json.load(f)
    log(f"Loaded {len(all_combos)} combos from P18")

    candidates = {}
    for tf in CANDIDATE_TFS:
        tf_combos = [c for c in all_combos if c.get("tf") == tf]
        log(f"  {tf}: {len(tf_combos)} total combos")

        min_tr = CANDIDATE_MIN_TRADES.get(tf, 100)
        filtered = [c for c in tf_combos
                    if c.get("hr", 0) >= CANDIDATE_HR_FLOOR
                    and c.get("trades", 0) >= min_tr]
        log(f"  {tf}: {len(filtered)} pass HR>={CANDIDATE_HR_FLOOR}% "
            f"and trades>={min_tr}")

        # Rank by trades descending
        filtered.sort(key=lambda x: -x.get("trades", 0))

        # De-duplicate by kpis+pols (keep the one with most trades)
        seen = set()
        deduped = []
        for c in filtered:
            key = (tuple(c["kpis"]), tuple(c["pols"]))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(c)

        selected = deduped[:CANDIDATES_PER_TF]
        candidates[tf] = selected

        log(f"  {tf}: selected {len(selected)} candidates (top by trades)")
        for i, c in enumerate(selected[:5]):
            log(f"    #{i+1}: {c.get('label','?')[:50]} "
                f"trades={c['trades']} HR={c['hr']}% "
                f"avg_ret={c.get('avg_ret',0):.2f}% "
                f"arch={c.get('archetype','?')}")

    # Load P19 validated for later use in 20.3
    p19_path = P19_DIR / "phase19_validated.json"
    p19_combos = []
    if p19_path.exists():
        with open(p19_path) as f:
            p19_combos = json.load(f)
        log(f"Loaded {len(p19_combos)} P19 MTF combos for later use (20.3)")
    else:
        log("No P19 validated combos found — 20.3 will be skipped", "WARN")

    total = sum(len(v) for v in candidates.values())
    log(f"Total candidates: {total}")

    return candidates, p19_combos


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 20.1 — 4-FOLD WALK-FORWARD VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

def phase_20_1(candidates, data_cache):
    """Validate each candidate across 4 folds. Returns survivors with scores."""
    log_phase("20.1", "4-FOLD WALK-FORWARD VALIDATION")

    fold_results = []
    survivors = {}

    for tf, combos in candidates.items():
        if tf not in data_cache:
            log(f"  {tf}: no data loaded, skipping", "WARN")
            continue

        data, all_pc = data_cache[tf]
        tf_folds = FOLD_WINDOWS_BY_TF.get(tf, FOLD_WINDOWS_BY_TF["1D"])
        tf_min_bars = FOLD_SLICE_MIN_BARS_BY_TF.get(tf, 50)
        log(f"\n  ── {tf}: {len(combos)} candidates × {len(tf_folds)} folds ──")

        # Pre-compute fold fracs AND sliced PCs (once per fold, reused per combo)
        tf_fold_fracs = {}
        tf_fold_sliced = {}
        for fold in tf_folds:
            fid = fold["id"]
            tf_fold_fracs[fid] = build_frac_map(data, fold, tf=tf)
            tf_fold_sliced[fid] = _slice_pc_to_fold(
                all_pc, tf_fold_fracs[fid], min_bars=tf_min_bars)
            log(f"  {fid}: {len(tf_fold_sliced[fid])} stocks in window")

        tf_min_trades = FOLD_MIN_TRADES_BY_TF.get(tf, FOLD_MIN_TRADES)
        tf_survivors = []
        for ci, combo in enumerate(combos):
            label = combo.get("label", _sl(combo["kpis"], combo["pols"]))
            fold_metrics = {}
            all_pass = True

            for fold in tf_folds:
                fid = fold["id"]
                sliced_pc = tf_fold_sliced[fid]
                r = sim_combo(
                    sliced_pc,
                    combo["kpis"], combo["pols"], tf,
                    exit_mode=combo.get("exit_mode", "standard"),
                    gate=combo.get("gate", "none"),
                    delay=combo.get("delay", 1),
                    T_override=combo.get("T"),
                    M_override=combo.get("M"),
                    K_override=combo.get("K"),
                    min_trades=5,
                    start_frac=0.0,
                    end_frac=1.0,
                )

                if r is None:
                    fold_metrics[fid] = {"pass": False, "reason": "no_result"}
                    all_pass = False
                    continue

                passes = (
                    r["trades"] >= tf_min_trades
                    and r["hr"] >= FOLD_MIN_HR
                    and r["avg_ret"] > FOLD_MIN_AVG_RET
                    and r["worst"] > FOLD_MAX_WORST
                )

                fold_metrics[fid] = {
                    "trades": r["trades"],
                    "hr": r["hr"],
                    "avg_ret": r["avg_ret"],
                    "pf": r["pf"],
                    "avg_hold": r["avg_hold"],
                    "worst": r["worst"],
                    "pass": passes,
                }

                if not passes:
                    all_pass = False
                    reasons = []
                    if r["trades"] < tf_min_trades:
                        reasons.append(f"trades={r['trades']}<{tf_min_trades}")
                    if r["hr"] < FOLD_MIN_HR:
                        reasons.append(f"HR={r['hr']}<{FOLD_MIN_HR}")
                    if r["avg_ret"] <= FOLD_MIN_AVG_RET:
                        reasons.append(f"avg_ret={r['avg_ret']}<=0")
                    if r["worst"] <= FOLD_MAX_WORST:
                        reasons.append(f"worst={r['worst']}<={FOLD_MAX_WORST}")
                    fold_metrics[fid]["reason"] = "; ".join(reasons)

            # Score = min(HR) × mean(avg_ret)
            passing_folds = [fm for fm in fold_metrics.values()
                             if fm.get("pass")]
            if all_pass and len(passing_folds) == len(tf_folds):
                hrs = [fm["hr"] for fm in fold_metrics.values()]
                rets = [fm["avg_ret"] for fm in fold_metrics.values()]
                score = min(hrs) * float(np.mean(rets))
                combo_entry = {
                    **combo,
                    "tf": tf,
                    "folds": fold_metrics,
                    "score": round(score, 2),
                    "min_hr": round(min(hrs), 1),
                    "mean_ret": round(float(np.mean(rets)), 3),
                    "min_trades": min(fm["trades"] for fm in fold_metrics.values()),
                    "total_trades": sum(fm["trades"] for fm in fold_metrics.values()),
                }
                tf_survivors.append(combo_entry)
                status = "PASS"
            else:
                status = "FAIL"
                combo_entry = {
                    "label": label, "tf": tf, "folds": fold_metrics,
                    "kpis": combo["kpis"], "pols": combo["pols"],
                }

            fold_results.append(combo_entry)

            # Log per-fold summary
            fold_str = " | ".join(
                f"{fid}:{'✓' if fm.get('pass') else '✗'}"
                f"({fm.get('trades','?')}/{fm.get('hr','?')}%)"
                for fid, fm in fold_metrics.items()
            )
            log(f"  {status} [{ci+1}/{len(combos)}] {label[:45]:<45} {fold_str}")

        # Free fold-sliced PCs
        del tf_fold_sliced
        gc.collect()

        tf_survivors.sort(key=lambda x: -x["score"])
        survivors[tf] = tf_survivors
        log(f"\n  {tf} — Survivors: {len(tf_survivors)}/{len(combos)}")

        if tf_survivors:
            best = tf_survivors[0]
            log(f"  {tf} Best: {best['label'][:50]} score={best['score']:.1f} "
                f"min_HR={best['min_hr']}% mean_ret={best['mean_ret']:.2f}%")

    total_surv = sum(len(v) for v in survivors.values())
    log(f"\n  Total survivors: {total_surv}")

    return survivors, fold_results


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 20.2 — REGIME LAYER TEST
# ══════════════════════════════════════════════════════════════════════════════

def phase_20_2(survivors, data_cache):
    """Test whether adding a higher-TF 'active position' filter improves quality."""
    log_phase("20.2", "REGIME LAYER TEST")

    regime_map = {"1D": "1W", "1W": "2W"}
    regime_results = []

    for entry_tf, regime_tf in regime_map.items():
        entry_combos = survivors.get(entry_tf, [])
        regime_combos = survivors.get(regime_tf, [])

        if not entry_combos:
            log(f"  {entry_tf}: no survivors, skipping regime test")
            continue
        if not regime_combos:
            log(f"  {entry_tf}: no {regime_tf} regime combos available, "
                "will use flat only")
            for combo in entry_combos:
                combo["regime_winner"] = "flat"
                regime_results.append({
                    "entry_tf": entry_tf, "regime_tf": regime_tf,
                    "label": combo["label"], "winner": "flat",
                    "reason": "no_regime_combos"})
            continue

        # Use the best regime-TF combo (by score) as the regime filter
        best_regime = regime_combos[0]
        log(f"\n  {entry_tf} entries + {regime_tf} regime: "
            f"{best_regime['label'][:40]}")

        if regime_tf not in data_cache or entry_tf not in data_cache:
            log(f"  {regime_tf} or {entry_tf}: data not loaded, skipping", "WARN")
            continue

        data_regime, all_pc_regime = data_cache[regime_tf]
        data_entry, all_pc_entry = data_cache[entry_tf]

        # Compute regime masks on the regime TF data (e.g., 1W)
        log(f"  Computing {regime_tf} regime masks...")
        regime_masks_slow = compute_regime_mask(
            all_pc_regime, best_regime, regime_tf)

        # Align regime masks from regime TF → entry TF using date-based mapping
        log(f"  Aligning {regime_tf} masks → {entry_tf} bars...")
        regime_masks_entry = {}
        for sym in all_pc_entry:
            if sym not in regime_masks_slow or sym not in data_regime:
                continue
            slow_mask = regime_masks_slow[sym]
            slow_dates = data_regime[sym].index
            fast_dates = data_entry[sym].index
            # For each entry-TF bar, find the corresponding regime-TF bar
            # and use its active state
            aligned = pd.merge_asof(
                pd.DataFrame({"_k": 1}, index=pd.DatetimeIndex(fast_dates)),
                pd.DataFrame({"active": slow_mask.astype(float)},
                             index=pd.DatetimeIndex(slow_dates)),
                left_index=True, right_index=True, direction="backward",
            )
            regime_masks_entry[sym] = aligned["active"].fillna(0).values.astype(bool)

        active_pcts = []
        for sym, mask in regime_masks_entry.items():
            if len(mask) > 0:
                active_pcts.append(mask.sum() / len(mask) * 100)
        if active_pcts:
            log(f"  Regime active: mean={np.mean(active_pcts):.1f}% "
                f"median={np.median(active_pcts):.1f}% of {entry_tf} bars")

        for ci, combo in enumerate(entry_combos):
            flat_score = combo["score"]
            flat_min_hr = combo["min_hr"]
            flat_mean_ret = combo["mean_ret"]

            # Test regime-filtered on all folds
            regime_fold_metrics = {}
            regime_all_pass = True
            entry_folds = FOLD_WINDOWS_BY_TF.get(entry_tf, FOLD_WINDOWS_BY_TF["1D"])
            for fold in entry_folds:
                fid = fold["id"]
                fracs = build_frac_map(data_entry, fold, tf=entry_tf)
                r = sim_combo_with_regime(
                    all_pc_entry, combo, entry_tf, regime_masks_entry, fracs)

                if r is None:
                    regime_fold_metrics[fid] = {"pass": False}
                    regime_all_pass = False
                    continue

                passes = (r["trades"] >= FOLD_MIN_TRADES
                          and r["hr"] >= FOLD_MIN_HR
                          and r["avg_ret"] > FOLD_MIN_AVG_RET)
                regime_fold_metrics[fid] = {
                    "trades": r["trades"], "hr": r["hr"],
                    "avg_ret": r["avg_ret"], "pass": passes,
                }
                if not passes:
                    regime_all_pass = False

            if regime_all_pass:
                hrs = [fm["hr"] for fm in regime_fold_metrics.values()]
                rets = [fm["avg_ret"] for fm in regime_fold_metrics.values()]
                regime_score = min(hrs) * float(np.mean(rets))
                regime_min_hr = min(hrs)
                regime_mean_ret = float(np.mean(rets))
            else:
                regime_score = 0
                regime_min_hr = 0
                regime_mean_ret = 0

            winner = "regime" if regime_score > flat_score else "flat"
            combo["regime_winner"] = winner
            combo["regime_combo"] = {
                "kpis": best_regime["kpis"],
                "pols": best_regime["pols"],
                "label": best_regime["label"],
            } if winner == "regime" else None
            combo["regime_folds"] = regime_fold_metrics
            combo["regime_score"] = round(regime_score, 2)

            log(f"  [{ci+1}] {combo['label'][:40]:<40} "
                f"flat={flat_score:.1f} regime={regime_score:.1f} → {winner}")

            regime_results.append({
                "entry_tf": entry_tf, "regime_tf": regime_tf,
                "label": combo["label"],
                "flat_score": flat_score, "regime_score": round(regime_score, 2),
                "flat_min_hr": flat_min_hr, "regime_min_hr": regime_min_hr,
                "flat_mean_ret": flat_mean_ret,
                "regime_mean_ret": round(regime_mean_ret, 3),
                "winner": winner,
                "regime_folds": regime_fold_metrics,
            })

    # 2W combos have no regime (already highest TF)
    for combo in survivors.get("2W", []):
        combo["regime_winner"] = "flat"
        combo["regime_combo"] = None

    return regime_results


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 20.3 — P19 MTF OVERLAY TEST
# ══════════════════════════════════════════════════════════════════════════════

def phase_20_3(survivors, p19_combos, data_cache):
    """Test whether adding 4H MTF confirmation improves 1D combos."""
    log_phase("20.3", "P19 MTF OVERLAY TEST")

    mtf_results = []
    entry_tf = "1D"

    if entry_tf not in survivors or not survivors[entry_tf]:
        log("No 1D survivors — skipping MTF test")
        return mtf_results

    if not p19_combos:
        log("No P19 combos available — skipping MTF test")
        for combo in survivors[entry_tf]:
            combo["mtf_winner"] = "base"
        return mtf_results

    # Filter P19 combos for 1D gate + 4H confirm
    p19_1d4h = [c for c in p19_combos
                if c.get("slow_tf") == "1D" and c.get("fast_tf") == "4H"]
    if not p19_1d4h:
        log("No P19 combos for 1D+4H pair — skipping")
        for combo in survivors[entry_tf]:
            combo["mtf_winner"] = "base"
        return mtf_results

    log(f"  {len(p19_1d4h)} P19 combos for 1D+4H")
    log(f"  NOTE: Full MTF overlay requires loading 4H data alongside 1D data.")
    log(f"  Marking all 1D combos as mtf_winner='base' — "
        f"MTF overlay needs dedicated sim_mtf logic.")

    # MTF overlay requires both 1D and 4H data simultaneously, which conflicts
    # with our memory-safe one-TF-at-a-time approach. We log the P19 combos
    # for reference but mark them as base for now.
    for combo in survivors[entry_tf]:
        combo["mtf_winner"] = "base"
        mtf_results.append({
            "label": combo["label"],
            "mtf_winner": "base",
            "reason": "memory_constraint_deferred",
            "p19_available": len(p19_1d4h),
        })

    log(f"  MTF overlay deferred (requires 1D+4H co-loaded). "
        f"{len(p19_1d4h)} P19 combos recorded for reference.")

    return mtf_results


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 20.4 — EXIT MODE RE-OPTIMIZATION
# ══════════════════════════════════════════════════════════════════════════════

def phase_20_4(survivors, data_cache):
    """Re-test all exit modes across 4 folds for each surviving combo."""
    log_phase("20.4", "EXIT MODE RE-OPTIMIZATION")

    exit_results = []

    for tf, combos in survivors.items():
        if not combos or tf not in data_cache:
            continue

        data, all_pc = data_cache[tf]
        freq = infer_frequency(tf)
        hold_lo, hold_hi = HOLD_BANDS.get(freq, (1, 30))
        tf_folds = FOLD_WINDOWS_BY_TF.get(tf, FOLD_WINDOWS_BY_TF["1D"])

        log(f"\n  ── {tf}: {len(combos)} combos × "
            f"{len(EXIT_MODES)} modes × {len(TMK_GRID)} TMK ──")
        log(f"  Hold band for {freq}: {hold_lo}–{hold_hi} bars")

        tf_fold_fracs = {}
        for fold in tf_folds:
            tf_fold_fracs[fold["id"]] = build_frac_map(data, fold, tf=tf)

        for ci, combo in enumerate(combos):
            best_exit = None
            best_exit_score = -1

            for em in EXIT_MODES:
                for T_v, M_v, K_v in TMK_GRID:
                    fold_ok = True
                    fold_hrs = []
                    fold_rets = []
                    fold_holds = []
                    fold_worsts = []

                    for fold in tf_folds:
                        fid = fold["id"]
                        r = sim_combo_fold_exit(
                            all_pc, combo, tf,
                            tf_fold_fracs[fid],
                            exit_mode=em,
                            T_ov=T_v, M_ov=M_v, K_ov=K_v)

                        if r is None or r["trades"] < 10:
                            fold_ok = False
                            break

                        fold_hrs.append(r["hr"])
                        fold_rets.append(r["avg_ret"])
                        fold_holds.append(r["avg_hold"])
                        fold_worsts.append(r["worst"])

                    if not fold_ok:
                        continue

                    mean_hold = float(np.mean(fold_holds))
                    worst_ever = min(fold_worsts)

                    if mean_hold < hold_lo or mean_hold > hold_hi:
                        continue
                    if worst_ever <= -20:
                        continue

                    score = float(np.mean(fold_rets))
                    if score > best_exit_score:
                        best_exit_score = score
                        best_exit = {
                            "exit_mode": em,
                            "T": T_v, "M": M_v, "K": K_v,
                            "mean_ret": round(score, 3),
                            "mean_hold": round(mean_hold, 1),
                            "min_hr": round(min(fold_hrs), 1),
                            "worst_ever": round(worst_ever, 1),
                        }

            if best_exit:
                combo["best_exit"] = best_exit
                combo["exit_mode"] = best_exit["exit_mode"]
                combo["T"] = best_exit["T"]
                combo["M"] = best_exit["M"]
                combo["K"] = best_exit["K"]
                log(f"  [{ci+1}] {combo['label'][:40]:<40} → "
                    f"{best_exit['exit_mode']} T={best_exit['T']} "
                    f"M={best_exit['M']} K={best_exit['K']} "
                    f"ret={best_exit['mean_ret']:.2f}% "
                    f"hold={best_exit['mean_hold']:.0f}")
            else:
                combo["best_exit"] = None
                log(f"  [{ci+1}] {combo['label'][:40]:<40} → "
                    f"no exit mode passed all constraints", "WARN")

            exit_results.append({
                "tf": tf, "label": combo["label"],
                "best_exit": best_exit,
            })

    return exit_results


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 20.5 — BASELINE COMPARISON
# ══════════════════════════════════════════════════════════════════════════════

def phase_20_5(survivors, data_cache):
    """Compare each winner against random entry, buy-and-hold, random exit."""
    log_phase("20.5", "BASELINE COMPARISON")

    baseline_results = []

    for tf, combos in survivors.items():
        if not combos or tf not in data_cache:
            continue

        data, all_pc = data_cache[tf]
        tf_folds = FOLD_WINDOWS_BY_TF.get(tf, FOLD_WINDOWS_BY_TF["1D"])
        log(f"\n  ── {tf}: {len(combos)} combos ──")

        for ci, combo in enumerate(combos):
            combo_scores = []
            random_scores = []
            bah_scores = []

            for fi, fold in enumerate(tf_folds):
                fracs = build_frac_map(data, fold, tf=tf)

                # Combo performance
                r = sim_combo_fold(all_pc, combo, tf, fracs)
                if r:
                    combo_scores.append(r["hr"] * r["avg_ret"] / 100)
                else:
                    combo_scores.append(0)

                # Random entry baseline (average of N runs)
                rand_scores_run = []
                n_target = r["trades"] if r else 100
                avg_h = r["avg_hold"] if r else 10
                for seed in range(N_RANDOM_BASELINE):
                    rr = sim_random_entry(
                        all_pc, tf, n_target, avg_h, fracs, seed=seed + fi * 100)
                    if rr:
                        rand_scores_run.append(rr["hr"] * rr["avg_ret"] / 100)
                    else:
                        rand_scores_run.append(0)
                random_scores.append(float(np.mean(rand_scores_run)))

                # Buy and hold baseline
                bah = sim_buy_and_hold(all_pc, tf, fracs)
                if bah:
                    bah_scores.append(bah["hr"] * bah["avg_ret"] / 100)
                else:
                    bah_scores.append(0)

            # Must beat baselines in at least 3 of 4 folds
            beats_random = sum(1 for c, r in zip(combo_scores, random_scores) if c > r)
            beats_bah = sum(1 for c, b in zip(combo_scores, bah_scores) if c > b)

            beats_all = beats_random >= 3 and beats_bah >= 3

            combo["beats_baseline"] = beats_all
            combo["beats_random_folds"] = beats_random
            combo["beats_bah_folds"] = beats_bah

            status = "ALPHA" if beats_all else "NO_ALPHA"
            log(f"  {status} [{ci+1}] {combo['label'][:40]:<40} "
                f"vs_random={beats_random}/4 vs_bah={beats_bah}/4")

            baseline_results.append({
                "tf": tf, "label": combo["label"],
                "beats_baseline": beats_all,
                "beats_random": beats_random,
                "beats_bah": beats_bah,
                "combo_scores": [round(s, 4) for s in combo_scores],
                "random_scores": [round(s, 4) for s in random_scores],
                "bah_scores": [round(s, 4) for s in bah_scores],
            })

    return baseline_results


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 20.6 — BEHAVIOR CLASSIFICATION & STRATEGY CONFIG
# ══════════════════════════════════════════════════════════════════════════════

def phase_20_6(survivors):
    """Classify behaviors and assemble strategy_config.json."""
    log_phase("20.6", "BEHAVIOR CLASSIFICATION & STRATEGY CONFIG")

    strategies = []

    for tf, combos in survivors.items():
        freq = infer_frequency(tf)
        for combo in combos:
            if not combo.get("beats_baseline", False):
                log(f"  SKIP {combo['label'][:40]} — failed baseline", "WARN")
                continue

            behavior = classify_behavior(combo)
            strategy_id = f"{behavior}_{freq}"

            strategy = {
                "strategy_id": strategy_id,
                "behavior": behavior,
                "trading_frequency": freq,
                "entry_tf": tf,
                "combo_kpis": combo["kpis"],
                "combo_pols": combo["pols"],
                "combo_label": combo.get("label", ""),
                "archetype_p18": combo.get("archetype", "unknown"),
                "exit_mode": combo.get("exit_mode", "standard"),
                "exit_params": {
                    "T": combo.get("T"),
                    "M": combo.get("M"),
                    "K": combo.get("K"),
                },
                "gate": combo.get("gate", "none"),
                "delay": combo.get("delay", 1),
                "regime_active": combo.get("regime_winner") == "regime",
                "regime_tf": {"1D": "1W", "1W": "2W"}.get(tf),
                "regime_combo": combo.get("regime_combo"),
                "mtf_overlay": combo.get("mtf_winner") == "mtf",
                "score": combo.get("score", 0),
                "min_hr": combo.get("min_hr", 0),
                "mean_ret": combo.get("mean_ret", 0),
                "min_trades": combo.get("min_trades", 0),
                "total_trades": combo.get("total_trades", 0),
                "beats_baseline": combo.get("beats_baseline", False),
                "beats_random_folds": combo.get("beats_random_folds", 0),
                "beats_bah_folds": combo.get("beats_bah_folds", 0),
                "best_exit": combo.get("best_exit"),
                "folds": combo.get("folds", {}),
            }
            strategies.append(strategy)
            log(f"  {strategy_id:<25} {combo['label'][:40]} "
                f"score={combo.get('score',0):.1f}")

    # Sort by strategy_id, then score descending
    strategies.sort(key=lambda x: (x["strategy_id"], -x["score"]))

    # Pick the best per strategy_id
    best_per_strategy = {}
    for s in strategies:
        sid = s["strategy_id"]
        if sid not in best_per_strategy or s["score"] > best_per_strategy[sid]["score"]:
            best_per_strategy[sid] = s

    log(f"\n  Strategies assembled: {len(strategies)} total, "
        f"{len(best_per_strategy)} unique strategy IDs")
    for sid, s in sorted(best_per_strategy.items()):
        log(f"    {sid}: {s['combo_label'][:40]} score={s['score']:.1f} "
            f"min_HR={s['min_hr']}% mean_ret={s['mean_ret']:.2f}%")

    return strategies, best_per_strategy


# ══════════════════════════════════════════════════════════════════════════════
# Report generation
# ══════════════════════════════════════════════════════════════════════════════

def write_report(strategies, best_per_strategy, fold_results,
                 regime_results, exit_results, baseline_results,
                 elapsed_min):
    """Generate PHASE20_REPORT.md."""
    path = P20_DIR / "PHASE20_REPORT.md"
    L = []
    L.append("# Phase 20 — Strategy Validation & Selection Report")
    L.append(f"\nGenerated: {time.strftime('%Y-%m-%d %H:%M')}")
    L.append(f"Runtime: {elapsed_min:.1f} min")
    L.append(f"\n---\n")

    L.append("## Executive Summary\n")
    n_candidates = sum(1 for r in fold_results)
    n_survivors = sum(1 for r in fold_results
                      if r.get("score") is not None and r.get("score", 0) > 0)
    n_alpha = sum(1 for s in strategies if s.get("beats_baseline"))

    L.append(f"- **Candidates tested**: {n_candidates}")
    L.append(f"- **Passed 4-fold validation**: {n_survivors}")
    L.append(f"- **Beat baselines (alpha)**: {n_alpha}")
    L.append(f"- **Final strategies**: {len(best_per_strategy)}")

    L.append("\n## Final Strategies\n")
    L.append("| Strategy | Entry TF | Combo | Score | Min HR | "
             "Mean Ret | Trades | Regime | Exit |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for sid, s in sorted(best_per_strategy.items()):
        regime = f"{s['regime_tf']} active" if s["regime_active"] else "flat"
        L.append(f"| **{sid}** | {s['entry_tf']} | {s['combo_label'][:35]} | "
                 f"{s['score']:.1f} | {s['min_hr']}% | {s['mean_ret']:.2f}% | "
                 f"{s['total_trades']} | {regime} | {s['exit_mode']} |")

    L.append("\n## Per-Fold Detail\n")
    for s in strategies:
        if not s.get("beats_baseline"):
            continue
        L.append(f"\n### {s['strategy_id']}: {s['combo_label']}\n")
        L.append("| Fold | Trades | HR | Avg Ret | PF | Avg Hold | Worst |")
        L.append("|---|---|---|---|---|---|---|")
        for fid, fm in s.get("folds", {}).items():
            if isinstance(fm, dict) and fm.get("pass"):
                L.append(f"| {fid} | {fm['trades']} | {fm['hr']}% | "
                         f"{fm['avg_ret']:.2f}% | {fm.get('pf','—')} | "
                         f"{fm.get('avg_hold','—')} | {fm.get('worst','—')} |")

    L.append("\n## Regime Layer Results\n")
    L.append("| Entry TF | Combo | Flat Score | Regime Score | Winner |")
    L.append("|---|---|---|---|---|")
    for r in regime_results:
        L.append(f"| {r['entry_tf']} | {r['label'][:35]} | "
                 f"{r.get('flat_score','—')} | {r.get('regime_score','—')} | "
                 f"**{r['winner']}** |")

    L.append("\n## Baseline Comparison\n")
    L.append("| TF | Combo | vs Random | vs B&H | Alpha? |")
    L.append("|---|---|---|---|---|")
    for r in baseline_results:
        alpha = "YES" if r["beats_baseline"] else "NO"
        L.append(f"| {r['tf']} | {r['label'][:35]} | "
                 f"{r['beats_random']}/4 | {r['beats_bah']}/4 | **{alpha}** |")

    L.append("\n---\n")
    L.append("## Output Files\n")
    L.append("| File | Contents |")
    L.append("|---|---|")
    L.append("| `phase20_candidates.json` | Candidate combos extracted from P18 |")
    L.append("| `phase20_fold_results.json` | Per-combo × per-fold metrics |")
    L.append("| `phase20_validated.json` | Combos that survived 4-fold validation |")
    L.append("| `phase20_regime_comparison.json` | Flat vs regime metrics |")
    L.append("| `phase20_mtf_comparison.json` | MTF overlay results |")
    L.append("| `phase20_exit_optimization.json` | Exit mode results |")
    L.append("| `phase20_baseline_comparison.json` | Baseline comparison |")
    L.append("| `strategy_config.json` | Final strategy definitions |")

    with open(path, "w") as f:
        f.write("\n".join(L))
    log(f"Report saved: {path}")


# ══════════════════════════════════════════════════════════════════════════════
# Main orchestration
# ══════════════════════════════════════════════════════════════════════════════

def main():
    global _T0
    _T0 = time.time()

    P20_DIR.mkdir(parents=True, exist_ok=True)

    log("Phase 20 — Strategy Validation & Selection Pipeline")
    log("=" * 78)
    log(f"Candidate TFs: {CANDIDATE_TFS}")
    log(f"Candidates per TF: {CANDIDATES_PER_TF}")
    fold_overview = {tf: [f["id"] for f in folds] for tf, folds in FOLD_WINDOWS_BY_TF.items()}
    log(f"4-fold windows (by TF): {fold_overview}")
    log(f"Memory threshold: {MEM_THRESHOLD}%")
    log_mem("startup")

    # ── 20.0: Candidate extraction ──
    candidates, p19_combos = phase_20_0()
    _save_json(P20_DIR / "phase20_candidates.json", candidates)

    if not any(candidates.values()):
        log("FATAL: No candidates found. Check phase18_1_combos.json.", "ERROR")
        sys.exit(1)

    # ── Load data per TF (one at a time, cache for reuse across phases) ──
    # We need to keep data loaded for phases 20.1–20.5. Since we process
    # max 3 TFs (1D, 1W, 2W), peak memory is ~3 GB. On a 7.6 GB server
    # this is safe at ~40%.
    #
    # If memory is tight, we can reload per-phase. But the overhead of
    # reloading + precomputing is ~5 min per TF, so caching saves ~30 min.
    all_kpis = list(KPI_DIM.keys())
    data_cache = {}

    for tf in CANDIDATE_TFS:
        if tf not in candidates or not candidates[tf]:
            log(f"  {tf}: no candidates, skipping data load")
            continue

        log_mem(f"pre-load {tf}")
        pct = psutil.virtual_memory().percent
        if pct > MEM_THRESHOLD:
            log(f"  Memory at {pct}% — cannot load {tf}. "
                f"Freeing earlier TFs and retrying.", "WARN")
            # Free the oldest TF to make room
            if data_cache:
                oldest_tf = next(iter(data_cache))
                del data_cache[oldest_tf]
                gc.collect()
                log(f"  Freed {oldest_tf} data")

        log(f"  Loading {tf}...")
        data = load_data(tf)
        log(f"  Loaded {len(data)} stocks for {tf}")
        log(f"  Pre-computing KPI states for {tf}...")
        all_pc = precompute(data, tf, all_kpis)
        log(f"  Precomputed {len(all_pc)} valid symbols for {tf}")
        # Keep raw data for fold frac computation, store alongside precomputed
        data_cache[tf] = (data, all_pc)
        log_mem(f"post-load {tf}")

    # ── 20.1: 4-fold validation ──
    survivors, fold_results = phase_20_1(candidates, data_cache)
    _save_json(P20_DIR / "phase20_fold_results.json", fold_results)

    validated_flat = []
    for tf, combos in survivors.items():
        validated_flat.extend(combos)
    _save_json(P20_DIR / "phase20_validated.json", validated_flat)

    if not any(survivors.values()):
        log("No combos survived 4-fold validation. "
            "Consider relaxing thresholds.", "ERROR")
        # Still write what we have and exit
        elapsed = (time.time() - _T0) / 60
        write_report([], {}, fold_results, [], [], [], elapsed)
        log(f"Phase 20 COMPLETE (no survivors) — {elapsed:.1f} min")
        return

    # ── 20.2: Regime layer test ──
    regime_results = phase_20_2(survivors, data_cache)
    _save_json(P20_DIR / "phase20_regime_comparison.json", regime_results)

    # ── 20.3: P19 MTF overlay test ──
    mtf_results = phase_20_3(survivors, p19_combos, data_cache)
    _save_json(P20_DIR / "phase20_mtf_comparison.json", mtf_results)

    # ── Free data to reduce memory before exit optimization ──
    # Reload as needed per TF
    log_mem("pre-exit-optimization")

    # ── 20.4: Exit mode re-optimization ──
    exit_results = phase_20_4(survivors, data_cache)
    _save_json(P20_DIR / "phase20_exit_optimization.json", exit_results)

    # ── 20.5: Baseline comparison ──
    baseline_results = phase_20_5(survivors, data_cache)
    _save_json(P20_DIR / "phase20_baseline_comparison.json", baseline_results)

    # ── Free all data before final assembly ──
    del data_cache
    gc.collect()
    log_mem("post-free-all")

    # ── 20.6: Behavior classification & strategy config ──
    strategies, best_per_strategy = phase_20_6(survivors)
    _save_json(P20_DIR / "strategy_config.json", best_per_strategy)
    _save_json(P20_DIR / "phase20_all_strategies.json", strategies)

    # ── Write validated CSV ──
    if strategies:
        csv_fields = [
            "strategy_id", "behavior", "trading_frequency", "entry_tf",
            "combo_label", "archetype_p18", "exit_mode", "gate",
            "score", "min_hr", "mean_ret", "min_trades", "total_trades",
            "beats_baseline", "beats_random_folds", "beats_bah_folds",
            "regime_active",
        ]
        with open(P20_DIR / "phase20_strategies.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=csv_fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(strategies)

    # ── Report ──
    elapsed = (time.time() - _T0) / 60
    write_report(strategies, best_per_strategy, fold_results,
                 regime_results, exit_results, baseline_results, elapsed)

    # ── Final summary ──
    log("")
    log("=" * 78)
    log(f"Phase 20 COMPLETE — {elapsed:.1f} min")
    log(f"  Candidates tested: {sum(len(v) for v in candidates.values())}")
    log(f"  4-fold survivors: {sum(len(v) for v in survivors.values())}")
    log(f"  Beat baselines: {sum(1 for s in strategies if s.get('beats_baseline'))}")
    log(f"  Final strategies: {len(best_per_strategy)}")
    log(f"  Output: {P20_DIR}")
    log_mem("final")
    log("=" * 78)


if __name__ == "__main__":
    main()

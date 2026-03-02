"""
Phase 17 — Unified Strategy Archetype Optimization

Stages executed in this script:
  1  Archetype-specific combo search (C3-C6, multi-polarity)
  2  Exit rule optimization per archetype top-N
  3  Entry gate + delay sweep
  4  Walk-forward validation (OOS-A / OOS-B)
  5  Cross-strategy comparison + final recommendation

Dataset: sample_300 enriched parquets (requires Stoof columns).
Output:  research/kpi_optimization/outputs/all/phase17/
"""
from __future__ import annotations

import csv
import json
import sys
import time
from collections import defaultdict
from itertools import combinations, product
from math import comb as _comb
from pathlib import Path

import numpy as np
import pandas as pd

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from trading_dashboard.kpis.catalog import compute_kpi_state_map
from trading_dashboard.kpis.rules import STATE_BULL, STATE_BEAR, STATE_NEUTRAL

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

ENRICHED_DIR = REPO_DIR / "research" / "data" / "feature_store" / "enriched" / "sample_300" / "stock_data"
OUTPUTS_DIR = Path(__file__).resolve().parent / "outputs" / "all" / "phase17"
STEP0_DIR = OUTPUTS_DIR / "step0"

ATR_PERIOD = 14
MAX_HOLD = 500
COMMISSION = 0.001
SLIPPAGE = 0.005
COST_PCT = (COMMISSION + SLIPPAGE) * 100
OOS_START = 0.70     # search on last 30% of data (matches Phase 16)
OOS_B_START = 0.85   # last 15% = OOS-B holdout
TOP_N = 5            # top combos per archetype per TF
HR_FLOOR = 55.0

EXIT_PARAMS = {
    "4H": {"T": 4, "M": 48, "K": 4.0},
    "1D": {"T": 4, "M": 40, "K": 4.0},
    "1W": {"T": 2, "M": 20, "K": 4.0},
    "2W": {"T": 2, "M": 10, "K": 4.0},
    "1M": {"T": 2, "M": 6,  "K": 4.0},
}

ALL_TFS = ["4H", "1D", "1W", "2W", "1M"]

# ══════════════════════════════════════════════════════════════════════════════
# KPI Dimension Mapping
# ══════════════════════════════════════════════════════════════════════════════

KPI_DIM = {
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

# ══════════════════════════════════════════════════════════════════════════════
# Strategy Archetypes
# ══════════════════════════════════════════════════════════════════════════════

ARCHETYPES = {
    "A_trend": {
        "label": "Trend Following",
        "anchor_dim": "trend",
        "pool_dims": ["trend", "momentum", "relative_strength"],
        "polarity": "bull_only",
        "exit_mode": "standard",
        "description": "All KPIs must be bullish. Classic trend-following.",
    },
    "B_dip": {
        "label": "Mean Reversion / Buy the Dip",
        "anchor_dim": "trend",
        "pool_dims": ["trend", "mean_reversion", "breakout", "momentum"],
        "polarity": "mixed",
        "exit_mode": "trend_anchor",
        "description": "Trend anchor bullish + contrarian KPIs bearish (oversold).",
    },
    "C_breakout": {
        "label": "Breakout / Momentum Surge",
        "anchor_dim": "breakout",
        "pool_dims": ["breakout", "momentum", "relative_strength"],
        "polarity": "bull_only",
        "exit_mode": "momentum_governed",
        "description": "Breakout signal triggers, momentum confirms.",
    },
    "D_risk": {
        "label": "Trend + Risk-Managed",
        "anchor_dim": "trend",
        "pool_dims": ["trend", "risk_exit", "momentum"],
        "polarity": "bull_only",
        "exit_mode": "risk_priority",
        "description": "Trend-following with risk KPIs for early exit.",
    },
    "E_mixed": {
        "label": "Full Mixed / Unconstrained",
        "anchor_dim": None,
        "pool_dims": list(set(KPI_DIM.values())),
        "polarity": "mixed",
        "exit_mode": "adaptive",
        "description": "Any KPI can enter, adaptive exit based on combo profile.",
    },
}

KPI_SHORT = {
    "Nadaraya-Watson Smoother": "NWSm", "cRSI": "cRSI", "OBVOSC_LB": "OBVOsc",
    "Madrid Ribbon": "Madrid", "GK Trend Ribbon": "GKTr", "Volume + MA20": "Vol>MA",
    "DEMA": "DEMA", "Donchian Ribbon": "Donch", "TuTCI": "TuTCI", "MA Ribbon": "MARib",
    "Ichimoku": "Ichi", "WT_LB": "WT", "SQZMOM_LB": "SQZ", "Stoch_MTM": "Stoch",
    "CM_Ult_MacD_MFT": "MACD", "ADX & DI": "ADX", "GMMA": "GMMA", "Mansfield RS": "Mansf",
    "SR Breaks": "SRBrk", "SuperTrend": "SupTr", "UT Bot Alert": "UTBot", "CM_P-SAR": "PSAR",
    "BB 30": "BB30", "Nadaraya-Watson Envelop (MAE)": "NWE-MAE",
    "Nadaraya-Watson Envelop (STD)": "NWE-STD", "Impulse Trend": "Impulse",
    "Nadaraya-Watson Envelop (Repainting)": "NWE-Rep",
    "RSI Strength & Consolidation Zones (Zeiierman)": "Zeiier",
    "MACD_BL": "MACD_BL", "WT_LB_BL": "WT_BL", "OBVOSC_BL": "OBV_BL",
    "CCI_Chop_BB_v1": "CCIv1", "CCI_Chop_BB_v2": "CCIv2", "ADX_DI_BL": "ADX_BL",
    "LuxAlgo_Norm_v1": "Luxv1", "LuxAlgo_Norm_v2": "Luxv2",
    "Risk_Indicator": "Risk", "PAI": "PAI",
}


def _s(k): return KPI_SHORT.get(k, k[:8])
def _sl(kpis, pols=None):
    if pols is None:
        return "+".join(_s(k) for k in kpis)
    return "+".join(f"{_s(k)}({'+' if p == 1 else '-'})" for k, p in zip(kpis, pols))


# ══════════════════════════════════════════════════════════════════════════════
# Data Loading & Precomputation
# ══════════════════════════════════════════════════════════════════════════════

def compute_atr(df, period=ATR_PERIOD):
    h, lo = df["High"].to_numpy(float), df["Low"].to_numpy(float)
    pc = np.roll(df["Close"].to_numpy(float), 1)
    pc[0] = np.nan
    tr = np.maximum(h - lo, np.maximum(np.abs(h - pc), np.abs(lo - pc)))
    return pd.Series(tr).rolling(window=period, min_periods=1).mean().to_numpy(float)


def load_data(tf):
    data = {}
    for f in sorted(ENRICHED_DIR.glob(f"*_{tf}.parquet")):
        sym = f.stem.rsplit(f"_{tf}", 1)[0]
        if sym in data:
            continue
        try:
            df = pd.read_parquet(f)
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index, errors="coerce")
            if hasattr(df.index, "tz") and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            df = df.sort_index()
            if len(df) >= 100 and "Close" in df.columns:
                data[sym] = df
        except Exception:
            continue
    return data


def precompute(data, tf, all_kpis):
    """Pre-compute per-KPI boolean arrays + gates for fast combo iteration."""
    all_pc = {}
    for sym, df in data.items():
        sm = compute_kpi_state_map(df)

        bulls = {}
        bears = {}
        nbull = {}
        nbear = {}
        for k in all_kpis:
            if k in sm:
                s = sm[k].to_numpy(int)
                bulls[k] = (s == STATE_BULL)
                bears[k] = (s == STATE_BEAR)
                nbull[k] = (s != STATE_BULL)
                nbear[k] = (s != STATE_BEAR)
        if not bulls:
            continue

        n = len(df)
        cl = df["Close"].to_numpy(float)
        op = df["Open"].to_numpy(float) if "Open" in df.columns else cl.copy()
        at = compute_atr(df, ATR_PERIOD)
        vol = df["Volume"].to_numpy(float) if "Volume" in df.columns else np.zeros(n)

        cl_s = pd.Series(cl)
        sma20 = cl_s.rolling(20, min_periods=20).mean().to_numpy(float)
        sma200 = cl_s.rolling(200, min_periods=200).mean().to_numpy(float)

        overext_ok = np.ones(n, dtype=bool)
        if tf in ("1W", "2W", "1M") and n > 5:
            ref = np.empty(n, dtype=float)
            ref[:5] = np.nan
            ref[5:] = cl[:-5]
            with np.errstate(divide="ignore", invalid="ignore"):
                pct = (cl - ref) / ref * 100
            overext_ok = ~(pct > 15.0)

        vol_spike_ok = np.ones(n, dtype=bool)
        if vol.sum() > 0:
            vol_ma = pd.Series(vol).rolling(20, min_periods=1).mean().to_numpy(float)
            with np.errstate(invalid="ignore"):
                s_raw = (vol >= 1.5 * vol_ma).astype(float)
            s_raw = np.nan_to_num(s_raw, nan=0.0)
            vol_spike_ok = pd.Series(s_raw).rolling(5, min_periods=1).max().to_numpy().astype(bool)

        all_pc[sym] = {
            "bulls": bulls, "bears": bears,
            "nbull": nbull, "nbear": nbear,
            "cl": cl, "op": op, "atr": at, "n": n,
            "sma20": sma20, "sma200": sma200,
            "overext_ok": overext_ok, "vol_spike_ok": vol_spike_ok,
        }
    return all_pc


# ══════════════════════════════════════════════════════════════════════════════
# Simulation Engine
# ══════════════════════════════════════════════════════════════════════════════

def sim_archetype(all_pc, combo_kpis, combo_pols, tf, *,
                  exit_mode="standard", gate="none", delay=1,
                  min_trades=10, start_frac=0.0, end_frac=1.0):
    """
    Simulate a combo with mixed polarities and archetype-specific exit.
    Uses pre-computed boolean arrays for speed.
    """
    T = EXIT_PARAMS[tf]["T"]
    M = EXIT_PARAMS[tf]["M"]
    K = EXIT_PARAMS[tf]["K"]
    trades = []

    exit_kpi_indices = _get_exit_kpi_indices(combo_kpis, combo_pols, exit_mode)

    for sym, pc in all_pc.items():
        bulls = pc["bulls"]
        bears = pc["bears"]
        nbull = pc["nbull"]
        nbear = pc["nbear"]

        if any(k not in bulls for k in combo_kpis):
            continue

        cl = pc["cl"]; op = pc["op"]; at = pc["atr"]; n = pc["n"]
        si = int(n * start_frac)
        ei = int(n * end_frac)
        if ei - si < 50:
            continue

        entry_match = np.ones(n, dtype=bool)
        for k, p in zip(combo_kpis, combo_pols):
            if p == 1:
                entry_match &= bulls[k]
            else:
                entry_match &= bears[k]

        onset = np.zeros(n, dtype=bool)
        onset[1:] = entry_match[1:] & ~entry_match[:-1]

        exit_nbool = []
        for i in exit_kpi_indices:
            k = combo_kpis[i]
            p = combo_pols[i]
            exit_nbool.append(nbull[k] if p == 1 else nbear[k])

        nk_exit = len(exit_nbool)
        if nk_exit == 0:
            exit_nbool = [nbull[k] if p == 1 else nbear[k] for k, p in zip(combo_kpis, combo_pols)]
            nk_exit = len(exit_nbool)

        j = si + 1
        while j < ei:
            if not onset[j]:
                j += 1; continue

            if gate == "sma20_200":
                if np.isnan(pc["sma20"][j]) or np.isnan(pc["sma200"][j]) or pc["sma20"][j] < pc["sma200"][j]:
                    j += 1; continue
            elif gate == "v5":
                if np.isnan(pc["sma20"][j]) or np.isnan(pc["sma200"][j]) or pc["sma20"][j] < pc["sma200"][j]:
                    j += 1; continue
                if not pc["overext_ok"][j] or not pc["vol_spike_ok"][j]:
                    j += 1; continue

            fill = j + delay
            if fill >= ei:
                break
            ep = float(op[fill]) if delay >= 1 else float(cl[j])
            if ep <= 0 or np.isnan(ep):
                j += 1; continue

            atr_val = at[fill]
            stop = ep - K * atr_val if not np.isnan(atr_val) and atr_val > 0 else ep * 0.95
            bars_since_reset = 0
            xi = None

            jj = fill + 1
            while jj < min(fill + MAX_HOLD + 1, ei):
                bars_since_reset += 1
                c = cl[jj]
                if np.isnan(c):
                    jj += 1; continue

                if c < stop:
                    xi = jj; break

                nb = sum(1 for arr in exit_nbool if jj < len(arr) and arr[jj])

                if exit_mode == "risk_priority" and nb > 0:
                    xi = jj; break

                bars_held = jj - fill
                if bars_held <= T:
                    if nb >= nk_exit:
                        xi = jj; break
                else:
                    if nb >= 2:
                        xi = jj; break

                if bars_since_reset >= M:
                    if nb == 0:
                        a_val = at[jj] if jj < len(at) else np.nan
                        stop = c - K * a_val if not np.isnan(a_val) and a_val > 0 else stop
                        bars_since_reset = 0
                    else:
                        xi = jj; break

                jj += 1

            if xi is None:
                xi = min(jj, ei - 1)
            is_open = (xi >= ei - 1 and jj >= ei)
            h = xi - fill
            if h <= 0 or is_open:
                j += 1; continue

            exit_fill = min(xi + 1, ei - 1)
            xp = float(op[exit_fill]) if exit_fill != xi else float(cl[xi])
            ret = (xp - ep) / ep * 100 - COST_PCT
            trades.append((ret, h))

            j = xi + 1

    if len(trades) < min_trades:
        return None

    rets = [t[0] for t in trades]
    nt = len(rets)
    hr = sum(1 for r in rets if r > 0) / nt * 100
    wi = sum(r for r in rets if r > 0)
    lo = abs(sum(r for r in rets if r <= 0))
    pf = round(wi / lo if lo > 0 else 999, 2)
    return {
        "trades": nt,
        "hr": round(hr, 1),
        "avg_ret": round(float(np.mean(rets)), 3),
        "pnl": round(sum(rets), 1),
        "pf": pf,
        "avg_hold": round(float(np.mean([t[1] for t in trades])), 1),
        "worst": round(min(rets), 1),
        "kpis": combo_kpis,
        "pols": combo_pols,
        "label": _sl(combo_kpis, combo_pols),
    }


def _get_exit_kpi_indices(combo_kpis, combo_pols, exit_mode):
    """Return indices into combo_kpis that govern exit."""
    if exit_mode == "trend_anchor":
        return [i for i, k in enumerate(combo_kpis) if KPI_DIM.get(k) == "trend"]
    elif exit_mode == "momentum_governed":
        return [i for i, k in enumerate(combo_kpis) if KPI_DIM.get(k) in ("momentum", "breakout")]
    elif exit_mode == "risk_priority":
        return [i for i, k in enumerate(combo_kpis) if KPI_DIM.get(k) == "risk_exit"]
    elif exit_mode == "adaptive":
        dims = set(KPI_DIM.get(k) for k in combo_kpis)
        if "risk_exit" in dims:
            return [i for i, k in enumerate(combo_kpis) if KPI_DIM.get(k) in ("risk_exit", "trend")]
        elif "mean_reversion" in dims:
            return [i for i, k in enumerate(combo_kpis) if KPI_DIM.get(k) == "trend"]
        return list(range(len(combo_kpis)))
    return list(range(len(combo_kpis)))


# ══════════════════════════════════════════════════════════════════════════════
# Combo Generation (with mixed-polarity support)
# ══════════════════════════════════════════════════════════════════════════════

def _load_eligible_kpis(tf):
    """Load eligible KPIs from Step 0 coverage report."""
    cov_path = STEP0_DIR / "kpi_coverage.json"
    drop_path = STEP0_DIR / "drop_kpis.json"
    excl_path = STEP0_DIR / "exclusion_pairs.json"

    tf_cov = {}
    drops = set()
    excl = []

    if cov_path.exists():
        with open(cov_path) as f:
            all_cov = json.load(f)
            tf_cov = all_cov.get(tf, {})

    if drop_path.exists():
        with open(drop_path) as f:
            d = json.load(f)
            drops = set(d.get(tf, []))

    if excl_path.exists():
        with open(excl_path) as f:
            e = json.load(f)
            excl = [tuple(p) for p in e.get(tf, [])]

    eligible = []
    for kpi, info in tf_cov.items():
        flag = info.get("flag", "")
        if flag in ("NO_DATA", "HIGH_NA"):
            continue
        if kpi in drops:
            continue
        eligible.append(kpi)

    return eligible, excl


def generate_combos(pool, size, polarity, anchor_dim, exclusion_pairs, max_combos=50000):
    """Generate combo (kpis, polarities) tuples."""
    combos = []
    excl_set = set()
    for a, b in exclusion_pairs:
        excl_set.add((a, b))
        excl_set.add((b, a))

    for combo in combinations(pool, size):
        skip = False
        for i in range(len(combo)):
            for j in range(i + 1, len(combo)):
                if (combo[i], combo[j]) in excl_set:
                    skip = True
                    break
            if skip:
                break
        if skip:
            continue

        has_anchor = anchor_dim is None or any(KPI_DIM.get(k) == anchor_dim for k in combo)
        if not has_anchor:
            continue

        if polarity == "bull_only":
            combos.append((list(combo), [1] * size))
        elif polarity == "mixed":
            anchors = [i for i, k in enumerate(combo) if KPI_DIM.get(k) == anchor_dim]
            contrarians = [i for i, k in enumerate(combo) if KPI_DIM.get(k) in ("mean_reversion",)]

            if not anchors and anchor_dim is not None:
                continue

            pols_base = [1] * size
            for ci in contrarians:
                pols_base[ci] = -1
            combos.append((list(combo), pols_base))

            if len(combos) >= max_combos:
                break
        else:
            combos.append((list(combo), [1] * size))

        if len(combos) >= max_combos:
            break

    return combos


# ══════════════════════════════════════════════════════════════════════════════
# Stage 1: Combo Search
# ══════════════════════════════════════════════════════════════════════════════

def stage_1(all_pc, tf, eligible_kpis, exclusion_pairs):
    """Run archetype-specific combo search."""
    print(f"\n{'='*100}")
    print(f"  STAGE 1: COMBO SEARCH — {tf}")
    print(f"{'='*100}")

    results = {}

    for arch_key, arch in ARCHETYPES.items():
        pool = [k for k in eligible_kpis if KPI_DIM.get(k) in arch["pool_dims"]]
        if len(pool) < 3:
            print(f"\n  {arch_key} ({arch['label']}): pool too small ({len(pool)} KPIs)")
            results[arch_key] = {}
            continue

        print(f"\n  ── {arch_key}: {arch['label']} ──")
        print(f"     Pool: {len(pool)} KPIs, exit_mode={arch['exit_mode']}")

        if arch_key == "E_mixed" and len(pool) > 15:
            pool = pool[:15]
            print(f"     (E_mixed trimmed to top {len(pool)} KPIs)")

        arch_results = {}
        max_sizes = [3, 4, 5] if len(pool) <= 15 else [3, 4]
        for size in max_sizes:
            if len(pool) < size:
                continue

            combos = generate_combos(pool, size, arch["polarity"],
                                     arch.get("anchor_dim"), exclusion_pairs,
                                     max_combos=15000)
            print(f"     C{size}: {len(combos)} combos...", end="", flush=True)
            t1 = time.time()

            hits = []
            for ckpis, cpols in combos:
                r = sim_archetype(all_pc, ckpis, cpols, tf,
                                  exit_mode=arch["exit_mode"],
                                  gate="none", delay=1,
                                  start_frac=OOS_START, end_frac=1.0)
                if r and r["hr"] >= HR_FLOOR:
                    r["archetype"] = arch_key
                    hits.append(r)

            hits.sort(key=lambda x: -x["pf"])
            arch_results[f"C{size}"] = hits[:TOP_N]
            elapsed = time.time() - t1
            print(f" {len(hits)} passed (HR>={HR_FLOOR}%), best PF={hits[0]['pf'] if hits else '—'}, {elapsed:.0f}s")

        results[arch_key] = arch_results

    _print_stage1(results, tf)
    return results


def _print_stage1(results, tf):
    print(f"\n{'='*120}")
    print(f"  STAGE 1 SUMMARY — {tf}")
    print(f"{'='*120}")
    hdr = f"  {'Arch':<12} {'Size':>4} {'Combo':<45} | {'Tr':>5} {'HR%':>6} {'AvgR%':>7} {'PnL%':>8} {'PF':>6} {'Hold':>5} {'Worst':>6}"
    print(hdr)
    print("  " + "-" * 115)

    for arch_key, arch_res in results.items():
        for size_label, combos in arch_res.items():
            for i, r in enumerate(combos[:3]):
                print(f"  {arch_key:<12} {size_label:>4} {r['label'][:45]:<45} | "
                      f"{r['trades']:>5} {r['hr']:>6.1f} {r['avg_ret']:>7.3f} {r['pnl']:>8.1f} "
                      f"{r['pf']:>6.2f} {r['avg_hold']:>5.1f} {r['worst']:>6.1f}")


# ══════════════════════════════════════════════════════════════════════════════
# Stage 2: Exit Rule Optimization
# ══════════════════════════════════════════════════════════════════════════════

EXIT_MODES = ["standard", "trend_anchor", "momentum_governed", "risk_priority", "adaptive"]


def stage_2(all_pc, stage1_results, tf):
    """Test all exit modes on top-N combos per archetype."""
    print(f"\n{'='*100}")
    print(f"  STAGE 2: EXIT RULE OPTIMIZATION — {tf}")
    print(f"{'='*100}")

    results = []

    for arch_key, arch_res in stage1_results.items():
        top_combos = []
        for size_label, combos in arch_res.items():
            top_combos.extend(combos[:2])

        if not top_combos:
            continue

        for combo in top_combos:
            best_exit = None
            best_pf = -1

            for em in EXIT_MODES:
                r = sim_archetype(all_pc, combo["kpis"], combo["pols"], tf,
                                  exit_mode=em, gate="none", delay=1,
                                  start_frac=OOS_START, end_frac=1.0)
                if r:
                    r["archetype"] = arch_key
                    r["exit_mode"] = em
                    results.append(r)
                    if r["pf"] > best_pf:
                        best_pf = r["pf"]
                        best_exit = em

            print(f"  {arch_key:<12} {combo['label'][:40]:<40} → best exit: {best_exit} (PF={best_pf:.2f})")

    return results


# ══════════════════════════════════════════════════════════════════════════════
# Stage 3: Entry Gate + Delay Sweep
# ══════════════════════════════════════════════════════════════════════════════

GATES = ["none", "sma20_200", "v5"]
DELAYS = [0, 1, 2, 3, 5]


def stage_3(all_pc, stage2_results, tf):
    """Sweep entry gates and delays on top exit-optimized combos."""
    print(f"\n{'='*100}")
    print(f"  STAGE 3: ENTRY GATE + DELAY SWEEP — {tf}")
    print(f"{'='*100}")

    top_by_arch = defaultdict(list)
    for r in stage2_results:
        top_by_arch[r["archetype"]].append(r)

    results = []

    for arch_key, arch_combos in top_by_arch.items():
        arch_combos.sort(key=lambda x: -x["pf"])
        for combo in arch_combos[:3]:
            print(f"\n  {arch_key}: {combo['label'][:50]}, exit_mode={combo['exit_mode']}")

            for gate in GATES:
                for H in DELAYS:
                    r = sim_archetype(all_pc, combo["kpis"], combo["pols"], tf,
                                      exit_mode=combo["exit_mode"],
                                      gate=gate, delay=H,
                                      start_frac=OOS_START, end_frac=1.0)
                    if r:
                        r["archetype"] = arch_key
                        r["exit_mode"] = combo["exit_mode"]
                        r["gate"] = gate
                        r["delay"] = H
                        results.append(r)

            best = max([r for r in results if r.get("archetype") == arch_key], key=lambda x: x["pf"], default=None)
            if best:
                print(f"    Best: gate={best.get('gate','?')}, H={best.get('delay','?')}, PF={best['pf']:.2f}, HR={best['hr']:.1f}%")

    return results


# ══════════════════════════════════════════════════════════════════════════════
# Stage 4: Walk-Forward Validation
# ══════════════════════════════════════════════════════════════════════════════

def stage_4(all_pc, stage3_results, tf):
    """Validate top combos on OOS-B holdout period."""
    print(f"\n{'='*100}")
    print(f"  STAGE 4: WALK-FORWARD VALIDATION — {tf}")
    print(f"{'='*100}")

    top_by_arch = defaultdict(list)
    for r in stage3_results:
        top_by_arch[r["archetype"]].append(r)

    validated = []
    failed = []

    for arch_key, arch_combos in top_by_arch.items():
        arch_combos.sort(key=lambda x: -x["pf"])
        seen = set()

        for combo in arch_combos[:5]:
            combo_key = tuple(sorted(zip(combo["kpis"], combo["pols"])))
            if combo_key in seen:
                continue
            seen.add(combo_key)

            is_r = sim_archetype(all_pc, combo["kpis"], combo["pols"], tf,
                                 exit_mode=combo.get("exit_mode", "standard"),
                                 gate=combo.get("gate", "none"),
                                 delay=combo.get("delay", 1),
                                 start_frac=OOS_START, end_frac=OOS_B_START,
                                 min_trades=5)

            oos_r = sim_archetype(all_pc, combo["kpis"], combo["pols"], tf,
                                  exit_mode=combo.get("exit_mode", "standard"),
                                  gate=combo.get("gate", "none"),
                                  delay=combo.get("delay", 1),
                                  start_frac=OOS_B_START, end_frac=1.0,
                                  min_trades=3)

            if not is_r:
                continue

            entry = {
                "archetype": arch_key,
                "kpis": combo["kpis"],
                "pols": combo["pols"],
                "label": combo["label"],
                "exit_mode": combo.get("exit_mode", "standard"),
                "gate": combo.get("gate", "none"),
                "delay": combo.get("delay", 1),
                "IS_trades": is_r["trades"],
                "IS_hr": is_r["hr"],
                "IS_pf": is_r["pf"],
                "IS_avg_ret": is_r["avg_ret"],
                "IS_pnl": is_r["pnl"],
            }

            if oos_r:
                entry["OOS_trades"] = oos_r["trades"]
                entry["OOS_hr"] = oos_r["hr"]
                entry["OOS_pf"] = oos_r["pf"]
                entry["OOS_avg_ret"] = oos_r["avg_ret"]
                entry["OOS_pnl"] = oos_r["pnl"]

                hr_decay = is_r["hr"] - oos_r["hr"]
                pf_ratio = oos_r["pf"] / is_r["pf"] if is_r["pf"] > 0 else 0

                entry["hr_decay"] = round(hr_decay, 1)
                entry["pf_ratio"] = round(pf_ratio, 2)

                passes = (
                    oos_r["hr"] >= 50.0 and
                    hr_decay <= 15.0 and
                    pf_ratio >= 0.5 and
                    oos_r["trades"] >= 3
                )
                entry["validated"] = passes
            else:
                entry["OOS_trades"] = 0
                entry["validated"] = False

            if entry["validated"]:
                validated.append(entry)
                status = "PASS"
            else:
                failed.append(entry)
                status = "FAIL"

            oos_hr = entry.get("OOS_hr", "—")
            oos_pf = entry.get("OOS_pf", "—")
            oos_tr = entry.get("OOS_trades", 0)
            print(f"  {status}  {arch_key:<12} {entry['label'][:45]:<45} "
                  f"IS: HR={is_r['hr']:.1f}% PF={is_r['pf']:.2f} | "
                  f"OOS: HR={oos_hr} PF={oos_pf} Tr={oos_tr}")

    print(f"\n  Validated: {len(validated)}, Failed: {len(failed)}")
    return validated, failed


# ══════════════════════════════════════════════════════════════════════════════
# Stage 5: Cross-Strategy Comparison + Final Recommendation
# ══════════════════════════════════════════════════════════════════════════════

def stage_5(validated_all):
    """Compare validated winners across TFs and archetypes."""
    print(f"\n{'='*100}")
    print(f"  STAGE 5: FINAL RECOMMENDATIONS")
    print(f"{'='*100}")

    if not validated_all:
        print("  No validated strategies found.")
        return {"recommendation": "HOLD_CURRENT", "winners": []}

    sorted_v = sorted(validated_all, key=lambda x: (-x.get("OOS_pf", 0), -x.get("OOS_hr", 0)))

    print(f"\n  Top validated strategies (ranked by OOS PF):")
    hdr = f"  {'TF':>4} {'Arch':<12} {'Combo':<45} {'Exit':>10} {'Gate':>10} | {'IS_HR':>6} {'IS_PF':>6} | {'OOS_HR':>6} {'OOS_PF':>6} {'OOS_Tr':>6}"
    print(hdr)
    print("  " + "-" * 125)

    for r in sorted_v[:20]:
        print(f"  {r.get('tf', '?'):>4} {r['archetype']:<12} {r['label'][:45]:<45} "
              f"{r['exit_mode']:>10} {r['gate']:>10} | "
              f"{r['IS_hr']:>6.1f} {r['IS_pf']:>6.2f} | "
              f"{r.get('OOS_hr', 0):>6.1f} {r.get('OOS_pf', 0):>6.2f} {r.get('OOS_trades', 0):>6}")

    best = sorted_v[0] if sorted_v else None
    rec = {
        "recommendation": "ADOPT" if best and best.get("OOS_pf", 0) >= 1.2 else "MONITOR",
        "best_strategy": best,
        "total_validated": len(validated_all),
        "all_validated": sorted_v,
    }

    if best:
        print(f"\n  RECOMMENDED STRATEGY:")
        print(f"    Archetype:  {best['archetype']} ({ARCHETYPES.get(best['archetype'], {}).get('label', '')})")
        print(f"    KPIs:       {best['label']}")
        print(f"    Exit Mode:  {best['exit_mode']}")
        print(f"    Gate:       {best['gate']}")
        print(f"    Delay:      {best.get('delay', 1)}")
        print(f"    IS  →  HR={best['IS_hr']:.1f}%, PF={best['IS_pf']:.2f}")
        print(f"    OOS →  HR={best.get('OOS_hr', 0):.1f}%, PF={best.get('OOS_pf', 0):.2f}, trades={best.get('OOS_trades', 0)}")
        action = "ADOPT: Strong OOS performance, replace current strategy." if rec["recommendation"] == "ADOPT" else "MONITOR: Promising but needs more OOS trades or PF > 1.2 to confirm."
        print(f"    Action:     {action}")

    return rec


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    t0 = time.time()
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Phase 17 — Unified Strategy Archetype Optimization")
    print(f"{'='*80}")
    print(f"Archetypes: {list(ARCHETYPES.keys())}")
    print(f"Timeframes: {ALL_TFS}")

    validated_all = []
    csv_rows = []

    available_tfs = []
    for tf in ALL_TFS:
        count = len(list(ENRICHED_DIR.glob(f"*_{tf}.parquet")))
        if count > 0:
            available_tfs.append(tf)
            print(f"  {tf}: {count} parquets")
        else:
            print(f"  {tf}: SKIPPED (no parquets)")

    for tf in available_tfs:
        print(f"\n{'#'*100}")
        print(f"  TIMEFRAME: {tf}")
        print(f"{'#'*100}")

        eligible, exclusion_pairs = _load_eligible_kpis(tf)
        if not eligible:
            all_kpis = list(KPI_DIM.keys())
            exclusion_pairs = []
            eligible = all_kpis
            print(f"  No Step 0 data for {tf}, using all {len(all_kpis)} KPIs")
        else:
            print(f"  Eligible KPIs: {len(eligible)}, exclusion pairs: {len(exclusion_pairs)}")

        all_kpis = list(set(eligible) | set(KPI_DIM.keys()))

        print(f"  Loading data...", end=" ", flush=True)
        data = load_data(tf)
        print(f"{len(data)} stocks")

        print(f"  Pre-computing KPI states...", end=" ", flush=True)
        all_pc = precompute(data, tf, all_kpis)
        print(f"{len(all_pc)} valid")

        # Stage 1
        s1 = stage_1(all_pc, tf, eligible, exclusion_pairs)

        # Stage 2
        s2 = stage_2(all_pc, s1, tf)

        # Stage 3
        s3 = stage_3(all_pc, s2, tf)

        # Stage 4
        validated, failed = stage_4(all_pc, s3, tf)
        for v in validated:
            v["tf"] = tf
        validated_all.extend(validated)

        # CSV rows
        for r in s2 + s3:
            csv_rows.append({
                "tf": tf,
                "archetype": r.get("archetype", "?"),
                "combo": r.get("label", "?"),
                "exit_mode": r.get("exit_mode", "?"),
                "gate": r.get("gate", "none"),
                "delay": r.get("delay", 1),
                "trades": r.get("trades", 0),
                "hr": r.get("hr", 0),
                "avg_ret": r.get("avg_ret", 0),
                "pnl": r.get("pnl", 0),
                "pf": r.get("pf", 0),
                "avg_hold": r.get("avg_hold", 0),
                "worst": r.get("worst", 0),
            })

    # Stage 5: Final Recommendation
    rec = stage_5(validated_all)

    # ── Save Outputs ──────────────────────────────────────────────────────

    csv_path = OUTPUTS_DIR / "phase17_all_results.csv"
    if csv_rows:
        fnames = ["tf", "archetype", "combo", "exit_mode", "gate", "delay",
                  "trades", "hr", "avg_ret", "pnl", "pf", "avg_hold", "worst"]
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(csv_rows)
        print(f"\nAll results CSV: {csv_path}")

    val_path = OUTPUTS_DIR / "phase17_validated.csv"
    if validated_all:
        fnames = ["tf", "archetype", "label", "exit_mode", "gate", "delay",
                  "IS_trades", "IS_hr", "IS_pf", "IS_avg_ret", "IS_pnl",
                  "OOS_trades", "OOS_hr", "OOS_pf", "OOS_avg_ret", "OOS_pnl",
                  "hr_decay", "pf_ratio", "validated"]
        with open(val_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(validated_all)
        print(f"Validated CSV: {val_path}")

    rec_path = OUTPUTS_DIR / "phase17_recommendation.json"
    rec_safe = {
        "recommendation": rec["recommendation"],
        "total_validated": rec["total_validated"],
        "best_strategy": {k: v for k, v in (rec.get("best_strategy") or {}).items()
                          if k not in ("kpi_state",)} if rec.get("best_strategy") else None,
    }
    with open(rec_path, "w") as f:
        json.dump(rec_safe, f, indent=2, default=str)
    print(f"Recommendation: {rec_path}")

    # ── Final Report ──────────────────────────────────────────────────────
    report_path = OUTPUTS_DIR / "PHASE17_REPORT.md"
    _write_report(report_path, rec, validated_all, csv_rows)
    print(f"Report: {report_path}")

    elapsed = time.time() - t0
    print(f"\nTotal runtime: {elapsed:.0f}s ({elapsed/60:.1f} min)")


def _write_report(path, rec, validated, csv_rows):
    """Write the final human-readable report."""
    lines = [
        "# Phase 17 — Strategy Archetype Optimization Report",
        f"\nGenerated: {time.strftime('%Y-%m-%d %H:%M')}",
        f"\n## Summary",
        f"- **Recommendation:** {rec['recommendation']}",
        f"- **Total validated strategies:** {rec['total_validated']}",
    ]

    best = rec.get("best_strategy")
    if best:
        lines += [
            f"\n## Best Strategy",
            f"- **Archetype:** {best['archetype']} ({ARCHETYPES.get(best['archetype'], {}).get('label', '')})",
            f"- **KPIs:** {best.get('label', '?')}",
            f"- **Exit Mode:** {best.get('exit_mode', '?')}",
            f"- **Entry Gate:** {best.get('gate', '?')}",
            f"- **Entry Delay:** {best.get('delay', '?')}",
            f"- **In-Sample:** HR={best.get('IS_hr', 0):.1f}%, PF={best.get('IS_pf', 0):.2f}, PnL={best.get('IS_pnl', 0):.1f}%",
            f"- **Out-of-Sample:** HR={best.get('OOS_hr', 0):.1f}%, PF={best.get('OOS_pf', 0):.2f}, Trades={best.get('OOS_trades', 0)}",
        ]

    if validated:
        lines += ["\n## All Validated Strategies", ""]
        lines.append(f"| TF | Archetype | Combo | Exit | Gate | IS HR | IS PF | OOS HR | OOS PF | OOS Tr |")
        lines.append(f"|---|---|---|---|---|---|---|---|---|---|")
        for v in sorted(validated, key=lambda x: -x.get("OOS_pf", 0)):
            lines.append(
                f"| {v.get('tf','?')} | {v['archetype']} | {v.get('label','?')[:40]} | "
                f"{v.get('exit_mode','?')} | {v.get('gate','?')} | "
                f"{v.get('IS_hr',0):.1f} | {v.get('IS_pf',0):.2f} | "
                f"{v.get('OOS_hr',0):.1f} | {v.get('OOS_pf',0):.2f} | {v.get('OOS_trades',0)} |"
            )

    lines += [
        "\n## Decision Framework",
        "1. **ADOPT** if OOS PF >= 1.2 AND OOS HR >= 55% AND OOS trades >= 10",
        "2. **MONITOR** if OOS PF >= 1.0 but below ADOPT thresholds",
        "3. **HOLD_CURRENT** if no strategies pass validation",
        "\n## Notes",
        "- Stoof indicators require re-enrichment (`fetch_sample300.py --force`)",
        "- 2W/1M timeframes need data generation before inclusion",
        "- Correlation exclusion pairs applied to prevent redundant combos",
    ]

    with open(path, "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()

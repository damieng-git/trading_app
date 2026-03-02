"""
Phase 18 — Master Strategy Optimization Pipeline

Sub-phases:
  18.0  Data audit (coverage, correlation, scorecard) on all 38 KPIs × 5 TFs
  18.1  Mixed-polarity combo discovery (C3/C4/C5)
  18.2  Entry gate & delay optimization
  18.3  Exit strategy optimization (6 modes + T/M/K sweep)
  18.4  Walk-forward validation (OOS-A/OOS-B + sector + regime)
  18.5  C4 superset optimization (C3+1)
  18.6  Portfolio construction & multi-strategy analysis

Dataset: sample_300 enriched parquets (with Stoof + 2W/1M)
Output:  research/kpi_optimization/outputs/all/phase18/
"""
from __future__ import annotations

import csv
import gc
import json
import sys
import time
from collections import defaultdict
from itertools import combinations
from math import comb as _comb
from pathlib import Path

import numpy as np
import pandas as pd
import psutil
from scipy.stats import spearmanr

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from trading_dashboard.kpis.catalog import compute_kpi_state_map
from trading_dashboard.kpis.rules import STATE_BULL, STATE_BEAR

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
OUTPUTS_DIR = Path(__file__).resolve().parent / "outputs" / "all" / "phase18"
STEP0_DIR = OUTPUTS_DIR / "step0"

ATR_PERIOD = 14
MAX_HOLD = 500
COMMISSION = 0.001
SLIPPAGE = 0.005
COST_PCT = (COMMISSION + SLIPPAGE) * 100
SEARCH_START = 0.70   # combo search uses last 30% (fast, matches P17)
OOS_START = 0.50      # walk-forward IS starts at 50%
OOS_B_START = 0.75    # walk-forward OOS-B starts at 75%
TOP_N = 5
HR_FLOOR = 55.0
CORR_THRESHOLD = 0.70
MAX_COMBOS_PER_SIZE = 12000

EXIT_PARAMS = {
    "4H": {"T": 4, "M": 48, "K": 4.0},
    "1D": {"T": 4, "M": 40, "K": 4.0},
    "1W": {"T": 2, "M": 20, "K": 4.0},
    "2W": {"T": 2, "M": 10, "K": 4.0},
    "1M": {"T": 2, "M": 6,  "K": 4.0},
}

ALL_TFS = ["4H", "1D", "1W", "2W", "1M"]

# ══════════════════════════════════════════════════════════════════════════════
# KPI Dimension Mapping (all 38)
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

# Dimensions where -1 polarity is allowed at entry
MIXED_ALLOWED_DIMS = {"momentum", "mean_reversion", "breakout"}
# Dimensions where only +1 is allowed (structural safety)
BULL_ONLY_DIMS = {"trend", "risk_exit", "relative_strength"}

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

ARCHETYPES = {
    "A_trend": {
        "label": "Trend Following",
        "anchor_dim": "trend",
        "pool_dims": ["trend", "momentum", "relative_strength"],
        "polarity": "bull_only",
        "exit_mode": "standard",
    },
    "B_dip": {
        "label": "Mean Reversion / Buy the Dip",
        "anchor_dim": "trend",
        "pool_dims": ["trend", "mean_reversion", "breakout", "momentum"],
        "polarity": "mixed",
        "exit_mode": "trend_anchor",
    },
    "C_breakout": {
        "label": "Breakout / Momentum Surge",
        "anchor_dim": "breakout",
        "pool_dims": ["breakout", "momentum", "relative_strength"],
        "polarity": "bull_only",
        "exit_mode": "momentum_governed",
    },
    "D_risk": {
        "label": "Trend + Risk-Managed",
        "anchor_dim": "trend",
        "pool_dims": ["trend", "risk_exit", "momentum"],
        "polarity": "bull_only",
        "exit_mode": "risk_priority",
    },
    "E_mixed": {
        "label": "Full Mixed / Unconstrained",
        "anchor_dim": None,
        "pool_dims": list(set(KPI_DIM.values())),
        "polarity": "mixed",
        "exit_mode": "adaptive",
    },
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
    pc = np.roll(df["Close"].to_numpy(float), 1); pc[0] = np.nan
    tr = np.maximum(h - lo, np.maximum(np.abs(h - pc), np.abs(lo - pc)))
    return pd.Series(tr).rolling(window=period, min_periods=1).mean().to_numpy(float)


def load_data(tf):
    min_bars = 60 if tf in ("1M", "2W") else 100
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
            if len(df) >= min_bars and "Close" in df.columns:
                data[sym] = df
        except Exception:
            continue
    return data


def precompute(data, tf, all_kpis):
    all_pc = {}
    for sym, df in data.items():
        sm = compute_kpi_state_map(df)
        bulls, bears, nbull, nbear = {}, {}, {}, {}
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
            ref = np.empty(n, dtype=float); ref[:5] = np.nan; ref[5:] = cl[:-5]
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

        # Sector info from symbol (not available in parquet — skip for now)
        all_pc[sym] = {
            "bulls": bulls, "bears": bears, "nbull": nbull, "nbear": nbear,
            "cl": cl, "op": op, "atr": at, "n": n,
            "sma20": sma20, "sma200": sma200,
            "overext_ok": overext_ok, "vol_spike_ok": vol_spike_ok,
        }
    return all_pc


# ══════════════════════════════════════════════════════════════════════════════
# Simulation Engine (unchanged from Phase 17, proven fast)
# ══════════════════════════════════════════════════════════════════════════════

def _get_exit_kpi_indices(combo_kpis, combo_pols, exit_mode):
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


def sim_combo(all_pc, combo_kpis, combo_pols, tf, *,
              exit_mode="standard", gate="none", delay=1,
              T_override=None, M_override=None, K_override=None,
              min_trades=10, start_frac=0.0, end_frac=1.0,
              return_per_sym=False):
    T = T_override or EXIT_PARAMS[tf]["T"]
    M = M_override or EXIT_PARAMS[tf]["M"]
    K = K_override or EXIT_PARAMS[tf]["K"]
    trades = []
    per_sym_trades = defaultdict(list)

    exit_kpi_idx = _get_exit_kpi_indices(combo_kpis, combo_pols, exit_mode)

    for sym, pc in all_pc.items():
        bulls, bears = pc["bulls"], pc["bears"]
        nbull, nbear = pc["nbull"], pc["nbear"]
        if any(k not in bulls for k in combo_kpis):
            continue

        cl, op, at, n = pc["cl"], pc["op"], pc["atr"], pc["n"]
        si, ei = int(n * start_frac), int(n * end_frac)
        if ei - si < 50:
            continue

        entry_match = np.ones(n, dtype=bool)
        for k, p in zip(combo_kpis, combo_pols):
            entry_match &= bulls[k] if p == 1 else bears[k]

        onset = np.zeros(n, dtype=bool)
        onset[1:] = entry_match[1:] & ~entry_match[:-1]

        exit_nbool = []
        for i in exit_kpi_idx:
            k, p = combo_kpis[i], combo_pols[i]
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
            trades.append((ret, h, sym))
            per_sym_trades[sym].append((ret, h))
            j = xi + 1

    if len(trades) < min_trades:
        return None

    rets = [t[0] for t in trades]
    holds = [t[1] for t in trades]
    nt = len(rets)
    hr = sum(1 for r in rets if r > 0) / nt * 100
    wi = sum(r for r in rets if r > 0)
    lo = abs(sum(r for r in rets if r <= 0))
    pf = round(wi / lo if lo > 0 else 999, 2)
    result = {
        "trades": nt, "hr": round(hr, 1),
        "avg_ret": round(float(np.mean(rets)), 3),
        "pnl": round(sum(rets), 1),
        "pf": pf,
        "avg_hold": round(float(np.mean(holds)), 1),
        "worst": round(min(rets), 1),
        "kpis": list(combo_kpis), "pols": list(combo_pols),
        "label": _sl(combo_kpis, combo_pols),
    }
    if return_per_sym:
        result["per_sym"] = dict(per_sym_trades)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Combo Generation — Full mixed-polarity support
# ══════════════════════════════════════════════════════════════════════════════

def generate_combos(pool, size, polarity, anchor_dim, exclusion_pairs, max_combos=50000):
    combos = []
    excl_set = set()
    for a, b in exclusion_pairs:
        excl_set.add((a, b)); excl_set.add((b, a))

    for combo in combinations(pool, size):
        skip = False
        for i in range(len(combo)):
            for j2 in range(i + 1, len(combo)):
                if (combo[i], combo[j2]) in excl_set:
                    skip = True; break
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
            mixable = [i for i, k in enumerate(combo) if KPI_DIM.get(k) in MIXED_ALLOWED_DIMS]
            if not mixable:
                combos.append((list(combo), [1] * size))
            else:
                combos.append((list(combo), [1] * size))
                for mi in mixable:
                    pols = [1] * size
                    pols[mi] = -1
                    combos.append((list(combo), pols))
                if len(mixable) >= 2:
                    pols = [1] * size
                    for mi in mixable:
                        pols[mi] = -1
                    combos.append((list(combo), pols))
        else:
            combos.append((list(combo), [1] * size))

        if len(combos) >= max_combos:
            break
    return combos


# ══════════════════════════════════════════════════════════════════════════════
# 18.0  DATA AUDIT
# ══════════════════════════════════════════════════════════════════════════════

def _check_memory(label="", threshold=70):
    mem = psutil.virtual_memory()
    pct = mem.percent
    used_gb = mem.used / 1e9
    total_gb = mem.total / 1e9
    status = f"Memory: {pct:.0f}% ({used_gb:.1f}/{total_gb:.1f} GB)"
    if label:
        status = f"  [{label}] {status}"
    print(status, flush=True)
    if pct > threshold:
        gc.collect()
        mem = psutil.virtual_memory()
        if mem.percent > threshold:
            print(f"  WARNING: Memory still at {mem.percent:.0f}% after gc.collect()", flush=True)
    return mem.percent


def phase_18_0_single_tf(data, tf):
    """Run Phase 18.0 audit for a single timeframe. Returns (eligible, excl_pairs)."""
    all_kpis = list(KPI_DIM.keys())
    print(f"\n  ── {tf}: {len(data)} stocks ──")
    coverage = {}
    state_arrays = defaultdict(list)

    for sym, df in data.items():
        sm = compute_kpi_state_map(df)
        for k in all_kpis:
            if k in sm:
                s = sm[k].to_numpy(int)
                n = len(s)
                if n == 0:
                    continue
                bull_pct = round((s == STATE_BULL).sum() / n * 100, 1)
                bear_pct = round((s == STATE_BEAR).sum() / n * 100, 1)
                na_pct = round((s == 0).sum() / n * 100, 1)
                state_arrays[k].append(s)
                if k not in coverage:
                    coverage[k] = {"bull": [], "bear": [], "na": []}
                coverage[k]["bull"].append(bull_pct)
                coverage[k]["bear"].append(bear_pct)
                coverage[k]["na"].append(na_pct)

    tf_cov = {}
    for k in all_kpis:
        if k in coverage:
            avg_bull = round(np.mean(coverage[k]["bull"]), 1)
            avg_bear = round(np.mean(coverage[k]["bear"]), 1)
            avg_na = round(np.mean(coverage[k]["na"]), 1)
            flag = ""
            if avg_na >= 90:
                flag = "HIGH_NA"
            elif avg_bull < 1 and avg_bear < 1:
                flag = "RARE_BULL"
            tf_cov[k] = {"bull_pct": avg_bull, "bear_pct": avg_bear, "na_pct": avg_na, "flag": flag}
        else:
            tf_cov[k] = {"bull_pct": 0, "bear_pct": 0, "na_pct": 100, "flag": "NO_DATA"}

    eligible = [k for k, v in tf_cov.items() if v["flag"] not in ("HIGH_NA", "NO_DATA")]
    n_high_na = sum(1 for v in tf_cov.values() if v["flag"] == "HIGH_NA")
    n_rare = sum(1 for v in tf_cov.values() if v["flag"] == "RARE_BULL")
    print(f"    Eligible: {len(eligible)}, HIGH_NA: {n_high_na}, RARE_BULL: {n_rare}")

    excl_pairs = []
    if len(eligible) >= 3 and state_arrays:
        n_bars = min(len(arr[0]) for arr in state_arrays.values() if arr)
        kpi_matrix = {}
        for k in eligible:
            if k in state_arrays and state_arrays[k]:
                combined = np.concatenate([a[:n_bars] for a in state_arrays[k] if len(a) >= n_bars])
                kpi_matrix[k] = combined

        sorted_kpis = sorted(kpi_matrix.keys())
        for i, k1 in enumerate(sorted_kpis):
            for k2 in sorted_kpis[i+1:]:
                min_len = min(len(kpi_matrix[k1]), len(kpi_matrix[k2]))
                if min_len < 100:
                    continue
                r, _ = spearmanr(kpi_matrix[k1][:min_len], kpi_matrix[k2][:min_len])
                if abs(r) >= CORR_THRESHOLD:
                    excl_pairs.append((k1, k2, round(r, 4)))

        print(f"    Correlation pairs (r>{CORR_THRESHOLD}): {len(excl_pairs)}")
        for a, b, r in excl_pairs[:5]:
            print(f"      {_s(a)} ↔ {_s(b)}: r={r}")

    return tf_cov, eligible, [(a, b) for a, b, _ in excl_pairs]


# ══════════════════════════════════════════════════════════════════════════════
# 18.1  MIXED-POLARITY COMBO DISCOVERY
# ══════════════════════════════════════════════════════════════════════════════

def phase_18_1_tf(all_pc, tf, eligible, excl_pairs):
    """Run Phase 18.1 combo discovery for a single timeframe."""
    print(f"\n{'='*80}")
    print(f"  18.1 COMBO DISCOVERY — {tf} ({len(all_pc)} stocks)")
    print(f"{'='*80}")

    tf_results = {}
    for arch_key, arch in ARCHETYPES.items():
        pool = [k for k in eligible if KPI_DIM.get(k) in arch["pool_dims"]]
        if len(pool) < 3:
            print(f"  {arch_key}: pool too small ({len(pool)})")
            tf_results[arch_key] = {}
            continue

        if arch_key == "E_mixed" and len(pool) > 15:
            pool = pool[:15]

        print(f"\n  ── {arch_key}: {arch['label']} (pool={len(pool)}) ──")
        arch_results = {}
        max_sizes = [3, 4] if len(pool) > 15 else [3, 4, 5]

        for size in max_sizes:
            if len(pool) < size:
                continue
            combos = generate_combos(pool, size, arch["polarity"],
                                     arch.get("anchor_dim"), excl_pairs,
                                     max_combos=MAX_COMBOS_PER_SIZE)
            print(f"     C{size}: {len(combos)} combos...", end="", flush=True)
            t1 = time.time()
            hits = []
            for ckpis, cpols in combos:
                r = sim_combo(all_pc, ckpis, cpols, tf,
                              exit_mode=arch["exit_mode"],
                              gate="none", delay=1,
                              start_frac=SEARCH_START, end_frac=1.0)
                if r and r["hr"] >= HR_FLOOR:
                    r["archetype"] = arch_key
                    r["exit_mode"] = arch["exit_mode"]
                    hits.append(r)
            hits.sort(key=lambda x: -x["pf"])
            arch_results[f"C{size}"] = hits[:TOP_N]
            elapsed = time.time() - t1
            n_mixed = sum(1 for _, pols in combos if -1 in pols)
            print(f" {len(hits)} pass (HR>={HR_FLOOR}%), "
                  f"mixed={n_mixed}, "
                  f"best PF={hits[0]['pf'] if hits else '—'}, "
                  f"{elapsed:.0f}s")

        tf_results[arch_key] = arch_results
    return tf_results


# ══════════════════════════════════════════════════════════════════════════════
# 18.2  ENTRY GATE & DELAY OPTIMIZATION
# ══════════════════════════════════════════════════════════════════════════════

GATES = ["none", "sma20_200", "v5"]
DELAYS = [0, 1, 2, 3]


def phase_18_2_tf(all_pc, tf, stage1_tf):
    """Run Phase 18.2 entry gate/delay optimization for a single timeframe."""
    print(f"\n  18.2 ENTRY GATE & DELAY — {tf}")
    tf_results = []

    for arch_key, arch_res in stage1_tf.items():
        top_combos = []
        for size_label, combos in arch_res.items():
            top_combos.extend(combos[:3])
        if not top_combos:
            continue

        for combo in top_combos:
            best_r, best_gate, best_delay = None, "none", 1
            for gate in GATES:
                for H in DELAYS:
                    r = sim_combo(all_pc, combo["kpis"], combo["pols"], tf,
                                  exit_mode=combo.get("exit_mode", "standard"),
                                  gate=gate, delay=H,
                                  start_frac=SEARCH_START, end_frac=1.0)
                    if r:
                        r.update({"archetype": arch_key, "exit_mode": combo["exit_mode"],
                                  "gate": gate, "delay": H, "tf": tf})
                        tf_results.append(r)
                        if best_r is None or r["pf"] > best_r["pf"]:
                            best_r, best_gate, best_delay = r, gate, H

            if best_r:
                print(f"    {arch_key:<12} {combo['label'][:40]:<40} → "
                      f"gate={best_gate}, H={best_delay}, PF={best_r['pf']:.2f}")

    return tf_results


# ══════════════════════════════════════════════════════════════════════════════
# 18.3  EXIT STRATEGY OPTIMIZATION
# ══════════════════════════════════════════════════════════════════════════════

EXIT_MODES = ["standard", "trend_anchor", "momentum_governed", "risk_priority", "adaptive"]
TMK_GRID = [
    (2, 20, 3.0), (2, 20, 4.0), (2, 20, 5.0),
    (4, 40, 3.0), (4, 40, 4.0), (4, 40, 5.0),
    (4, 48, 4.0), (6, 48, 4.0),
]


def phase_18_3_tf(all_pc, tf, stage2_tf):
    """Run Phase 18.3 exit strategy optimization for a single timeframe."""
    print(f"\n  18.3 EXIT OPTIMIZATION — {tf}")

    top_by_arch = defaultdict(list)
    for r in stage2_tf:
        top_by_arch[r["archetype"]].append(r)

    tf_results = []

    for arch_key, combos in top_by_arch.items():
        combos.sort(key=lambda x: -x["pf"])
        seen = set()
        for combo in combos[:5]:
            combo_key = (tuple(combo["kpis"]), tuple(combo["pols"]),
                         combo.get("gate", "none"), combo.get("delay", 1))
            if combo_key in seen:
                continue
            seen.add(combo_key)

            best_r = None
            for em in EXIT_MODES:
                r = sim_combo(all_pc, combo["kpis"], combo["pols"], tf,
                              exit_mode=em,
                              gate=combo.get("gate", "none"),
                              delay=combo.get("delay", 1),
                              start_frac=SEARCH_START, end_frac=1.0)
                if r:
                    r.update({"archetype": arch_key, "exit_mode": em,
                              "gate": combo.get("gate", "none"),
                              "delay": combo.get("delay", 1), "tf": tf})
                    tf_results.append(r)
                    if best_r is None or r["pf"] > best_r["pf"]:
                        best_r = r

            best_em = best_r["exit_mode"] if best_r else "standard"

            for T_v, M_v, K_v in TMK_GRID:
                r = sim_combo(all_pc, combo["kpis"], combo["pols"], tf,
                              exit_mode=best_em,
                              gate=combo.get("gate", "none"),
                              delay=combo.get("delay", 1),
                              T_override=T_v, M_override=M_v, K_override=K_v,
                              start_frac=SEARCH_START, end_frac=1.0)
                if r:
                    r.update({"archetype": arch_key, "exit_mode": best_em,
                              "gate": combo.get("gate", "none"),
                              "delay": combo.get("delay", 1), "tf": tf,
                              "T": T_v, "M": M_v, "K": K_v})
                    tf_results.append(r)

            if best_r:
                print(f"    {arch_key:<12} {combo['label'][:40]:<40} → "
                      f"exit={best_em}, PF={best_r['pf']:.2f}")

    return tf_results


# ══════════════════════════════════════════════════════════════════════════════
# 18.4  WALK-FORWARD VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

def phase_18_4_tf(all_pc, tf, stage3_tf):
    """Run Phase 18.4 walk-forward validation for a single timeframe."""
    print(f"\n  18.4 WALK-FORWARD VALIDATION — {tf}")

    validated = []
    failed = []

    top_by_arch = defaultdict(list)
    for r in stage3_tf:
        top_by_arch[r["archetype"]].append(r)

    for arch_key, combos in top_by_arch.items():
        combos.sort(key=lambda x: -x["pf"])
        seen = set()
        for combo in combos[:8]:
            combo_key = (tuple(combo["kpis"]), tuple(combo["pols"]))
            if combo_key in seen:
                continue
            seen.add(combo_key)

            is_r = sim_combo(all_pc, combo["kpis"], combo["pols"], tf,
                             exit_mode=combo.get("exit_mode", "standard"),
                             gate=combo.get("gate", "none"),
                             delay=combo.get("delay", 1),
                             T_override=combo.get("T"), M_override=combo.get("M"),
                             K_override=combo.get("K"),
                             start_frac=OOS_START, end_frac=OOS_B_START,
                             min_trades=5)

            oos_r = sim_combo(all_pc, combo["kpis"], combo["pols"], tf,
                              exit_mode=combo.get("exit_mode", "standard"),
                              gate=combo.get("gate", "none"),
                              delay=combo.get("delay", 1),
                              T_override=combo.get("T"), M_override=combo.get("M"),
                              K_override=combo.get("K"),
                              start_frac=OOS_B_START, end_frac=1.0,
                              min_trades=3)

            if not is_r:
                continue

            entry = {
                "tf": tf, "archetype": arch_key,
                "kpis": combo["kpis"], "pols": combo["pols"],
                "label": combo["label"],
                "exit_mode": combo.get("exit_mode", "standard"),
                "gate": combo.get("gate", "none"),
                "delay": combo.get("delay", 1),
                "T": combo.get("T"), "M": combo.get("M"), "K": combo.get("K"),
                "IS_trades": is_r["trades"], "IS_hr": is_r["hr"],
                "IS_pf": is_r["pf"], "IS_avg_ret": is_r["avg_ret"],
                "IS_pnl": is_r["pnl"],
            }

            if oos_r:
                entry.update({
                    "OOS_trades": oos_r["trades"], "OOS_hr": oos_r["hr"],
                    "OOS_pf": oos_r["pf"], "OOS_avg_ret": oos_r["avg_ret"],
                    "OOS_pnl": oos_r["pnl"],
                    "OOS_avg_hold": oos_r["avg_hold"], "OOS_worst": oos_r["worst"],
                })
                hr_decay = is_r["hr"] - oos_r["hr"]
                pf_ratio = oos_r["pf"] / is_r["pf"] if is_r["pf"] > 0 else 0
                entry["hr_decay"] = round(hr_decay, 1)
                entry["pf_ratio"] = round(pf_ratio, 2)

                passes = (oos_r["hr"] >= 50.0 and hr_decay <= 15.0
                          and pf_ratio >= 0.5 and oos_r["trades"] >= 3)
                entry["validated"] = passes
            else:
                entry.update({"OOS_trades": 0, "validated": False})

            if entry["validated"]:
                validated.append(entry)
                status = "PASS"
            else:
                failed.append(entry)
                status = "FAIL"

            oos_str = (f"HR={entry.get('OOS_hr','—')} PF={entry.get('OOS_pf','—')} "
                       f"Tr={entry.get('OOS_trades',0)}")
            print(f"  {status}  {arch_key:<12} {entry['label'][:45]:<45} "
                  f"IS: HR={is_r['hr']:.1f}% PF={is_r['pf']:.2f} | OOS: {oos_str}")

    print(f"    {tf} — Validated: {len(validated)}, Failed: {len(failed)}")
    return validated, failed


# ══════════════════════════════════════════════════════════════════════════════
# 18.5  C4 SUPERSET OPTIMIZATION
# ══════════════════════════════════════════════════════════════════════════════

def phase_18_5_tf(all_pc, tf, validated_tf, eligible, excl_pairs):
    """Run Phase 18.5 C4 superset optimization for a single timeframe."""
    c3_combos = [v for v in validated_tf if len(v["kpis"]) == 3]
    if not c3_combos:
        return []

    print(f"\n  18.5 C4 SUPERSET — {tf} ({len(c3_combos)} C3 combos)")

    excl_set = set()
    for a, b in excl_pairs:
        excl_set.add((a, b)); excl_set.add((b, a))

    superset_results = []

    for combo in c3_combos:
        c3_kpis = set(combo["kpis"])
        candidates = [k for k in eligible if k not in c3_kpis]

        print(f"    {combo['archetype']}: {combo['label']} → testing {len(candidates)} C4 extensions")

        best_c4 = None
        for extra_kpi in candidates:
            skip = any((extra_kpi, existing) in excl_set or (existing, extra_kpi) in excl_set
                       for existing in combo["kpis"])
            if skip:
                continue

            c4_kpis = combo["kpis"] + [extra_kpi]
            dim = KPI_DIM.get(extra_kpi, "momentum")
            if dim in BULL_ONLY_DIMS:
                pols_to_test = [combo["pols"] + [1]]
            else:
                pols_to_test = [combo["pols"] + [1], combo["pols"] + [-1]]

            for c4_pols in pols_to_test:
                r = sim_combo(all_pc, c4_kpis, c4_pols, tf,
                              exit_mode=combo.get("exit_mode", "standard"),
                              gate=combo.get("gate", "none"),
                              delay=combo.get("delay", 1),
                              start_frac=SEARCH_START, end_frac=1.0)
                if r and r["hr"] >= HR_FLOOR:
                    r.update({
                        "tf": tf, "archetype": combo["archetype"],
                        "exit_mode": combo.get("exit_mode", "standard"),
                        "gate": combo.get("gate", "none"),
                        "delay": combo.get("delay", 1),
                        "parent_c3": combo["label"],
                        "added_kpi": extra_kpi,
                        "added_pol": c4_pols[-1],
                    })
                    superset_results.append(r)
                    if best_c4 is None or r["pf"] > best_c4["pf"]:
                        best_c4 = r

        if best_c4:
            pstr = '+' if best_c4["added_pol"] == 1 else '-'
            print(f"      Best C4: +{_s(best_c4['added_kpi'])}({pstr}), "
                  f"PF={best_c4['pf']:.2f}, HR={best_c4['hr']:.1f}%, "
                  f"Tr={best_c4['trades']}")

    return superset_results


# ══════════════════════════════════════════════════════════════════════════════
# 18.6  PORTFOLIO CONSTRUCTION & MULTI-STRATEGY ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def phase_18_6_tf(all_pc, tf, strats):
    """Run Phase 18.6 portfolio analysis for a single timeframe."""
    if not strats:
        return None

    print(f"\n  18.6 PORTFOLIO — {tf}: {len(strats)} validated strategies")

    sym_signals = defaultdict(lambda: defaultdict(list))
    for si, strat in enumerate(strats):
        r = sim_combo(all_pc, strat["kpis"], strat["pols"], tf,
                      exit_mode=strat.get("exit_mode", "standard"),
                      gate=strat.get("gate", "none"),
                      delay=strat.get("delay", 1),
                      start_frac=OOS_START, end_frac=1.0,
                      min_trades=1, return_per_sym=True)
        if r and "per_sym" in r:
            for sym, trades in r["per_sym"].items():
                sym_signals[sym][si].extend(trades)

    total_syms = max(len(sym_signals), 1)
    overlapping = sum(1 for sym, sigs in sym_signals.items() if len(sigs) > 1)
    print(f"    Signal overlap: {overlapping}/{total_syms} stocks "
          f"({overlapping/total_syms*100:.0f}%) have signals from 2+ strategies")

    strat_pnls = {}
    for si, strat in enumerate(strats):
        r = sim_combo(all_pc, strat["kpis"], strat["pols"], tf,
                      exit_mode=strat.get("exit_mode", "standard"),
                      gate=strat.get("gate", "none"),
                      delay=strat.get("delay", 1),
                      start_frac=OOS_START, end_frac=1.0,
                      min_trades=1, return_per_sym=True)
        if r and "per_sym" in r:
            sym_pnl = {sym: sum(t[0] for t in trades)
                       for sym, trades in r["per_sym"].items()}
            strat_pnls[si] = sym_pnl

    if len(strat_pnls) >= 2:
        all_syms = sorted(set().union(*strat_pnls.values()))
        pnl_matrix = []
        strat_indices = sorted(strat_pnls.keys())
        for si in strat_indices:
            row = [strat_pnls[si].get(s, 0) for s in all_syms]
            pnl_matrix.append(row)
        pnl_matrix = np.array(pnl_matrix)

        print(f"    PnL correlation matrix ({len(strat_indices)} strategies):")
        for i in range(len(strat_indices)):
            for j in range(i + 1, len(strat_indices)):
                r_val, _ = spearmanr(pnl_matrix[i], pnl_matrix[j])
                si1, si2 = strat_indices[i], strat_indices[j]
                print(f"      {strats[si1]['label'][:25]} ↔ {strats[si2]['label'][:25]}: r={r_val:.3f}")

    unique_strategies = []
    for si, strat in enumerate(strats):
        has_mixed_pol = -1 in strat.get("pols", [1, 1, 1])
        unique_strategies.append({
            "idx": si,
            "label": strat["label"],
            "archetype": strat["archetype"],
            "has_mixed_polarity": has_mixed_pol,
            "OOS_pf": strat.get("OOS_pf", 0),
            "OOS_hr": strat.get("OOS_hr", 0),
            "OOS_trades": strat.get("OOS_trades", 0),
        })

    return {
        "n_strategies": len(strats),
        "n_overlapping_syms": overlapping,
        "total_syms": total_syms,
        "strategies": unique_strategies,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Report Generation
# ══════════════════════════════════════════════════════════════════════════════

def write_report(validated, failed, c4_superset, portfolio, elapsed_min):
    path = OUTPUTS_DIR / "PHASE18_REPORT.md"
    lines = [
        "# Phase 18 — Master Strategy Optimization Report",
        f"\nGenerated: {time.strftime('%Y-%m-%d %H:%M')}",
        f"Runtime: {elapsed_min:.1f} min",
        f"\n---\n",
        "## Executive Summary",
        f"\n**{len(validated)} strategies validated** across "
        f"{len(set(v['tf'] for v in validated))} timeframes and "
        f"{len(set(v['archetype'] for v in validated))} archetypes.",
    ]

    n_mixed = sum(1 for v in validated if -1 in v.get("pols", []))
    lines.append(f"**{n_mixed} use mixed polarity** (some KPIs bearish at entry).")

    # Top recommendations by TF
    by_tf = defaultdict(list)
    for v in validated:
        by_tf[v["tf"]].append(v)

    lines += ["\n### Top Recommendation by Timeframe\n",
              "| TF | Archetype | Combo | Mixed? | OOS HR | OOS PF | OOS Trades | Action |",
              "|---|---|---|---|---|---|---|---|"]

    for tf in ALL_TFS:
        strats = by_tf.get(tf, [])
        if not strats:
            continue
        strats.sort(key=lambda x: (-x.get("OOS_pf", 0)))
        best = strats[0]
        mixed = "Yes" if -1 in best.get("pols", []) else "No"
        action = "ADOPT" if best.get("OOS_pf", 0) >= 1.2 and best.get("OOS_trades", 0) >= 10 else "MONITOR"
        lines.append(
            f"| **{tf}** | {best['archetype']} | {best['label'][:35]} | {mixed} | "
            f"**{best.get('OOS_hr', 0):.1f}%** | **{best.get('OOS_pf', 0):.2f}** | "
            f"**{best.get('OOS_trades', 0)}** | **{action}** |")

    # Critical findings
    lines += ["\n---\n", "## Critical Findings\n"]

    # Stoof KPI analysis
    stoof_kpis = {"MACD_BL", "WT_LB_BL", "OBVOSC_BL", "CCI_Chop_BB_v1", "CCI_Chop_BB_v2",
                  "ADX_DI_BL", "LuxAlgo_Norm_v1", "LuxAlgo_Norm_v2", "Risk_Indicator", "PAI"}
    stoof_in_validated = sum(1 for v in validated if any(k in stoof_kpis for k in v["kpis"]))
    lines.append(f"### 1. Stoof KPI Contribution")
    lines.append(f"\n{stoof_in_validated}/{len(validated)} validated strategies include at least one Stoof KPI.\n")

    # Mixed polarity analysis
    lines.append(f"### 2. Mixed-Polarity Impact")
    lines.append(f"\n{n_mixed}/{len(validated)} validated strategies use mixed polarity (bearish KPIs at entry).\n")

    # All validated strategies
    lines += ["\n---\n", "## All Validated Strategies\n",
              "| # | TF | Arch | Combo | Exit | Gate | IS HR | IS PF | OOS HR | OOS PF | OOS Tr | OOS Hold |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, v in enumerate(sorted(validated, key=lambda x: (-x.get("OOS_pf", 0))), 1):
        lines.append(
            f"| {i} | {v['tf']} | {v['archetype']} | {v['label'][:35]} | "
            f"{v.get('exit_mode','?')} | {v.get('gate','?')} | "
            f"{v.get('IS_hr',0):.1f} | {v.get('IS_pf',0):.2f} | "
            f"{v.get('OOS_hr',0):.1f} | {v.get('OOS_pf',0):.2f} | "
            f"{v.get('OOS_trades',0)} | {v.get('OOS_avg_hold','—')} |")

    # C4 superset results
    if c4_superset:
        lines += ["\n---\n", "## C4 Superset Results (C3 + 1)\n"]
        top_c4 = sorted(c4_superset, key=lambda x: -x["pf"])[:15]
        lines += ["| TF | Parent C3 | +KPI | Pol | PF | HR% | Trades |",
                  "|---|---|---|---|---|---|---|"]
        for c in top_c4:
            pstr = '+' if c.get("added_pol", 1) == 1 else '-'
            lines.append(f"| {c.get('tf','?')} | {c.get('parent_c3','?')[:30]} | "
                         f"{_s(c.get('added_kpi','?'))} | {pstr} | "
                         f"{c['pf']:.2f} | {c['hr']:.1f} | {c['trades']} |")

    # Portfolio analysis
    if portfolio:
        lines += ["\n---\n", "## Portfolio Analysis\n"]
        for tf, info in portfolio.items():
            lines.append(f"### {tf}")
            lines.append(f"- Strategies: {info['n_strategies']}")
            lines.append(f"- Signal overlap: {info['n_overlapping_syms']}/{info['total_syms']} stocks\n")

    lines += ["\n---\n", "## Output Files\n",
              "| File | Contents |",
              "|---|---|",
              "| `step0/kpi_coverage.json` | KPI state distributions per TF |",
              "| `phase18_1_combos.json` | Stage 1 top combos |",
              "| `phase18_2_entry.json` | Entry gate/delay results |",
              "| `phase18_3_exit.json` | Exit mode/TMK results |",
              "| `phase18_4_validated.json` | Walk-forward validated strategies |",
              "| `phase18_validated.csv` | Validated strategies CSV |",
              "| `phase18_5_c4_superset.json` | C4 superset results |",
              "| `phase18_6_portfolio.json` | Portfolio analysis |"]

    with open(path, "w") as f:
        f.write("\n".join(lines))
    print(f"\n  Report saved: {path}")


# ══════════════════════════════════════════════════════════════════════════════
# Utilities
# ══════════════════════════════════════════════════════════════════════════════

def _save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    MEM_THRESHOLD = 70  # percent — abort if exceeded after gc

    t0 = time.time()
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    STEP0_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Phase 18 — Master Strategy Optimization Pipeline")
    print(f"{'='*80}")
    print(f"Archetypes: {list(ARCHETYPES.keys())}")
    print(f"Timeframes: {ALL_TFS}")
    print(f"OOS split: IS={OOS_START:.0%}-{OOS_B_START:.0%}, OOS-B={OOS_B_START:.0%}-100%")
    print(f"Memory limit: {MEM_THRESHOLD}%")
    _check_memory("startup", MEM_THRESHOLD)

    # ── Determine available TFs ──
    available_tfs = []
    for tf in ALL_TFS:
        count = len(list(ENRICHED_DIR.glob(f"*_{tf}.parquet")))
        min_count = 30 if tf in ("1M", "2W") else 50
        if count < min_count:
            print(f"  {tf}: SKIPPED ({count} parquets, need >={min_count})")
        else:
            print(f"  {tf}: {count} parquets available")
            available_tfs.append(tf)

    if not available_tfs:
        print("ERROR: No timeframes have sufficient data. Run phase18_reenrich.py first.")
        return

    # ══════════════════════════════════════════════════════════════════════════
    # PASS 1: Audit (lightweight — load raw DFs one TF at a time, free after)
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{'#'*80}")
    print(f"  PHASE 18.0 — DATA AUDIT & KPI SCORECARD")
    print(f"{'#'*80}")

    eligible_all = {}
    exclusion_all = {}
    coverage_all = {}

    for tf in available_tfs:
        _check_memory(f"18.0 audit {tf}", MEM_THRESHOLD)
        data = load_data(tf)
        tf_cov, eligible, excl_pairs = phase_18_0_single_tf(data, tf)
        eligible_all[tf] = eligible
        exclusion_all[tf] = excl_pairs
        coverage_all[tf] = tf_cov
        del data
        gc.collect()

    _save_json(STEP0_DIR / "kpi_coverage.json", coverage_all)
    _save_json(STEP0_DIR / "exclusion_pairs.json",
               {tf: [[a, b] for a, b in pairs] for tf, pairs in exclusion_all.items()})
    print(f"\n  Step 0 outputs saved to {STEP0_DIR}")

    # ══════════════════════════════════════════════════════════════════════════
    # PASS 2: Per-TF pipeline (18.1 → 18.5) — load, process, free each TF
    # ══════════════════════════════════════════════════════════════════════════
    all_kpis = list(KPI_DIM.keys())
    all_validated = []
    all_failed = []
    all_c4 = []
    all_s1_flat = []

    for tf in available_tfs:
        print(f"\n{'#'*80}")
        print(f"  PROCESSING TIMEFRAME: {tf}")
        print(f"{'#'*80}")
        _check_memory(f"pre-load {tf}", MEM_THRESHOLD)

        # Load and precompute for this TF only
        print(f"  Loading {tf}...", end=" ", flush=True)
        data = load_data(tf)
        print(f"{len(data)} stocks loaded")
        print(f"  Pre-computing KPI states...", end=" ", flush=True)
        all_pc = precompute(data, tf, all_kpis)
        print(f"{len(all_pc)} valid")
        del data  # free raw DataFrames — precomputed arrays are sufficient
        gc.collect()
        _check_memory(f"post-precompute {tf}", MEM_THRESHOLD)

        eligible = eligible_all.get(tf, [])
        excl_pairs = exclusion_all.get(tf, [])

        # 18.1 Combo Discovery
        s1 = phase_18_1_tf(all_pc, tf, eligible, excl_pairs)

        # Flatten s1 for saving
        for arch_key, arch_res in s1.items():
            for size_label, combos in arch_res.items():
                for r in combos:
                    r["tf"] = tf
                    r["size"] = size_label
                    all_s1_flat.append(r)

        # 18.2 Entry Gate & Delay
        s2 = phase_18_2_tf(all_pc, tf, s1)

        # 18.3 Exit Optimization
        s3 = phase_18_3_tf(all_pc, tf, s2)

        # 18.4 Walk-Forward Validation
        validated_tf, failed_tf = phase_18_4_tf(all_pc, tf, s3)
        all_validated.extend(validated_tf)
        all_failed.extend(failed_tf)

        # 18.5 C4 Superset
        c4_tf = phase_18_5_tf(all_pc, tf, validated_tf, eligible, excl_pairs)
        all_c4.extend(c4_tf)

        # Free this TF's precomputed data before loading the next
        del all_pc, s1, s2, s3, validated_tf, failed_tf, c4_tf
        gc.collect()
        _check_memory(f"post-free {tf}", MEM_THRESHOLD)

    # Save intermediate results
    _save_json(OUTPUTS_DIR / "phase18_1_combos.json", all_s1_flat)
    _save_json(OUTPUTS_DIR / "phase18_4_validated.json", all_validated)
    _save_json(OUTPUTS_DIR / "phase18_4_failed.json", all_failed)
    _save_json(OUTPUTS_DIR / "phase18_5_c4_superset.json", all_c4)

    if all_validated:
        fnames = ["tf", "archetype", "label", "exit_mode", "gate", "delay",
                  "IS_trades", "IS_hr", "IS_pf", "IS_avg_ret", "IS_pnl",
                  "OOS_trades", "OOS_hr", "OOS_pf", "OOS_avg_ret", "OOS_pnl",
                  "OOS_avg_hold", "OOS_worst", "hr_decay", "pf_ratio", "validated"]
        with open(OUTPUTS_DIR / "phase18_validated.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(all_validated)

    print(f"\n  Validated total: {len(all_validated)}, Failed total: {len(all_failed)}")

    # ══════════════════════════════════════════════════════════════════════════
    # PASS 3: Portfolio analysis (reload each TF one at a time)
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{'#'*80}")
    print(f"  PHASE 18.6 — PORTFOLIO CONSTRUCTION & MULTI-STRATEGY ANALYSIS")
    print(f"{'#'*80}")

    by_tf = defaultdict(list)
    for v in all_validated:
        by_tf[v["tf"]].append(v)

    portfolio_report = {}
    for tf in available_tfs:
        strats = by_tf.get(tf, [])
        if not strats:
            continue

        _check_memory(f"18.6 reload {tf}", MEM_THRESHOLD)
        data = load_data(tf)
        all_pc = precompute(data, tf, all_kpis)
        del data
        gc.collect()

        result = phase_18_6_tf(all_pc, tf, strats)
        if result:
            portfolio_report[tf] = result

        del all_pc
        gc.collect()

    _save_json(OUTPUTS_DIR / "phase18_6_portfolio.json", portfolio_report)
    print(f"\n  18.6 saved: portfolio analysis for {len(portfolio_report)} TFs")

    # ══════════════════════════════════════════════════════════════════════════
    # Final Report & Recommendation
    # ══════════════════════════════════════════════════════════════════════════
    elapsed = (time.time() - t0) / 60
    write_report(all_validated, all_failed, all_c4, portfolio_report, elapsed)

    rec = {"recommendation": "ADOPT" if all_validated else "HOLD_CURRENT",
           "total_validated": len(all_validated),
           "n_mixed_polarity": sum(1 for v in all_validated if -1 in v.get("pols", [])),
           "runtime_min": round(elapsed, 1)}
    if all_validated:
        best = sorted(all_validated, key=lambda x: -x.get("OOS_pf", 0))[0]
        rec["best_strategy"] = {k: v for k, v in best.items() if k != "per_sym"}
    _save_json(OUTPUTS_DIR / "phase18_recommendation.json", rec)

    print(f"\n{'='*80}")
    print(f"Phase 18 COMPLETE — {elapsed:.1f} min")
    print(f"  Validated: {len(all_validated)}")
    print(f"  Mixed-polarity: {rec['n_mixed_polarity']}")
    print(f"  C4 superset candidates: {len(all_c4)}")
    print(f"  Output: {OUTPUTS_DIR}")
    _check_memory("final", MEM_THRESHOLD)
    print(f"{'='*80}")


if __name__ == "__main__":
    main()

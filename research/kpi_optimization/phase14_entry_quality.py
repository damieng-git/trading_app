"""
Phase 14 — Entry Quality Optimization (Run 1)

Three independent experiments to improve per-trade quality, tested on two
datasets (sample_300 + entry_stocks), 1D timeframe, onset-only entries.

  14a  SMA gate variants
       Compare Close>SMA200 (current) with SMA20>SMA200, SMA20>SMA50,
       SMA50>SMA200, Close>SMA100, SMA20>SMA100, SMA100>SMA200, and
       full trend stacks.

  14b  Breakout confirmation layer
       Require a recent breakout signal (BB30 dip, NWE bull, cRSI breakout,
       SR Breaks, volume spike, Stoch_MTM transition) within N bars of C3
       onset. Tests individual signals + a confluence score.

  14f  ATR% risk gate
       Skip entries where the initial stop distance (4×ATR / Close) is
       too wide (high risk) or too tight (whipsaw).

Baseline: Exit Flow v4, C3 onset + C4 scale-up, Close>SMA200 on 1D,
          0.1% commission, OOS = last 30%.
"""

from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from trading_dashboard.kpis.catalog import compute_kpi_state_map
from trading_dashboard.kpis.rules import STATE_BULL

OUTPUTS_ROOT = Path(__file__).resolve().parent / "outputs" / "all" / "phase14"

OOS_FRACTION = 0.70
ATR_PERIOD = 14
MAX_HOLD = 500
COMM = 0.1

EXIT_PARAMS_1D = {"T": 4, "M": 40, "K": 4.0}

C3_KPIS = ["Nadaraya-Watson Smoother", "Madrid Ribbon", "Volume + MA20"]
C4_KPIS = ["Nadaraya-Watson Smoother", "Madrid Ribbon", "GK Trend Ribbon", "cRSI"]


# ── Dataset configuration ───────────────────────────────────────────────

def _load_entry_stocks() -> set:
    path = REPO_DIR / "apps" / "dashboard" / "configs" / "lists" / "entry_stocks.csv"
    with open(path, encoding="utf-8") as f:
        return {row[0].strip().upper() for row in csv.reader(f)
                if row and row[0].strip().lower() != "ticker" and row[0].strip()}


DATASETS = {
    "sample_300": {
        "enriched_dir": REPO_DIR / "research" / "data" / "feature_store"
                        / "enriched" / "sample_300" / "stock_data",
        "ticker_filter": None,
        "label": "sample_300",
    },
    "entry_stocks": {
        "enriched_dir": REPO_DIR / "data" / "feature_store"
                        / "enriched" / "dashboard" / "stock_data",
        "ticker_filter": _load_entry_stocks(),
        "label": "entry_stocks",
    },
}


# ── Helpers ──────────────────────────────────────────────────────────────

def compute_atr(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    h, lo, pc = df["High"].to_numpy(float), df["Low"].to_numpy(float), np.roll(df["Close"].to_numpy(float), 1)
    pc[0] = np.nan
    tr = np.maximum(h - lo, np.maximum(np.abs(h - pc), np.abs(lo - pc)))
    atr = pd.Series(tr).rolling(window=period, min_periods=1).mean().to_numpy(float)
    return atr


def load_data(enriched_dir: Path, timeframe: str,
              ticker_filter: set | None = None) -> Dict[str, pd.DataFrame]:
    data = {}
    for f in sorted(enriched_dir.glob(f"*_{timeframe}.parquet")):
        symbol = f.stem.rsplit(f"_{timeframe}", 1)[0]
        if ticker_filter and symbol not in ticker_filter:
            continue
        try:
            df = pd.read_parquet(f)
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index, errors="coerce")
            if hasattr(df.index, "tz") and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            df = df.sort_index()
            if len(df) >= 200 and "Close" in df.columns:
                data[symbol] = df
        except Exception:
            continue
    return data


def precompute(data: dict) -> dict:
    """Pre-compute everything needed for all three experiments."""
    precomp = {}
    for sym, df in data.items():
        sm = compute_kpi_state_map(df)
        if any(k not in sm for k in C3_KPIS):
            continue

        n = len(df)
        cl = df["Close"].to_numpy(float)
        vol = df["Volume"].to_numpy(float) if "Volume" in df.columns else np.zeros(n)
        atr = compute_atr(df, ATR_PERIOD)

        c3_bull = np.ones(n, dtype=bool)
        for k in C3_KPIS:
            c3_bull &= (sm[k] == STATE_BULL).to_numpy(bool)

        c4_avail = all(k in sm for k in C4_KPIS)
        c4_bull = np.zeros(n, dtype=bool)
        if c4_avail:
            c4_bull = np.ones(n, dtype=bool)
            for k in C4_KPIS:
                c4_bull &= (sm[k] == STATE_BULL).to_numpy(bool)

        # SMA arrays
        cl_s = pd.Series(cl)
        sma20 = cl_s.rolling(20, min_periods=20).mean().to_numpy(float)
        sma50 = cl_s.rolling(50, min_periods=50).mean().to_numpy(float)
        sma100 = cl_s.rolling(100, min_periods=100).mean().to_numpy(float)
        sma200 = cl_s.rolling(200, min_periods=200).mean().to_numpy(float)

        # Volume MA20
        vol_ma20 = pd.Series(vol).rolling(20, min_periods=1).mean().to_numpy(float)

        # Breakout signals (boolean arrays, True on signal bar only)
        bb_lower = pd.to_numeric(df.get("BB_lower", pd.Series(dtype=float)), errors="coerce").to_numpy(float)
        bb_dip = cl < bb_lower  # Close touched below BB lower band

        nwe_mae_bull = np.zeros(n, dtype=bool)
        if "NWE_MAE_env_crossunder" in df.columns:
            nwe_mae_bull = df["NWE_MAE_env_crossunder"].fillna(False).to_numpy(bool)
        elif "NWE_MAE_env_lower" in df.columns:
            lower = pd.to_numeric(df["NWE_MAE_env_lower"], errors="coerce").to_numpy(float)
            prev_cl = np.roll(cl, 1); prev_cl[0] = np.nan
            prev_lower = np.roll(lower, 1); prev_lower[0] = np.nan
            nwe_mae_bull = (cl < lower) & (prev_cl >= prev_lower)

        nwe_std_bull = np.zeros(n, dtype=bool)
        if "NWE_STD_env_crossunder" in df.columns:
            nwe_std_bull = df["NWE_STD_env_crossunder"].fillna(False).to_numpy(bool)
        elif "NWE_STD_env_lower" in df.columns:
            lower = pd.to_numeric(df["NWE_STD_env_lower"], errors="coerce").to_numpy(float)
            prev_cl = np.roll(cl, 1); prev_cl[0] = np.nan
            prev_lower = np.roll(lower, 1); prev_lower[0] = np.nan
            nwe_std_bull = (cl < lower) & (prev_cl >= prev_lower)

        crsi_breakout = np.zeros(n, dtype=bool)
        if "cRSI" in sm:
            crsi_s = (sm["cRSI"] == STATE_BULL).to_numpy(bool)
            crsi_prev = np.roll(crsi_s, 1); crsi_prev[0] = False
            crsi_breakout = crsi_s & ~crsi_prev

        sr_break = np.zeros(n, dtype=bool)
        if "SR_state" in df.columns:
            sr = pd.to_numeric(df["SR_state"], errors="coerce").to_numpy(float)
            sr_prev = np.roll(sr, 1); sr_prev[0] = np.nan
            sr_break = (sr == 1) & (sr_prev != 1)

        vol_spike = np.zeros(n, dtype=bool)
        if vol.sum() > 0:
            vol_spike = vol > (1.5 * vol_ma20)

        stoch_trans = np.zeros(n, dtype=bool)
        if "Stoch_MTM" in sm:
            stoch_s = (sm["Stoch_MTM"] == STATE_BULL).to_numpy(bool)
            stoch_prev = np.roll(stoch_s, 1); stoch_prev[0] = False
            stoch_trans = stoch_s & ~stoch_prev

        # Per-KPI non-bull for exit logic
        kpi_nbull = {}
        for k in set(C3_KPIS + C4_KPIS):
            if k in sm:
                kpi_nbull[k] = (sm[k] != STATE_BULL).to_numpy(bool)

        precomp[sym] = {
            "n": n, "cl": cl, "vol": vol, "atr": atr,
            "c3_bull": c3_bull, "c4_avail": c4_avail, "c4_bull": c4_bull,
            "sma20": sma20, "sma50": sma50, "sma100": sma100, "sma200": sma200,
            "vol_ma20": vol_ma20,
            "bb_dip": bb_dip,
            "nwe_mae_bull": nwe_mae_bull, "nwe_std_bull": nwe_std_bull,
            "crsi_breakout": crsi_breakout,
            "sr_break": sr_break,
            "vol_spike": vol_spike,
            "stoch_trans": stoch_trans,
            "kpi_nbull": kpi_nbull, "sm": sm,
        }
    return precomp


# ── Trade execution (shared) ────────────────────────────────────────────

def _run_trade(pc: dict, j: int) -> tuple | None:
    """Execute one trade from bar j. Returns (weighted_ret, raw_ret, hold_bars)."""
    T, M, K = EXIT_PARAMS_1D["T"], EXIT_PARAMS_1D["M"], EXIT_PARAMS_1D["K"]
    cl = pc["cl"]; atr = pc["atr"]; n = pc["n"]
    c4_avail = pc["c4_avail"]; c4_bull = pc["c4_bull"]
    kpi_nbull = pc["kpi_nbull"]

    ep = cl[j]
    if ep <= 0:
        return None
    stop = ep - K * atr[j] if atr[j] > 0 else -np.inf
    bars_since_reset = 0
    size = 1.5 if (c4_avail and c4_bull[j]) else 1.0
    was_scaled = size == 1.5
    active = C4_KPIS if was_scaled else C3_KPIS
    nk = len(active)
    xi = None

    jj = j + 1
    while jj < min(j + MAX_HOLD + 1, n):
        bars_since_reset += 1
        c = cl[jj]
        bars = jj - j

        if c < stop:
            xi = jj; break

        if not was_scaled and c4_avail and c4_bull[jj]:
            size = 1.5; was_scaled = True
            active = C4_KPIS; nk = len(active)

        nb = sum(1 for kk in active if kk in kpi_nbull and jj < len(kpi_nbull[kk]) and kpi_nbull[kk][jj])

        if bars <= T:
            if nb >= nk:
                xi = jj; break
        else:
            if nb >= 2:
                xi = jj; break

        if bars_since_reset >= M:
            if nb == 0:
                stop = c - K * atr[jj] if jj < len(atr) and atr[jj] > 0 else stop
                bars_since_reset = 0
            else:
                xi = jj; break
        jj += 1

    if xi is None:
        xi = min(jj, n - 1)
    xp = cl[xi]
    h = xi - j
    if h <= 0:
        return None
    ret = (xp - ep) / ep * 100 - COMM
    return (ret * size, ret, h)


def _stats(trades: list) -> dict:
    if not trades:
        return {"n": 0, "hr": 0, "pnl": 0, "pf": 0, "avg": 0, "worst": 0, "avg_hold": 0}
    rw = [t[0] for t in trades]
    ru = [t[1] for t in trades]
    nt = len(trades)
    hr = sum(1 for r in rw if r > 0) / nt * 100
    wi = sum(r for r in rw if r > 0)
    lo = abs(sum(r for r in rw if r <= 0))
    return {
        "n": nt, "hr": round(hr, 1), "pnl": round(sum(rw)),
        "pf": round(wi / lo if lo > 0 else 999, 2),
        "avg": round(float(np.mean(ru)), 2),
        "worst": round(min(ru), 1),
        "avg_hold": round(float(np.mean([t[2] for t in trades])), 1),
    }


def _find_onsets(pc: dict):
    """Yield onset bar indices in OOS region that pass Close>SMA200."""
    c3 = pc["c3_bull"]; cl = pc["cl"]; n = pc["n"]; sma200 = pc["sma200"]
    si = int(n * OOS_FRACTION)
    j = si
    while j < n:
        if c3[j] and (j == 0 or not c3[j - 1]):
            if not np.isnan(sma200[j]) and cl[j] >= sma200[j]:
                yield j
                trade = _run_trade(pc, j)
                if trade:
                    j = j + trade[2] + 1
                    continue
        j += 1


# ═════════════════════════════════════════════════════════════════════════
# 14a — SMA gate variants
# ═════════════════════════════════════════════════════════════════════════

SMA_FILTERS = {
    "No filter":              lambda pc, j: True,
    "Close > SMA20":          lambda pc, j: not np.isnan(pc["sma20"][j]) and pc["cl"][j] >= pc["sma20"][j],
    "Close > SMA50":          lambda pc, j: not np.isnan(pc["sma50"][j]) and pc["cl"][j] >= pc["sma50"][j],
    "Close > SMA100":         lambda pc, j: not np.isnan(pc["sma100"][j]) and pc["cl"][j] >= pc["sma100"][j],
    "Close > SMA200":         lambda pc, j: not np.isnan(pc["sma200"][j]) and pc["cl"][j] >= pc["sma200"][j],
    "SMA20 > SMA50":          lambda pc, j: not np.isnan(pc["sma20"][j]) and not np.isnan(pc["sma50"][j]) and pc["sma20"][j] > pc["sma50"][j],
    "SMA20 > SMA100":         lambda pc, j: not np.isnan(pc["sma20"][j]) and not np.isnan(pc["sma100"][j]) and pc["sma20"][j] > pc["sma100"][j],
    "SMA20 > SMA200":         lambda pc, j: not np.isnan(pc["sma20"][j]) and not np.isnan(pc["sma200"][j]) and pc["sma20"][j] > pc["sma200"][j],
    "SMA50 > SMA100":         lambda pc, j: not np.isnan(pc["sma50"][j]) and not np.isnan(pc["sma100"][j]) and pc["sma50"][j] > pc["sma100"][j],
    "SMA50 > SMA200":         lambda pc, j: not np.isnan(pc["sma50"][j]) and not np.isnan(pc["sma200"][j]) and pc["sma50"][j] > pc["sma200"][j],
    "SMA100 > SMA200":        lambda pc, j: not np.isnan(pc["sma100"][j]) and not np.isnan(pc["sma200"][j]) and pc["sma100"][j] > pc["sma200"][j],
    "SMA20>50>200 stack":     lambda pc, j: (not np.isnan(pc["sma20"][j]) and not np.isnan(pc["sma50"][j]) and not np.isnan(pc["sma200"][j])
                                              and pc["sma20"][j] > pc["sma50"][j] > pc["sma200"][j]),
    "SMA20>50>100>200 stack": lambda pc, j: (not np.isnan(pc["sma20"][j]) and not np.isnan(pc["sma50"][j]) and not np.isnan(pc["sma100"][j]) and not np.isnan(pc["sma200"][j])
                                              and pc["sma20"][j] > pc["sma50"][j] > pc["sma100"][j] > pc["sma200"][j]),
}


def run_14a(precomp: dict) -> list:
    results = []
    for label, gate_fn in SMA_FILTERS.items():
        trades = []
        blocked = 0
        for sym, pc in precomp.items():
            c3 = pc["c3_bull"]; n = pc["n"]
            si = int(n * OOS_FRACTION)
            j = si
            while j < n:
                if not c3[j] or (j > 0 and c3[j - 1]):
                    j += 1; continue
                if not gate_fn(pc, j):
                    blocked += 1; j += 1; continue
                trade = _run_trade(pc, j)
                if trade is None:
                    j += 1; continue
                trades.append(trade)
                j = j + trade[2] + 1
        st = _stats(trades)
        st["label"] = label
        st["blocked"] = blocked
        results.append(st)
    return results


# ═════════════════════════════════════════════════════════════════════════
# 14b — Breakout confirmation layer
# ═════════════════════════════════════════════════════════════════════════

BREAKOUT_SIGNALS = {
    "BB30 dip":       "bb_dip",
    "NWE(MAE) bull":  "nwe_mae_bull",
    "NWE(STD) bull":  "nwe_std_bull",
    "cRSI breakout":  "crsi_breakout",
    "SR Break":       "sr_break",
    "Vol spike 1.5x": "vol_spike",
    "Stoch_MTM trans": "stoch_trans",
}

LOOKBACK_WINDOWS = [1, 2, 3, 5, 10]


def _recent_signal(arr: np.ndarray, j: int, lookback: int) -> bool:
    """Check if any True in arr[j-lookback+1 : j+1]."""
    start = max(0, j - lookback + 1)
    return bool(np.any(arr[start:j + 1]))


def run_14b(precomp: dict) -> list:
    results = []

    # Baseline (onset + Close>SMA200, no breakout requirement)
    base_trades = []
    for sym, pc in precomp.items():
        c3 = pc["c3_bull"]; cl = pc["cl"]; n = pc["n"]; sma200 = pc["sma200"]
        si = int(n * OOS_FRACTION)
        j = si
        while j < n:
            if not c3[j] or (j > 0 and c3[j - 1]):
                j += 1; continue
            if np.isnan(sma200[j]) or cl[j] < sma200[j]:
                j += 1; continue
            trade = _run_trade(pc, j)
            if trade is None:
                j += 1; continue
            base_trades.append(trade)
            j = j + trade[2] + 1
    st = _stats(base_trades)
    st["label"] = "Baseline (no breakout)"
    st["blocked"] = 0
    results.append(st)

    # Individual breakout signals at various lookbacks
    for sig_label, sig_key in BREAKOUT_SIGNALS.items():
        for lb in LOOKBACK_WINDOWS:
            trades = []
            blocked = 0
            for sym, pc in precomp.items():
                c3 = pc["c3_bull"]; cl = pc["cl"]; n = pc["n"]; sma200 = pc["sma200"]
                sig_arr = pc[sig_key]
                si = int(n * OOS_FRACTION)
                j = si
                while j < n:
                    if not c3[j] or (j > 0 and c3[j - 1]):
                        j += 1; continue
                    if np.isnan(sma200[j]) or cl[j] < sma200[j]:
                        j += 1; continue
                    if not _recent_signal(sig_arr, j, lb):
                        blocked += 1; j += 1; continue
                    trade = _run_trade(pc, j)
                    if trade is None:
                        j += 1; continue
                    trades.append(trade)
                    j = j + trade[2] + 1
            st = _stats(trades)
            st["label"] = f"{sig_label} N={lb}"
            st["blocked"] = blocked
            results.append(st)

    # Confluence score: count how many breakout signals fired within N bars
    for lb in [3, 5, 10]:
        for min_score in [1, 2, 3]:
            trades = []
            blocked = 0
            for sym, pc in precomp.items():
                c3 = pc["c3_bull"]; cl = pc["cl"]; n = pc["n"]; sma200 = pc["sma200"]
                si = int(n * OOS_FRACTION)
                j = si
                while j < n:
                    if not c3[j] or (j > 0 and c3[j - 1]):
                        j += 1; continue
                    if np.isnan(sma200[j]) or cl[j] < sma200[j]:
                        j += 1; continue
                    score = sum(1 for sig_key in BREAKOUT_SIGNALS.values()
                                if _recent_signal(pc[sig_key], j, lb))
                    if score < min_score:
                        blocked += 1; j += 1; continue
                    trade = _run_trade(pc, j)
                    if trade is None:
                        j += 1; continue
                    trades.append(trade)
                    j = j + trade[2] + 1
            st = _stats(trades)
            st["label"] = f"Confluence>={min_score} N={lb}"
            st["blocked"] = blocked
            results.append(st)

    return results


# ═════════════════════════════════════════════════════════════════════════
# 14f — ATR% risk gate
# ═════════════════════════════════════════════════════════════════════════

def run_14f(precomp: dict) -> list:
    max_thresholds = [2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 100.0]  # 100 = no cap
    min_thresholds = [0.0, 0.3, 0.5, 0.8, 1.0]  # 0 = no floor

    results = []

    for max_pct in max_thresholds:
        for min_pct in min_thresholds:
            if min_pct >= max_pct:
                continue
            trades = []
            blocked = 0
            for sym, pc in precomp.items():
                c3 = pc["c3_bull"]; cl = pc["cl"]; n = pc["n"]
                sma200 = pc["sma200"]; atr = pc["atr"]
                si = int(n * OOS_FRACTION)
                j = si
                while j < n:
                    if not c3[j] or (j > 0 and c3[j - 1]):
                        j += 1; continue
                    if np.isnan(sma200[j]) or cl[j] < sma200[j]:
                        j += 1; continue
                    atr_pct = (atr[j] / cl[j]) * 100 if cl[j] > 0 else 999
                    risk_pct = atr_pct * EXIT_PARAMS_1D["K"]  # 4× ATR as stop distance
                    if risk_pct > max_pct * EXIT_PARAMS_1D["K"] or atr_pct < min_pct:
                        blocked += 1; j += 1; continue
                    trade = _run_trade(pc, j)
                    if trade is None:
                        j += 1; continue
                    trades.append(trade)
                    j = j + trade[2] + 1

            st = _stats(trades)
            max_label = f"{max_pct}%" if max_pct < 100 else "none"
            min_label = f"{min_pct}%" if min_pct > 0 else "none"
            st["label"] = f"ATR%: [{min_label}, {max_label}]"
            st["blocked"] = blocked
            st["max_atr_pct"] = max_pct
            st["min_atr_pct"] = min_pct
            results.append(st)

    return results


# ═════════════════════════════════════════════════════════════════════════
# Print helpers
# ═════════════════════════════════════════════════════════════════════════

def _hdr():
    print(f"    {'Label':<30} {'Trades':>7} {'Blk':>6} {'HR%':>6} {'PnL':>10} "
          f"{'Δ PnL%':>8} {'PF':>7} {'Avg%':>7} {'Worst%':>7} {'Hold':>5}")
    print(f"    {'—'*30} {'—'*7} {'—'*6} {'—'*6} {'—'*10} {'—'*8} {'—'*7} {'—'*7} {'—'*7} {'—'*5}")


def _row(r: dict, base_pnl: float = 0):
    d = (r["pnl"] - base_pnl) / abs(base_pnl) * 100 if base_pnl else 0
    print(f"    {r['label']:<30} {r['n']:>7} {r.get('blocked',0):>6} {r['hr']:>6.1f} {r['pnl']:>+10} "
          f"{d:>+7.1f}% {r['pf']:>7.1f} {r['avg']:>+7.2f} {r['worst']:>+7.1f} {r['avg_hold']:>5.0f}")


# ═════════════════════════════════════════════════════════════════════════
# Main
# ═════════════════════════════════════════════════════════════════════════

def main():
    t0 = time.time()
    OUTPUTS_ROOT.mkdir(parents=True, exist_ok=True)
    all_results: Dict[str, Any] = {}

    print("=" * 100)
    print("  Phase 14 — Entry Quality Optimization (Run 1)")
    print("  14a: SMA gate variants | 14b: Breakout confirmation | 14f: ATR% risk gate")
    print("  Dual-dataset: sample_300 + entry_stocks, 1D, onset-only, OOS 30%, 0.1% comm")
    print("=" * 100)

    for ds_name, ds_cfg in DATASETS.items():
        print(f"\n{'╔' + '═'*98 + '╗'}")
        print(f"{'║'} DATASET: {ds_cfg['label']:<88}{'║'}")
        print(f"{'╚' + '═'*98 + '╝'}")

        data = load_data(ds_cfg["enriched_dir"], "1D", ds_cfg.get("ticker_filter"))
        print(f"  Loaded {len(data)} stocks")

        t1 = time.time()
        pc = precompute(data)
        print(f"  Pre-compute done ({time.time()-t1:.0f}s, {len(pc)} stocks)")

        # ── 14a ──────────────────────────────────────────────────────────
        print(f"\n  ┌─ 14a: SMA Gate Variants ─────────────────────────────")
        res_a = run_14a(pc)
        _hdr()
        bp = res_a[0]["pnl"]
        for r in res_a:
            _row(r, bp)

        # ── 14b ──────────────────────────────────────────────────────────
        print(f"\n  ┌─ 14b: Breakout Confirmation Layer ──────────────────")
        res_b = run_14b(pc)
        _hdr()
        bp_b = res_b[0]["pnl"]
        for r in res_b:
            _row(r, bp_b)

        # ── 14f ──────────────────────────────────────────────────────────
        print(f"\n  ┌─ 14f: ATR% Risk Gate ───────────────────────────────")
        res_f = run_14f(pc)
        # Sort by PF descending for readability
        res_f.sort(key=lambda x: -x["pf"])
        _hdr()
        bp_f = next((r["pnl"] for r in res_f if r.get("max_atr_pct", 0) >= 100 and r.get("min_atr_pct", 0) == 0), res_f[0]["pnl"])
        for r in res_f:
            _row(r, bp_f)

        all_results[ds_name] = {"14a": res_a, "14b": res_b, "14f": res_f}

    # ── Cross-dataset comparison (best findings) ─────────────────────────
    print(f"\n{'═' * 100}")
    print(f"  CROSS-DATASET COMPARISON — Best findings")
    print(f"{'═' * 100}")

    if "sample_300" in all_results and "entry_stocks" in all_results:
        # 14a: compare top SMA filters
        print(f"\n  14a — SMA Gate (top by PF):")
        print(f"    {'Filter':<30} {'── sample_300 ──':>32} {'── entry_stocks ──':>32}")
        print(f"    {'':30} {'Trades':>7} {'HR%':>6} {'PF':>7} {'Avg%':>7}  "
              f"{'Trades':>7} {'HR%':>6} {'PF':>7} {'Avg%':>7}")
        s_a = {r["label"]: r for r in all_results["sample_300"]["14a"]}
        e_a = {r["label"]: r for r in all_results["entry_stocks"]["14a"]}
        for lbl in SMA_FILTERS.keys():
            if lbl in s_a and lbl in e_a:
                sa, ea = s_a[lbl], e_a[lbl]
                print(f"    {lbl:<30} {sa['n']:>7} {sa['hr']:>6.1f} {sa['pf']:>7.1f} {sa['avg']:>+7.2f}  "
                      f"{ea['n']:>7} {ea['hr']:>6.1f} {ea['pf']:>7.1f} {ea['avg']:>+7.2f}")

        # 14b: compare top breakout filters
        print(f"\n  14b — Breakout Confirmation (top 10 by PF, consistent):")
        s_b = {r["label"]: r for r in all_results["sample_300"]["14b"]}
        e_b = {r["label"]: r for r in all_results["entry_stocks"]["14b"]}
        common = sorted(set(s_b.keys()) & set(e_b.keys()),
                        key=lambda l: -(s_b[l]["pf"] + e_b[l]["pf"]) / 2)
        print(f"    {'Filter':<30} {'── sample_300 ──':>32} {'── entry_stocks ──':>32}")
        print(f"    {'':30} {'Trades':>7} {'HR%':>6} {'PF':>7} {'Δ PnL%':>8}  "
              f"{'Trades':>7} {'HR%':>6} {'PF':>7} {'Δ PnL%':>8}")
        sb_base = all_results["sample_300"]["14b"][0]["pnl"]
        eb_base = all_results["entry_stocks"]["14b"][0]["pnl"]
        for lbl in common[:12]:
            sb, eb = s_b[lbl], e_b[lbl]
            sd = (sb["pnl"] - sb_base) / abs(sb_base) * 100 if sb_base else 0
            ed = (eb["pnl"] - eb_base) / abs(eb_base) * 100 if eb_base else 0
            print(f"    {lbl:<30} {sb['n']:>7} {sb['hr']:>6.1f} {sb['pf']:>7.1f} {sd:>+7.1f}%  "
                  f"{eb['n']:>7} {eb['hr']:>6.1f} {eb['pf']:>7.1f} {ed:>+7.1f}%")

    # ── Save ─────────────────────────────────────────────────────────────
    def _ser(obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        return str(obj)

    jp = OUTPUTS_ROOT / "phase14_results.json"
    jp.write_text(json.dumps(all_results, indent=2, default=_ser), encoding="utf-8")
    print(f"\n  Results saved to {jp}")
    print(f"  Total runtime: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

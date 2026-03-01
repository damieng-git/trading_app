"""
Phase 13 — Screener Quality Optimization

Four experiments to reduce screener hits from ~700 to ~150 while improving
hit rate, return, and P&L.  Each experiment runs on two datasets:

  - sample_300:   curated backtest universe (268 stocks, long history)
  - entry_stocks: live screener output     (714 stocks, ~3yr history)

Experiments:

  13a  Transition vs continuation entries
       Are new combo onsets (off→on) more profitable than re-entries into
       an already-active combo?

  13b  TrendScore minimum sensitivity
       What threshold improves HR and PF without sacrificing too much PnL?

  13c  Market cap sensitivity
       Does filtering small-caps improve trade quality?

  13d  Alternative KPI combinations
       Can C5 or C6 combos (more KPIs) improve per-trade quality?
       Can stricter 3-KPI or 4-KPI combos beat the locked ones?

Baseline: Exit Flow v4, C3+C4 at 1x/1.5x, SMA200 on 1D/1W, 0.1% comm.
OOS: last 30% of each stock's history.
"""

from __future__ import annotations

import csv
import json
import sys
import time
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from trading_dashboard.kpis.catalog import (
    compute_kpi_state_map,
    KPI_TREND_ORDER,
    KPI_BREAKOUT_ORDER,
)
from trading_dashboard.kpis.rules import STATE_BULL

OUTPUTS_ROOT = Path(__file__).resolve().parent / "outputs" / "all" / "phase13"

OOS_FRACTION = 0.70
ATR_PERIOD = 14
MAX_HOLD = 500
COMM = 0.1  # 0.1% round-trip commission

EXIT_PARAMS = {
    "4H": {"T": 4, "M": 48, "K": 4.0},
    "1D": {"T": 4, "M": 40, "K": 4.0},
    "1W": {"T": 2, "M": 20, "K": 4.0},
}

ENTRY_COMBOS = {
    "4H": {
        "C3": ["Nadaraya-Watson Smoother", "DEMA", "Stoch_MTM"],
        "C4": ["Nadaraya-Watson Smoother", "Madrid Ribbon", "GK Trend Ribbon", "cRSI"],
    },
    "1D": {
        "C3": ["Nadaraya-Watson Smoother", "Madrid Ribbon", "Volume + MA20"],
        "C4": ["Nadaraya-Watson Smoother", "Madrid Ribbon", "GK Trend Ribbon", "cRSI"],
    },
    "1W": {
        "C3": ["Nadaraya-Watson Smoother", "DEMA", "cRSI"],
        "C4": ["Nadaraya-Watson Smoother", "Stoch_MTM", "cRSI", "Volume + MA20"],
    },
}

KPI_WEIGHTS: Dict[str, float] = {
    "Nadaraya-Watson Smoother": 3.0, "BB 30": 2.0, "SR Breaks": 1.5,
    "cRSI": 1.5, "Stoch_MTM": 1.2, "CM_P-SAR": 1.2, "Mansfield RS": 1.2,
    "Ichimoku": 1.0, "DEMA": 1.0, "WT_LB": 1.0, "CM_Ult_MacD_MFT": 1.0,
    "UT Bot Alert": 1.0, "SuperTrend": 1.0, "SQZMOM_LB": 1.0, "ADX & DI": 1.0,
    "RSI Strength & Consolidation Zones (Zeiierman)": 1.0,
    "Nadaraya-Watson Envelop (MAE)": 1.0, "Nadaraya-Watson Envelop (STD)": 1.0,
    "GK Trend Ribbon": 0.8, "Impulse Trend": 0.8, "Madrid Ribbon": 0.8,
    "Donchian Ribbon": 0.8, "MA Ribbon": 0.8, "TuTCI": 0.8, "GMMA": 0.8,
    "OBVOSC_LB": 1.0, "Volume + MA20": 1.0,
}

EXCLUDED_KPIS = {"Nadaraya-Watson Envelop (Repainting)"}

ALL_KPIS: List[str] = []
_seen: set = set()
for _k in list(KPI_TREND_ORDER) + list(KPI_BREAKOUT_ORDER) + [
    "GK Trend Ribbon", "Impulse Trend", "Volume + MA20",
]:
    if _k not in _seen and _k not in EXCLUDED_KPIS:
        ALL_KPIS.append(_k)
        _seen.add(_k)

KPI_SHORT = {
    "Nadaraya-Watson Smoother": "NWSm", "cRSI": "cRSI", "OBVOSC_LB": "OBVOsc",
    "Madrid Ribbon": "Madrid", "GK Trend Ribbon": "GKTr", "Volume + MA20": "Vol>MA",
    "DEMA": "DEMA", "Donchian Ribbon": "Donch", "TuTCI": "TuTCI", "MA Ribbon": "MARib",
    "Ichimoku": "Ichi", "WT_LB": "WT", "SQZMOM_LB": "SQZ", "Stoch_MTM": "Stoch",
    "CM_Ult_MacD_MFT": "MACD", "ADX & DI": "ADX", "GMMA": "GMMA", "Mansfield RS": "Mansf",
    "SR Breaks": "SRBrk", "SuperTrend": "SupTr", "UT Bot Alert": "UTBot", "CM_P-SAR": "PSAR",
    "BB 30": "BB30", "Nadaraya-Watson Envelop (MAE)": "NWE-MAE",
    "Nadaraya-Watson Envelop (STD)": "NWE-STD", "Impulse Trend": "Impulse",
    "RSI Strength & Consolidation Zones (Zeiierman)": "Zeiier",
}


def _s(kpi: str) -> str:
    return KPI_SHORT.get(kpi, kpi[:8])


def _sl(kpis: list, sep: str = "+") -> str:
    return sep.join(_s(k) for k in kpis)


# ── Dataset configuration ───────────────────────────────────────────────

def _load_entry_stocks() -> set:
    path = REPO_DIR / "apps" / "dashboard" / "configs" / "lists" / "entry_stocks.csv"
    with open(path, encoding="utf-8") as f:
        return {row[0].strip().upper() for row in csv.reader(f)
                if row and row[0].strip().lower() != "ticker" and row[0].strip()}


def _load_sector_map() -> dict:
    path = REPO_DIR / "apps" / "dashboard" / "configs" / "sector_map.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


DATASETS = {
    "sample_300": {
        "enriched_dir": REPO_DIR / "research" / "data" / "feature_store"
                        / "enriched" / "sample_300" / "stock_data",
        "ticker_filter": None,
        "label": "sample_300 (curated backtest)",
    },
    "entry_stocks": {
        "enriched_dir": REPO_DIR / "data" / "feature_store"
                        / "enriched" / "dashboard" / "stock_data",
        "ticker_filter": _load_entry_stocks(),
        "label": f"entry_stocks (last scan)",
    },
}


# ── Helpers ──────────────────────────────────────────────────────────────

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, lo, pc = df["High"], df["Low"], df["Close"].shift(1)
    tr = pd.concat([h - lo, (h - pc).abs(), (lo - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()


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
            if len(df) >= 100 and "Close" in df.columns:
                data[symbol] = df
        except Exception:
            continue
    return data


def precompute(data: dict, c3_kpis: list, c4_kpis: list) -> dict:
    precomp = {}
    for sym, df in data.items():
        sm = compute_kpi_state_map(df)
        if any(k not in sm for k in c3_kpis):
            continue
        c3_bull = pd.Series(True, index=df.index)
        for k in c3_kpis:
            c3_bull &= (sm[k] == STATE_BULL)
        c4_avail = all(k in sm for k in c4_kpis)
        c4_bull = pd.Series(False, index=df.index)
        if c4_avail:
            c4_bull = pd.Series(True, index=df.index)
            for k in c4_kpis:
                c4_bull &= (sm[k] == STATE_BULL)
        cl = df["Close"].to_numpy(float)
        vol = df["Volume"].to_numpy(float) if "Volume" in df.columns else np.zeros(len(df))
        at = compute_atr(df, ATR_PERIOD).to_numpy(float)

        # TrendScore per bar for 13b experiment
        ts_arr = np.zeros(len(df), dtype=float)
        for k in KPI_TREND_ORDER:
            s = sm.get(k)
            if s is not None:
                w = KPI_WEIGHTS.get(k, 1.0)
                vals = s.to_numpy(float)
                mask = np.isin(vals, [1, -1])
                ts_arr[mask] += w * vals[mask]

        precomp[sym] = {
            "df": df, "sm": sm,
            "c3_bull": c3_bull, "c4_avail": c4_avail, "c4_bull": c4_bull,
            "cl": cl, "vol": vol, "atr": at, "n": len(df),
            "trend_score": ts_arr,
        }
    return precomp


def _run_trade(pc: dict, sm: dict, j: int, c3_kpis: list, c4_kpis: list,
               T: int, M: int, K: float) -> tuple:
    """Run a single trade from entry bar j. Returns (weighted_ret, raw_ret, hold, reason, size)."""
    cl = pc["cl"]; at = pc["atr"]; n = pc["n"]
    c4_avail = pc["c4_avail"]; c4_bull = pc["c4_bull"]

    ep = float(cl[j])
    stop_price = ep
    stop = stop_price - K * at[j] if at[j] > 0 else -np.inf
    bars_since_reset = 0
    current_size = 1.0
    max_level = "C3"
    was_scaled = False

    if c4_avail and c4_bull.iloc[j]:
        current_size = 1.5
        max_level = "C4"
        was_scaled = True

    active_kpis = c4_kpis if max_level == "C4" else c3_kpis
    nk = len(active_kpis)
    xi = None
    reason = "mh"

    j_inner = j + 1
    while j_inner < min(j + MAX_HOLD + 1, n):
        bars_since_reset += 1
        c = float(cl[j_inner])
        total_bars = j_inner - j

        if c < stop:
            xi = j_inner; reason = "atr"; break

        if not was_scaled and c4_avail and c4_bull.iloc[j_inner]:
            current_size = 1.5; max_level = "C4"; was_scaled = True
            active_kpis = c4_kpis; nk = len(active_kpis)

        nb = sum(1 for kk in active_kpis
                 if kk in sm and j_inner < len(sm[kk])
                 and int(sm[kk].iloc[j_inner]) != STATE_BULL)

        if total_bars <= T:
            if nb >= nk:
                xi = j_inner; reason = "len"; break
        else:
            if nb >= 2:
                xi = j_inner; reason = "str"; break

        if bars_since_reset >= M:
            nb_c = sum(1 for kk in active_kpis
                       if kk in sm and j_inner < len(sm[kk])
                       and int(sm[kk].iloc[j_inner]) != STATE_BULL)
            if nb_c == 0:
                stop_price = c
                stop = stop_price - K * at[j_inner] if j_inner < len(at) and at[j_inner] > 0 else stop
                bars_since_reset = 0
            else:
                xi = j_inner; reason = "reset_exit"; break

        j_inner += 1

    if xi is None:
        xi = min(j_inner, n - 1)

    xp = float(cl[xi])
    h = xi - j
    if h <= 0:
        return None
    ret = (xp - ep) / ep * 100 - COMM
    return (ret * current_size, ret, h, reason, current_size)


def _stats(trades: list) -> dict:
    if not trades:
        return {"n": 0, "hr": 0, "pnl": 0, "pf": 0, "avg": 0, "worst": 0, "avg_hold": 0}
    rets_w = [t[0] for t in trades]
    rets_u = [t[1] for t in trades]
    holds = [t[2] for t in trades]
    nt = len(trades)
    hr = sum(1 for r in rets_w if r > 0) / nt * 100
    wi = sum(r for r in rets_w if r > 0)
    lo = abs(sum(r for r in rets_w if r <= 0))
    return {
        "n": nt, "hr": round(hr, 1),
        "pnl": round(sum(rets_w)),
        "pf": round(wi / lo if lo > 0 else 999, 2),
        "avg": round(float(np.mean(rets_u)), 2),
        "worst": round(min(rets_u), 1),
        "avg_hold": round(float(np.mean(holds)), 1),
    }


# ═════════════════════════════════════════════════════════════════════════
# 13a — Transition vs Continuation
# ═════════════════════════════════════════════════════════════════════════

def run_13a(precomp: dict, c3_kpis: list, c4_kpis: list,
            T: int, M: int, K: float, tf: str) -> dict:
    """Split trades into onset (transition) and continuation (re-entry into active combo)."""
    onset_trades = []
    continuation_trades = []

    for sym, pc in precomp.items():
        sm = pc["sm"]; c3_bull = pc["c3_bull"]; cl = pc["cl"]; n = pc["n"]
        sma200_ok = None
        if tf in ("1D", "1W") and n >= 200:
            sma200 = pd.Series(cl).rolling(200, min_periods=200).mean().to_numpy()
            sma200_ok = cl >= sma200

        si = int(n * OOS_FRACTION)
        j = si
        while j < n:
            if not c3_bull.iloc[j]:
                j += 1; continue
            if sma200_ok is not None and not sma200_ok[j]:
                j += 1; continue
            ep = float(cl[j])
            if ep <= 0:
                j += 1; continue

            is_onset = (j == 0 or not c3_bull.iloc[j - 1])

            trade = _run_trade(pc, sm, j, c3_kpis, c4_kpis, T, M, K)
            if trade is None:
                j += 1; continue

            if is_onset:
                onset_trades.append(trade)
            else:
                continuation_trades.append(trade)

            xi = j + trade[2]
            j = xi + 1

    return {
        "onset": _stats(onset_trades),
        "continuation": _stats(continuation_trades),
        "combined": _stats(onset_trades + continuation_trades),
    }


# ═════════════════════════════════════════════════════════════════════════
# 13b — TrendScore minimum sensitivity
# ═════════════════════════════════════════════════════════════════════════

def run_13b(precomp: dict, c3_kpis: list, c4_kpis: list,
            T: int, M: int, K: float, tf: str) -> list:
    thresholds = [0, 2, 3, 4, 5, 6, 7, 8, 10, 12]
    results = []

    for min_ts in thresholds:
        trades = []
        blocked = 0
        for sym, pc in precomp.items():
            sm = pc["sm"]; c3_bull = pc["c3_bull"]; cl = pc["cl"]; n = pc["n"]
            ts_arr = pc["trend_score"]
            sma200_ok = None
            if tf in ("1D", "1W") and n >= 200:
                sma200 = pd.Series(cl).rolling(200, min_periods=200).mean().to_numpy()
                sma200_ok = cl >= sma200

            si = int(n * OOS_FRACTION)
            j = si
            while j < n:
                if not c3_bull.iloc[j]:
                    j += 1; continue
                if sma200_ok is not None and not sma200_ok[j]:
                    j += 1; continue
                is_onset = (j == 0 or not c3_bull.iloc[j - 1])
                if not is_onset:
                    j += 1; continue
                ep = float(cl[j])
                if ep <= 0:
                    j += 1; continue

                if ts_arr[j] < min_ts:
                    blocked += 1
                    j += 1; continue

                trade = _run_trade(pc, sm, j, c3_kpis, c4_kpis, T, M, K)
                if trade is None:
                    j += 1; continue
                trades.append(trade)
                j = j + trade[2] + 1

        st = _stats(trades)
        st["min_ts"] = min_ts
        st["blocked"] = blocked
        results.append(st)

    return results


# ═════════════════════════════════════════════════════════════════════════
# 13c — Market cap sensitivity
# ═════════════════════════════════════════════════════════════════════════

def run_13c(precomp: dict, c3_kpis: list, c4_kpis: list,
            T: int, M: int, K: float, tf: str,
            sector_map: dict) -> list:
    thresholds_b = [0.3, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0]
    results = []

    for min_b in thresholds_b:
        min_mcap = min_b * 1e9
        trades = []
        skipped_syms = 0
        for sym, pc in precomp.items():
            meta = sector_map.get(sym, {})
            fund = meta.get("fundamentals", {}) if isinstance(meta, dict) else {}
            mcap = fund.get("market_cap")
            if mcap is not None:
                try:
                    if float(mcap) < min_mcap:
                        skipped_syms += 1
                        continue
                except (TypeError, ValueError):
                    pass

            sm = pc["sm"]; c3_bull = pc["c3_bull"]; cl = pc["cl"]; n = pc["n"]
            sma200_ok = None
            if tf in ("1D", "1W") and n >= 200:
                sma200 = pd.Series(cl).rolling(200, min_periods=200).mean().to_numpy()
                sma200_ok = cl >= sma200

            si = int(n * OOS_FRACTION)
            j = si
            while j < n:
                if not c3_bull.iloc[j]:
                    j += 1; continue
                if sma200_ok is not None and not sma200_ok[j]:
                    j += 1; continue
                is_onset = (j == 0 or not c3_bull.iloc[j - 1])
                if not is_onset:
                    j += 1; continue
                ep = float(cl[j])
                if ep <= 0:
                    j += 1; continue

                trade = _run_trade(pc, sm, j, c3_kpis, c4_kpis, T, M, K)
                if trade is None:
                    j += 1; continue
                trades.append(trade)
                j = j + trade[2] + 1

        st = _stats(trades)
        st["min_mcap_b"] = min_b
        st["skipped_syms"] = skipped_syms
        results.append(st)

    return results


# ═════════════════════════════════════════════════════════════════════════
# 13d — Alternative KPI combinations (vectorized)
# ═════════════════════════════════════════════════════════════════════════

TOP_KPIS_FOR_LARGE_COMBOS = [
    "Nadaraya-Watson Smoother", "cRSI", "Madrid Ribbon", "GK Trend Ribbon",
    "DEMA", "Donchian Ribbon", "OBVOSC_LB", "Volume + MA20",
    "Stoch_MTM", "CM_P-SAR", "Mansfield RS", "Ichimoku",
    "WT_LB", "CM_Ult_MacD_MFT", "SuperTrend",
]


def _precompute_all_bulls(data: dict, c4_kpis: list | None = None) -> dict:
    """Pre-compute per-KPI bull signals as numpy arrays (fast for combo search)."""
    if c4_kpis is None:
        c4_kpis = ENTRY_COMBOS.get("1D", {}).get("C4", [])
    all_pc = {}
    for sym, df in data.items():
        sm = compute_kpi_state_map(df)
        bulls = {}
        for k in ALL_KPIS:
            if k in sm:
                bulls[k] = (sm[k] == STATE_BULL).to_numpy(bool)
        if not bulls:
            continue
        cl = df["Close"].to_numpy(float)
        at = compute_atr(df, ATR_PERIOD).to_numpy(float)
        sma200 = None
        if len(df) >= 200:
            sma200 = pd.Series(cl).rolling(200, min_periods=200).mean().to_numpy()

        c4_avail = all(k in bulls for k in c4_kpis)
        c4_bull = np.zeros(len(df), dtype=bool)
        if c4_avail:
            c4_bull = np.ones(len(df), dtype=bool)
            for k in c4_kpis:
                c4_bull &= bulls[k]

        kpi_nbull = {}
        for k in ALL_KPIS:
            if k in sm:
                kpi_nbull[k] = (sm[k] != STATE_BULL).to_numpy(bool)

        all_pc[sym] = {
            "sm": sm, "bulls": bulls, "kpi_nbull": kpi_nbull,
            "cl": cl, "atr": at, "n": len(df), "sma200": sma200,
            "c4_avail": c4_avail, "c4_bull": c4_bull,
        }
    return all_pc


def _sim_combo_fast(all_pc: dict, combo_kpis: list, c4_kpis: list,
                    T: int, M: int, K: float, tf: str,
                    min_trades: int = 15) -> dict | None:
    """Simulate a single combo — numpy-only inner loop."""
    trades = []
    for sym, pc in all_pc.items():
        bulls = pc["bulls"]
        if any(k not in bulls for k in combo_kpis):
            continue
        cl = pc["cl"]; at = pc["atr"]; n = pc["n"]

        c3_bull = bulls[combo_kpis[0]].copy()
        for k in combo_kpis[1:]:
            c3_bull &= bulls[k]

        sma200_ok = None
        if tf in ("1D", "1W") and pc["sma200"] is not None:
            sma200_ok = cl >= pc["sma200"]

        c4_avail = pc["c4_avail"]
        c4_bull = pc["c4_bull"]
        kpi_nbull = pc["kpi_nbull"]

        si = int(n * OOS_FRACTION)
        j = si
        while j < n:
            if not c3_bull[j]:
                j += 1; continue
            if sma200_ok is not None and not sma200_ok[j]:
                j += 1; continue
            is_onset = (j == 0 or not c3_bull[j - 1])
            if not is_onset:
                j += 1; continue
            ep = cl[j]
            if ep <= 0:
                j += 1; continue

            # ── trade execution (inlined for speed) ──
            stop_price = ep
            stop = stop_price - K * at[j] if at[j] > 0 else -np.inf
            bars_since_reset = 0
            current_size = 1.5 if (c4_avail and c4_bull[j]) else 1.0
            was_scaled = current_size == 1.5
            active_kpis = c4_kpis if was_scaled else combo_kpis
            nk = len(active_kpis)
            xi = None

            jj = j + 1
            while jj < min(j + MAX_HOLD + 1, n):
                bars_since_reset += 1
                c = cl[jj]
                total_bars = jj - j

                if c < stop:
                    xi = jj; break

                if not was_scaled and c4_avail and c4_bull[jj]:
                    current_size = 1.5; was_scaled = True
                    active_kpis = c4_kpis; nk = len(active_kpis)

                nb = sum(1 for kk in active_kpis
                         if kk in kpi_nbull and jj < len(kpi_nbull[kk])
                         and kpi_nbull[kk][jj])

                if total_bars <= T:
                    if nb >= nk:
                        xi = jj; break
                else:
                    if nb >= 2:
                        xi = jj; break

                if bars_since_reset >= M:
                    if nb == 0:
                        stop_price = c
                        stop = stop_price - K * at[jj] if jj < len(at) and at[jj] > 0 else stop
                        bars_since_reset = 0
                    else:
                        xi = jj; break

                jj += 1

            if xi is None:
                xi = min(jj, n - 1)

            xp = cl[xi]
            h = xi - j
            if h > 0:
                ret = (xp - ep) / ep * 100 - COMM
                trades.append((ret * current_size, ret, h))

            j = xi + 1

    if len(trades) < min_trades:
        return None
    rets_w = [t[0] for t in trades]
    rets_u = [t[1] for t in trades]
    nt = len(trades)
    hr = sum(1 for r in rets_w if r > 0) / nt * 100
    wi = sum(r for r in rets_w if r > 0)
    lo = abs(sum(r for r in rets_w if r <= 0))
    return {
        "n": nt, "hr": round(hr, 1), "pnl": round(sum(rets_w)),
        "pf": round(wi / lo if lo > 0 else 999, 2),
        "avg": round(float(np.mean(rets_u)), 2),
        "worst": round(min(rets_u), 1),
        "avg_hold": round(float(np.mean([t[2] for t in trades])), 1),
        "kpis": combo_kpis,
        "label": _sl(combo_kpis),
    }


def run_13d(data: dict, c4_kpis: list,
            T: int, M: int, K: float, tf: str,
            combo_sizes: list = None,
            top_n: int = 10,
            hr_floor: float = 60.0,
            min_trades: int = 15) -> dict:
    if combo_sizes is None:
        combo_sizes = [3, 4, 5, 6]

    all_pc = _precompute_all_bulls(data, c4_kpis)
    avail_kpis_full = sorted({k for pc in all_pc.values() for k in pc["bulls"]} & set(ALL_KPIS))
    avail_kpis_top = [k for k in TOP_KPIS_FOR_LARGE_COMBOS if k in set(avail_kpis_full)]
    print(f"    Available KPIs: {len(avail_kpis_full)} full, {len(avail_kpis_top)} top (for C5/C6)")

    results_by_size = {}

    for k in combo_sizes:
        pool = avail_kpis_full if k <= 4 else avail_kpis_top
        from math import comb as _comb
        n_combos = _comb(len(pool), k)
        print(f"    Searching C{k}: {n_combos} combos from {len(pool)} KPIs...",
              end="", flush=True)
        t1 = time.time()
        hits = []

        for combo in combinations(pool, k):
            combo_list = list(combo)
            r = _sim_combo_fast(all_pc, combo_list, c4_kpis, T, M, K, tf, min_trades)
            if r is None:
                continue
            if r["hr"] < hr_floor:
                continue
            hits.append(r)

        hits.sort(key=lambda x: -x["pnl"])
        top = hits[:top_n]
        results_by_size[f"C{k}"] = top
        elapsed = time.time() - t1
        print(f" {len(hits)} passed HR>={hr_floor}%, {elapsed:.0f}s")

    return results_by_size


# ═════════════════════════════════════════════════════════════════════════
# Print helpers
# ═════════════════════════════════════════════════════════════════════════

def _print_header_sim():
    print(f"    {'Label':<28} {'Trades':>7} {'HR%':>6} {'PnL':>10} "
          f"{'Δ PnL%':>8} {'PF':>7} {'Avg%':>7} {'Worst%':>7} {'Hold':>5}")
    print(f"    {'—'*28} {'—'*7} {'—'*6} {'—'*10} {'—'*8} {'—'*7} {'—'*7} {'—'*7} {'—'*5}")


def _print_row(label: str, r: dict, base_pnl: float = 0):
    delta = (r["pnl"] - base_pnl) / abs(base_pnl) * 100 if base_pnl else 0
    print(f"    {label:<28} {r['n']:>7} {r['hr']:>6.1f} {r['pnl']:>+10} "
          f"{delta:>+7.1f}% {r['pf']:>7.1f} {r['avg']:>+7.2f} {r['worst']:>+7.1f} "
          f"{r.get('avg_hold', 0):>5.0f}")


# ═════════════════════════════════════════════════════════════════════════
# Main
# ═════════════════════════════════════════════════════════════════════════

def main():
    t_global = time.time()
    OUTPUTS_ROOT.mkdir(parents=True, exist_ok=True)
    sector_map = _load_sector_map()
    all_results: Dict[str, Any] = {}

    print("=" * 95)
    print("  Phase 13 — Screener Quality Optimization")
    print("  Dual-dataset: sample_300 + entry_stocks, OOS 30%, 0.1% comm")
    print("=" * 95)

    for ds_name, ds_cfg in DATASETS.items():
        ds_results: Dict[str, Any] = {}
        print(f"\n{'╔' + '═'*93 + '╗'}")
        print(f"{'║'} DATASET: {ds_cfg['label']:<83}{'║'}")
        print(f"{'╚' + '═'*93 + '╝'}")

        for tf_key in ["1D"]:  # primary screener TF first; extend to 4H/1W if needed
            print(f"\n  {'━' * 91}")
            print(f"  TIMEFRAME: {tf_key}")
            print(f"  {'━' * 91}")

            p = EXIT_PARAMS[tf_key]
            T, M, K = p["T"], p["M"], p["K"]
            c3_kpis = ENTRY_COMBOS[tf_key]["C3"]
            c4_kpis = ENTRY_COMBOS[tf_key]["C4"]

            data = load_data(ds_cfg["enriched_dir"], tf_key,
                             ticker_filter=ds_cfg.get("ticker_filter"))
            print(f"  Loaded {len(data)} stocks")

            t1 = time.time()
            pc = precompute(data, c3_kpis, c4_kpis)
            print(f"  Pre-compute done ({time.time()-t1:.0f}s, {len(pc)} stocks with KPIs)")

            # ── 13a: Transition vs Continuation ──────────────────────────
            print(f"\n  ┌─ 13a: Transition vs Continuation ─────────────────────")
            res_a = run_13a(pc, c3_kpis, c4_kpis, T, M, K, tf_key)
            _print_header_sim()
            base_pnl = res_a["onset"]["pnl"]
            _print_row("Onset (transition)", res_a["onset"])
            _print_row("Continuation (re-entry)", res_a["continuation"], base_pnl)
            _print_row("Combined (all entries)", res_a["combined"], base_pnl)

            # ── 13b: TrendScore Sensitivity ──────────────────────────────
            print(f"\n  ┌─ 13b: TrendScore Sensitivity ───────────────────────")
            res_b = run_13b(pc, c3_kpis, c4_kpis, T, M, K, tf_key)
            _print_header_sim()
            base_pnl_b = res_b[0]["pnl"] if res_b else 0
            for r in res_b:
                lbl = f"TS >= {r['min_ts']}  (blk={r['blocked']})"
                _print_row(lbl, r, base_pnl_b)

            # ── 13c: Market Cap Sensitivity ──────────────────────────────
            print(f"\n  ┌─ 13c: Market Cap Sensitivity ──────────────────────")
            res_c = run_13c(pc, c3_kpis, c4_kpis, T, M, K, tf_key, sector_map)
            _print_header_sim()
            base_pnl_c = res_c[0]["pnl"] if res_c else 0
            for r in res_c:
                lbl = f"Mcap >= ${r['min_mcap_b']}B  (skip={r['skipped_syms']})"
                _print_row(lbl, r, base_pnl_c)

            # ── 13d: Alternative Combos ──────────────────────────────────
            print(f"\n  ┌─ 13d: Alternative KPI Combos ──────────────────────")
            res_d = run_13d(data, c4_kpis, T, M, K, tf_key,
                            combo_sizes=[3, 4, 5, 6],
                            top_n=5, hr_floor=60.0,
                            min_trades=10 if ds_name == "entry_stocks" else 15)

            for size_label, combos in res_d.items():
                print(f"\n    Top 5 {size_label} combos (by P&L):")
                _print_header_sim()
                for r in combos:
                    _print_row(r["label"], r, base_pnl_b)

            # Locked combo comparison
            locked_c3 = _sim_combo_fast(
                _precompute_all_bulls(data, c4_kpis), c3_kpis, c4_kpis, T, M, K, tf_key, 1)
            if locked_c3:
                print(f"\n    Locked C3 ({_sl(c3_kpis)}):")
                _print_header_sim()
                _print_row("LOCKED C3", locked_c3)

            ds_results[tf_key] = {
                "13a": res_a,
                "13b": res_b,
                "13c": res_c,
                "13d": {k: [_serialize(c) for c in v] for k, v in res_d.items()},
            }

        all_results[ds_name] = ds_results

    # ── Cross-dataset comparison ─────────────────────────────────────────
    print(f"\n{'═' * 95}")
    print(f"  CROSS-DATASET COMPARISON (1D)")
    print(f"{'═' * 95}")

    if "1D" in all_results.get("sample_300", {}) and "1D" in all_results.get("entry_stocks", {}):
        s300 = all_results["sample_300"]["1D"]
        entry = all_results["entry_stocks"]["1D"]

        print(f"\n  13a — Transition vs Continuation:")
        print(f"    {'Type':<24} {'── sample_300 ──':>32} {'── entry_stocks ──':>32}")
        print(f"    {'':24} {'Trades':>7} {'HR%':>6} {'PF':>7} {'Avg%':>7}  "
              f"{'Trades':>7} {'HR%':>6} {'PF':>7} {'Avg%':>7}")
        for tag in ["onset", "continuation"]:
            sa = s300["13a"][tag]
            ea = entry["13a"][tag]
            print(f"    {tag:<24} {sa['n']:>7} {sa['hr']:>6.1f} {sa['pf']:>7.1f} {sa['avg']:>+7.2f}  "
                  f"{ea['n']:>7} {ea['hr']:>6.1f} {ea['pf']:>7.1f} {ea['avg']:>+7.2f}")

        print(f"\n  13b — TrendScore (onset-only trades):")
        print(f"    {'Min TS':<10} {'── sample_300 ──':>32} {'── entry_stocks ──':>32} {'Consistent?':>12}")
        print(f"    {'':10} {'Trades':>7} {'HR%':>6} {'PF':>7} {'Δ PnL%':>8}  "
              f"{'Trades':>7} {'HR%':>6} {'PF':>7} {'Δ PnL%':>8}")
        s_base = s300["13b"][0]["pnl"]
        e_base = entry["13b"][0]["pnl"]
        for i, sb in enumerate(s300["13b"]):
            eb = entry["13b"][i] if i < len(entry["13b"]) else None
            if eb is None:
                continue
            sd = (sb["pnl"] - s_base) / abs(s_base) * 100 if s_base else 0
            ed = (eb["pnl"] - e_base) / abs(e_base) * 100 if e_base else 0
            s_pf_d = sb["pf"] - s300["13b"][0]["pf"]
            e_pf_d = eb["pf"] - entry["13b"][0]["pf"]
            consistent = "✓" if (s_pf_d >= 0 and e_pf_d >= 0) or (s_pf_d < 0 and e_pf_d < 0) else "✗"
            print(f"    TS>={sb['min_ts']:<6} {sb['n']:>7} {sb['hr']:>6.1f} {sb['pf']:>7.1f} {sd:>+7.1f}%  "
                  f"{eb['n']:>7} {eb['hr']:>6.1f} {eb['pf']:>7.1f} {ed:>+7.1f}%  {consistent:>10}")

    # ── Save ─────────────────────────────────────────────────────────────
    jp = OUTPUTS_ROOT / "phase13_results.json"
    jp.write_text(json.dumps(all_results, indent=2, default=str), encoding="utf-8")
    print(f"\n  Results saved to {jp}")
    print(f"  Total runtime: {time.time()-t_global:.0f}s")


def _serialize(d: dict) -> dict:
    """Make dict JSON-safe."""
    out = {}
    for k, v in d.items():
        if isinstance(v, (list, tuple)) and v and isinstance(v[0], str):
            out[k] = v
        elif isinstance(v, (int, float, str, bool, type(None))):
            out[k] = v
        elif isinstance(v, dict):
            out[k] = _serialize(v)
        else:
            out[k] = str(v)
    return out


if __name__ == "__main__":
    main()

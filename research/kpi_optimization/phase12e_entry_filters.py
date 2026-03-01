"""
Phase 12e — Entry Quality Filters

Tests four entry-gate filters inspired by the IPAY 1W Dec-2024 loss:

  A) Overextension: block entry if Close > X% above Close[N bars ago]
     Sensitivities: 5-bar lookback at 8%, 10%, 12%, 15%
                    10-bar lookback at 12%, 15%, 20%, 25%

  B) Volume confirmation: entry-bar Volume must be >= X × Volume_MA20
     Sensitivities: 0.8x, 1.0x, 1.2x, 1.5x

  C) Minimum bars for SMA200: block entry if data length < 200 bars
     (binary: on/off — only affects 1W where some stocks have < 200 bars)

  D) Trend age: block entry if C4 has been continuously active for >= N bars
     before the current C3 onset (late entry into mature trend)
     Sensitivities: 3, 5, 8, 12 bars of prior C4 activity

Each filter tested independently against the locked baseline (Exit Flow v4,
C3+C4 at 1x/1.5x, SMA200 on 1D/1W, 0.1% commission).

Dataset: sample_300 (295 stocks), OOS = last 30%.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from trading_dashboard.kpis.catalog import compute_kpi_state_map
from trading_dashboard.kpis.rules import STATE_BULL

ENRICHED_DIR = REPO_DIR / "research" / "data" / "feature_store" / "enriched" / "sample_300" / "stock_data"
OUTPUTS_ROOT = Path(__file__).resolve().parent / "outputs"

IS_FRACTION = 0.70
ATR_PERIOD = 14
MAX_HOLD = 500
COMM = 0.1

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


def compute_atr(df, period=14):
    h, lo, pc = df["High"], df["Low"], df["Close"].shift(1)
    tr = pd.concat([h - lo, (h - pc).abs(), (lo - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()


def load_data(enriched_dir: Path, timeframe: str) -> Dict[str, pd.DataFrame]:
    data = {}
    for f in sorted(enriched_dir.glob(f"*_{timeframe}.parquet")):
        symbol = f.stem.rsplit(f"_{timeframe}", 1)[0]
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


def precompute(data, c3_kpis, c4_kpis):
    """Pre-compute KPI state maps and bull signals to avoid redundant work."""
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
        precomp[sym] = {
            "df": df, "sm": sm, "c3_bull": c3_bull, "c4_avail": c4_avail,
            "c4_bull": c4_bull, "cl": cl, "vol": vol, "atr": at, "n": len(df),
        }
    return precomp


def run_sim(precomp: dict, c3_kpis, c4_kpis, T, M, K, tf,
            overext_lookback: int = 0, overext_pct: float = 0,
            vol_min_ratio: float = 0,
            require_sma200_data: bool = False,
            trend_age_max: int = 0) -> dict:
    trades = []
    blocked = {"overext": 0, "vol": 0, "sma_data": 0, "trend_age": 0}

    for sym, pc in precomp.items():
        df = pc["df"]; sm = pc["sm"]
        c3_bull = pc["c3_bull"]; c4_avail = pc["c4_avail"]; c4_bull = pc["c4_bull"]
        cl = pc["cl"]; vol = pc["vol"]; at = pc["atr"]; n = pc["n"]

        sma200_ok = None
        if tf in ("1D", "1W") and n >= 200:
            sma200 = pd.Series(cl).rolling(200, min_periods=200).mean().to_numpy()
            sma200_ok = cl >= sma200

        # Filter C: require enough data for SMA200
        if require_sma200_data and tf in ("1D", "1W") and n < 200:
            blocked["sma_data"] += 1
            continue

        vol_ma20 = pd.Series(vol).rolling(20, min_periods=1).mean().to_numpy() if vol_min_ratio > 0 else None

        si = int(n * IS_FRACTION)
        j = si

        while j < n:
            if not c3_bull.iloc[j]:
                j += 1
                continue

            # SMA200 filter (existing)
            if sma200_ok is not None and not sma200_ok[j]:
                j += 1
                continue

            # C3 onset check
            is_onset = (j == 0 or not c3_bull.iloc[j - 1])
            if not is_onset:
                j += 1
                continue

            ep = float(cl[j])
            if ep <= 0:
                j += 1
                continue

            # --- FILTER A: Overextension ---
            if overext_lookback > 0 and overext_pct > 0:
                lb_idx = j - overext_lookback
                if lb_idx >= 0:
                    ref_price = float(cl[lb_idx])
                    if ref_price > 0 and (ep - ref_price) / ref_price * 100 > overext_pct:
                        blocked["overext"] += 1
                        j += 1
                        continue

            # --- FILTER B: Volume confirmation ---
            if vol_min_ratio > 0 and vol_ma20 is not None:
                v = vol[j]
                vma = vol_ma20[j]
                if vma > 0 and v / vma < vol_min_ratio:
                    blocked["vol"] += 1
                    j += 1
                    continue

            # --- FILTER D: Trend age (C4 active too long before C3) ---
            if trend_age_max > 0 and c4_avail and c4_bull.iloc[j]:
                c4_run = 0
                for back in range(1, j + 1):
                    if c4_bull.iloc[j - back]:
                        c4_run += 1
                    else:
                        break
                if c4_run >= trend_age_max:
                    blocked["trend_age"] += 1
                    j += 1
                    continue

            # --- Open position ---
            ei = j
            stop_price = ep
            stop = stop_price - K * at[ei] if at[ei] > 0 else -np.inf
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

            j_inner = ei + 1
            while j_inner < min(ei + MAX_HOLD + 1, n):
                bars_since_reset += 1
                c = float(cl[j_inner])
                total_bars = j_inner - ei

                if c < stop:
                    xi = j_inner
                    reason = "atr"
                    break

                if not was_scaled and c4_avail and c4_bull.iloc[j_inner]:
                    current_size = 1.5
                    max_level = "C4"
                    was_scaled = True
                    active_kpis = c4_kpis
                    nk = len(active_kpis)

                nb = sum(1 for kk in active_kpis
                         if kk in sm and j_inner < len(sm[kk])
                         and int(sm[kk].iloc[j_inner]) != STATE_BULL)

                if total_bars <= T:
                    if nb >= nk:
                        xi = j_inner
                        reason = "len"
                        break
                else:
                    if nb >= 2:
                        xi = j_inner
                        reason = "str"
                        break

                if bars_since_reset >= M:
                    nb_c = sum(1 for kk in active_kpis
                               if kk in sm and j_inner < len(sm[kk])
                               and int(sm[kk].iloc[j_inner]) != STATE_BULL)
                    if nb_c == 0:
                        stop_price = c
                        stop = stop_price - K * at[j_inner] if j_inner < len(at) and at[j_inner] > 0 else stop
                        bars_since_reset = 0
                    else:
                        xi = j_inner
                        reason = "reset_exit"
                        break

                j_inner += 1

            if xi is None:
                xi = min(j_inner, n - 1)

            xp = float(cl[xi])
            h = xi - ei
            if h > 0:
                ret = (xp - ep) / ep * 100 - COMM
                trades.append((ret * current_size, ret, reason))

            j = xi + 1

    if not trades:
        return {"n": 0, "hr": 0, "pnl": 0, "pf": 0, "avg": 0, "worst": 0,
                "avg_hold": 0, "blocked": blocked}

    rets_w = [t[0] for t in trades]
    rets_u = [t[1] for t in trades]
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
        "avg_hold": round(float(np.mean([0])), 1),
        "blocked": blocked,
    }


def main():
    t0 = time.time()
    out_root = OUTPUTS_ROOT / "all" / "phase12e"
    out_root.mkdir(parents=True, exist_ok=True)
    all_results: Dict[str, Any] = {}

    print("=" * 90)
    print("  Phase 12e — Entry Quality Filters")
    print("  sample_300, OOS 30%, 0.1% commission, C3+C4 at 1x/1.5x")
    print("=" * 90)

    filters = [
        ("Baseline",              {}),
        # A) Overextension — 5-bar lookback
        ("A: Overext 5b >8%",     {"overext_lookback": 5, "overext_pct": 8}),
        ("A: Overext 5b >10%",    {"overext_lookback": 5, "overext_pct": 10}),
        ("A: Overext 5b >12%",    {"overext_lookback": 5, "overext_pct": 12}),
        ("A: Overext 5b >15%",    {"overext_lookback": 5, "overext_pct": 15}),
        # A) Overextension — 10-bar lookback
        ("A: Overext 10b >12%",   {"overext_lookback": 10, "overext_pct": 12}),
        ("A: Overext 10b >15%",   {"overext_lookback": 10, "overext_pct": 15}),
        ("A: Overext 10b >20%",   {"overext_lookback": 10, "overext_pct": 20}),
        ("A: Overext 10b >25%",   {"overext_lookback": 10, "overext_pct": 25}),
        # B) Volume confirmation
        ("B: Vol >= 0.8x MA20",   {"vol_min_ratio": 0.8}),
        ("B: Vol >= 1.0x MA20",   {"vol_min_ratio": 1.0}),
        ("B: Vol >= 1.2x MA20",   {"vol_min_ratio": 1.2}),
        ("B: Vol >= 1.5x MA20",   {"vol_min_ratio": 1.5}),
        # C) Minimum data for SMA200
        ("C: Require 200 bars",   {"require_sma200_data": True}),
        # D) Trend age
        ("D: C4 age < 3 bars",    {"trend_age_max": 3}),
        ("D: C4 age < 5 bars",    {"trend_age_max": 5}),
        ("D: C4 age < 8 bars",    {"trend_age_max": 8}),
        ("D: C4 age < 12 bars",   {"trend_age_max": 12}),
    ]

    for tf_key in ["4H", "1D", "1W"]:
        print(f"\n{'━' * 90}")
        print(f"  TIMEFRAME: {tf_key}")
        print(f"{'━' * 90}")

        p = EXIT_PARAMS[tf_key]
        T, M, K = p["T"], p["M"], p["K"]
        c3_kpis = ENTRY_COMBOS[tf_key]["C3"]
        c4_kpis = ENTRY_COMBOS[tf_key]["C4"]

        data = load_data(ENRICHED_DIR, tf_key)
        print(f"  Loaded {len(data)} stocks")
        print(f"  Pre-computing KPI states...")
        t1 = time.time()
        pc = precompute(data, c3_kpis, c4_kpis)
        print(f"  Pre-compute done ({time.time()-t1:.0f}s, {len(pc)} stocks)")

        base = None
        tf_results = []

        print(f"\n  {'Filter':<26} {'Trades':>7} {'Blocked':>8} {'HR%':>6} {'PnL':>10} {'Δ PnL%':>8} {'PF':>7} {'Worst':>7}")
        print(f"  {'—'*26} {'—'*7} {'—'*8} {'—'*6} {'—'*10} {'—'*8} {'—'*7} {'—'*7}")

        for label, params in filters:
            r = run_sim(pc, c3_kpis, c4_kpis, T, M, K, tf_key, **params)
            if base is None:
                base = r
            total_blocked = sum(r["blocked"].values())
            delta = (r["pnl"] - base["pnl"]) / abs(base["pnl"]) * 100 if base["pnl"] else 0
            print(f"  {label:<26} {r['n']:>7} {total_blocked:>8} {r['hr']:>6.1f} {r['pnl']:>+10} {delta:>+7.1f}% {r['pf']:>7.1f} {r['worst']:>+7.1f}")

            tf_results.append({
                "filter": label, "params": params,
                "n": r["n"], "hr": r["hr"], "pnl": r["pnl"], "pf": r["pf"],
                "worst": r["worst"], "blocked": r["blocked"],
                "delta_pnl_pct": round(delta, 1),
            })

        all_results[tf_key] = tf_results

    # Cross-TF summary for best filters
    print(f"\n{'━' * 90}")
    print(f"  CROSS-TIMEFRAME SUMMARY")
    print(f"{'━' * 90}")

    for label, _ in filters:
        if label == "Baseline":
            continue
        row = []
        for tf in ["4H", "1D", "1W"]:
            for r in all_results[tf]:
                if r["filter"] == label:
                    row.append(r)
                    break
        if len(row) == 3:
            deltas = [r["delta_pnl_pct"] for r in row]
            avg_delta = np.mean(deltas)
            pf_changes = []
            for tf, r in zip(["4H", "1D", "1W"], row):
                base_r = all_results[tf][0]
                pf_changes.append(r["pf"] - base_r["pf"])
            avg_pf = np.mean(pf_changes)
            worst_changes = []
            for tf, r in zip(["4H", "1D", "1W"], row):
                base_r = all_results[tf][0]
                worst_changes.append(r["worst"] - base_r["worst"])

            blocks = [sum(r["blocked"].values()) for r in row]
            print(f"\n  {label}")
            print(f"    PnL Δ:  4H={deltas[0]:+.1f}%  1D={deltas[1]:+.1f}%  1W={deltas[2]:+.1f}%  avg={avg_delta:+.1f}%")
            print(f"    PF Δ:   4H={pf_changes[0]:+.1f}  1D={pf_changes[1]:+.1f}  1W={pf_changes[2]:+.1f}  avg={avg_pf:+.1f}")
            print(f"    Worst Δ: 4H={worst_changes[0]:+.1f}  1D={worst_changes[1]:+.1f}  1W={worst_changes[2]:+.1f}")
            print(f"    Blocked: 4H={blocks[0]}  1D={blocks[1]}  1W={blocks[2]}")

    jp = out_root / "phase12e_results.json"
    jp.write_text(json.dumps(all_results, indent=2, default=str))
    print(f"\n  Results saved to {jp}")
    print(f"  Done in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

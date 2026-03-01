"""
Phase 12d — Breakeven Stop Test

Compares baseline Exit Flow v4 against a variant that moves the stop-loss
to the entry price (breakeven) once the stock dips below entry and then
recovers back to Close >= entry_price.

Rule:
  - On entry: stop = entry_price - K × ATR (unchanged)
  - Track whether Close has ever been < entry_price during the trade
  - If it was below, and Close >= entry_price again → move stop to entry_price
  - Breakeven stop is a floor: checkpoint resets can move it higher but never lower
  - One-way ratchet: once at breakeven, it stays at least at entry_price

All other Exit Flow v4 logic unchanged (lenient/strict stages, KPI invalidation,
M-bar checkpoint resets, ATR stop, C4 scale-up).

Dataset: sample_300 (295 stocks), OOS = last 30%.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from trading_dashboard.kpis.catalog import compute_kpi_state_map
from trading_dashboard.kpis.rules import STATE_BULL

ENRICHED_DIR = REPO_DIR / "research" / "data" / "feature_store" / "enriched" / "sample_300" / "stock_data"
OUTPUTS_ROOT = Path(__file__).resolve().parent / "outputs"

IS_FRACTION = 0.70
ATR_PERIOD = 14
MAX_HOLD_HARD_CAP = 500
COMMISSION_PCT = 0.1

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


@dataclass
class Trade:
    entry_bar: int
    exit_bar: int
    entry_price: float
    exit_price: float
    return_pct: float
    weighted_return: float
    holding_bars: int
    exit_reason: str
    max_level: str
    size_at_exit: float
    be_triggered: bool  # whether breakeven stop was activated


@dataclass
class Result:
    name: str
    trades: List[Trade]
    n: int
    hr: float
    avg_ret: float
    total_pnl: float
    pf: float
    avg_hold: float
    worst: float
    atr_exits: int
    be_exits: int  # exits specifically caused by breakeven stop


def run_sim(data: Dict[str, pd.DataFrame],
            c3_kpis: List[str], c4_kpis: List[str],
            T: int, M: int, K: float,
            use_breakeven: bool,
            use_sma200: bool = False,
            tf: str = "1D") -> Result:
    all_trades: List[Trade] = []
    c4_weight = 1.5

    for sym, df in data.items():
        if df.empty:
            continue
        sm = compute_kpi_state_map(df)
        if any(k not in sm for k in c3_kpis):
            continue

        c3_bull = pd.Series(True, index=df.index)
        for kpi in c3_kpis:
            c3_bull &= (sm[kpi] == STATE_BULL)

        c4_available = all(k in sm for k in c4_kpis)
        c4_bull = pd.Series(False, index=df.index)
        if c4_available:
            c4_bull = pd.Series(True, index=df.index)
            for kpi in c4_kpis:
                c4_bull &= (sm[kpi] == STATE_BULL)

        cl = df["Close"].to_numpy(float)
        at = compute_atr(df, ATR_PERIOD).to_numpy(float)
        n = len(df)

        sma200_ok = None
        if use_sma200 and tf in ("1D", "1W") and n >= 200:
            sma200 = pd.Series(cl).rolling(200, min_periods=200).mean().to_numpy()
            sma200_ok = cl >= sma200

        si = int(n * IS_FRACTION)
        j = si

        while j < n:
            if not c3_bull.iloc[j]:
                j += 1
                continue

            if sma200_ok is not None and not sma200_ok[j]:
                j += 1
                continue

            ep = float(cl[j])
            if ep <= 0:
                j += 1
                continue

            ei = j
            stop_price = ep
            stop = stop_price - K * at[ei] if at[ei] > 0 else -np.inf
            bars_since_reset = 0
            current_size = 1.0
            max_level = "C3"
            was_scaled = False

            if c4_available and c4_bull.iloc[j]:
                current_size = c4_weight
                max_level = "C4"
                was_scaled = True

            active_kpis = c4_kpis if max_level == "C4" else c3_kpis
            nk = len(active_kpis)
            xi = None
            reason = "mh"

            was_below_entry = False
            be_active = False

            j_inner = ei + 1
            while j_inner < min(ei + MAX_HOLD_HARD_CAP + 1, n):
                bars_since_reset += 1
                c = float(cl[j_inner])
                total_bars = j_inner - ei

                if c < stop:
                    xi = j_inner
                    reason = "be_stop" if (use_breakeven and be_active and stop >= ep) else "atr"
                    break

                # Breakeven stop logic
                if use_breakeven and not be_active:
                    if c < ep:
                        was_below_entry = True
                    elif was_below_entry and c >= ep:
                        be_active = True
                        if ep > stop:
                            stop = ep
                            stop_price = max(stop_price, ep)

                if not was_scaled and c4_available and c4_bull.iloc[j_inner]:
                    current_size = c4_weight
                    max_level = "C4"
                    was_scaled = True
                    active_kpis = c4_kpis
                    nk = len(active_kpis)

                nb = sum(1 for kk in active_kpis
                         if kk in sm and j_inner < len(sm[kk])
                         and int(sm[kk].iloc[j_inner]) != STATE_BULL)

                if total_bars <= T:
                    if nb >= nk:
                        xi, reason = j_inner, "len"
                        break
                else:
                    if nb >= 2:
                        xi, reason = j_inner, "str"
                        break

                if bars_since_reset >= M:
                    nb_check = sum(1 for kk in active_kpis
                                   if kk in sm and j_inner < len(sm[kk])
                                   and int(sm[kk].iloc[j_inner]) != STATE_BULL)
                    if nb_check == 0:
                        new_stop_price = c
                        new_atr_stop = new_stop_price - K * at[j_inner] if j_inner < len(at) and at[j_inner] > 0 else stop
                        # Checkpoint can only move stop UP (breakeven floor)
                        if new_atr_stop > stop:
                            stop_price = new_stop_price
                            stop = new_atr_stop
                        bars_since_reset = 0
                    else:
                        xi, reason = j_inner, "reset_exit"
                        break

                j_inner += 1

            if xi is None:
                xi = min(j_inner, n - 1)

            xp = float(cl[xi])
            h = xi - ei
            if h > 0:
                ret_pct = (xp - ep) / ep * 100 - COMMISSION_PCT
                weighted_ret = ret_pct * current_size
                all_trades.append(Trade(
                    ei, xi, ep, xp, ret_pct, weighted_ret,
                    h, reason, max_level, current_size, be_active,
                ))

            j = xi + 1

    nt = len(all_trades)
    if nt == 0:
        return Result("", [], 0, 0, 0, 0, 0, 0, 0, 0, 0)

    rets = [t.weighted_return for t in all_trades]
    hr = sum(1 for r in rets if r > 0) / nt * 100
    wi = sum(r for r in rets if r > 0)
    lo = abs(sum(r for r in rets if r <= 0))
    pf = wi / lo if lo > 0 else 999.0
    worst = min(t.return_pct for t in all_trades)
    atr_exits = sum(1 for t in all_trades if t.exit_reason == "atr")
    be_exits = sum(1 for t in all_trades if t.exit_reason == "be_stop")

    label = "Breakeven" if use_breakeven else "Baseline"
    return Result(
        name=label, trades=all_trades, n=nt, hr=hr,
        avg_ret=float(np.mean(rets)),
        total_pnl=float(np.sum(rets)),
        pf=pf,
        avg_hold=float(np.mean([t.holding_bars for t in all_trades])),
        worst=worst,
        atr_exits=atr_exits,
        be_exits=be_exits,
    )


def analyze_breakeven_impact(base: Result, be: Result) -> Dict[str, Any]:
    """Detailed analysis of what the breakeven stop changes."""
    be_triggered_trades = [t for t in be.trades if t.be_triggered]
    be_not_triggered = [t for t in be.trades if not t.be_triggered]

    be_triggered_rets = [t.weighted_return for t in be_triggered_trades] if be_triggered_trades else [0]
    be_stopped_trades = [t for t in be.trades if t.exit_reason == "be_stop"]
    be_stopped_rets = [t.weighted_return for t in be_stopped_trades] if be_stopped_trades else [0]

    base_atr_trades = [t for t in base.trades if t.exit_reason == "atr"]
    base_atr_rets = [t.return_pct for t in base_atr_trades] if base_atr_trades else [0]

    return {
        "be_triggered_count": len(be_triggered_trades),
        "be_triggered_pct": len(be_triggered_trades) / be.n * 100 if be.n else 0,
        "be_triggered_avg_ret": float(np.mean(be_triggered_rets)),
        "be_stopped_count": len(be_stopped_trades),
        "be_stopped_avg_ret": float(np.mean(be_stopped_rets)),
        "base_atr_exit_count": len(base_atr_trades),
        "base_atr_worst": float(min(base_atr_rets)) if base_atr_rets else 0,
        "be_atr_worst": float(min(t.return_pct for t in be.trades if t.exit_reason in ("atr", "be_stop"))) if any(t.exit_reason in ("atr", "be_stop") for t in be.trades) else 0,
    }


def main():
    t0 = time.time()
    out_root = OUTPUTS_ROOT / "all" / "phase12d"
    out_root.mkdir(parents=True, exist_ok=True)

    results_json: Dict[str, Any] = {}

    print("=" * 72)
    print("  Phase 12d — Breakeven Stop Test")
    print("  Dataset: sample_300 (295 stocks), OOS: last 30%")
    print("  Commission: 0.1% round-trip")
    print("=" * 72)

    for tf_key in ["4H", "1D", "1W"]:
        print(f"\n{'—' * 72}")
        print(f"  TIMEFRAME: {tf_key}")
        print(f"{'—' * 72}")

        data = load_data(ENRICHED_DIR, tf_key)
        print(f"  Loaded {len(data)} stocks")

        p = EXIT_PARAMS[tf_key]
        T, M, K = p["T"], p["M"], p["K"]
        c3_kpis = ENTRY_COMBOS[tf_key]["C3"]
        c4_kpis = ENTRY_COMBOS[tf_key]["C4"]
        use_sma200 = tf_key in ("1D", "1W")

        print(f"  C3: {', '.join(c3_kpis)}")
        print(f"  C4: {', '.join(c4_kpis)}")
        print(f"  Exit: T={T}, M={M}, K={K}, SMA200={'Yes' if use_sma200 else 'No'}")

        t1 = time.time()
        base = run_sim(data, c3_kpis, c4_kpis, T, M, K,
                       use_breakeven=False, use_sma200=use_sma200, tf=tf_key)
        t_base = time.time() - t1

        t1 = time.time()
        be = run_sim(data, c3_kpis, c4_kpis, T, M, K,
                     use_breakeven=True, use_sma200=use_sma200, tf=tf_key)
        t_be = time.time() - t1

        impact = analyze_breakeven_impact(base, be)

        pnl_delta = be.total_pnl - base.total_pnl
        pnl_delta_pct = pnl_delta / abs(base.total_pnl) * 100 if base.total_pnl != 0 else 0

        print(f"\n  {'Metric':<28} {'Baseline':>12} {'Breakeven':>12} {'Delta':>12}")
        print(f"  {'—'*28} {'—'*12} {'—'*12} {'—'*12}")
        print(f"  {'Trades':<28} {base.n:>12} {be.n:>12} {be.n - base.n:>+12}")
        print(f"  {'Hit Rate %':<28} {base.hr:>12.1f} {be.hr:>12.1f} {be.hr - base.hr:>+12.1f}")
        print(f"  {'Avg Return %':<28} {base.avg_ret:>12.2f} {be.avg_ret:>12.2f} {be.avg_ret - base.avg_ret:>+12.2f}")
        print(f"  {'Total PnL (weighted) %':<28} {base.total_pnl:>+12.0f} {be.total_pnl:>+12.0f} {pnl_delta:>+12.0f}")
        print(f"  {'PnL Change':<28} {'':>12} {'':>12} {pnl_delta_pct:>+11.1f}%")
        print(f"  {'Profit Factor':<28} {base.pf:>12.1f} {be.pf:>12.1f} {be.pf - base.pf:>+12.1f}")
        print(f"  {'Avg Hold (bars)':<28} {base.avg_hold:>12.1f} {be.avg_hold:>12.1f} {be.avg_hold - base.avg_hold:>+12.1f}")
        print(f"  {'Worst Trade %':<28} {base.worst:>12.1f} {be.worst:>12.1f} {be.worst - base.worst:>+12.1f}")
        print(f"  {'ATR Stop Exits':<28} {base.atr_exits:>12} {be.atr_exits:>12} {be.atr_exits - base.atr_exits:>+12}")
        print(f"  {'BE Stop Exits':<28} {'—':>12} {be.be_exits:>12}")

        print(f"\n  Breakeven stop analysis:")
        print(f"    Trades where BE triggered:  {impact['be_triggered_count']} ({impact['be_triggered_pct']:.1f}%)")
        print(f"    Avg return (BE triggered):  {impact['be_triggered_avg_ret']:+.2f}%")
        print(f"    Trades exited BY BE stop:   {impact['be_stopped_count']}")
        print(f"    Avg return (BE exits):      {impact['be_stopped_avg_ret']:+.2f}%")
        print(f"    Baseline worst ATR exit:    {impact['base_atr_worst']:+.1f}%")
        print(f"    BE worst stop exit:         {impact['be_atr_worst']:+.1f}%")
        print(f"    Elapsed: baseline {t_base:.0f}s, breakeven {t_be:.0f}s")

        results_json[tf_key] = {
            "baseline": {
                "n": base.n, "hr": round(base.hr, 1), "avg_ret": round(base.avg_ret, 2),
                "total_pnl": round(base.total_pnl), "pf": round(base.pf, 2),
                "avg_hold": round(base.avg_hold, 1), "worst": round(base.worst, 1),
                "atr_exits": base.atr_exits,
            },
            "breakeven": {
                "n": be.n, "hr": round(be.hr, 1), "avg_ret": round(be.avg_ret, 2),
                "total_pnl": round(be.total_pnl), "pf": round(be.pf, 2),
                "avg_hold": round(be.avg_hold, 1), "worst": round(be.worst, 1),
                "atr_exits": be.atr_exits, "be_exits": be.be_exits,
            },
            "delta": {
                "pnl_change": round(pnl_delta), "pnl_change_pct": round(pnl_delta_pct, 1),
                "hr_change": round(be.hr - base.hr, 1), "pf_change": round(be.pf - base.pf, 2),
                "worst_change": round(be.worst - base.worst, 1),
            },
            "impact": {
                "be_triggered_count": impact["be_triggered_count"],
                "be_triggered_pct": round(impact["be_triggered_pct"], 1),
                "be_triggered_avg_ret": round(impact["be_triggered_avg_ret"], 2),
                "be_stopped_count": impact["be_stopped_count"],
                "be_stopped_avg_ret": round(impact["be_stopped_avg_ret"], 2),
            },
        }

    jp = out_root / "phase12d_results.json"
    jp.write_text(json.dumps(results_json, indent=2, default=str))
    print(f"\n  Results saved to {jp}")

    print(f"\n{'=' * 72}")
    print(f"  SUMMARY")
    print(f"{'=' * 72}")
    print(f"\n  {'TF':<6} {'Base PnL':>10} {'BE PnL':>10} {'Δ PnL':>10} {'Δ%':>8} {'Base PF':>8} {'BE PF':>8} {'Δ HR':>8} {'Δ Worst':>8} {'BE Exits':>9}")
    print(f"  {'—'*6} {'—'*10} {'—'*10} {'—'*10} {'—'*8} {'—'*8} {'—'*8} {'—'*8} {'—'*8} {'—'*9}")
    for tf in ["4H", "1D", "1W"]:
        r = results_json[tf]
        b, e, d = r["baseline"], r["breakeven"], r["delta"]
        print(f"  {tf:<6} {b['total_pnl']:>+10} {e['total_pnl']:>+10} {d['pnl_change']:>+10} {d['pnl_change_pct']:>+7.1f}% {b['pf']:>8.1f} {e['pf']:>8.1f} {d['hr_change']:>+7.1f} {d['worst_change']:>+7.1f} {e['be_exits']:>9}")

    print(f"\n  Done in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

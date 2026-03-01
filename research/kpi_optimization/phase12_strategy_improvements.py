"""
Phase 12 — Strategy Improvement Investigation

Tests three categories of improvements against the locked Exit Flow v4 baseline:

1. TRAILING STOP VARIANTS
   A) Trailing ATR: after lenient stage, stop = max_close - K*ATR (ratchets up)
   B) Tightening K: K=4 → K=3 after T bars → K=2 after M bars
   C) Partial take: close 50% at +2*ATR gain, trail rest with K=2

2. REGIME FILTERS
   A) Breadth filter: suppress entries when <40% of universe has bullish NWSm
   B) Market trend: suppress entries when market index < 200-bar SMA

3. PORTFOLIO-LEVEL RISK CONTROLS
   Max concurrent positions (15/20/25), sector cap, equity curve stop.
   Requires day-by-day portfolio simulation across all stocks.

All tests run on OOS data (last 30%) with 0.1% commission.
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from trading_dashboard.kpis.catalog import compute_kpi_state_map
from trading_dashboard.kpis.rules import STATE_BULL

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
OUTPUTS_DIR = Path(__file__).resolve().parent / "outputs" / "all" / "phase12"
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

ATR_PERIOD = 14
MAX_HOLD = 500
IS_FRACTION = 0.70
COMMISSION_RT = 0.001

GLOBAL_EXIT = {
    "4H": {"T": 4, "M": 48, "K": 4.0},
    "1D": {"T": 4, "M": 40, "K": 4.0},
    "1W": {"T": 2, "M": 20, "K": 4.0},
}

GLOBAL_COMBOS = {
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

SAMPLE_META_PATH = REPO_DIR / "research" / "sample_universe" / "sample_meta.json"

# ── Helpers ──────────────────────────────────────────────────────────

def compute_atr(df, period=14):
    h, l, pc = df["High"], df["Low"], df["Close"].shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()


def load_data(tf):
    data = {}
    for ext in ["parquet", "csv"]:
        for f in sorted(ENRICHED_DIR.glob(f"*_{tf}.{ext}")):
            sym = f.stem.rsplit(f"_{tf}", 1)[0]
            if sym in data:
                continue
            try:
                df = pd.read_parquet(f) if ext == "parquet" else pd.read_csv(f, index_col=0, parse_dates=True)
                if hasattr(df.index, 'tz') and df.index.tz is not None:
                    df.index = df.index.tz_localize(None)
                if len(df) >= 100 and "Close" in df.columns:
                    data[sym] = df
            except Exception:
                continue
    return data


def load_sector_map():
    if SAMPLE_META_PATH.exists():
        with open(SAMPLE_META_PATH) as f:
            meta = json.load(f)
        return {s["symbol"]: s.get("sector", "Unknown") for s in meta.get("stocks", [])}
    return {}


# ── Exit Flow Simulators ─────────────────────────────────────────────

def _sim_single_stock(df, sm, c3_kpis, c4_kpis, T, M, K, si, ei_limit,
                      mode="baseline", c4_weight=1.5):
    """Simulate trades on one stock. Returns list of trade dicts.

    mode: "baseline" | "trailing" | "tightening" | "partial"
    """
    c3_bull = pd.Series(True, index=df.index)
    for kpi in c3_kpis:
        c3_bull &= (sm[kpi] == STATE_BULL)

    c4_avail = all(k in sm for k in c4_kpis)
    c4_bull = pd.Series(False, index=df.index)
    if c4_avail:
        c4_bull = pd.Series(True, index=df.index)
        for kpi in c4_kpis:
            c4_bull &= (sm[kpi] == STATE_BULL)

    cl = df["Close"].to_numpy(float)
    at = compute_atr(df, ATR_PERIOD).to_numpy(float)
    n = len(df)
    trades = []

    j = si
    while j < ei_limit:
        if not c3_bull.iloc[j]:
            j += 1
            continue
        if j > 0 and c3_bull.iloc[j - 1]:
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
        scaled = c4_avail and c4_bull.iloc[j]
        active_kpis = c4_kpis if scaled else c3_kpis
        nk = len(active_kpis)
        max_close = ep
        partial_taken = False
        partial_ret = 0.0

        xi = None
        reason = "mh"
        j_inner = ei + 1

        while j_inner < min(ei + MAX_HOLD + 1, n):
            bars_since_reset += 1
            c = float(cl[j_inner])
            total_bars = j_inner - ei

            if c > max_close:
                max_close = c

            # ── Stop logic varies by mode ──
            current_stop = stop
            if mode == "trailing" and total_bars > T:
                trail_stop = max_close - K * at[j_inner] if at[j_inner] > 0 else stop
                current_stop = max(stop, trail_stop)
                stop = current_stop
            elif mode == "tightening":
                if total_bars <= T:
                    k_eff = K
                elif total_bars <= M:
                    k_eff = 3.0
                else:
                    k_eff = 2.0
                current_stop = max(stop, max_close - k_eff * at[j_inner]) if at[j_inner] > 0 else stop
                stop = current_stop
            elif mode == "partial" and not partial_taken:
                atr_at_entry = at[ei] if at[ei] > 0 else 1.0
                if c >= ep + 2.0 * atr_at_entry:
                    partial_ret = (c - ep) / ep * 100 * 0.5
                    partial_taken = True
                    stop = max(stop, c - 2.0 * at[j_inner]) if at[j_inner] > 0 else stop
                    current_stop = stop

            if c < current_stop:
                xi = j_inner
                reason = "atr"
                break

            if not scaled and c4_avail and c4_bull.iloc[j_inner]:
                scaled = True
                active_kpis = c4_kpis
                nk = len(active_kpis)

            nb = sum(1 for kk in active_kpis if kk in sm and j_inner < len(sm[kk]) and int(sm[kk].iloc[j_inner]) != STATE_BULL)
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
                if nb == 0:
                    stop_price = c
                    stop = stop_price - K * at[j_inner] if j_inner < len(at) and at[j_inner] > 0 else stop
                    bars_since_reset = 0
                else:
                    xi = j_inner
                    reason = "reset"
                    break

            j_inner += 1

        if xi is None:
            xi = min(j_inner, n - 1)
        h = xi - ei
        if h > 0:
            gross = (float(cl[xi]) - ep) / ep * 100
            net = gross - COMMISSION_RT * 100

            if mode == "partial" and partial_taken:
                remaining_ret = net * 0.5
                net_total = partial_ret - COMMISSION_RT * 50 + remaining_ret
                gross_total = gross * 0.5 + (partial_ret / 0.5 * 0.5) if partial_ret else gross
            else:
                net_total = net
                gross_total = gross

            weight = c4_weight if scaled else 1.0
            trades.append({
                "entry_idx": ei,
                "exit_idx": xi,
                "entry_date": str(df.index[ei])[:10],
                "exit_date": str(df.index[xi])[:10],
                "entry_price": ep,
                "exit_price": float(cl[xi]),
                "gross": round(gross_total, 4),
                "net": round(net_total, 4),
                "weighted_net": round(net_total * weight, 4),
                "hold": h,
                "scaled": scaled,
                "weight": weight,
                "reason": reason,
                "max_close": max_close,
                "max_gain_pct": round((max_close - ep) / ep * 100, 2),
            })

        j = xi + 1

    return trades


def aggregate_trades(all_trades):
    if not all_trades:
        return None
    nets = [t["net"] for t in all_trades]
    weighted = [t["weighted_net"] for t in all_trades]
    holds = [t["hold"] for t in all_trades]
    max_gains = [t["max_gain_pct"] for t in all_trades]
    n = len(nets)
    hr = sum(1 for r in nets if r > 0) / n * 100
    wi = sum(r for r in weighted if r > 0)
    lo = abs(sum(r for r in weighted if r <= 0))
    n_scaled = sum(1 for t in all_trades if t["scaled"])

    giveback = []
    for t in all_trades:
        if t["max_gain_pct"] > 0 and t["net"] < t["max_gain_pct"]:
            giveback.append(t["max_gain_pct"] - t["net"])

    return {
        "n": n,
        "n_scaled": n_scaled,
        "pct_scaled": round(n_scaled / n * 100, 1) if n else 0,
        "hr": round(hr, 1),
        "avg": round(float(np.mean(nets)), 2),
        "pnl_1x": round(sum(nets)),
        "pnl_w": round(sum(weighted)),
        "pf": round(wi / lo if lo > 0 else 999.0, 2),
        "worst": round(min(nets), 1),
        "avg_hold": round(float(np.mean(holds)), 1),
        "max_hold": int(max(holds)),
        "avg_max_gain": round(float(np.mean(max_gains)), 2),
        "avg_giveback": round(float(np.mean(giveback)), 2) if giveback else 0,
    }


# ── PART 1: Trailing Stop Variants ──────────────────────────────────

def run_trailing_stop_analysis(data_all_tf):
    print("\n" + "=" * 70)
    print("PART 1: TRAILING STOP VARIANTS")
    print("=" * 70)

    modes = ["baseline", "trailing", "tightening", "partial"]
    mode_labels = {
        "baseline": "Baseline (Exit Flow v4)",
        "trailing": "A) Trailing ATR",
        "tightening": "B) Tightening K",
        "partial": "C) Partial Take 50%",
    }

    results = {}

    for tf in ["4H", "1D", "1W"]:
        print(f"\n── {tf} ──")
        data = data_all_tf.get(tf, {})
        if not data:
            print(f"  No data for {tf}")
            continue

        T = GLOBAL_EXIT[tf]["T"]
        M = GLOBAL_EXIT[tf]["M"]
        K = GLOBAL_EXIT[tf]["K"]
        c3 = GLOBAL_COMBOS[tf]["C3"]
        c4 = GLOBAL_COMBOS[tf]["C4"]

        results[tf] = {}

        for mode in modes:
            all_trades = []
            for sym, df in data.items():
                sm = compute_kpi_state_map(df)
                if any(k not in sm for k in c3):
                    continue
                n = len(df)
                si = int(n * IS_FRACTION)
                trades = _sim_single_stock(df, sm, c3, c4, T, M, K, si, n,
                                           mode=mode, c4_weight=1.5)
                for t in trades:
                    t["symbol"] = sym
                all_trades.extend(trades)

            agg = aggregate_trades(all_trades)
            results[tf][mode] = {"agg": agg, "trades": all_trades}

            if agg:
                print(f"  {mode_labels[mode]:35s} | n={agg['n']:>5d} HR={agg['hr']:>5.1f}% "
                      f"Avg={agg['avg']:>+6.2f}% PnL(w)={agg['pnl_w']:>+8,d}% "
                      f"PF={agg['pf']:>5.2f} Worst={agg['worst']:>+6.1f}% "
                      f"AvgHold={agg['avg_hold']:>5.1f} "
                      f"AvgMaxGain={agg['avg_max_gain']:>+6.2f}% "
                      f"AvgGiveback={agg['avg_giveback']:>5.2f}%")

    return results


# ── PART 2: Regime Filters ──────────────────────────────────────────

def compute_breadth_timeseries(data, tf):
    """For each date, compute fraction of stocks with bullish NWSm."""
    nwsm_key = "Nadaraya-Watson Smoother"
    date_bull = defaultdict(lambda: [0, 0])  # [bull_count, total_count]

    for sym, df in data.items():
        sm = compute_kpi_state_map(df)
        s = sm.get(nwsm_key)
        if s is None:
            continue
        for i in range(len(s)):
            dt = str(df.index[i])[:10]
            date_bull[dt][1] += 1
            if int(s.iloc[i]) == STATE_BULL:
                date_bull[dt][0] += 1

    breadth = {}
    for dt, (b, t) in sorted(date_bull.items()):
        breadth[dt] = b / t if t > 0 else 0.5
    return breadth


def run_regime_filter_analysis(data_all_tf):
    print("\n" + "=" * 70)
    print("PART 2: REGIME FILTERS")
    print("=" * 70)

    thresholds = [0.30, 0.35, 0.40, 0.45, 0.50]
    results = {}

    for tf in ["4H", "1D", "1W"]:
        print(f"\n── {tf} ──")
        data = data_all_tf.get(tf, {})
        if not data:
            continue

        T = GLOBAL_EXIT[tf]["T"]
        M = GLOBAL_EXIT[tf]["M"]
        K = GLOBAL_EXIT[tf]["K"]
        c3 = GLOBAL_COMBOS[tf]["C3"]
        c4 = GLOBAL_COMBOS[tf]["C4"]

        breadth = compute_breadth_timeseries(data, tf)
        results[tf] = {}

        # Baseline (no filter)
        all_trades_base = []
        for sym, df in data.items():
            sm = compute_kpi_state_map(df)
            if any(k not in sm for k in c3):
                continue
            n = len(df)
            si = int(n * IS_FRACTION)
            trades = _sim_single_stock(df, sm, c3, c4, T, M, K, si, n,
                                       mode="baseline", c4_weight=1.5)
            for t in trades:
                t["symbol"] = sym
            all_trades_base.extend(trades)

        agg_base = aggregate_trades(all_trades_base)
        results[tf]["no_filter"] = {"agg": agg_base, "trades": all_trades_base}
        if agg_base:
            print(f"  {'No filter (baseline)':35s} | n={agg_base['n']:>5d} HR={agg_base['hr']:>5.1f}% "
                  f"PnL(w)={agg_base['pnl_w']:>+8,d}% PF={agg_base['pf']:>5.2f} "
                  f"Worst={agg_base['worst']:>+6.1f}%")

        # Breadth filter at various thresholds
        for thresh in thresholds:
            filtered_trades = []
            for t in all_trades_base:
                entry_date = t["entry_date"]
                b = breadth.get(entry_date, 0.5)
                if b >= thresh:
                    filtered_trades.append(t)

            agg = aggregate_trades(filtered_trades)
            label = f"Breadth >= {thresh:.0%}"
            results[tf][f"breadth_{thresh}"] = {"agg": agg, "trades": filtered_trades}
            if agg:
                skipped = agg_base["n"] - agg["n"] if agg_base else 0
                print(f"  {label:35s} | n={agg['n']:>5d} HR={agg['hr']:>5.1f}% "
                      f"PnL(w)={agg['pnl_w']:>+8,d}% PF={agg['pf']:>5.2f} "
                      f"Worst={agg['worst']:>+6.1f}% "
                      f"Skipped={skipped}")

        # Market trend filter (use NWSm of broad index if available)
        # We check for SPY, ^GSPC, or ^STOXX600E in the data
        market_syms = [s for s in data.keys() if s in ("SPY", "^GSPC", "^STOXX50E", "_STOXX600")]
        if market_syms:
            mkt_sym = market_syms[0]
            mkt_df = data[mkt_sym]
            mkt_sma200 = mkt_df["Close"].rolling(200, min_periods=50).mean()
            mkt_above = mkt_df["Close"] >= mkt_sma200
            mkt_above_map = {str(dt)[:10]: bool(v) for dt, v in zip(mkt_df.index, mkt_above)}

            filtered_trades = [t for t in all_trades_base if mkt_above_map.get(t["entry_date"], True)]
            agg = aggregate_trades(filtered_trades)
            results[tf]["mkt_trend"] = {"agg": agg, "trades": filtered_trades}
            if agg:
                skipped = agg_base["n"] - agg["n"] if agg_base else 0
                print(f"  {f'Market trend ({mkt_sym}>SMA200)':35s} | n={agg['n']:>5d} HR={agg['hr']:>5.1f}% "
                      f"PnL(w)={agg['pnl_w']:>+8,d}% PF={agg['pf']:>5.2f} "
                      f"Worst={agg['worst']:>+6.1f}% "
                      f"Skipped={skipped}")

    return results


# ── PART 3: Portfolio-Level Risk Controls ────────────────────────────

def run_portfolio_sim(data_all_tf):
    """Portfolio simulation with proper equal-weight position sizing.

    Each position gets 1/N of total capital (where N = max positions allowed).
    Trades that overlap in time compete for slots. The portfolio equity
    reflects realistic capital allocation, not independent per-stock sums.
    """
    print("\n" + "=" * 70)
    print("PART 3: PORTFOLIO-LEVEL RISK CONTROLS")
    print("=" * 70)

    sector_map = load_sector_map()
    results = {}

    for tf in ["1D"]:
        print(f"\n── {tf} ──")
        data = data_all_tf.get(tf, {})
        if not data:
            continue

        T = GLOBAL_EXIT[tf]["T"]
        M = GLOBAL_EXIT[tf]["M"]
        K = GLOBAL_EXIT[tf]["K"]
        c3 = GLOBAL_COMBOS[tf]["C3"]
        c4 = GLOBAL_COMBOS[tf]["C4"]

        all_trades = []
        for sym, df in data.items():
            sm = compute_kpi_state_map(df)
            if any(k not in sm for k in c3):
                continue
            n = len(df)
            si = int(n * IS_FRACTION)
            trades = _sim_single_stock(df, sm, c3, c4, T, M, K, si, n,
                                       mode="baseline", c4_weight=1.5)
            for t in trades:
                t["symbol"] = sym
                t["sector"] = sector_map.get(sym, "Unknown")
            all_trades.extend(trades)

        all_trades.sort(key=lambda t: t["entry_date"])

        # Baseline (independent sum — same as per-stock backtest)
        agg_base = aggregate_trades(all_trades)
        results["no_limit"] = agg_base
        if agg_base:
            print(f"  {'No limits (indep. sum)':35s} | n={agg_base['n']:>5d} HR={agg_base['hr']:>5.1f}% "
                  f"PnL(w)={agg_base['pnl_w']:>+8,d}% PF={agg_base['pf']:>5.2f}")

        # Overlap analysis
        from collections import Counter
        date_overlap = Counter()
        for t in all_trades:
            for d in pd.date_range(t["entry_date"], t["exit_date"], freq="B"):
                date_overlap[str(d)[:10]] += 1
        if date_overlap:
            overlaps = list(date_overlap.values())
            print(f"\n  Position overlap stats:")
            print(f"    Avg concurrent positions: {np.mean(overlaps):.1f}")
            print(f"    Max concurrent positions: {max(overlaps)}")
            print(f"    Median concurrent:        {np.median(overlaps):.0f}")
            p90 = np.percentile(overlaps, 90)
            p95 = np.percentile(overlaps, 95)
            print(f"    P90 concurrent:           {p90:.0f}")
            print(f"    P95 concurrent:           {p95:.0f}")

        # Equal-weight portfolio simulation
        for max_pos in [15, 20, 25, 30, 50]:
            eq_curve, accepted, dd_stats = _portfolio_equal_weight(
                all_trades, max_positions=max_pos, capital=100_000)
            agg = aggregate_trades(accepted)
            results[f"max_{max_pos}"] = {
                **(agg or {}),
                "max_dd": dd_stats["max_dd"],
                "final_equity": eq_curve[-1] if eq_curve else 0,
                "sharpe_approx": dd_stats.get("sharpe", 0),
            }
            if agg:
                print(f"  {f'EW Max {max_pos} pos':35s} | n={agg['n']:>5d} HR={agg['hr']:>5.1f}% "
                      f"PnL(w)={agg['pnl_w']:>+8,d}% PF={agg['pf']:>5.2f} "
                      f"MaxDD={dd_stats['max_dd']:.1%} "
                      f"Final={eq_curve[-1]/1000:.0f}k "
                      f"Skip={agg_base['n'] - agg['n']}")

        # Sector cap (with equal-weight)
        for sector_cap in [3, 5, 8]:
            eq_curve, accepted, dd_stats = _portfolio_equal_weight(
                all_trades, max_positions=25, capital=100_000,
                sector_cap=sector_cap)
            agg = aggregate_trades(accepted)
            results[f"sec_cap_{sector_cap}"] = {
                **(agg or {}),
                "max_dd": dd_stats["max_dd"],
            }
            if agg:
                print(f"  {f'EW 25 pos, {sector_cap}/sector':35s} | n={agg['n']:>5d} HR={agg['hr']:>5.1f}% "
                      f"PnL(w)={agg['pnl_w']:>+8,d}% PF={agg['pf']:>5.2f} "
                      f"MaxDD={dd_stats['max_dd']:.1%} "
                      f"Skip={agg_base['n'] - agg['n']}")

        # Equity stop variants (with equal-weight, max 25 pos)
        for dd_pause in [0.08, 0.12, 0.15]:
            eq_curve, accepted, dd_stats = _portfolio_equal_weight(
                all_trades, max_positions=25, capital=100_000,
                dd_pause_threshold=dd_pause)
            agg = aggregate_trades(accepted)
            results[f"eq_stop_{int(dd_pause*100)}"] = {
                **(agg or {}),
                "max_dd": dd_stats["max_dd"],
            }
            if agg:
                print(f"  {f'EW 25 pos, pause at -{dd_pause:.0%}DD':35s} | n={agg['n']:>5d} HR={agg['hr']:>5.1f}% "
                      f"PnL(w)={agg['pnl_w']:>+8,d}% PF={agg['pf']:>5.2f} "
                      f"MaxDD={dd_stats['max_dd']:.1%} "
                      f"Skip={agg_base['n'] - agg['n']}")

        # Save equity curve for the 25-position run
        _, _, _ = _portfolio_equal_weight(all_trades, max_positions=25, capital=100_000)
        results["equity_curve"] = _

    return results


def _portfolio_equal_weight(trades, max_positions=25, capital=100_000,
                            sector_cap=None, dd_pause_threshold=None):
    """Day-by-day portfolio sim with equal-weight position sizing.

    Each position gets (capital / max_positions) allocated.
    Returns: (equity_curve_by_trade, accepted_trades, dd_stats)
    """
    sorted_trades = sorted(trades, key=lambda t: t["entry_date"])
    slot_size = capital / max_positions
    equity = capital
    peak = capital
    max_dd = 0
    paused = False

    active = []  # list of (exit_date, sector, slot_return)
    accepted = []
    equity_curve = [equity]
    daily_returns = []

    for t in sorted_trades:
        # Clean expired positions and collect returns
        still_active = []
        for exit_dt, sector, pending in active:
            if exit_dt <= t["entry_date"]:
                ret_dollar = slot_size * (pending / 100.0)
                equity += ret_dollar
                daily_returns.append(pending)
            else:
                still_active.append((exit_dt, sector, pending))
        active = still_active

        # Check drawdown pause
        peak = max(peak, equity)
        dd = (peak - equity) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

        if dd_pause_threshold and dd >= dd_pause_threshold:
            paused = True
        if paused and dd < dd_pause_threshold * 0.5:
            paused = False
        if paused:
            continue

        # Check capacity
        if len(active) >= max_positions:
            continue

        # Check sector cap
        if sector_cap:
            sec = t.get("sector", "Unknown")
            sec_count = sum(1 for _, s, _ in active if s == sec)
            if sec_count >= sector_cap:
                continue

        # Accept trade
        accepted.append(t)
        active.append((t["exit_date"], t.get("sector", "Unknown"), t["weighted_net"]))
        equity_curve.append(equity)

    # Close remaining positions
    for exit_dt, sector, pending in active:
        ret_dollar = slot_size * (pending / 100.0)
        equity += ret_dollar
        daily_returns.append(pending)
    equity_curve.append(equity)

    peak = max(peak, equity)
    dd = (peak - equity) / peak if peak > 0 else 0
    max_dd = max(max_dd, dd)

    sharpe = 0
    if daily_returns:
        avg_r = np.mean(daily_returns)
        std_r = np.std(daily_returns) if len(daily_returns) > 1 else 1
        sharpe = (avg_r / std_r) * np.sqrt(252 / 26) if std_r > 0 else 0  # annualized approx

    dd_stats = {
        "max_dd": max_dd,
        "sharpe": round(sharpe, 2),
        "final_equity": round(equity),
    }
    return equity_curve, accepted, dd_stats


# ── Visualization ────────────────────────────────────────────────────

def plot_trailing_stop_results(results):
    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    modes = ["baseline", "trailing", "tightening", "partial"]
    colors = ["#666666", "#2196f3", "#ff9800", "#4caf50"]
    labels = ["Baseline", "A) Trailing", "B) Tighten K", "C) Partial 50%"]

    for idx, tf in enumerate(["4H", "1D", "1W"]):
        ax = axes[idx]
        if tf not in results:
            continue
        tf_data = results[tf]
        pnls = [tf_data[m]["agg"]["pnl_w"] if tf_data.get(m, {}).get("agg") else 0 for m in modes]
        bars = ax.bar(labels, pnls, color=colors, edgecolor="white", linewidth=0.5)
        ax.set_title(f"{tf} — Weighted P&L")
        ax.set_ylabel("P&L (%)")
        ax.axhline(y=pnls[0], color="#666666", linestyle="--", alpha=0.5)
        for bar, val in zip(bars, pnls):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(pnls) * 0.02,
                    f"{val:+,}", ha="center", va="bottom", fontsize=9, fontweight="bold")
        ax.tick_params(axis='x', rotation=15)

    fig.suptitle("Trailing Stop Variants vs Baseline", fontsize=14, fontweight="bold")
    plt.tight_layout()
    fig.savefig(OUTPUTS_DIR / "trailing_stop_comparison.png")
    plt.close(fig)

    # Worst trade & giveback comparison
    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    for idx, tf in enumerate(["4H", "1D", "1W"]):
        ax = axes[idx]
        if tf not in results:
            continue
        tf_data = results[tf]
        worsts = [tf_data[m]["agg"]["worst"] if tf_data.get(m, {}).get("agg") else 0 for m in modes]
        givebacks = [tf_data[m]["agg"]["avg_giveback"] if tf_data.get(m, {}).get("agg") else 0 for m in modes]

        x = np.arange(len(modes))
        w = 0.35
        b1 = ax.bar(x - w/2, worsts, w, color="#ef5350", label="Worst Trade", edgecolor="white")
        b2 = ax.bar(x + w/2, [-g for g in givebacks], w, color="#ff9800", label="Avg Giveback", edgecolor="white")
        ax.set_title(f"{tf} — Risk Metrics")
        ax.set_ylabel("% Return")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=8, rotation=15)
        ax.legend(fontsize=8)
        ax.axhline(0, color="white", linewidth=0.5)

    fig.suptitle("Risk Metrics by Exit Mode", fontsize=14, fontweight="bold")
    plt.tight_layout()
    fig.savefig(OUTPUTS_DIR / "trailing_stop_risk.png")
    plt.close(fig)


def plot_regime_results(results):
    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    thresholds = [0.30, 0.35, 0.40, 0.45, 0.50]

    for idx, tf in enumerate(["4H", "1D", "1W"]):
        ax = axes[idx]
        if tf not in results:
            continue
        tf_data = results[tf]
        base_pnl = tf_data.get("no_filter", {}).get("agg", {}).get("pnl_w", 0)
        base_pf = tf_data.get("no_filter", {}).get("agg", {}).get("pf", 0)

        thresh_labels = ["None"] + [f"≥{t:.0%}" for t in thresholds]
        pnls = [base_pnl]
        pfs = [base_pf]
        for t in thresholds:
            a = tf_data.get(f"breadth_{t}", {}).get("agg", {})
            pnls.append(a.get("pnl_w", 0) if a else 0)
            pfs.append(a.get("pf", 0) if a else 0)

        color_pnl = "#2196f3"
        color_pf = "#4caf50"
        x = np.arange(len(thresh_labels))
        ax2 = ax.twinx()
        ax.bar(x - 0.2, pnls, 0.4, color=color_pnl, alpha=0.8, label="P&L(w)")
        ax2.plot(x, pfs, "o-", color=color_pf, linewidth=2, label="PF")
        ax.set_title(f"{tf} — Breadth Filter")
        ax.set_ylabel("P&L (%)", color=color_pnl)
        ax2.set_ylabel("Profit Factor", color=color_pf)
        ax.set_xticks(x)
        ax.set_xticklabels(thresh_labels, fontsize=9)

    fig.suptitle("Regime Filter: NWSm Breadth Threshold", fontsize=14, fontweight="bold")
    plt.tight_layout()
    fig.savefig(OUTPUTS_DIR / "regime_breadth_filter.png")
    plt.close(fig)


def plot_portfolio_results(results):
    # Position limit comparison
    fig, axes = plt.subplots(1, 2, figsize=(16, 6))

    limits = ["no_limit", "max_15", "max_20", "max_25", "max_30", "max_50"]
    limit_labels = ["No limit\n(indep.)", "Max 15", "Max 20", "Max 25", "Max 30", "Max 50"]

    def _get(key, field, default=0):
        v = results.get(key)
        if isinstance(v, dict):
            return v.get(field, default)
        return default

    pnls = [_get(k, "pnl_w") for k in limits]
    hrs = [_get(k, "hr") for k in limits]
    ns = [_get(k, "n") for k in limits]
    dds = [_get(k, "max_dd") for k in limits]

    ax = axes[0]
    x = np.arange(len(limits))
    bars = ax.bar(x, pnls, color="#2196f3", edgecolor="white")
    ax.set_xticks(x)
    ax.set_xticklabels(limit_labels, fontsize=9)
    ax.set_ylabel("P&L (%)")
    ax.set_title("P&L by Position Limit (1D)")
    for bar, pnl, n in zip(bars, pnls, ns):
        if pnl:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(pnls) * 0.01,
                    f"{pnl:+,}\n{n}t", ha="center", va="bottom", fontsize=8)

    ax = axes[1]
    dd_labels = [f"{d:.1%}" if d else "n/a" for d in dds]
    colors = ["#ef5350" if d and d > 0.15 else "#ff9800" if d and d > 0.10 else "#4caf50" for d in dds]
    bars = ax.bar(x, [d * 100 if d else 0 for d in dds], color=colors, edgecolor="white")
    ax.set_xticks(x)
    ax.set_xticklabels(limit_labels, fontsize=9)
    ax.set_ylabel("Max Drawdown (%)")
    ax.set_title("Max Drawdown by Position Limit (1D)")

    fig.suptitle("Portfolio-Level Risk Controls", fontsize=14, fontweight="bold")
    plt.tight_layout()
    fig.savefig(OUTPUTS_DIR / "portfolio_position_limits.png")
    plt.close(fig)


# ── Main ─────────────────────────────────────────────────────────────

def main():
    t0 = time.time()

    print("Loading data...")
    data_all_tf = {}
    for tf in ["4H", "1D", "1W"]:
        data_all_tf[tf] = load_data(tf)
        print(f"  {tf}: {len(data_all_tf[tf])} stocks")

    # Part 1: Trailing stops
    trailing_results = run_trailing_stop_analysis(data_all_tf)
    plot_trailing_stop_results(trailing_results)

    # Part 2: Regime filters
    regime_results = run_regime_filter_analysis(data_all_tf)
    plot_regime_results(regime_results)

    # Part 3: Portfolio risk controls
    portfolio_results = run_portfolio_sim(data_all_tf)
    plot_portfolio_results(portfolio_results)

    # Save summary
    summary = {
        "trailing": {},
        "regime": {},
        "portfolio": {},
    }
    for tf in ["4H", "1D", "1W"]:
        if tf in trailing_results:
            summary["trailing"][tf] = {
                mode: trailing_results[tf][mode]["agg"]
                for mode in trailing_results[tf]
                if trailing_results[tf][mode].get("agg")
            }
        if tf in regime_results:
            summary["regime"][tf] = {
                k: regime_results[tf][k]["agg"]
                for k in regime_results[tf]
                if regime_results[tf][k].get("agg")
            }
    summary["portfolio"] = {
        k: v for k, v in portfolio_results.items()
        if isinstance(v, dict) and "n" in v
    }

    with open(OUTPUTS_DIR / "phase12_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s. Outputs in {OUTPUTS_DIR}")


if __name__ == "__main__":
    main()

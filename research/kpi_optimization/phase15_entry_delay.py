"""
Phase 15 — Entry Delay Sensitivity Test

Measures how fill delay (H bars after C3 onset) affects trade quality.
Sweeps H = {0, 1, 2, 3, 5, 10} across all three timeframes (4H, 1D, 1W)
for both Entry v4 (onset-only) and Entry v5 (full gates).

  v4 gates: C3 onset only
  v5 gates: C3 onset + SMA20>=SMA200 (1D/1W) + vol spike 1.5x + overextension (1W)

ATR stop is computed at the fill bar (signal + H), not the signal bar.
Exit Flow v4 is identical for both versions.

Dataset: sample_300 (~295 stocks), OOS = last 30%.
Commission: 0.1% + 0.5% slippage per trade.
"""

from __future__ import annotations

import csv
import sys
import time
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
OUTPUTS_DIR = Path(__file__).resolve().parent / "outputs" / "all" / "phase15"
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

OOS_FRACTION = 0.70
ATR_PERIOD = 14
MAX_HOLD = 500
COMMISSION = 0.001
SLIPPAGE = 0.005
COST = COMMISSION + SLIPPAGE

DELAYS = [0, 1, 2, 3, 5, 10]

COMBOS = {
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

EXIT_PARAMS = {
    "4H": {"T": 4, "M": 48, "K": 4.0},
    "1D": {"T": 4, "M": 40, "K": 4.0},
    "1W": {"T": 2, "M": 20, "K": 4.0},
}

OVEREXT_LOOKBACK = 5
OVEREXT_PCT = 15.0
VOL_SPIKE_MULT = 1.5
VOL_SPIKE_LOOKBACK = 5


def compute_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> np.ndarray:
    h, lo = df["High"].to_numpy(float), df["Low"].to_numpy(float)
    pc = np.roll(df["Close"].to_numpy(float), 1)
    pc[0] = np.nan
    tr = np.maximum(h - lo, np.maximum(np.abs(h - pc), np.abs(lo - pc)))
    return pd.Series(tr).rolling(window=period, min_periods=1).mean().to_numpy(float)


def load_data(tf: str) -> dict[str, pd.DataFrame]:
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
            if len(df) >= 200 and "Close" in df.columns:
                data[sym] = df
        except Exception:
            continue
    return data


def precompute(data: dict, tf: str) -> dict:
    c3_kpis = COMBOS[tf]["C3"]
    c4_kpis = COMBOS[tf]["C4"]
    precomp = {}

    for sym, df in data.items():
        sm = compute_kpi_state_map(df)
        if any(k not in sm for k in c3_kpis):
            continue

        n = len(df)
        cl = df["Close"].to_numpy(float)
        op = df["Open"].to_numpy(float) if "Open" in df.columns else cl.copy()
        vol = df["Volume"].to_numpy(float) if "Volume" in df.columns else np.zeros(n)
        atr = compute_atr(df, ATR_PERIOD)

        c3_bull = np.ones(n, dtype=bool)
        for k in c3_kpis:
            c3_bull &= (sm[k] == STATE_BULL).to_numpy(bool)

        c4_avail = all(k in sm for k in c4_kpis)
        c4_bull = np.zeros(n, dtype=bool)
        if c4_avail:
            c4_bull = np.ones(n, dtype=bool)
            for k in c4_kpis:
                c4_bull &= (sm[k] == STATE_BULL).to_numpy(bool)

        # v5 gates
        cl_s = pd.Series(cl)
        sma20 = cl_s.rolling(20, min_periods=20).mean().to_numpy(float)
        sma200 = cl_s.rolling(200, min_periods=200).mean().to_numpy(float)

        # Overextension (1W only)
        overext_ok = np.ones(n, dtype=bool)
        if tf == "1W" and n > OVEREXT_LOOKBACK:
            ref = np.empty(n, dtype=float)
            ref[:OVEREXT_LOOKBACK] = np.nan
            ref[OVEREXT_LOOKBACK:] = cl[:-OVEREXT_LOOKBACK]
            with np.errstate(divide="ignore", invalid="ignore"):
                pct_chg = (cl - ref) / ref * 100
            overext_ok = ~(pct_chg > OVEREXT_PCT)

        # Volume spike
        vol_spike_ok = np.ones(n, dtype=bool)
        if vol.sum() > 0:
            vol_ma20 = pd.Series(vol).rolling(20, min_periods=1).mean().to_numpy(float)
            with np.errstate(invalid="ignore"):
                spike_raw = (vol >= VOL_SPIKE_MULT * vol_ma20).astype(float)
            spike_raw = np.nan_to_num(spike_raw, nan=0.0)
            vol_spike_ok = pd.Series(spike_raw).rolling(
                VOL_SPIKE_LOOKBACK, min_periods=1
            ).max().to_numpy().astype(bool)

        # Per-KPI non-bull arrays for exit logic
        kpi_nbull = {}
        for k in set(c3_kpis + c4_kpis):
            if k in sm:
                kpi_nbull[k] = (sm[k] != STATE_BULL).to_numpy(bool)

        hi = df["High"].to_numpy(float) if "High" in df.columns else cl.copy()
        lo_arr = df["Low"].to_numpy(float) if "Low" in df.columns else cl.copy()

        precomp[sym] = {
            "n": n, "cl": cl, "op": op, "hi": hi, "lo": lo_arr, "atr": atr,
            "c3_bull": c3_bull, "c4_avail": c4_avail, "c4_bull": c4_bull,
            "sma20": sma20, "sma200": sma200,
            "overext_ok": overext_ok, "vol_spike_ok": vol_spike_ok,
            "kpi_nbull": kpi_nbull,
        }
    return precomp


def _run_trade(pc: dict, fill_bar: int, tf: str) -> tuple | None:
    """Execute one trade from fill_bar. Returns (weighted_ret, raw_ret, hold_bars, scaled)."""
    T = EXIT_PARAMS[tf]["T"]
    M = EXIT_PARAMS[tf]["M"]
    K = EXIT_PARAMS[tf]["K"]
    c3_kpis = COMBOS[tf]["C3"]
    c4_kpis = COMBOS[tf]["C4"]
    cl = pc["cl"]; op = pc["op"]; atr = pc["atr"]; n = pc["n"]
    c4_avail = pc["c4_avail"]; c4_bull = pc["c4_bull"]
    kpi_nbull = pc["kpi_nbull"]

    ep = float(op[fill_bar]) if fill_bar > 0 else float(cl[fill_bar])
    if ep <= 0 or np.isnan(ep):
        return None

    atr_val = atr[fill_bar]
    stop = ep - K * atr_val if not np.isnan(atr_val) and atr_val > 0 else ep * 0.95
    bars_since_reset = 0
    scaled = c4_avail and c4_bull[fill_bar]
    active = c4_kpis if scaled else c3_kpis
    nk = len(active)
    xi = None

    j = fill_bar + 1
    while j < min(fill_bar + MAX_HOLD + 1, n):
        bars_since_reset += 1
        c = float(cl[j])
        if np.isnan(c):
            j += 1
            continue

        if c < stop:
            xi = j; break

        if not scaled and c4_avail and c4_bull[j]:
            scaled = True
            active = c4_kpis; nk = len(active)

        nb = sum(1 for kk in active if kk in kpi_nbull and j < len(kpi_nbull[kk]) and kpi_nbull[kk][j])
        bars_held = j - fill_bar

        if bars_held <= T:
            if nb >= nk:
                xi = j; break
        else:
            if nb >= 2:
                xi = j; break

        if bars_since_reset >= M:
            if nb == 0:
                a_val = atr[j] if j < len(atr) else np.nan
                stop = c - K * a_val if not np.isnan(a_val) and a_val > 0 else stop
                bars_since_reset = 0
            else:
                xi = j; break
        j += 1

    if xi is None:
        xi = min(j, n - 1)

    # Exit fill: next-bar open for closed trades, close for last bar
    is_open = (xi == n - 1 and j >= n)
    exit_fill = min(xi + 1, n - 1) if not is_open and xi < n - 1 else xi
    xp = float(op[exit_fill]) if exit_fill != xi else float(cl[xi])
    h = xi - fill_bar
    if h <= 0 or is_open:
        return None
    weight = 1.5 if scaled else 1.0
    ret = (xp - ep) / ep * 100 - COST * 100
    return (ret * weight, ret, h, scaled)


FILTER_DELAYS = [1, 2, 3, 5, 10]


def simulate(precomp: dict, tf: str, delay: int, version: str,
             filter_mode: str = "all") -> list:
    """Run all trades for a given (TF, delay, version) across all stocks.

    filter_mode:
      "all"              — no filtering (default)
      "h1_down_survived" — only signals where bar s+1 closed below bar s
                           (down bar) but did NOT breach the ATR stop
    """
    K = EXIT_PARAMS[tf]["K"]
    trades = []
    for sym, pc in precomp.items():
        n = pc["n"]
        si = int(n * OOS_FRACTION)
        c3 = pc["c3_bull"]
        j = si

        while j < n:
            if not c3[j] or (j > 0 and c3[j - 1]):
                j += 1
                continue

            # v5 gates (skipped for v4)
            if version == "v5":
                if tf in ("1D", "1W"):
                    if np.isnan(pc["sma20"][j]) or np.isnan(pc["sma200"][j]) or pc["sma20"][j] < pc["sma200"][j]:
                        j += 1
                        continue
                if not pc["overext_ok"][j]:
                    j += 1
                    continue
                if not pc["vol_spike_ok"][j]:
                    j += 1
                    continue

            # H1 down-but-survived filter
            if filter_mode == "h1_down_survived":
                h1 = j + 1
                if h1 >= n:
                    break
                cl_signal = pc["cl"][j]
                cl_h1 = pc["cl"][h1]
                is_down = cl_h1 < cl_signal
                atr_h1 = pc["atr"][h1]
                op_h1 = pc["op"][h1]
                stop_h1 = (op_h1 - K * atr_h1
                           if not np.isnan(atr_h1) and atr_h1 > 0
                           else op_h1 * 0.95)
                survived = cl_h1 >= stop_h1
                if not (is_down and survived):
                    j += 1
                    continue

            # Hybrid: H1 down+survived → enter at H=2, otherwise enter at H=1
            if filter_mode == "hybrid":
                h1 = j + 1
                if h1 >= n:
                    break
                cl_signal = pc["cl"][j]
                cl_h1 = pc["cl"][h1]
                is_down = cl_h1 < cl_signal
                atr_h1 = pc["atr"][h1]
                op_h1 = pc["op"][h1]
                stop_h1 = (op_h1 - K * atr_h1
                           if not np.isnan(atr_h1) and atr_h1 > 0
                           else op_h1 * 0.95)
                survived = cl_h1 >= stop_h1
                if is_down and survived:
                    delay = 2
                else:
                    delay = 1

            fill_bar = j + delay
            if delay == 0:
                fill_bar = j

            if fill_bar >= n:
                break
            if pc["op"][fill_bar] <= 0 or np.isnan(pc["op"][fill_bar]):
                j += 1
                continue

            result = _run_trade(pc, fill_bar, tf)
            if result:
                trades.append(result)
                j = fill_bar + result[2] + 1
            else:
                j += 1
    return trades


def aggregate(trades: list) -> dict:
    if not trades:
        return {"trades": 0, "hr": 0.0, "avg_ret": 0.0, "pnl": 0.0,
                "pf": 0.0, "avg_hold": 0.0, "worst": 0.0, "c4_pct": 0.0}
    rw = [t[0] for t in trades]
    ru = [t[1] for t in trades]
    nt = len(trades)
    hr = sum(1 for r in rw if r > 0) / nt * 100
    wi = sum(r for r in rw if r > 0)
    lo = abs(sum(r for r in rw if r <= 0))
    c4_count = sum(1 for t in trades if t[3])
    return {
        "trades": nt,
        "hr": round(hr, 1),
        "avg_ret": round(float(np.mean(ru)), 2),
        "pnl": round(sum(rw)),
        "pf": round(wi / lo if lo > 0 else 999, 2),
        "avg_hold": round(float(np.mean([t[2] for t in trades])), 1),
        "worst": round(min(ru), 1),
        "c4_pct": round(c4_count / nt * 100, 1),
    }


def _print_table(results: list, title: str):
    print(f"\n{'='*110}")
    print(f"  {title}")
    print(f"{'='*110}")
    hdr = f"{'Ver':>4} {'TF':>3} {'H':>3} | {'Trades':>6} {'HR%':>6} {'AvgRet%':>8} {'PnL%':>8} {'PF':>6} {'AvgHold':>8} {'Worst%':>7} {'C4%':>5}"
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        print(f"{r['version']:>4} {r['tf']:>3} {r['H']:>3} | "
              f"{r['trades']:>6} {r['hr']:>6.1f} {r['avg_ret']:>8.2f} {r['pnl']:>8.0f} "
              f"{r['pf']:>6.2f} {r['avg_hold']:>8.1f} {r['worst']:>7.1f} {r['c4_pct']:>5.1f}")


def _save_charts(results: list, delays: list, suffix: str, title_extra: str):
    for tf in ("4H", "1D", "1W"):
        fig, ax1 = plt.subplots(figsize=(10, 5))
        ax2 = ax1.twinx()

        for version, ls, alpha in [("v4", "--", 0.7), ("v5", "-", 1.0)]:
            rows = [r for r in results if r["tf"] == tf and r["version"] == version]
            hs = [r["H"] for r in rows]
            pnls = [r["pnl"] for r in rows]
            hrs = [r["hr"] for r in rows]

            color_pnl = "#4fc3f7" if version == "v5" else "#90a4ae"
            color_hr = "#66bb6a" if version == "v5" else "#a5d6a7"

            ax1.bar([h + (0.3 if version == "v5" else -0.3) for h in hs],
                    pnls, width=0.5, alpha=alpha, color=color_pnl,
                    label=f"{version} PnL%", edgecolor="none")
            ax2.plot(hs, hrs, ls, color=color_hr, linewidth=2, marker="o",
                     markersize=5, label=f"{version} HR%", alpha=alpha)

        ax1.set_xlabel("Entry Delay H (bars)")
        ax1.set_ylabel("Cumulative PnL %", color="#4fc3f7")
        ax2.set_ylabel("Hit Rate %", color="#66bb6a")
        ax1.set_xticks(delays)
        ax1.set_title(f"Entry Delay Sensitivity — {tf}{title_extra}")

        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper right", fontsize=8)

        fig_path = OUTPUTS_DIR / f"entry_delay_{tf}{suffix}.png"
        fig.savefig(fig_path)
        plt.close(fig)
        print(f"Chart saved: {fig_path}")


def main():
    t0 = time.time()
    all_results = []
    filtered_results = []

    for tf in ("4H", "1D", "1W"):
        print(f"\n{'='*70}")
        print(f"  Timeframe: {tf}")
        print(f"{'='*70}")

        print(f"  Loading data...", end=" ", flush=True)
        data = load_data(tf)
        print(f"{len(data)} stocks")

        print(f"  Pre-computing KPIs...", end=" ", flush=True)
        precomp = precompute(data, tf)
        print(f"{len(precomp)} valid")

        for version in ("v4", "v5"):
            for H in DELAYS:
                trades = simulate(precomp, tf, H, version, filter_mode="all")
                stats = aggregate(trades)
                stats.update({"tf": tf, "version": version, "H": H, "filter": "all"})
                all_results.append(stats)

            for H in FILTER_DELAYS:
                trades = simulate(precomp, tf, H, version, filter_mode="h1_down_survived")
                stats = aggregate(trades)
                stats.update({"tf": tf, "version": version, "H": H, "filter": "h1_down"})
                filtered_results.append(stats)

    # Hybrid: H1 down → H=2, otherwise H=1
    hybrid_results = []
    for tf in ("4H", "1D", "1W"):
        data = load_data(tf)
        precomp = precompute(data, tf)
        for version in ("v4", "v5"):
            trades = simulate(precomp, tf, 0, version, filter_mode="hybrid")
            stats = aggregate(trades)
            stats.update({"tf": tf, "version": version, "H": "hyb", "filter": "hybrid"})
            hybrid_results.append(stats)

    _print_table(all_results,
                 "ENTRY DELAY SENSITIVITY — ALL SIGNALS — sample_300 OOS (last 30%)")
    _print_table(filtered_results,
                 "ENTRY DELAY — H1 DOWN BAR + SURVIVED ATR STOP — sample_300 OOS (last 30%)")

    # Comparison table: baseline H=1 vs hybrid vs H1-down-at-H2
    print(f"\n{'='*110}")
    print(f"  STRATEGY COMPARISON — sample_300 OOS (last 30%)")
    print(f"{'='*110}")
    hdr = f"{'Strategy':>28} {'TF':>3} | {'Trades':>6} {'HR%':>6} {'AvgRet%':>8} {'PnL%':>8} {'PF':>6} {'AvgHold':>8} {'Worst%':>7} {'C4%':>5}"
    print(hdr)
    print("-" * len(hdr))

    for tf in ("4H", "1D", "1W"):
        for version in ("v4", "v5"):
            # Baseline H=1
            base = [r for r in all_results if r["tf"] == tf and r["version"] == version and r["H"] == 1][0]
            label = f"{version} baseline (H=1)"
            print(f"{label:>28} {tf:>3} | "
                  f"{base['trades']:>6} {base['hr']:>6.1f} {base['avg_ret']:>8.2f} {base['pnl']:>8.0f} "
                  f"{base['pf']:>6.2f} {base['avg_hold']:>8.1f} {base['worst']:>7.1f} {base['c4_pct']:>5.1f}")

            # Hybrid
            hyb = [r for r in hybrid_results if r["tf"] == tf and r["version"] == version][0]
            label = f"{version} hybrid (down→H2, up→H1)"
            print(f"{label:>28} {tf:>3} | "
                  f"{hyb['trades']:>6} {hyb['hr']:>6.1f} {hyb['avg_ret']:>8.2f} {hyb['pnl']:>8.0f} "
                  f"{hyb['pf']:>6.2f} {hyb['avg_hold']:>8.1f} {hyb['worst']:>7.1f} {hyb['c4_pct']:>5.1f}")

            # Delta
            d_hr = hyb["hr"] - base["hr"]
            d_pnl = hyb["pnl"] - base["pnl"]
            d_pf = hyb["pf"] - base["pf"]
            d_worst = hyb["worst"] - base["worst"]
            print(f"{'Δ hybrid vs baseline':>28} {tf:>3} | "
                  f"{'':>6} {d_hr:>+6.1f} {'':>8} {d_pnl:>+8.0f} "
                  f"{d_pf:>+6.2f} {'':>8} {d_worst:>+7.1f} {'':>5}")
        print("-" * len(hdr))

    combined = all_results + filtered_results + hybrid_results
    csv_path = OUTPUTS_DIR / "entry_delay_sensitivity.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["filter", "version", "tf", "H", "trades", "hr",
                                          "avg_ret", "pnl", "pf", "avg_hold", "worst", "c4_pct"])
        w.writeheader()
        for r in combined:
            w.writerow(r)
    print(f"\nCSV saved: {csv_path}")

    _save_charts(all_results, DELAYS, "", "")
    _save_charts(filtered_results, FILTER_DELAYS, "_h1_down", " (H1 down + survived)")

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")


if __name__ == "__main__":
    main()

"""
Phase 12b — Per-Stock Entry Confirmation Filters

Instead of the universe-wide breadth filter, test per-stock trend
confirmation gates that suppress C3 entries during consolidation.

Each filter adds a condition on top of C3: C3 fires + filter passes → enter.
If C3 fires but filter fails → skip entry.

Filters tested:
  - SMA crossovers (20/50/200)
  - GMMA bullish (KPI state = short group > long group)
  - ADX trending (>20, >25)
  - MACD bullish (MACD > signal)
  - Combinations

All tests on OOS data (last 30%), 0.1% commission, sample_300 universe.
"""

from __future__ import annotations

import json
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
OUTPUTS_DIR = Path(__file__).resolve().parent / "outputs" / "all" / "phase12b"
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


def precompute_filters(df):
    """Compute all confirmation filter signals for a stock. Returns dict of bool arrays."""
    cl = df["Close"].astype(float)
    n = len(df)

    filters = {}

    # SMA crossovers
    sma20 = cl.rolling(20, min_periods=20).mean()
    sma50 = cl.rolling(50, min_periods=50).mean()
    sma200 = cl.rolling(200, min_periods=200).mean()

    filters["Close > SMA20"] = (cl > sma20).to_numpy()
    filters["Close > SMA50"] = (cl > sma50).to_numpy()
    filters["Close > SMA200"] = (cl > sma200).to_numpy()
    filters["SMA20 > SMA50"] = (sma20 > sma50).to_numpy()
    filters["SMA50 > SMA200"] = (sma50 > sma200).to_numpy()
    filters["SMA20 > SMA200"] = (sma20 > sma200).to_numpy()

    # EMA crossovers
    ema12 = cl.ewm(span=12, adjust=False).mean()
    ema26 = cl.ewm(span=26, adjust=False).mean()
    filters["EMA12 > EMA26"] = (ema12 > ema26).to_numpy()

    # GMMA (from enriched columns)
    short_cols = [c for c in df.columns if c.startswith("GMMA_ema_")
                  and c.split("_")[-1].isdigit() and int(c.split("_")[-1]) in {3, 5, 8, 10, 12, 15}]
    long_cols = [c for c in df.columns if c.startswith("GMMA_ema_")
                 and c.split("_")[-1].isdigit() and int(c.split("_")[-1]) in {30, 35, 40, 45, 50, 60}]
    if short_cols and long_cols:
        smin = df[short_cols].astype(float).min(axis=1)
        lmax = df[long_cols].astype(float).max(axis=1)
        filters["GMMA bullish"] = (smin > lmax).to_numpy()
    else:
        filters["GMMA bullish"] = np.ones(n, dtype=bool)

    # ADX
    if "ADX" in df.columns:
        adx = pd.to_numeric(df["ADX"], errors="coerce").fillna(0)
        filters["ADX > 20"] = (adx > 20).to_numpy()
        filters["ADX > 25"] = (adx > 25).to_numpy()
    else:
        filters["ADX > 20"] = np.ones(n, dtype=bool)
        filters["ADX > 25"] = np.ones(n, dtype=bool)

    # MACD
    if "MACD" in df.columns and "MACD_signal" in df.columns:
        macd = pd.to_numeric(df["MACD"], errors="coerce").fillna(0)
        macd_sig = pd.to_numeric(df["MACD_signal"], errors="coerce").fillna(0)
        filters["MACD > Signal"] = (macd > macd_sig).to_numpy()
    else:
        filters["MACD > Signal"] = np.ones(n, dtype=bool)

    # Combos
    filters["SMA50>200 + GMMA"] = filters["SMA50 > SMA200"] & filters["GMMA bullish"]
    filters["Close>SMA50 + ADX>20"] = filters["Close > SMA50"] & filters["ADX > 20"]
    filters["GMMA + ADX>20"] = filters["GMMA bullish"] & filters["ADX > 20"]
    filters["SMA20>50 + MACD"] = filters["SMA20 > SMA50"] & filters["MACD > Signal"]

    return filters


def sim_with_filter(df, sm, c3_kpis, c4_kpis, T, M, K, si, ei_limit,
                    entry_filter=None, c4_weight=1.5):
    """Run unified position sim with an optional entry confirmation filter."""
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

        # Apply entry confirmation filter
        if entry_filter is not None and not entry_filter[j]:
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

        xi = None
        j_inner = ei + 1
        while j_inner < min(ei + MAX_HOLD + 1, n):
            bars_since_reset += 1
            c = float(cl[j_inner])

            if c < stop:
                xi = j_inner; break

            if not scaled and c4_avail and c4_bull.iloc[j_inner]:
                scaled = True
                active_kpis = c4_kpis
                nk = len(active_kpis)

            nb = sum(1 for kk in active_kpis if kk in sm and j_inner < len(sm[kk]) and int(sm[kk].iloc[j_inner]) != STATE_BULL)
            total_bars = j_inner - ei
            if total_bars <= T:
                if nb >= nk:
                    xi = j_inner; break
            else:
                if nb >= 2:
                    xi = j_inner; break

            if bars_since_reset >= M:
                if nb == 0:
                    stop_price = c
                    stop = stop_price - K * at[j_inner] if j_inner < len(at) and at[j_inner] > 0 else stop
                    bars_since_reset = 0
                else:
                    xi = j_inner; break
            j_inner += 1

        if xi is None:
            xi = min(j_inner, n - 1)
        h = xi - ei
        if h > 0:
            gross = (float(cl[xi]) - ep) / ep * 100
            net = gross - COMMISSION_RT * 100
            weight = c4_weight if scaled else 1.0
            trades.append({
                "net": round(net, 4),
                "weighted_net": round(net * weight, 4),
                "hold": h,
                "scaled": scaled,
            })
        j = xi + 1

    return trades


def aggregate(trades):
    if not trades:
        return None
    nets = [t["net"] for t in trades]
    weighted = [t["weighted_net"] for t in trades]
    n = len(nets)
    hr = sum(1 for r in nets if r > 0) / n * 100
    wi = sum(r for r in weighted if r > 0)
    lo = abs(sum(r for r in weighted if r <= 0))
    n_scaled = sum(1 for t in trades if t["scaled"])
    avg_hold = float(np.mean([t["hold"] for t in trades]))
    return {
        "n": n, "n_scaled": n_scaled,
        "hr": round(hr, 1),
        "avg": round(float(np.mean(nets)), 2),
        "pnl_w": round(sum(weighted)),
        "pf": round(wi / lo if lo > 0 else 999.0, 2),
        "worst": round(min(nets), 1),
        "avg_hold": round(avg_hold, 1),
    }


def main():
    t0 = time.time()

    filter_names = [
        "No filter (baseline)",
        "Close > SMA20",
        "Close > SMA50",
        "Close > SMA200",
        "SMA20 > SMA50",
        "SMA50 > SMA200",
        "SMA20 > SMA200",
        "EMA12 > EMA26",
        "GMMA bullish",
        "ADX > 20",
        "ADX > 25",
        "MACD > Signal",
        "SMA50>200 + GMMA",
        "Close>SMA50 + ADX>20",
        "GMMA + ADX>20",
        "SMA20>50 + MACD",
    ]

    all_results = {}

    for tf in ["4H", "1D", "1W"]:
        print(f"\n{'='*70}")
        print(f"  {tf}")
        print(f"{'='*70}")

        data = load_data(tf)
        print(f"  Loaded {len(data)} stocks")

        T = GLOBAL_EXIT[tf]["T"]
        M = GLOBAL_EXIT[tf]["M"]
        K = GLOBAL_EXIT[tf]["K"]
        c3 = GLOBAL_COMBOS[tf]["C3"]
        c4 = GLOBAL_COMBOS[tf]["C4"]

        # Precompute KPI states and filters for all stocks
        stock_data = {}
        for sym, df in data.items():
            sm = compute_kpi_state_map(df)
            if any(k not in sm for k in c3):
                continue
            filt = precompute_filters(df)
            stock_data[sym] = (df, sm, filt)
        print(f"  {len(stock_data)} stocks with valid KPIs")

        tf_results = {}
        print(f"\n  {'Filter':<30s} | {'n':>5s} {'HR':>6s} {'Avg':>7s} {'PnL(w)':>9s} {'PF':>6s} {'Worst':>7s} {'Hold':>5s} | {'vs base':>8s}")
        print(f"  {'-'*30}-+-{'-'*5}-{'-'*6}-{'-'*7}-{'-'*9}-{'-'*6}-{'-'*7}-{'-'*5}-+-{'-'*8}")

        base_pnl = None

        for fname in filter_names:
            all_trades = []
            for sym, (df, sm, filt) in stock_data.items():
                n = len(df)
                si = int(n * IS_FRACTION)
                ef = None if fname == "No filter (baseline)" else filt.get(fname.replace("No filter (baseline)", ""))
                trades = sim_with_filter(df, sm, c3, c4, T, M, K, si, n,
                                         entry_filter=ef, c4_weight=1.5)
                all_trades.extend(trades)

            agg = aggregate(all_trades)
            tf_results[fname] = agg

            if agg:
                if base_pnl is None:
                    base_pnl = agg["pnl_w"]
                delta = ((agg["pnl_w"] - base_pnl) / abs(base_pnl) * 100) if base_pnl else 0
                delta_str = f"{delta:>+7.1f}%" if fname != "No filter (baseline)" else "   base"
                print(f"  {fname:<30s} | {agg['n']:>5d} {agg['hr']:>5.1f}% {agg['avg']:>+6.2f}% {agg['pnl_w']:>+8,d}% {agg['pf']:>5.2f} {agg['worst']:>+6.1f}% {agg['avg_hold']:>5.1f} | {delta_str}")

        all_results[tf] = tf_results

    # Save results
    with open(OUTPUTS_DIR / "phase12b_summary.json", "w") as f:
        json.dump(all_results, f, indent=2)

    # Visualization
    plot_results(all_results, filter_names)

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s. Outputs in {OUTPUTS_DIR}")


def plot_results(all_results, filter_names):
    short_labels = {
        "No filter (baseline)": "Baseline",
        "Close > SMA20": "C>SMA20",
        "Close > SMA50": "C>SMA50",
        "Close > SMA200": "C>SMA200",
        "SMA20 > SMA50": "SMA20>50",
        "SMA50 > SMA200": "SMA50>200",
        "SMA20 > SMA200": "SMA20>200",
        "EMA12 > EMA26": "EMA12>26",
        "GMMA bullish": "GMMA",
        "ADX > 20": "ADX>20",
        "ADX > 25": "ADX>25",
        "MACD > Signal": "MACD",
        "SMA50>200 + GMMA": "SMA50>200\n+GMMA",
        "Close>SMA50 + ADX>20": "C>SMA50\n+ADX>20",
        "GMMA + ADX>20": "GMMA\n+ADX>20",
        "SMA20>50 + MACD": "SMA20>50\n+MACD",
    }

    for tf in ["4H", "1D", "1W"]:
        if tf not in all_results:
            continue
        tf_data = all_results[tf]
        base = tf_data.get("No filter (baseline)")
        if not base:
            continue

        fig, axes = plt.subplots(2, 2, figsize=(20, 12))

        labels = [short_labels.get(f, f) for f in filter_names]
        pnls = [tf_data.get(f, {}).get("pnl_w", 0) if tf_data.get(f) else 0 for f in filter_names]
        hrs = [tf_data.get(f, {}).get("hr", 0) if tf_data.get(f) else 0 for f in filter_names]
        pfs = [tf_data.get(f, {}).get("pf", 0) if tf_data.get(f) else 0 for f in filter_names]
        ns = [tf_data.get(f, {}).get("n", 0) if tf_data.get(f) else 0 for f in filter_names]

        x = np.arange(len(filter_names))

        # P&L
        ax = axes[0, 0]
        colors = ["#666666"] + ["#4caf50" if p > base["pnl_w"] else "#ef5350" if p < base["pnl_w"] * 0.9 else "#2196f3" for p in pnls[1:]]
        ax.bar(x, pnls, color=colors, edgecolor="white", linewidth=0.5)
        ax.axhline(base["pnl_w"], color="#666666", linestyle="--", alpha=0.5)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=7, rotation=45, ha="right")
        ax.set_ylabel("P&L (%)")
        ax.set_title(f"Weighted P&L")

        # Hit Rate
        ax = axes[0, 1]
        colors = ["#666666"] + ["#4caf50" if h > base["hr"] else "#ef5350" if h < base["hr"] - 2 else "#2196f3" for h in hrs[1:]]
        ax.bar(x, hrs, color=colors, edgecolor="white", linewidth=0.5)
        ax.axhline(base["hr"], color="#666666", linestyle="--", alpha=0.5)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=7, rotation=45, ha="right")
        ax.set_ylabel("Hit Rate (%)")
        ax.set_title(f"Hit Rate")

        # Profit Factor
        ax = axes[1, 0]
        colors = ["#666666"] + ["#4caf50" if p > base["pf"] else "#ef5350" if p < base["pf"] * 0.9 else "#2196f3" for p in pfs[1:]]
        ax.bar(x, pfs, color=colors, edgecolor="white", linewidth=0.5)
        ax.axhline(base["pf"], color="#666666", linestyle="--", alpha=0.5)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=7, rotation=45, ha="right")
        ax.set_ylabel("Profit Factor")
        ax.set_title(f"Profit Factor")

        # Trade Count
        ax = axes[1, 1]
        ax.bar(x, ns, color="#78909c", edgecolor="white", linewidth=0.5)
        ax.axhline(base["n"], color="#666666", linestyle="--", alpha=0.5)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=7, rotation=45, ha="right")
        ax.set_ylabel("Trades")
        ax.set_title(f"Trade Count")

        fig.suptitle(f"{tf} — Entry Confirmation Filters vs Baseline", fontsize=15, fontweight="bold")
        plt.tight_layout()
        fig.savefig(OUTPUTS_DIR / f"confirmation_filters_{tf}.png")
        plt.close(fig)

    # Summary heatmap: delta vs baseline for each filter × TF
    fig, ax = plt.subplots(figsize=(14, 8))
    tfs = ["4H", "1D", "1W"]
    fnames = filter_names[1:]  # skip baseline
    matrix_pnl = np.zeros((len(fnames), len(tfs)))
    matrix_pf = np.zeros((len(fnames), len(tfs)))

    for j, tf in enumerate(tfs):
        base = all_results.get(tf, {}).get("No filter (baseline)")
        if not base:
            continue
        for i, f in enumerate(fnames):
            agg = all_results.get(tf, {}).get(f)
            if agg:
                matrix_pnl[i, j] = (agg["pnl_w"] - base["pnl_w"]) / abs(base["pnl_w"]) * 100
                matrix_pf[i, j] = agg["pf"] - base["pf"]

    im = ax.imshow(matrix_pf, cmap="RdYlGn", aspect="auto", vmin=-3, vmax=3)
    ax.set_xticks(range(len(tfs)))
    ax.set_xticklabels(tfs, fontsize=12)
    ax.set_yticks(range(len(fnames)))
    ax.set_yticklabels(fnames, fontsize=10)
    ax.set_title("Profit Factor Delta vs Baseline (green = better)", fontsize=14, fontweight="bold")

    for i in range(len(fnames)):
        for j in range(len(tfs)):
            pnl_d = matrix_pnl[i, j]
            pf_d = matrix_pf[i, j]
            ax.text(j, i, f"PF {pf_d:+.1f}\nP&L {pnl_d:+.0f}%",
                    ha="center", va="center", fontsize=8,
                    color="black" if abs(pf_d) < 2 else "white")

    plt.colorbar(im, ax=ax, label="PF Delta")
    plt.tight_layout()
    fig.savefig(OUTPUTS_DIR / "confirmation_heatmap.png")
    plt.close(fig)


if __name__ == "__main__":
    main()

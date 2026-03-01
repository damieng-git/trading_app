"""
Phase 12c — Volatility-Aware Position Sizing

Test two approaches to limit damage from high-volatility entries:

1. ATR% Threshold: reject entries when ATR/Close > X% (absolute gate)
2. ATR%-Scaled Sizing: weight = base_weight × (target_atr_pct / actual_atr_pct),
   capped at base_weight. Stocks with higher ATR% get smaller positions.

Both compared against the baseline (Exit Flow v4 + Close > SMA200 on 1D/1W).
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
OUTPUTS_DIR = Path(__file__).resolve().parent / "outputs" / "all" / "phase12c"
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

# --- Thresholds to test ---
ATR_PCT_THRESHOLDS = [3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
ATR_PCT_TARGETS = [1.5, 2.0, 2.5, 3.0]
RELATIVE_ATR_CAPS = [0.60, 0.70, 0.80, 0.90, 1.00, 1.10, 1.20]
RELATIVE_ATR_LOOKBACKS = [50, 100, 200]


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


def sim_volatility(df, sm, c3_kpis, c4_kpis, T, M, K, si, ei_limit, tf,
                   mode="baseline", atr_pct_cap=None, atr_pct_target=None,
                   relative_atr_cap=None, relative_atr_lookback=100):
    """Run unified position sim with volatility-aware sizing.

    mode:
      - "baseline": standard sizing (1x C3, 1.5x C4) with Close > SMA200 on 1D/1W
      - "threshold": reject entries where ATR% > atr_pct_cap
      - "scaled":    weight = base_weight × min(1, atr_pct_target / atr_pct_at_entry)
      - "relative":  reject entries where ATR > relative_atr_cap × rolling_mean_ATR(lookback)
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

    # SMA200 filter (1D/1W only)
    sma200_ok = None
    if tf in ("1D", "1W") and n >= 200:
        sma200 = pd.Series(cl).rolling(200, min_periods=200).mean().to_numpy()
        sma200_ok = cl >= sma200

    # Rolling mean ATR for relative mode
    rolling_mean_atr = None
    if mode == "relative" and relative_atr_lookback is not None:
        rolling_mean_atr = pd.Series(at).rolling(
            relative_atr_lookback, min_periods=relative_atr_lookback
        ).mean().to_numpy()

    trades = []
    j = si
    while j < ei_limit:
        if not c3_bull.iloc[j]:
            j += 1; continue
        if j > 0 and c3_bull.iloc[j - 1]:
            j += 1; continue

        # SMA200 filter
        if sma200_ok is not None and not sma200_ok[j]:
            j += 1; continue

        ep = float(cl[j])
        if ep <= 0:
            j += 1; continue

        atr_pct = at[j] / ep * 100 if ep > 0 and at[j] > 0 else 0.0

        # Mode-specific entry gate
        if mode == "threshold" and atr_pct_cap is not None:
            if atr_pct > atr_pct_cap:
                j += 1; continue

        if mode == "relative" and relative_atr_cap is not None and rolling_mean_atr is not None:
            if np.isnan(rolling_mean_atr[j]) or rolling_mean_atr[j] <= 0:
                j += 1; continue
            if at[j] > relative_atr_cap * rolling_mean_atr[j]:
                j += 1; continue

        ei = j
        stop_price = ep
        stop = stop_price - K * at[ei] if at[ei] > 0 else -np.inf
        bars_since_reset = 0
        scaled = c4_avail and c4_bull.iloc[j]
        active_kpis = c4_kpis if scaled else c3_kpis
        nk = len(active_kpis)

        # Position weight
        base_weight = 1.5 if scaled else 1.0
        if mode == "scaled" and atr_pct_target is not None and atr_pct > 0:
            vol_scalar = min(1.0, atr_pct_target / atr_pct)
            weight = base_weight * vol_scalar
        else:
            weight = base_weight

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
                if mode == "scaled" and atr_pct_target is not None and atr_pct > 0:
                    weight = 1.5 * min(1.0, atr_pct_target / atr_pct)
                else:
                    weight = 1.5
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
            trades.append({
                "net": round(net, 4),
                "weighted_net": round(net * weight, 4),
                "hold": h,
                "scaled": scaled,
                "weight": round(weight, 3),
                "atr_pct": round(atr_pct, 2),
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
    avg_weight = float(np.mean([t["weight"] for t in trades]))
    return {
        "n": n,
        "hr": round(hr, 1),
        "avg": round(float(np.mean(nets)), 2),
        "pnl_w": round(sum(weighted)),
        "pf": round(wi / lo if lo > 0 else 999.0, 2),
        "worst": round(min(weighted), 1),
        "worst_raw": round(min(nets), 1),
        "avg_hold": round(float(np.mean([t["hold"] for t in trades])), 1),
        "avg_weight": round(avg_weight, 3),
    }


def atr_pct_distribution(all_trades):
    """Get distribution stats for ATR% at entry across all trades."""
    atr_pcts = [t["atr_pct"] for t in all_trades if "atr_pct" in t]
    if not atr_pcts:
        return {}
    arr = np.array(atr_pcts)
    return {
        "p10": round(float(np.percentile(arr, 10)), 2),
        "p25": round(float(np.percentile(arr, 25)), 2),
        "p50": round(float(np.percentile(arr, 50)), 2),
        "p75": round(float(np.percentile(arr, 75)), 2),
        "p90": round(float(np.percentile(arr, 90)), 2),
        "p95": round(float(np.percentile(arr, 95)), 2),
        "mean": round(float(np.mean(arr)), 2),
        "max": round(float(np.max(arr)), 2),
    }


def main():
    t0 = time.time()
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
        c3_kpis = GLOBAL_COMBOS[tf]["C3"]
        c4_kpis = GLOBAL_COMBOS[tf]["C4"]

        results_tf = {}

        # --- Baseline ---
        all_trades_base = []
        for sym, df in data.items():
            sm = compute_kpi_state_map(df)
            if any(sm.get(k) is None for k in c3_kpis):
                continue
            si = int(len(df) * IS_FRACTION)
            trades = sim_volatility(df, sm, c3_kpis, c4_kpis, T, M, K, si, len(df), tf,
                                    mode="baseline")
            all_trades_base.extend(trades)

        agg_base = aggregate(all_trades_base)
        dist = atr_pct_distribution(all_trades_base)
        results_tf["baseline"] = agg_base
        print(f"\n  Baseline: {agg_base}")
        print(f"  ATR% distribution at entry: {dist}")

        # --- Approach 1: ATR% Threshold ---
        for cap in ATR_PCT_THRESHOLDS:
            label = f"ATR% <= {cap}"
            all_trades = []
            for sym, df in data.items():
                sm = compute_kpi_state_map(df)
                if any(sm.get(k) is None for k in c3_kpis):
                    continue
                si = int(len(df) * IS_FRACTION)
                trades = sim_volatility(df, sm, c3_kpis, c4_kpis, T, M, K, si, len(df), tf,
                                        mode="threshold", atr_pct_cap=cap)
                all_trades.extend(trades)
            agg = aggregate(all_trades)
            results_tf[label] = agg
            if agg and agg_base:
                delta_pnl = agg["pnl_w"] - agg_base["pnl_w"]
                delta_n = agg["n"] - agg_base["n"]
                print(f"  {label:20s}: n={agg['n']:5d} ({delta_n:+d}) | PnL={agg['pnl_w']:>7d} ({delta_pnl:+d}) | PF={agg['pf']:.2f} | HR={agg['hr']:.1f}% | worst={agg['worst']:.1f}% | avg_w={agg['avg_weight']:.3f}")

        # --- Approach 2: ATR%-Scaled Sizing ---
        for target in ATR_PCT_TARGETS:
            label = f"Scaled target={target}%"
            all_trades = []
            for sym, df in data.items():
                sm = compute_kpi_state_map(df)
                if any(sm.get(k) is None for k in c3_kpis):
                    continue
                si = int(len(df) * IS_FRACTION)
                trades = sim_volatility(df, sm, c3_kpis, c4_kpis, T, M, K, si, len(df), tf,
                                        mode="scaled", atr_pct_target=target)
                all_trades.extend(trades)
            agg = aggregate(all_trades)
            results_tf[label] = agg
            if agg and agg_base:
                delta_pnl = agg["pnl_w"] - agg_base["pnl_w"]
                print(f"  {label:20s}: n={agg['n']:5d}       | PnL={agg['pnl_w']:>7d} ({delta_pnl:+d}) | PF={agg['pf']:.2f} | HR={agg['hr']:.1f}% | worst={agg['worst']:.1f}% | avg_w={agg['avg_weight']:.3f}")

        # --- Approach 3: Relative ATR (ATR < cap × rolling_mean_ATR(lookback)) ---
        print(f"\n  --- Relative ATR filter ---")
        for lb in RELATIVE_ATR_LOOKBACKS:
            for cap in RELATIVE_ATR_CAPS:
                label = f"ATR<{int(cap*100)}% mean({lb})"
                all_trades = []
                for sym, df in data.items():
                    sm = compute_kpi_state_map(df)
                    if any(sm.get(k) is None for k in c3_kpis):
                        continue
                    si = int(len(df) * IS_FRACTION)
                    trades = sim_volatility(df, sm, c3_kpis, c4_kpis, T, M, K, si, len(df), tf,
                                            mode="relative", relative_atr_cap=cap,
                                            relative_atr_lookback=lb)
                    all_trades.extend(trades)
                agg = aggregate(all_trades)
                results_tf[label] = agg
                if agg and agg_base:
                    delta_pnl = agg["pnl_w"] - agg_base["pnl_w"]
                    delta_n = agg["n"] - agg_base["n"]
                    print(f"  {label:24s}: n={agg['n']:5d} ({delta_n:+d}) | PnL={agg['pnl_w']:>7d} ({delta_pnl:+d}) | PF={agg['pf']:.2f} | HR={agg['hr']:.1f}% | worst={agg['worst']:.1f}%")

        all_results[tf] = results_tf

    elapsed = time.time() - t0
    print(f"\nTotal time: {elapsed:.1f}s")

    # Save results
    summary_path = OUTPUTS_DIR / "phase12c_summary.json"
    summary_path.write_text(json.dumps(all_results, indent=2), encoding="utf-8")
    print(f"Saved: {summary_path}")

    plot_results(all_results)
    return all_results


def plot_results(all_results):
    for tf, results in all_results.items():
        if not results or "baseline" not in results or results["baseline"] is None:
            continue

        base = results["baseline"]
        labels = []
        pnl_vals = []
        pf_vals = []
        hr_vals = []
        worst_vals = []
        n_vals = []
        colors = []

        for label, agg in results.items():
            if agg is None:
                continue
            labels.append(label)
            pnl_vals.append(agg["pnl_w"])
            pf_vals.append(agg["pf"])
            hr_vals.append(agg["hr"])
            worst_vals.append(agg["worst"])
            n_vals.append(agg["n"])
            if label == "baseline":
                colors.append("#64b5f6")
            elif label.startswith("ATR%"):
                colors.append("#ef5350")
            else:
                colors.append("#66bb6a")

        fig, axes = plt.subplots(2, 2, figsize=(16, 10))
        fig.suptitle(f"Phase 12c — Volatility Sizing ({tf})", fontsize=15, fontweight="bold")

        y_pos = np.arange(len(labels))

        # P&L
        ax = axes[0, 0]
        ax.barh(y_pos, pnl_vals, color=colors, edgecolor="white", linewidth=0.3)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels, fontsize=8)
        ax.set_xlabel("Total Weighted P&L (%)")
        ax.set_title("Total P&L")
        ax.axvline(base["pnl_w"], color="#64b5f6", linestyle="--", alpha=0.5)
        for i, v in enumerate(pnl_vals):
            ax.text(v + max(pnl_vals) * 0.01, i, f"{v:,}", va="center", fontsize=7)

        # PF
        ax = axes[0, 1]
        ax.barh(y_pos, pf_vals, color=colors, edgecolor="white", linewidth=0.3)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels, fontsize=8)
        ax.set_xlabel("Profit Factor")
        ax.set_title("Profit Factor")
        ax.axvline(base["pf"], color="#64b5f6", linestyle="--", alpha=0.5)
        for i, v in enumerate(pf_vals):
            ax.text(v + 0.05, i, f"{v:.2f}", va="center", fontsize=7)

        # Worst trade
        ax = axes[1, 0]
        ax.barh(y_pos, worst_vals, color=colors, edgecolor="white", linewidth=0.3)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels, fontsize=8)
        ax.set_xlabel("Worst Trade (weighted %)")
        ax.set_title("Worst Single Trade")
        for i, v in enumerate(worst_vals):
            ax.text(v - 0.5, i, f"{v:.1f}%", va="center", fontsize=7, ha="right")

        # Trade count
        ax = axes[1, 1]
        ax.barh(y_pos, n_vals, color=colors, edgecolor="white", linewidth=0.3)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels, fontsize=8)
        ax.set_xlabel("Trade Count")
        ax.set_title("Number of Trades")
        for i, v in enumerate(n_vals):
            ax.text(v + max(n_vals) * 0.01, i, str(v), va="center", fontsize=7)

        plt.tight_layout()
        out = OUTPUTS_DIR / f"volatility_sizing_{tf}.png"
        plt.savefig(out)
        plt.close()
        print(f"  Saved: {out}")

    # Heatmap: delta PnL and delta PF vs baseline for each TF
    fig, axes = plt.subplots(1, 2, figsize=(16, 8))
    fig.suptitle("Phase 12c — Impact vs Baseline (all TFs)", fontsize=15, fontweight="bold")

    tfs = [tf for tf in ["4H", "1D", "1W"] if tf in all_results]
    variant_labels = []
    for tf in tfs:
        for label in all_results[tf]:
            if label != "baseline" and label not in variant_labels:
                variant_labels.append(label)

    for ax_idx, (metric, title, fmt) in enumerate([
        ("pnl_w", "P&L Delta vs Baseline (%)", "+,"),
        ("pf", "PF Delta vs Baseline", "+.2f"),
    ]):
        ax = axes[ax_idx]
        matrix = np.full((len(variant_labels), len(tfs)), np.nan)
        for ti, tf in enumerate(tfs):
            base = all_results[tf].get("baseline")
            if not base:
                continue
            for vi, vl in enumerate(variant_labels):
                agg = all_results[tf].get(vl)
                if agg:
                    matrix[vi, ti] = agg[metric] - base[metric]

        cmap = "RdYlGn"
        vmax = max(abs(np.nanmin(matrix)), abs(np.nanmax(matrix))) if not np.all(np.isnan(matrix)) else 1
        im = ax.imshow(matrix, cmap=cmap, aspect="auto", vmin=-vmax, vmax=vmax)
        ax.set_xticks(range(len(tfs)))
        ax.set_xticklabels(tfs)
        ax.set_yticks(range(len(variant_labels)))
        ax.set_yticklabels(variant_labels, fontsize=8)
        ax.set_title(title)
        for vi in range(len(variant_labels)):
            for ti in range(len(tfs)):
                v = matrix[vi, ti]
                if not np.isnan(v):
                    ax.text(ti, vi, f"{v:{fmt}}", ha="center", va="center", fontsize=7,
                            color="black" if abs(v) < vmax * 0.5 else "white")
        plt.colorbar(im, ax=ax, shrink=0.8)

    plt.tight_layout()
    out = OUTPUTS_DIR / "volatility_heatmap.png"
    plt.savefig(out)
    plt.close()
    print(f"  Saved: {out}")


if __name__ == "__main__":
    main()

"""
Phase 16 — PF/Return-Optimized Strategy Search

Re-runs the combo + entry gate optimization with Profit Factor as the
primary objective instead of cumulative PnL.  Targets a trader who takes
~10 trades/day and cares about per-trade quality over volume.

Steps:
  16a  Exhaustive combo search (C3–C6) ranked by PF
  16b  Entry gate sweep on top PF combos
  16c  Entry delay sensitivity (H=0..10) on new combos

Dataset: sample_300 (~295 stocks), OOS last 30%.
Commission: 0.1% + 0.5% slippage.
Exit Flow v4 (T/M/K per TF).
"""

from __future__ import annotations

import csv
import json
import sys
import time
from itertools import combinations
from math import comb as _comb
from pathlib import Path

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
OUTPUTS_DIR = Path(__file__).resolve().parent / "outputs" / "all" / "phase16"
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

OOS_FRACTION = 0.70
ATR_PERIOD = 14
MAX_HOLD = 500
COMMISSION = 0.001
SLIPPAGE = 0.005
COST_PCT = (COMMISSION + SLIPPAGE) * 100

EXIT_PARAMS = {
    "4H": {"T": 4, "M": 48, "K": 4.0},
    "1D": {"T": 4, "M": 40, "K": 4.0},
    "1W": {"T": 2, "M": 20, "K": 4.0},
}

LOCKED_COMBOS = {
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

EXCLUDED_KPIS = {"Nadaraya-Watson Envelop (Repainting)"}

ALL_KPIS: list[str] = []
_seen: set = set()
for _k in list(KPI_TREND_ORDER) + list(KPI_BREAKOUT_ORDER) + [
    "GK Trend Ribbon", "Impulse Trend", "Volume + MA20",
]:
    if _k not in _seen and _k not in EXCLUDED_KPIS:
        ALL_KPIS.append(_k)
        _seen.add(_k)

TOP_KPIS = [
    "Nadaraya-Watson Smoother", "cRSI", "Madrid Ribbon", "GK Trend Ribbon",
    "DEMA", "Donchian Ribbon", "OBVOSC_LB", "Volume + MA20",
    "Stoch_MTM", "CM_P-SAR", "Mansfield RS", "Ichimoku",
    "WT_LB", "CM_Ult_MacD_MFT", "SuperTrend",
]

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


def _sl(kpis: list) -> str:
    return "+".join(_s(k) for k in kpis)


# ── Data loading ─────────────────────────────────────────────────────────

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


def precompute_bulls(data: dict, tf: str) -> dict:
    """Pre-compute per-KPI bull arrays + gates for fast combo iteration."""
    all_pc = {}
    for sym, df in data.items():
        sm = compute_kpi_state_map(df)
        bulls = {}
        for k in ALL_KPIS:
            if k in sm:
                bulls[k] = (sm[k] == STATE_BULL).to_numpy(bool)
        if not bulls:
            continue

        n = len(df)
        cl = df["Close"].to_numpy(float)
        op = df["Open"].to_numpy(float) if "Open" in df.columns else cl.copy()
        at = compute_atr(df, ATR_PERIOD)
        vol = df["Volume"].to_numpy(float) if "Volume" in df.columns else np.zeros(n)

        cl_s = pd.Series(cl)
        sma20 = cl_s.rolling(20, min_periods=20).mean().to_numpy(float)
        sma50 = cl_s.rolling(50, min_periods=50).mean().to_numpy(float)
        sma100 = cl_s.rolling(100, min_periods=100).mean().to_numpy(float)
        sma200 = cl_s.rolling(200, min_periods=200).mean().to_numpy(float)

        # Overextension (1W)
        overext_ok = np.ones(n, dtype=bool)
        if tf == "1W" and n > 5:
            ref = np.empty(n, dtype=float)
            ref[:5] = np.nan
            ref[5:] = cl[:-5]
            with np.errstate(divide="ignore", invalid="ignore"):
                pct_chg = (cl - ref) / ref * 100
            overext_ok = ~(pct_chg > 15.0)

        # Volume spike
        vol_spike_ok = np.ones(n, dtype=bool)
        if vol.sum() > 0:
            vol_ma20 = pd.Series(vol).rolling(20, min_periods=1).mean().to_numpy(float)
            with np.errstate(invalid="ignore"):
                spike_raw = (vol >= 1.5 * vol_ma20).astype(float)
            spike_raw = np.nan_to_num(spike_raw, nan=0.0)
            vol_spike_ok = pd.Series(spike_raw).rolling(5, min_periods=1).max().to_numpy().astype(bool)

        kpi_nbull = {}
        for k in ALL_KPIS:
            if k in sm:
                kpi_nbull[k] = (sm[k] != STATE_BULL).to_numpy(bool)

        all_pc[sym] = {
            "bulls": bulls, "kpi_nbull": kpi_nbull,
            "cl": cl, "op": op, "atr": at, "n": n,
            "sma20": sma20, "sma50": sma50, "sma100": sma100, "sma200": sma200,
            "overext_ok": overext_ok, "vol_spike_ok": vol_spike_ok,
        }
    return all_pc


# ── Simulation engine ────────────────────────────────────────────────────

def sim_combo(all_pc: dict, combo_kpis: list, c4_kpis: list,
              tf: str, *, gate: str = "none", delay: int = 1,
              min_trades: int = 20) -> dict | None:
    """Simulate a combo across all stocks. Returns stats dict or None."""
    T = EXIT_PARAMS[tf]["T"]
    M = EXIT_PARAMS[tf]["M"]
    K = EXIT_PARAMS[tf]["K"]
    trades = []

    for sym, pc in all_pc.items():
        bulls = pc["bulls"]
        if any(k not in bulls for k in combo_kpis):
            continue
        cl = pc["cl"]; op = pc["op"]; at = pc["atr"]; n = pc["n"]
        kpi_nbull = pc["kpi_nbull"]

        c3_bull = bulls[combo_kpis[0]].copy()
        for k in combo_kpis[1:]:
            c3_bull &= bulls[k]

        c4_avail = all(k in bulls for k in c4_kpis)
        c4_bull = np.zeros(n, dtype=bool)
        if c4_avail:
            c4_bull = np.ones(n, dtype=bool)
            for k in c4_kpis:
                c4_bull &= bulls[k]

        si = int(n * OOS_FRACTION)
        j = si
        while j < n:
            if not c3_bull[j]:
                j += 1; continue
            if j > 0 and c3_bull[j - 1]:
                j += 1; continue

            # Entry gates
            if gate in ("sma200", "sma20_200", "sma_stack", "v5", "v5_sr"):
                if tf in ("1D", "1W"):
                    if gate == "sma200":
                        if np.isnan(pc["sma200"][j]) or cl[j] < pc["sma200"][j]:
                            j += 1; continue
                    elif gate == "sma20_200":
                        if np.isnan(pc["sma20"][j]) or np.isnan(pc["sma200"][j]) or pc["sma20"][j] < pc["sma200"][j]:
                            j += 1; continue
                    elif gate == "sma_stack":
                        if (np.isnan(pc["sma20"][j]) or np.isnan(pc["sma50"][j]) or
                            np.isnan(pc["sma100"][j]) or np.isnan(pc["sma200"][j]) or
                            not (pc["sma20"][j] > pc["sma50"][j] > pc["sma100"][j] > pc["sma200"][j])):
                            j += 1; continue
                    else:  # v5, v5_sr
                        if np.isnan(pc["sma20"][j]) or np.isnan(pc["sma200"][j]) or pc["sma20"][j] < pc["sma200"][j]:
                            j += 1; continue

            if gate in ("v5", "v5_sr"):
                if not pc["overext_ok"][j]:
                    j += 1; continue
                if not pc["vol_spike_ok"][j]:
                    j += 1; continue

            fill_bar = j + delay
            if fill_bar >= n:
                break
            ep = float(op[fill_bar]) if delay >= 1 else float(cl[j])
            if ep <= 0 or np.isnan(ep):
                j += 1; continue

            atr_val = at[fill_bar]
            stop = ep - K * atr_val if not np.isnan(atr_val) and atr_val > 0 else ep * 0.95
            bars_since_reset = 0
            scaled = c4_avail and c4_bull[fill_bar]
            active = c4_kpis if scaled else combo_kpis
            nk = len(active)
            xi = None

            jj = fill_bar + 1
            while jj < min(fill_bar + MAX_HOLD + 1, n):
                bars_since_reset += 1
                c = float(cl[jj])
                if np.isnan(c):
                    jj += 1; continue

                if c < stop:
                    xi = jj; break

                if not scaled and c4_avail and c4_bull[jj]:
                    scaled = True
                    active = c4_kpis; nk = len(active)

                nb = sum(1 for kk in active if kk in kpi_nbull and jj < len(kpi_nbull[kk]) and kpi_nbull[kk][jj])
                bars_held = jj - fill_bar

                if bars_held <= T:
                    if nb >= nk:
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
                xi = min(jj, n - 1)
            is_open = (xi == n - 1 and jj >= n)
            exit_fill = min(xi + 1, n - 1) if not is_open and xi < n - 1 else xi
            xp = float(op[exit_fill]) if exit_fill != xi else float(cl[xi])
            h = xi - fill_bar
            if h <= 0 or is_open:
                j += 1; continue
            weight = 1.5 if scaled else 1.0
            ret = (xp - ep) / ep * 100 - COST_PCT
            trades.append((ret * weight, ret, h, scaled))

            j = xi + 1

    if len(trades) < min_trades:
        return None

    rw = [t[0] for t in trades]
    ru = [t[1] for t in trades]
    nt = len(trades)
    hr = sum(1 for r in rw if r > 0) / nt * 100
    wi = sum(r for r in rw if r > 0)
    lo = abs(sum(r for r in rw if r <= 0))
    c4_count = sum(1 for t in trades if t[3])
    return {
        "trades": nt, "hr": round(hr, 1),
        "avg_ret": round(float(np.mean(ru)), 2),
        "pnl": round(sum(rw)),
        "pf": round(wi / lo if lo > 0 else 999, 2),
        "avg_hold": round(float(np.mean([t[2] for t in trades])), 1),
        "worst": round(min(ru), 1),
        "c4_pct": round(c4_count / nt * 100, 1),
        "kpis": combo_kpis,
        "label": _sl(combo_kpis),
    }


# ── 16a: Combo search ───────────────────────────────────────────────────

def run_16a(all_pc: dict, c4_kpis: list, tf: str,
            top_n: int = 10, hr_floor: float = 60.0,
            min_trades: int = 20) -> dict:
    results = {}
    for size in [3, 4, 5, 6]:
        pool = ALL_KPIS if size <= 4 else TOP_KPIS
        pool = [k for k in pool if any(k in pc["bulls"] for pc in all_pc.values())]
        nc = _comb(len(pool), size)
        print(f"    C{size}: {nc} combos from {len(pool)} KPIs...", end="", flush=True)
        t1 = time.time()

        hits = []
        for combo in combinations(pool, size):
            combo_list = list(combo)
            r = sim_combo(all_pc, combo_list, c4_kpis, tf,
                          gate="none", delay=1, min_trades=min_trades)
            if r is None:
                continue
            if r["hr"] < hr_floor:
                continue
            hits.append(r)

        # Sort by PF (primary objective)
        hits.sort(key=lambda x: -x["pf"])
        results[f"C{size}"] = hits[:top_n]
        elapsed = time.time() - t1
        print(f" {len(hits)} passed (HR>={hr_floor}%), {elapsed:.0f}s")

    return results


# ── 16b: Entry gate sweep ───────────────────────────────────────────────

GATES = [
    ("none", "No gate"),
    ("sma200", "Close > SMA200"),
    ("sma20_200", "SMA20 > SMA200"),
    ("sma_stack", "SMA20>50>100>200"),
    ("v5", "v5 (SMA20>200 + vol + overext)"),
]


def run_16b(all_pc: dict, combo_kpis: list, c4_kpis: list, tf: str) -> list:
    results = []
    for gate_key, gate_label in GATES:
        r = sim_combo(all_pc, combo_kpis, c4_kpis, tf,
                      gate=gate_key, delay=1, min_trades=5)
        if r:
            r["gate"] = gate_label
            results.append(r)
    return results


# ── 16c: Entry delay sweep ──────────────────────────────────────────────

DELAYS = [0, 1, 2, 3, 5, 10]


def run_16c(all_pc: dict, combo_kpis: list, c4_kpis: list, tf: str,
            gate: str = "none") -> list:
    results = []
    for H in DELAYS:
        r = sim_combo(all_pc, combo_kpis, c4_kpis, tf,
                      gate=gate, delay=H, min_trades=5)
        if r:
            r["H"] = H
            results.append(r)
    return results


# ── Printing helpers ─────────────────────────────────────────────────────

def _print_combo_table(results: dict, title: str, tf: str):
    print(f"\n{'='*120}")
    print(f"  {title} — {tf}")
    print(f"{'='*120}")
    hdr = f"  {'#':>2} {'Combo':<40} | {'Trades':>6} {'HR%':>6} {'AvgRet%':>8} {'PnL%':>8} {'PF':>7} {'AvgHold':>8} {'Worst%':>7} {'C4%':>5}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for size_label, combos in results.items():
        if not combos:
            print(f"  {size_label}: no combos passed filters")
            continue
        print(f"  ── {size_label} {'─'*100}")
        for i, r in enumerate(combos, 1):
            print(f"  {i:>2} {r['label']:<40} | "
                  f"{r['trades']:>6} {r['hr']:>6.1f} {r['avg_ret']:>8.2f} {r['pnl']:>8.0f} "
                  f"{r['pf']:>7.2f} {r['avg_hold']:>8.1f} {r['worst']:>7.1f} {r['c4_pct']:>5.1f}")


def _print_gate_table(results: list, combo_label: str, tf: str):
    print(f"\n  Entry gates on {combo_label} — {tf}:")
    hdr = f"    {'Gate':<30} | {'Trades':>6} {'HR%':>6} {'AvgRet%':>8} {'PnL%':>8} {'PF':>7} {'Worst%':>7}"
    print(hdr)
    print("    " + "-" * (len(hdr) - 4))
    for r in results:
        print(f"    {r['gate']:<30} | "
              f"{r['trades']:>6} {r['hr']:>6.1f} {r['avg_ret']:>8.2f} {r['pnl']:>8.0f} "
              f"{r['pf']:>7.2f} {r['worst']:>7.1f}")


def _print_delay_table(results: list, combo_label: str, tf: str):
    print(f"\n  Delay sweep on {combo_label} — {tf}:")
    hdr = f"    {'H':>3} | {'Trades':>6} {'HR%':>6} {'AvgRet%':>8} {'PnL%':>8} {'PF':>7} {'Worst%':>7}"
    print(hdr)
    print("    " + "-" * (len(hdr) - 4))
    for r in results:
        print(f"    {r['H']:>3} | "
              f"{r['trades']:>6} {r['hr']:>6.1f} {r['avg_ret']:>8.2f} {r['pnl']:>8.0f} "
              f"{r['pf']:>7.2f} {r['worst']:>7.1f}")


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    t0 = time.time()
    all_combo_results = {}
    all_gate_results = {}
    all_delay_results = {}
    csv_rows = []

    for tf in ("4H", "1D", "1W"):
        print(f"\n{'#'*70}")
        print(f"  TIMEFRAME: {tf}")
        print(f"{'#'*70}")

        c4_kpis = LOCKED_COMBOS[tf]["C4"]
        locked_c3 = LOCKED_COMBOS[tf]["C3"]

        print(f"  Loading data...", end=" ", flush=True)
        data = load_data(tf)
        print(f"{len(data)} stocks")

        print(f"  Pre-computing KPI bulls...", end=" ", flush=True)
        all_pc = precompute_bulls(data, tf)
        print(f"{len(all_pc)} valid")

        # ── 16a: Combo search ────────────────────────────────────────────
        print(f"\n  ── 16a: Exhaustive combo search (ranked by PF) ──")
        combo_results = run_16a(all_pc, c4_kpis, tf)

        # Also run the locked C3 for comparison
        locked_r = sim_combo(all_pc, locked_c3, c4_kpis, tf, gate="none", delay=1, min_trades=1)
        if locked_r:
            locked_r["label"] = f"LOCKED: {locked_r['label']}"
            combo_results["LOCKED"] = [locked_r]

        _print_combo_table(combo_results, "16a: COMBO SEARCH (ranked by PF)", tf)
        all_combo_results[tf] = combo_results

        # Save combo CSV rows
        for size_label, combos in combo_results.items():
            for rank, r in enumerate(combos, 1):
                csv_rows.append({
                    "step": "16a", "tf": tf, "size": size_label, "rank": rank,
                    "combo": r["label"], "gate": "none", "H": 1,
                    **{k: r[k] for k in ("trades", "hr", "avg_ret", "pnl", "pf", "avg_hold", "worst", "c4_pct")},
                })

        # ── 16b: Entry gates on top PF combos ────────────────────────────
        print(f"\n  ── 16b: Entry gate sweep ──")
        gate_results_tf = {}
        test_combos = []
        for size_label in ("C3", "C4", "C5", "C6"):
            combos = combo_results.get(size_label, [])
            if combos:
                test_combos.append((size_label, combos[0]))  # top-1 per size

        for size_label, best in test_combos:
            gr = run_16b(all_pc, best["kpis"], c4_kpis, tf)
            gate_results_tf[f"{size_label}: {best['label']}"] = gr
            _print_gate_table(gr, f"{size_label} top-1: {best['label']}", tf)

            for r in gr:
                csv_rows.append({
                    "step": "16b", "tf": tf, "size": size_label, "rank": 1,
                    "combo": best["label"], "gate": r["gate"], "H": 1,
                    **{k: r[k] for k in ("trades", "hr", "avg_ret", "pnl", "pf", "avg_hold", "worst", "c4_pct")},
                })

        all_gate_results[tf] = gate_results_tf

        # ── 16c: Delay sweep on top PF combo + best gate ─────────────────
        print(f"\n  ── 16c: Entry delay sweep ──")
        delay_results_tf = {}
        for size_label, best in test_combos:
            # Find best gate for this combo
            gr = gate_results_tf.get(f"{size_label}: {best['label']}", [])
            best_gate_key = "none"
            if gr:
                best_gate = max(gr, key=lambda x: x["pf"])
                for gk, gl in GATES:
                    if gl == best_gate["gate"]:
                        best_gate_key = gk
                        break

            dr = run_16c(all_pc, best["kpis"], c4_kpis, tf, gate=best_gate_key)
            key = f"{size_label}: {best['label']} (gate={best_gate_key})"
            delay_results_tf[key] = dr
            _print_delay_table(dr, key, tf)

            for r in dr:
                csv_rows.append({
                    "step": "16c", "tf": tf, "size": size_label, "rank": 1,
                    "combo": best["label"], "gate": best_gate_key, "H": r["H"],
                    **{k: r[k] for k in ("trades", "hr", "avg_ret", "pnl", "pf", "avg_hold", "worst", "c4_pct")},
                })

        all_delay_results[tf] = delay_results_tf

    # ── Summary comparison ───────────────────────────────────────────────
    print(f"\n{'#'*120}")
    print(f"  SUMMARY: LOCKED (PnL-opt) vs TOP PF COMBOS — per TF")
    print(f"{'#'*120}")

    hdr = f"  {'TF':>3} {'Strategy':<50} | {'Trades':>6} {'HR%':>6} {'AvgRet%':>8} {'PnL%':>8} {'PF':>7} {'Worst%':>7}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    for tf in ("4H", "1D", "1W"):
        cr = all_combo_results[tf]
        locked = cr.get("LOCKED", [None])[0]
        if locked:
            print(f"  {tf:>3} {locked['label']:<50} | "
                  f"{locked['trades']:>6} {locked['hr']:>6.1f} {locked['avg_ret']:>8.2f} {locked['pnl']:>8.0f} "
                  f"{locked['pf']:>7.2f} {locked['worst']:>7.1f}")

        for size in ("C3", "C4", "C5", "C6"):
            combos = cr.get(size, [])
            if combos:
                best = combos[0]
                print(f"  {tf:>3} {'PF-best ' + size + ': ' + best['label']:<50} | "
                      f"{best['trades']:>6} {best['hr']:>6.1f} {best['avg_ret']:>8.2f} {best['pnl']:>8.0f} "
                      f"{best['pf']:>7.2f} {best['worst']:>7.1f}")
        print("  " + "-" * (len(hdr) - 2))

    # Save CSV
    csv_path = OUTPUTS_DIR / "phase16_results.csv"
    fieldnames = ["step", "tf", "size", "rank", "combo", "gate", "H",
                   "trades", "hr", "avg_ret", "pnl", "pf", "avg_hold", "worst", "c4_pct"]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in csv_rows:
            w.writerow(row)
    print(f"\nCSV saved: {csv_path}")

    elapsed = time.time() - t0
    print(f"\nTotal runtime: {elapsed:.0f}s ({elapsed/60:.1f} min)")


if __name__ == "__main__":
    main()

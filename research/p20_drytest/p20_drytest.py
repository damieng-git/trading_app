"""Phase 20 Dry Test — walk-forward strategy validation on dashboard stocks.

Tests v6, dip_buy, swing, trend across 4H/1D/1W/2W/1M with 2-fold
walk-forward (IS + OOS) on all ~187 enriched symbols.

Memory budget: ≤1.5 GB peak (server has 7.6 GB, 70% cap = 5.3 GB,
dashboard uses ~2.4 GB).
"""

from __future__ import annotations

import gc
import glob
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import psutil

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from trading_dashboard.kpis.catalog import compute_kpi_state_map  # noqa: E402
from apps.dashboard.strategy import (  # noqa: E402
    compute_position_events,
    compute_polarity_position_events,
)

ENRICHED_DIR = REPO / "data" / "feature_store" / "enriched" / "dashboard" / "stock_data"
CONFIG_JSON = REPO / "apps" / "dashboard" / "configs" / "config.json"
OUT_DIR = Path(__file__).resolve().parent
LOG_FILE = OUT_DIR / "p20_drytest.log"
RESULTS_FILE = OUT_DIR / "p20_drytest_results.json"

TIMEFRAMES = ["4H", "1D", "1W", "2W", "1M"]
MEM_LIMIT_PCT = 70.0

# ── Strategy definitions ──────────────────────────────────────────────

def _load_strategies() -> dict:
    cfg = json.loads(CONFIG_JSON.read_text(encoding="utf-8"))
    v6_c3 = cfg.get("combo_3_kpis", [])
    v6_c4 = cfg.get("combo_4_kpis", [])
    strats = {
        "v6": {
            "type": "v6",
            "c3_kpis": v6_c3,
            "c4_kpis": v6_c4,
        }
    }
    for sk, sd in cfg.get("strategy_setups", {}).items():
        if sd.get("entry_type") != "polarity_combo":
            continue
        combos = sd.get("combos", {})
        c3d = combos.get("c3", {})
        c4d = combos.get("c4")
        exit_def = sd.get("exit_combos")
        strats[sk] = {
            "type": "polarity",
            "c3_kpis": c3d.get("kpis", []),
            "c3_pols": c3d.get("pols", []),
            "c4_kpis": c4d.get("kpis") if c4d else None,
            "c4_pols": c4d.get("pols") if c4d else None,
            "exit_kpis": exit_def.get("kpis") if exit_def else None,
            "exit_pols": exit_def.get("pols") if exit_def else None,
        }
    return strats


def _run_strategy(df, st, sdef, tf):
    """Run one strategy on one df slice → list[dict] of trade events."""
    if sdef["type"] == "v6":
        return compute_position_events(df, st, sdef["c3_kpis"], sdef["c4_kpis"], tf)
    return compute_polarity_position_events(
        df, st,
        sdef["c3_kpis"], sdef["c3_pols"],
        sdef.get("c4_kpis"), sdef.get("c4_pols"),
        tf,
        exit_kpis=sdef.get("exit_kpis"),
        exit_pols=sdef.get("exit_pols"),
    )


# ── Metrics ───────────────────────────────────────────────────────────

def _compute_metrics(events: list[dict]) -> dict:
    closed = [e for e in events if e.get("ret_pct") is not None and e["exit_reason"] != "Open"]
    if not closed:
        return {
            "trades": 0, "winners": 0, "losers": 0,
            "hr_pct": None, "avg_ret": None, "med_ret": None,
            "tot_ret": 0.0, "pf": None, "avg_hold": None,
            "win_avg": None, "loss_avg": None, "max_dd": None,
        }
    rets = [e["ret_pct"] for e in closed]
    holds = [e.get("hold", 0) for e in closed]
    winners = [r for r in rets if r > 0]
    losers = [r for r in rets if r <= 0]
    n = len(rets)
    gross_win = sum(winners) if winners else 0
    gross_loss = abs(sum(losers)) if losers else 0

    cumulative = np.cumsum(rets)
    running_max = np.maximum.accumulate(cumulative)
    drawdowns = cumulative - running_max
    max_dd = float(np.min(drawdowns)) if len(drawdowns) > 0 else 0.0

    return {
        "trades": n,
        "winners": len(winners),
        "losers": len(losers),
        "hr_pct": round(len(winners) / n * 100, 1),
        "avg_ret": round(sum(rets) / n, 2),
        "med_ret": round(float(np.median(rets)), 2),
        "tot_ret": round(sum(rets), 2),
        "pf": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "avg_hold": round(sum(holds) / n, 1),
        "win_avg": round(sum(winners) / len(winners), 2) if winners else None,
        "loss_avg": round(sum(losers) / len(losers), 2) if losers else None,
        "max_dd": round(max_dd, 2),
    }


# ── Memory guard ──────────────────────────────────────────────────────

def _check_memory():
    pct = psutil.virtual_memory().percent
    if pct > MEM_LIMIT_PCT:
        gc.collect()
        pct2 = psutil.virtual_memory().percent
        if pct2 > MEM_LIMIT_PCT:
            log(f"WARN: memory at {pct2:.1f}% after gc (limit {MEM_LIMIT_PCT}%)")
    return pct


def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── Main ──────────────────────────────────────────────────────────────

def main():
    t0 = time.perf_counter()
    log("=" * 70)
    log("P20-DryTest: Strategy Walk-Forward Validation")
    log("=" * 70)

    strats = _load_strategies()
    log(f"Strategies: {list(strats.keys())}")
    log(f"Timeframes: {TIMEFRAMES}")
    mem_pct = _check_memory()
    log(f"Memory at start: {mem_pct:.1f}%")

    # results[strat][tf][fold][period] = metrics_dict
    results: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    # Also track per-symbol trade counts for coverage
    coverage: dict = defaultdict(lambda: defaultdict(int))

    for tf in TIMEFRAMES:
        log(f"\n{'─'*60}")
        log(f"Timeframe: {tf}")
        log(f"{'─'*60}")

        pattern = str(ENRICHED_DIR / f"*_{tf}.parquet")
        files = sorted(glob.glob(pattern))
        log(f"  Files: {len(files)}")
        if not files:
            continue

        # Accumulate events per strategy × fold × period
        all_events: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

        for fi, fpath in enumerate(files):
            sym = os.path.basename(fpath).replace(f"_{tf}.parquet", "")
            df = pd.read_parquet(fpath)
            if df is None or df.empty or len(df) < 20:
                continue

            st = compute_kpi_state_map(df)
            n = len(df)
            mid = n // 2

            # 2-fold split
            folds = {
                "F1": {"IS": (0, mid), "OOS": (mid, n)},
                "F2": {"IS": (mid, n), "OOS": (0, mid)},
            }

            for skey, sdef in strats.items():
                for fold_name, periods in folds.items():
                    for period_name, (start, end) in periods.items():
                        df_slice = df.iloc[start:end].copy()
                        if len(df_slice) < 10:
                            continue
                        st_slice = {}
                        for k, s in st.items():
                            st_slice[k] = s.iloc[start:end].reset_index(drop=True)

                        try:
                            events = _run_strategy(df_slice, st_slice, sdef, tf)
                            all_events[skey][(fold_name, period_name)]["events"].extend(events)
                            if events:
                                coverage[(skey, tf)] += 1
                        except Exception:
                            pass

            # Memory cleanup every 20 symbols
            if (fi + 1) % 20 == 0:
                del df, st
                gc.collect()
                mem = _check_memory()
                log(f"  [{fi+1}/{len(files)}] {sym} done, mem={mem:.1f}%")

        # Log progress
        mem = _check_memory()
        log(f"  All {len(files)} symbols done for {tf}, mem={mem:.1f}%")

        # Compute metrics per strategy/fold/period
        for skey in strats:
            for (fold_name, period_name), data in all_events[skey].items():
                metrics = _compute_metrics(data["events"])
                results[skey][tf][fold_name][period_name] = metrics

        # Cleanup TF data
        del all_events
        gc.collect()

    elapsed = time.perf_counter() - t0

    # ── Summary tables ────────────────────────────────────────────────
    log(f"\n{'='*90}")
    log("RESULTS SUMMARY")
    log(f"{'='*90}")

    # Aggregate OOS across folds
    summary: dict = defaultdict(lambda: defaultdict(dict))
    for skey in strats:
        for tf in TIMEFRAMES:
            oos_events_all = []
            is_events_all = []
            for fold_name in ["F1", "F2"]:
                oos_m = results[skey][tf].get(fold_name, {}).get("OOS", {})
                is_m = results[skey][tf].get(fold_name, {}).get("IS", {})
                if oos_m:
                    summary[skey][tf]["OOS"] = summary[skey][tf].get("OOS", {})
                if is_m:
                    summary[skey][tf]["IS"] = summary[skey][tf].get("IS", {})

    # Print OOS table
    header = f"{'Strategy':<10} {'TF':<5} {'Period':<5} {'Trades':>7} {'HR%':>6} {'AvgRet':>8} {'MedRet':>8} {'TotRet':>9} {'PF':>7} {'AvgHold':>8} {'WinAvg':>8} {'LossAvg':>9} {'MaxDD':>8}"
    log(header)
    log("-" * len(header))

    for skey in ["v6", "dip_buy", "swing", "trend"]:
        for tf in TIMEFRAMES:
            for fold_name in ["F1", "F2"]:
                for period in ["IS", "OOS"]:
                    m = results.get(skey, {}).get(tf, {}).get(fold_name, {}).get(period, {})
                    if not m or m.get("trades", 0) == 0:
                        continue
                    pf_str = f"{m['pf']:.2f}" if m.get("pf") is not None else "∞"
                    dd_str = f"{m['max_dd']:.1f}" if m.get("max_dd") is not None else "-"
                    label = f"{fold_name}-{period}"
                    log(
                        f"{skey:<10} {tf:<5} {label:<8} "
                        f"{m['trades']:>5} {m.get('hr_pct', 0):>6.1f} "
                        f"{m.get('avg_ret', 0):>8.2f} {m.get('med_ret', 0):>8.2f} "
                        f"{m.get('tot_ret', 0):>9.1f} {pf_str:>7} "
                        f"{m.get('avg_hold', 0):>8.1f} "
                        f"{m.get('win_avg', 0) or 0:>8.2f} "
                        f"{m.get('loss_avg', 0) or 0:>9.2f} "
                        f"{dd_str:>8}"
                    )
            log("")

    # Condensed OOS-average table
    log(f"\n{'='*90}")
    log("OOS AVERAGE (mean of F1-OOS + F2-OOS)")
    log(f"{'='*90}")
    header2 = f"{'Strategy':<10} {'TF':<5} {'Trades':>7} {'HR%':>6} {'AvgRet':>8} {'TotRet':>9} {'PF':>7} {'AvgHold':>8} {'MaxDD':>8}"
    log(header2)
    log("-" * len(header2))

    final_table = {}
    for skey in ["v6", "dip_buy", "swing", "trend"]:
        for tf in TIMEFRAMES:
            oos_metrics = []
            for fold_name in ["F1", "F2"]:
                m = results.get(skey, {}).get(tf, {}).get(fold_name, {}).get("OOS", {})
                if m and m.get("trades", 0) > 0:
                    oos_metrics.append(m)
            if not oos_metrics:
                continue

            avg_trades = sum(m["trades"] for m in oos_metrics) / len(oos_metrics)
            avg_hr = np.mean([m["hr_pct"] for m in oos_metrics if m.get("hr_pct") is not None])
            avg_ret = np.mean([m["avg_ret"] for m in oos_metrics if m.get("avg_ret") is not None])
            avg_tot = sum(m["tot_ret"] for m in oos_metrics) / len(oos_metrics)
            pfs = [m["pf"] for m in oos_metrics if m.get("pf") is not None]
            avg_pf = np.mean(pfs) if pfs else None
            avg_hold = np.mean([m["avg_hold"] for m in oos_metrics if m.get("avg_hold") is not None])
            dds = [m["max_dd"] for m in oos_metrics if m.get("max_dd") is not None]
            avg_dd = np.mean(dds) if dds else None

            pf_str = f"{avg_pf:.2f}" if avg_pf is not None else "∞"
            dd_str = f"{avg_dd:.1f}" if avg_dd is not None else "-"

            log(
                f"{skey:<10} {tf:<5} {avg_trades:>7.0f} {avg_hr:>6.1f} "
                f"{avg_ret:>8.2f} {avg_tot:>9.1f} {pf_str:>7} "
                f"{avg_hold:>8.1f} {dd_str:>8}"
            )
            final_table[f"{skey}_{tf}"] = {
                "strategy": skey, "tf": tf,
                "trades": round(avg_trades), "hr_pct": round(float(avg_hr), 1),
                "avg_ret": round(float(avg_ret), 2), "tot_ret": round(float(avg_tot), 2),
                "pf": round(float(avg_pf), 2) if avg_pf is not None else None,
                "avg_hold": round(float(avg_hold), 1),
                "max_dd": round(float(avg_dd), 1) if avg_dd is not None else None,
            }
        log("")

    # Save full results
    serializable = {}
    for skey in results:
        serializable[skey] = {}
        for tf in results[skey]:
            serializable[skey][tf] = {}
            for fold in results[skey][tf]:
                serializable[skey][tf][fold] = dict(results[skey][tf][fold])

    output = {
        "run_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_s": round(elapsed, 1),
        "symbols": len(glob.glob(str(ENRICHED_DIR / "*_1D.parquet"))),
        "strategies": list(strats.keys()),
        "timeframes": TIMEFRAMES,
        "detailed": serializable,
        "oos_summary": final_table,
    }
    RESULTS_FILE.write_text(json.dumps(output, indent=2, allow_nan=False, default=str), encoding="utf-8")
    log(f"\nResults saved to {RESULTS_FILE}")
    log(f"Elapsed: {elapsed:.1f}s ({elapsed/60:.1f}m)")
    log("P20-DryTest complete.")


if __name__ == "__main__":
    main()

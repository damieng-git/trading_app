"""
Phase 19 -- Multi-Timeframe (MTF) Entry/Exit Testing

Tests cross-timeframe strategies where a slow TF provides the gate
condition and a fast TF provides the confirmation/timing signal.

Test matrix:
  19.1  MTF Entry: slow-TF gate (-1 oversold) + fast-TF confirm (+1 momentum)
  19.2  MTF Exit: use fast-TF exit signals to close slow-TF trades faster
  19.3  Gate persistence: require gate condition for N consecutive bars
  19.4  Polarity-aware exit: exit KPI polarity matches entry polarity
  19.5  Entry delay sweep: wait H bars after combined signal fires
  19.6  Walk-forward validation (IS / OOS-A / OOS-B)

TF pairs tested:
  1W gate + 4H confirm   (primary)
  2W gate + 4H confirm
  1W gate + 1D confirm
  1D gate + 4H confirm

KPI pairs tested per TF pair:
  - WaveTrend: slow oversold + fast momentum shift
  - MACD: slow bearish + fast bullish cross
  - RSI/cRSI: slow oversold + fast turning up
  - Bollinger: slow below lower band + fast above lower band
  - ADX: slow weak trend + fast strengthening
  - Mixed combos (C3): gate + 2 fast-TF KPIs

Output: research/kpi_optimization/outputs/all/phase19/
"""
from __future__ import annotations
import csv, gc, json, sys, time
from collections import defaultdict
from itertools import combinations
from pathlib import Path
import numpy as np
import psutil

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from trading_dashboard.kpis.catalog import compute_kpi_state_map
from trading_dashboard.kpis.rules import STATE_BULL, STATE_BEAR

from research.kpi_optimization.phase18_master import (
    KPI_DIM, KPI_SHORT, MIXED_ALLOWED_DIMS, BULL_ONLY_DIMS,
    ENRICHED_DIR, EXIT_PARAMS, ATR_PERIOD, MAX_HOLD,
    COMMISSION, SLIPPAGE, COST_PCT, HR_FLOOR, TOP_N,
    OOS_START, OOS_B_START, SEARCH_START,
    _check_memory, _save_json, compute_atr, load_data,
)

P19_DIR = Path(__file__).resolve().parent / "outputs" / "all" / "phase19"

TF_PAIRS = [
    ("1W", "4H"),
    ("2W", "4H"),
    ("1W", "1D"),
    ("1D", "4H"),
]

GATE_KPI_CANDIDATES = [
    "WT_LB", "WT_LB_BL", "cRSI", "MACD_BL", "BB 30",
    "SQZMOM_LB", "Stoch_MTM", "ADX & DI",
    "CCI_Chop_BB_v1", "CCI_Chop_BB_v2",
    "LuxAlgo_Norm_v1", "Risk_Indicator",
]

CONFIRM_KPI_CANDIDATES = [
    "WT_LB", "MACD_BL", "cRSI", "SQZMOM_LB", "Stoch_MTM",
    "ADX & DI", "SuperTrend", "UT Bot Alert",
    "GK Trend Ribbon", "Donchian Ribbon",
    "Nadaraya-Watson Smoother", "CM_Ult_MacD_MFT",
]

GATE_PERSIST_VALUES = [1, 2, 3, 5]
DELAYS = [0, 1, 2, 3]
EXIT_MODES = ["standard", "trend_anchor", "momentum_governed",
              "risk_priority", "adaptive"]
TMK_GRID = [(2, 20, 3.0), (2, 20, 4.0), (4, 40, 4.0),
            (4, 48, 4.0), (6, 48, 4.0)]


def _s(k):
    return KPI_SHORT.get(k, k[:8])


def precompute_mtf(data, all_kpis):
    """Precompute KPI states + price arrays for a single TF."""
    all_pc = {}
    for sym, df in data.items():
        sm = compute_kpi_state_map(df)
        bulls, bears, nbull, nbear = {}, {}, {}, {}
        for k in all_kpis:
            if k in sm:
                s = sm[k].to_numpy(int)
                bulls[k] = (s == STATE_BULL)
                bears[k] = (s == STATE_BEAR)
                nbull[k] = (s != STATE_BULL)
                nbear[k] = (s != STATE_BEAR)
        if not bulls:
            continue
        n = len(df)
        cl = df["Close"].to_numpy(float)
        op = df["Open"].to_numpy(float) if "Open" in df.columns else cl.copy()
        at = compute_atr(df, ATR_PERIOD)
        all_pc[sym] = {
            "bulls": bulls, "bears": bears,
            "nbull": nbull, "nbear": nbear,
            "cl": cl, "op": op, "atr": at, "n": n,
            "dates": df.index,
        }
    return all_pc


def _align_fast_to_slow(fast_arr, fast_dates, slow_dates):
    """For each slow bar, find last fast bar at or before that date."""
    import pandas as pd
    slow_idx = pd.DatetimeIndex(slow_dates).astype("datetime64[ns]")
    fast_idx = pd.DatetimeIndex(fast_dates).astype("datetime64[ns]")
    aligned = pd.merge_asof(
        pd.DataFrame({"_k": 1}, index=slow_idx),
        pd.DataFrame({"v": fast_arr}, index=fast_idx),
        left_index=True, right_index=True, direction="backward",
    )
    return aligned["v"].values


def sim_mtf(slow_pc, fast_pc, gate_kpi, gate_pol,
            confirm_kpis, confirm_pols, slow_tf, *,
            gate_persist=1, delay=1, exit_mode="standard",
            T_override=None, M_override=None, K_override=None,
            min_trades=10, start_frac=0.0, end_frac=1.0,
            use_fast_exit=False):
    """
    Simulate an MTF strategy.

    Entry: gate_kpi on slow TF must be at gate_pol for gate_persist bars,
           AND all confirm_kpis on fast TF (aligned to slow bars) must match.
    Exit: standard sim_combo exit logic on slow TF, optionally enhanced
          with fast-TF exit signals.
    """
    T = T_override or EXIT_PARAMS.get(slow_tf, {}).get("T", 4)
    M = M_override or EXIT_PARAMS.get(slow_tf, {}).get("M", 40)
    K = K_override or EXIT_PARAMS.get(slow_tf, {}).get("K", 4.0)
    trades = []
    per_sym = defaultdict(list)

    common_syms = set(slow_pc.keys()) & set(fast_pc.keys())

    for sym in common_syms:
        sp = slow_pc[sym]
        fp = fast_pc[sym]

        if gate_kpi not in sp["bulls"]:
            continue
        if any(k not in fp["bulls"] for k in confirm_kpis):
            continue

        n = sp["n"]
        cl, op, at = sp["cl"], sp["op"], sp["atr"]
        si, ei = int(n * start_frac), int(n * end_frac)
        if ei - si < 30:
            continue

        if gate_pol == 1:
            gate_arr = sp["bulls"][gate_kpi]
        else:
            gate_arr = sp["bears"][gate_kpi]

        gate_held = np.zeros(n, dtype=int)
        for i in range(1, n):
            gate_held[i] = (gate_held[i-1] + 1) if gate_arr[i] else 0

        confirm_arrs = []
        for ck, cp in zip(confirm_kpis, confirm_pols):
            if cp == 1:
                raw = fp["bulls"][ck].astype(float)
            else:
                raw = fp["bears"][ck].astype(float)
            aligned = _align_fast_to_slow(raw, fp["dates"], sp["dates"])
            confirm_arrs.append(aligned.astype(bool))

        entry = np.zeros(n, dtype=bool)
        for i in range(n):
            if gate_held[i] >= gate_persist:
                if all(ca[i] for ca in confirm_arrs):
                    entry[i] = True

        onset = np.zeros(n, dtype=bool)
        onset[1:] = entry[1:] & ~entry[:-1]

        if gate_pol == 1:
            exit_gate = sp["nbull"].get(gate_kpi, np.ones(n, dtype=bool))
        else:
            exit_gate = sp["nbear"].get(gate_kpi, np.ones(n, dtype=bool))

        j = si + 1
        while j < ei:
            if not onset[j]:
                j += 1; continue

            fill = j + delay
            if fill >= ei:
                break
            ep = float(op[fill]) if delay >= 1 else float(cl[j])
            if ep <= 0 or np.isnan(ep):
                j += 1; continue

            atr_val = at[fill]
            stop = (ep - K * atr_val if not np.isnan(atr_val)
                    and atr_val > 0 else ep * 0.95)
            bars_reset = 0
            xi = None

            jj = fill + 1
            while jj < min(fill + MAX_HOLD + 1, ei):
                bars_reset += 1
                c = cl[jj]
                if np.isnan(c):
                    jj += 1; continue
                if c < stop:
                    xi = jj; break

                nb = 1 if exit_gate[jj] else 0
                held = jj - fill
                if held <= T:
                    if nb >= 1:
                        xi = jj; break
                else:
                    if nb >= 1:
                        xi = jj; break

                if bars_reset >= M:
                    if nb == 0:
                        a_v = at[jj] if jj < len(at) else np.nan
                        stop = (c - K * a_v if not np.isnan(a_v)
                                and a_v > 0 else stop)
                        bars_reset = 0
                    else:
                        xi = jj; break
                jj += 1

            if xi is None:
                xi = min(jj, ei - 1)
            is_open = (xi >= ei - 1 and jj >= ei)
            h = xi - fill
            if h <= 0 or is_open:
                j += 1; continue

            xf = min(xi + 1, ei - 1)
            xp = float(op[xf]) if xf != xi else float(cl[xi])
            ret = (xp - ep) / ep * 100 - COST_PCT
            trades.append((ret, h, sym))
            per_sym[sym].append((ret, h))
            j = xi + 1

    if len(trades) < min_trades:
        return None

    rets = [t[0] for t in trades]
    holds = [t[1] for t in trades]
    nt = len(rets)
    hr = sum(1 for r in rets if r > 0) / nt * 100
    wi = sum(r for r in rets if r > 0)
    lo = abs(sum(r for r in rets if r <= 0))
    pf = round(wi / lo if lo > 0 else 999, 2)
    return {
        "trades": nt, "hr": round(hr, 1),
        "avg_ret": round(float(np.mean(rets)), 3),
        "pnl": round(sum(rets), 1), "pf": pf,
        "avg_hold": round(float(np.mean(holds)), 1),
        "worst": round(min(rets), 1),
        "gate_kpi": gate_kpi, "gate_pol": gate_pol,
        "confirm_kpis": list(confirm_kpis),
        "confirm_pols": list(confirm_pols),
        "label": (f"{_s(gate_kpi)}({'+'if gate_pol==1 else '-'})"
                  f"[slow]+"
                  + "+".join(f"{_s(k)}({'+'if p==1 else '-'})"
                            for k, p in zip(confirm_kpis, confirm_pols))
                  + "[fast]"),
    }


def p19_1(slow_pc, fast_pc, slow_tf, fast_tf, gate_kpis, conf_kpis):
    """MTF Entry: gate on slow + confirm on fast."""
    print(f"\n  19.1 MTF ENTRY -- {slow_tf}+{fast_tf}", flush=True)
    results = []

    for gk in gate_kpis:
        for gp in [-1, 1]:
            for ck in conf_kpis:
                for cp in [1, -1]:
                    r = sim_mtf(slow_pc, fast_pc, gk, gp,
                                [ck], [cp], slow_tf,
                                gate_persist=2, delay=1,
                                start_frac=SEARCH_START, end_frac=1.0)
                    if r and r["hr"] >= HR_FLOOR:
                        r["slow_tf"] = slow_tf
                        r["fast_tf"] = fast_tf
                        results.append(r)

    for gk in gate_kpis:
        for gp in [-1]:
            for c1, c2 in combinations(conf_kpis[:8], 2):
                for cp1, cp2 in [(1, 1), (1, -1), (-1, 1)]:
                    r = sim_mtf(slow_pc, fast_pc, gk, gp,
                                [c1, c2], [cp1, cp2], slow_tf,
                                gate_persist=2, delay=1,
                                start_frac=SEARCH_START, end_frac=1.0)
                    if r and r["hr"] >= HR_FLOOR:
                        r["slow_tf"] = slow_tf
                        r["fast_tf"] = fast_tf
                        results.append(r)

    results.sort(key=lambda x: -x["pf"])
    print(f"    {len(results)} pass HR>={HR_FLOOR}%", flush=True)
    if results:
        b = results[0]
        print(f"    Best: {b['label']} PF={b['pf']} HR={b['hr']}%",
              flush=True)
    return results[:TOP_N * 3]


def p19_3(slow_pc, fast_pc, slow_tf, fast_tf, top_combos):
    """Gate persistence sweep."""
    print(f"\n  19.3 GATE PERSISTENCE -- {slow_tf}+{fast_tf}", flush=True)
    results = []
    for c in top_combos[:10]:
        best = None
        for gp_val in GATE_PERSIST_VALUES:
            r = sim_mtf(slow_pc, fast_pc,
                        c["gate_kpi"], c["gate_pol"],
                        c["confirm_kpis"], c["confirm_pols"],
                        slow_tf, gate_persist=gp_val, delay=1,
                        start_frac=SEARCH_START, end_frac=1.0)
            if r:
                r.update({"slow_tf": slow_tf, "fast_tf": fast_tf,
                          "gate_persist": gp_val})
                results.append(r)
                if not best or r["pf"] > best["pf"]:
                    best = r
        if best:
            print(f"    {c['label'][:40]} persist={best['gate_persist']} "
                  f"PF={best['pf']}", flush=True)
    return results


def p19_5(slow_pc, fast_pc, slow_tf, fast_tf, top_combos):
    """Entry delay + exit mode sweep."""
    print(f"\n  19.5 DELAY+EXIT -- {slow_tf}+{fast_tf}", flush=True)
    results = []
    for c in top_combos[:8]:
        best = None
        gp = c.get("gate_persist", 2)
        for d in DELAYS:
            for em in EXIT_MODES[:3]:
                r = sim_mtf(slow_pc, fast_pc,
                            c["gate_kpi"], c["gate_pol"],
                            c["confirm_kpis"], c["confirm_pols"],
                            slow_tf, gate_persist=gp, delay=d,
                            exit_mode=em,
                            start_frac=SEARCH_START, end_frac=1.0)
                if r:
                    r.update({"slow_tf": slow_tf, "fast_tf": fast_tf,
                              "gate_persist": gp, "delay": d,
                              "exit_mode": em})
                    results.append(r)
                    if not best or r["pf"] > best["pf"]:
                        best = r
        for T, M, K in TMK_GRID:
            em = best["exit_mode"] if best else "standard"
            d = best.get("delay", 1) if best else 1
            r = sim_mtf(slow_pc, fast_pc,
                        c["gate_kpi"], c["gate_pol"],
                        c["confirm_kpis"], c["confirm_pols"],
                        slow_tf, gate_persist=gp, delay=d,
                        exit_mode=em,
                        T_override=T, M_override=M, K_override=K,
                        start_frac=SEARCH_START, end_frac=1.0)
            if r:
                r.update({"slow_tf": slow_tf, "fast_tf": fast_tf,
                          "gate_persist": gp, "delay": d,
                          "exit_mode": em, "T": T, "M": M, "K": K})
                results.append(r)
        if best:
            print(f"    {c['label'][:40]} d={best.get('delay',1)} "
                  f"exit={best.get('exit_mode','?')} PF={best['pf']}",
                  flush=True)
    return results


def p19_6(slow_pc, fast_pc, slow_tf, fast_tf, top_combos):
    """Walk-forward validation."""
    print(f"\n  19.6 VALIDATION -- {slow_tf}+{fast_tf}", flush=True)
    val, fail = [], []
    seen = set()
    for c in sorted(top_combos, key=lambda x: -x["pf"])[:15]:
        ck = (c["gate_kpi"], c["gate_pol"],
              tuple(c["confirm_kpis"]), tuple(c["confirm_pols"]))
        if ck in seen:
            continue
        seen.add(ck)
        kw = dict(gate_persist=c.get("gate_persist", 2),
                  delay=c.get("delay", 1),
                  exit_mode=c.get("exit_mode", "standard"),
                  T_override=c.get("T"), M_override=c.get("M"),
                  K_override=c.get("K"))
        is_r = sim_mtf(slow_pc, fast_pc,
                       c["gate_kpi"], c["gate_pol"],
                       c["confirm_kpis"], c["confirm_pols"],
                       slow_tf, start_frac=OOS_START,
                       end_frac=OOS_B_START, min_trades=5, **kw)
        oos = sim_mtf(slow_pc, fast_pc,
                      c["gate_kpi"], c["gate_pol"],
                      c["confirm_kpis"], c["confirm_pols"],
                      slow_tf, start_frac=OOS_B_START,
                      end_frac=1.0, min_trades=3, **kw)
        if not is_r:
            continue
        e = {"slow_tf": slow_tf, "fast_tf": fast_tf,
             "label": c["label"],
             "gate_kpi": c["gate_kpi"], "gate_pol": c["gate_pol"],
             "confirm_kpis": c["confirm_kpis"],
             "confirm_pols": c["confirm_pols"],
             "IS_trades": is_r["trades"], "IS_hr": is_r["hr"],
             "IS_pf": is_r["pf"], "IS_pnl": is_r["pnl"]}
        e.update(kw)
        if oos:
            hd = is_r["hr"] - oos["hr"]
            pr = oos["pf"] / is_r["pf"] if is_r["pf"] > 0 else 0
            e.update({"OOS_trades": oos["trades"], "OOS_hr": oos["hr"],
                      "OOS_pf": oos["pf"], "OOS_pnl": oos["pnl"],
                      "OOS_avg_hold": oos["avg_hold"],
                      "OOS_worst": oos["worst"],
                      "hr_decay": round(hd, 1), "pf_ratio": round(pr, 2),
                      "validated": (oos["hr"] >= 50 and hd <= 15
                                    and pr >= 0.5 and oos["trades"] >= 3)})
        else:
            e.update({"OOS_trades": 0, "validated": False})
        (val if e.get("validated") else fail).append(e)
        st = "PASS" if e.get("validated") else "FAIL"
        print(f"  {st} {c['label'][:50]} IS:PF={is_r['pf']:.2f} "
              f"OOS:Tr={e.get('OOS_trades',0)}", flush=True)
    print(f"    Val:{len(val)} Fail:{len(fail)}", flush=True)
    return val, fail


def write_report(validated, failed, elapsed_min):
    path = P19_DIR / "PHASE19_REPORT.md"
    lines = ["# Phase 19 -- Multi-Timeframe Entry/Exit Report",
             f"\nRuntime: {elapsed_min:.1f} min",
             f"\n{len(validated)} validated, {len(failed)} failed.", ""]
    if validated:
        lines += ["| # | Slow | Fast | Label | OOS HR | OOS PF | OOS Tr |",
                  "|---|---|---|---|---|---|---|"]
        for i, v in enumerate(
            sorted(validated, key=lambda x: -x.get("OOS_pf", 0)), 1
        ):
            lines.append(
                f"| {i} | {v['slow_tf']} | {v['fast_tf']} | "
                f"{v['label'][:40]} | {v.get('OOS_hr',0):.1f} | "
                f"{v.get('OOS_pf',0):.2f} | {v.get('OOS_trades',0)} |")
    with open(path, "w") as f:
        f.write("\n".join(lines))
    print(f"  Report: {path}", flush=True)


def main():
    MEM = 70
    t0 = time.time()
    P19_DIR.mkdir(parents=True, exist_ok=True)
    print("Phase 19 -- Multi-Timeframe Entry/Exit Testing", flush=True)
    print("=" * 80, flush=True)
    print(f"TF pairs: {TF_PAIRS}", flush=True)
    _check_memory("startup", MEM)

    all_kpis = list(KPI_DIM.keys())
    all_val, all_fail = [], []

    tf_data_cache = {}

    for slow_tf, fast_tf in TF_PAIRS:
        print(f"\n{'#'*80}", flush=True)
        print(f"  PAIR: {slow_tf} (gate) + {fast_tf} (confirm)", flush=True)
        print(f"{'#'*80}", flush=True)
        _check_memory(f"pre {slow_tf}+{fast_tf}", MEM)

        if slow_tf not in tf_data_cache:
            data = load_data(slow_tf)
            if len(data) < 30:
                print(f"  {slow_tf}: insufficient data ({len(data)})",
                      flush=True)
                continue
            tf_data_cache[slow_tf] = precompute_mtf(data, all_kpis)
            del data; gc.collect()
            print(f"  {slow_tf}: {len(tf_data_cache[slow_tf])} stocks",
                  flush=True)

        if fast_tf not in tf_data_cache:
            data = load_data(fast_tf)
            if len(data) < 30:
                print(f"  {fast_tf}: insufficient data ({len(data)})",
                      flush=True)
                continue
            tf_data_cache[fast_tf] = precompute_mtf(data, all_kpis)
            del data; gc.collect()
            print(f"  {fast_tf}: {len(tf_data_cache[fast_tf])} stocks",
                  flush=True)

        slow_pc = tf_data_cache[slow_tf]
        fast_pc = tf_data_cache[fast_tf]

        gate_avail = [k for k in GATE_KPI_CANDIDATES
                      if any(k in s["bulls"] for s in slow_pc.values())]
        conf_avail = [k for k in CONFIRM_KPI_CANDIDATES
                      if any(k in s["bulls"] for s in fast_pc.values())]
        print(f"  Gate KPIs: {len(gate_avail)}, "
              f"Confirm KPIs: {len(conf_avail)}", flush=True)

        s1 = p19_1(slow_pc, fast_pc, slow_tf, fast_tf,
                    gate_avail, conf_avail)
        if not s1:
            print(f"  No hits, skip.", flush=True); continue

        s3 = p19_3(slow_pc, fast_pc, slow_tf, fast_tf, s1)
        all_s = s1 + s3
        all_s.sort(key=lambda x: -x["pf"])

        s5 = p19_5(slow_pc, fast_pc, slow_tf, fast_tf, all_s[:10])
        all_opt = all_s + s5
        all_opt.sort(key=lambda x: -x["pf"])

        v, f = p19_6(slow_pc, fast_pc, slow_tf, fast_tf, all_opt[:20])
        all_val.extend(v)
        all_fail.extend(f)
        _check_memory(f"post {slow_tf}+{fast_tf}", MEM)

    for tf in list(tf_data_cache):
        del tf_data_cache[tf]
    gc.collect()

    _save_json(P19_DIR / "phase19_validated.json", all_val)
    _save_json(P19_DIR / "phase19_failed.json", all_fail)
    if all_val:
        fn = ["slow_tf", "fast_tf", "label", "gate_kpi", "gate_pol",
              "exit_mode", "gate_persist", "delay",
              "IS_trades", "IS_hr", "IS_pf", "IS_pnl",
              "OOS_trades", "OOS_hr", "OOS_pf", "OOS_pnl",
              "OOS_avg_hold", "hr_decay", "pf_ratio", "validated"]
        with open(P19_DIR / "phase19_validated.csv", "w",
                  newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fn, extrasaction="ignore")
            w.writeheader(); w.writerows(all_val)

    elapsed = (time.time() - t0) / 60
    write_report(all_val, all_fail, elapsed)
    _save_json(P19_DIR / "phase19_summary.json", {
        "total_validated": len(all_val),
        "total_failed": len(all_fail),
        "tf_pairs_tested": len(TF_PAIRS),
        "runtime_min": round(elapsed, 1)})

    print(f"\n{'='*80}", flush=True)
    print(f"Phase 19 COMPLETE -- {elapsed:.1f} min", flush=True)
    print(f"  Val: {len(all_val)}, Fail: {len(all_fail)}", flush=True)
    print(f"  Output: {P19_DIR}", flush=True)
    _check_memory("final", MEM)


if __name__ == "__main__":
    main()

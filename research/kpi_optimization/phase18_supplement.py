"""
Phase 18 Supplement -- Mixed-Polarity Gap Fix

Gap 1: bull_only archetypes never tested -1 polarity (buy-the-dip missed).
Gap 2: mixed archetypes missed intermediate permutations (2-of-3 flipped).

Pipeline: Supp-1 -> Supp-2 -> Supp-3 -> Supp-4 (mirrors 18.1-18.4)
Output: outputs/all/phase18_supplement/
"""
from __future__ import annotations
import csv, gc, json, sys, time
from collections import defaultdict
from itertools import combinations, product
from pathlib import Path
import numpy as np
import psutil

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from research.kpi_optimization.phase18_master import (
    ALL_TFS, ARCHETYPES, BULL_ONLY_DIMS, HR_FLOOR, KPI_DIM,
    MAX_COMBOS_PER_SIZE, MIXED_ALLOWED_DIMS, OOS_B_START, OOS_START,
    SEARCH_START, TOP_N, ENRICHED_DIR,
    _check_memory, _save_json, load_data, precompute, sim_combo,
)

SUPP_DIR = Path(__file__).resolve().parent / "outputs" / "all" / "phase18_supplement"
GATES = ["none", "sma20_200", "v5"]
DELAYS = [0, 1, 2, 3]
EXIT_MODES = ["standard", "trend_anchor", "momentum_governed",
              "risk_priority", "adaptive"]
TMK_GRID = [(2, 20, 3.0), (2, 20, 4.0), (2, 20, 5.0),
            (4, 40, 3.0), (4, 40, 4.0), (4, 40, 5.0),
            (4, 48, 4.0), (6, 48, 4.0)]


def _gen_ext_pol(pool, size, anchor_dim, excl_pairs, pol_mode,
                 max_combos=50000):
    excl = set()
    for a, b in excl_pairs:
        excl.add((a, b)); excl.add((b, a))
    combos = []
    for combo in combinations(pool, size):
        if any((combo[i], combo[j]) in excl
               for i in range(len(combo))
               for j in range(i + 1, len(combo))):
            continue
        if anchor_dim and not any(KPI_DIM.get(k) == anchor_dim for k in combo):
            continue
        mix_idx = [i for i, k in enumerate(combo)
                   if KPI_DIM.get(k) in MIXED_ALLOWED_DIMS]
        if not mix_idx:
            continue
        for bits in product([1, -1], repeat=len(mix_idx)):
            nf = sum(1 for b in bits if b == -1)
            if pol_mode == "dip_in_bull" and nf == 0:
                continue
            if pol_mode == "full_mixed" and nf in (0, 1, len(bits)):
                continue
            pols = [1] * size
            for idx, bit in zip(mix_idx, bits):
                pols[idx] = bit
            combos.append((list(combo), pols))
            if len(combos) >= max_combos:
                return combos
    return combos


def supp_1(all_pc, tf, eligible, excl_pairs):
    print(f"\n{'='*80}", flush=True)
    print(f"  SUPP-1 EXTENDED POLARITY -- {tf} ({len(all_pc)} stocks)", flush=True)
    tf_res = {}
    for ak, arch in ARCHETYPES.items():
        pool = [k for k in eligible if KPI_DIM.get(k) in arch["pool_dims"]]
        if len(pool) < 3:
            tf_res[ak] = {}; continue
        if ak == "E_mixed" and len(pool) > 15:
            pool = pool[:15]
        pm = "dip_in_bull" if arch["polarity"] == "bull_only" else "full_mixed"
        print(f"  {ak}: {arch['label']} pool={len(pool)} mode={pm}", flush=True)
        ar = {}
        for sz in ([3, 4] if len(pool) > 15 else [3, 4, 5]):
            if len(pool) < sz:
                continue
            combos = _gen_ext_pol(pool, sz, arch.get("anchor_dim"),
                                  excl_pairs, pm, MAX_COMBOS_PER_SIZE)
            print(f"    C{sz}: {len(combos)} combos...", end="", flush=True)
            if not combos:
                print(" skip", flush=True); continue
            t1 = time.time()
            hits = []
            for ck, cp in combos:
                r = sim_combo(all_pc, ck, cp, tf,
                              exit_mode=arch["exit_mode"],
                              gate="none", delay=1,
                              start_frac=SEARCH_START, end_frac=1.0)
                if r and r["hr"] >= HR_FLOOR:
                    r.update({"archetype": ak,
                              "exit_mode": arch["exit_mode"], "tf": tf})
                    hits.append(r)
            hits.sort(key=lambda x: -x["pf"])
            ar[f"C{sz}"] = hits[:TOP_N]
            nm = sum(1 for _, p in combos if -1 in p)
            pf = f"{hits[0]['pf']}" if hits else "none"
            print(f" {len(hits)} pass mixed={nm} best={pf} "
                  f"{time.time()-t1:.0f}s", flush=True)
        tf_res[ak] = ar
    return tf_res


def supp_2(all_pc, tf, s1):
    print(f"\n  SUPP-2 GATE/DELAY -- {tf}", flush=True)
    res = []
    for ak, ar in s1.items():
        tops = [c for sl in ar.values() for c in sl[:3]]
        for c in tops:
            best = None
            for g in GATES:
                for h in DELAYS:
                    r = sim_combo(all_pc, c["kpis"], c["pols"], tf,
                                  exit_mode=c.get("exit_mode", "standard"),
                                  gate=g, delay=h,
                                  start_frac=SEARCH_START, end_frac=1.0)
                    if r:
                        r.update({"archetype": ak, "exit_mode": c["exit_mode"],
                                  "gate": g, "delay": h, "tf": tf})
                        res.append(r)
                        if not best or r["pf"] > best["pf"]:
                            best = r
            if best:
                print(f"    {ak:<12} gate={best['gate']} H={best['delay']} "
                      f"PF={best['pf']:.2f}", flush=True)
    return res


def supp_3(all_pc, tf, s2):
    print(f"\n  SUPP-3 EXIT OPT -- {tf}", flush=True)
    by_a = defaultdict(list)
    for r in s2:
        by_a[r["archetype"]].append(r)
    res = []
    for ak, combos in by_a.items():
        combos.sort(key=lambda x: -x["pf"])
        seen = set()
        for c in combos[:5]:
            ck = (tuple(c["kpis"]), tuple(c["pols"]),
                  c.get("gate", "none"), c.get("delay", 1))
            if ck in seen:
                continue
            seen.add(ck)
            best = None
            for em in EXIT_MODES:
                r = sim_combo(all_pc, c["kpis"], c["pols"], tf,
                              exit_mode=em,
                              gate=c.get("gate", "none"),
                              delay=c.get("delay", 1),
                              start_frac=SEARCH_START, end_frac=1.0)
                if r:
                    r.update({"archetype": ak, "exit_mode": em,
                              "gate": c.get("gate", "none"),
                              "delay": c.get("delay", 1), "tf": tf})
                    res.append(r)
                    if not best or r["pf"] > best["pf"]:
                        best = r
            bem = best["exit_mode"] if best else "standard"
            for T, M, K in TMK_GRID:
                r = sim_combo(all_pc, c["kpis"], c["pols"], tf,
                              exit_mode=bem,
                              gate=c.get("gate", "none"),
                              delay=c.get("delay", 1),
                              T_override=T, M_override=M, K_override=K,
                              start_frac=SEARCH_START, end_frac=1.0)
                if r:
                    r.update({"archetype": ak, "exit_mode": bem,
                              "gate": c.get("gate", "none"),
                              "delay": c.get("delay", 1), "tf": tf,
                              "T": T, "M": M, "K": K})
                    res.append(r)
            if best:
                print(f"    {ak:<12} exit={bem} PF={best['pf']:.2f}",
                      flush=True)
    return res


def supp_4(all_pc, tf, s3):
    print(f"\n  SUPP-4 VALIDATION -- {tf}", flush=True)
    val, fail = [], []
    by_a = defaultdict(list)
    for r in s3:
        by_a[r["archetype"]].append(r)
    for ak, combos in by_a.items():
        combos.sort(key=lambda x: -x["pf"])
        seen = set()
        for c in combos[:8]:
            ck = (tuple(c["kpis"]), tuple(c["pols"]))
            if ck in seen:
                continue
            seen.add(ck)
            kw = dict(exit_mode=c.get("exit_mode", "standard"),
                      gate=c.get("gate", "none"),
                      delay=c.get("delay", 1),
                      T_override=c.get("T"), M_override=c.get("M"),
                      K_override=c.get("K"))
            is_r = sim_combo(all_pc, c["kpis"], c["pols"], tf,
                             start_frac=OOS_START, end_frac=OOS_B_START,
                             min_trades=5, **kw)
            oos = sim_combo(all_pc, c["kpis"], c["pols"], tf,
                            start_frac=OOS_B_START, end_frac=1.0,
                            min_trades=3, **kw)
            if not is_r:
                continue
            e = {"tf": tf, "archetype": ak,
                 "kpis": c["kpis"], "pols": c["pols"],
                 "label": c["label"], "gap_fix": True,
                 "IS_trades": is_r["trades"], "IS_hr": is_r["hr"],
                 "IS_pf": is_r["pf"], "IS_avg_ret": is_r["avg_ret"],
                 "IS_pnl": is_r["pnl"]}
            e.update(kw)
            if oos:
                hd = is_r["hr"] - oos["hr"]
                pr = oos["pf"] / is_r["pf"] if is_r["pf"] > 0 else 0
                e.update({"OOS_trades": oos["trades"],
                          "OOS_hr": oos["hr"], "OOS_pf": oos["pf"],
                          "OOS_avg_ret": oos["avg_ret"],
                          "OOS_pnl": oos["pnl"],
                          "OOS_avg_hold": oos["avg_hold"],
                          "OOS_worst": oos["worst"],
                          "hr_decay": round(hd, 1),
                          "pf_ratio": round(pr, 2),
                          "validated": (oos["hr"] >= 50 and hd <= 15
                                        and pr >= 0.5
                                        and oos["trades"] >= 3)})
            else:
                e.update({"OOS_trades": 0, "validated": False})
            (val if e["validated"] else fail).append(e)
            st = "PASS" if e["validated"] else "FAIL"
            print(f"  {st} {ak:<12} IS:HR={is_r['hr']:.1f}% "
                  f"PF={is_r['pf']:.2f} OOS:Tr={e.get('OOS_trades',0)}",
                  flush=True)
    print(f"    {tf} Val:{len(val)} Fail:{len(fail)}", flush=True)
    return val, fail


def write_report(validated, failed, elapsed_min):
    path = SUPP_DIR / "PHASE18_SUPPLEMENT_REPORT.md"
    lines = ["# Phase 18 Supplement -- Mixed-Polarity Gap Fix",
             f"\nRuntime: {elapsed_min:.1f} min",
             f"\n{len(validated)} validated, {len(failed)} failed.", ""]
    if validated:
        n1 = sum(1 for v in validated
                 if v["archetype"] in ("A_trend", "C_breakout", "D_risk"))
        lines += [f"- Gap 1 (dip-in-bull): {n1}",
                  f"- Gap 2 (intermediate perms): {len(validated)-n1}", "",
                  "| # | TF | Arch | Combo | OOS HR | OOS PF | OOS Tr |",
                  "|---|---|---|---|---|---|---|"]
        for i, v in enumerate(
            sorted(validated, key=lambda x: -x.get("OOS_pf", 0)), 1
        ):
            lines.append(
                f"| {i} | {v['tf']} | {v['archetype']} | "
                f"{v['label'][:35]} | {v.get('OOS_hr',0):.1f} | "
                f"{v.get('OOS_pf',0):.2f} | {v.get('OOS_trades',0)} |")
    with open(path, "w") as f:
        f.write("\n".join(lines))
    print(f"  Report: {path}", flush=True)


def main():
    MEM = 70
    t0 = time.time()
    SUPP_DIR.mkdir(parents=True, exist_ok=True)
    print("Phase 18 Supplement -- Mixed-Polarity Gap Fix", flush=True)
    print("=" * 80, flush=True)
    _check_memory("startup", MEM)

    audit_dir = (Path(__file__).resolve().parent
                 / "outputs" / "all" / "phase18" / "step0")
    try:
        with open(audit_dir / "exclusion_pairs.json") as f:
            raw_excl = json.load(f)
    except FileNotFoundError:
        raw_excl = {}

    avail = []
    for tf in ALL_TFS:
        cnt = len(list(ENRICHED_DIR.glob(f"*_{tf}.parquet")))
        mn = 30 if tf in ("1M", "2W") else 50
        if cnt >= mn:
            print(f"  {tf}: {cnt} parquets", flush=True)
            avail.append(tf)
        else:
            print(f"  {tf}: SKIP ({cnt})", flush=True)
    if not avail:
        print("ERROR: No TFs.", flush=True); return

    all_kpis = list(KPI_DIM.keys())
    all_val, all_fail = [], []

    for tf in avail:
        print(f"\n{'#'*80}\n  TF: {tf}\n{'#'*80}", flush=True)
        _check_memory(f"pre {tf}", MEM)
        data = load_data(tf)
        print(f"  {len(data)} stocks", flush=True)
        pc = precompute(data, tf, all_kpis)
        print(f"  {len(pc)} valid", flush=True)
        del data; gc.collect()
        ep = [tuple(p) for p in raw_excl.get(tf, [])]
        elig = [k for k in all_kpis
                if any(k in s["bulls"] for s in pc.values())]

        s1 = supp_1(pc, tf, elig, ep)
        tot = sum(len(c) for ar in s1.values() for c in ar.values())
        if tot == 0:
            print(f"  No hits, skip.", flush=True)
            del pc; gc.collect(); continue
        s2 = supp_2(pc, tf, s1)
        s3 = supp_3(pc, tf, s2)
        v, f = supp_4(pc, tf, s3)
        all_val.extend(v); all_fail.extend(f)
        del pc, s1, s2, s3; gc.collect()
        _check_memory(f"post {tf}", MEM)

    _save_json(SUPP_DIR / "supplement_validated.json", all_val)
    _save_json(SUPP_DIR / "supplement_failed.json", all_fail)
    if all_val:
        fn = ["tf", "archetype", "label", "exit_mode", "gate", "delay",
              "IS_trades", "IS_hr", "IS_pf", "OOS_trades", "OOS_hr",
              "OOS_pf", "OOS_avg_hold", "hr_decay", "pf_ratio",
              "validated", "gap_fix"]
        with open(SUPP_DIR / "supplement_validated.csv", "w",
                  newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fn, extrasaction="ignore")
            w.writeheader(); w.writerows(all_val)

    elapsed = (time.time() - t0) / 60
    write_report(all_val, all_fail, elapsed)
    _save_json(SUPP_DIR / "supplement_summary.json", {
        "total_validated": len(all_val),
        "total_failed": len(all_fail),
        "gap1": sum(1 for v in all_val
                    if v["archetype"] in ("A_trend", "C_breakout", "D_risk")),
        "gap2": sum(1 for v in all_val
                    if v["archetype"] in ("B_dip", "E_mixed")),
        "runtime_min": round(elapsed, 1)})
    print(f"\n{'='*80}", flush=True)
    print(f"COMPLETE -- {elapsed:.1f} min", flush=True)
    print(f"  Val: {len(all_val)}, Fail: {len(all_fail)}", flush=True)
    print(f"  Output: {SUPP_DIR}", flush=True)
    _check_memory("final", MEM)


if __name__ == "__main__":
    main()

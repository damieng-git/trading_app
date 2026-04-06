# Strategy Audit — All 4 Strategies
_Audited: 2026-03-06 | Re-verified: 2026-04-06_

This document covers every confirmed bug, design gap, and inconsistency found across
the four strategies. **Note:** Swing strategy has been removed and replaced by Arch-A
(pullback). Active strategies as of 2026-04-06: `trend`, `dip_buy`, `arch_a`, `stoof`.

---

## Reference: How the Pipeline Works

```
config.json (strategy_setups)
       |
       +-- build_dashboard.py
       |     -- compute_polarity_position_events()  →  position_events_by_strategy[skey]
       |     -- compute_position_events()           →  position_events  (legacy, "Trend" only)
       |     -- compute_stoof_position_events()     →  position_events_by_strategy["stoof"]
       |     -- all written into per-symbol JSON asset files
       |
       +-- screener_builder.py
       |     -- compute_position_status()           →  top-level signal_action, l12m_pnl
       |     -- compute_polarity_position_status()  →  strat_statuses[skey]
       |
       +-- serve_dashboard.py  /api/pnl-summary
       |     -- _compute_pnl_summary(strategy=)     →  P&L tab data (now strategy-aware)
       |
       +-- chart_builder.js (frontend)
             -- reads position_events_by_strategy[activeStrat] for polarity + stoof strategies
             -- falls back to position_events (legacy) for "trend" only
```

---

## 1. TREND Strategy

### BUG-T1: `combos_by_tf` ignored when computing position events
**Status: ✅ FIXED (2026-04-06)**
**File:** `apps/dashboard/build_dashboard.py`

Build pipeline now resolves per-TF combos before flat fallback:
```python
_cbytf = sdef.get("combos_by_tf", {})
combos = _cbytf.get(tf) or sdef.get("combos", {})
```
Frontend and backend now use the same TF-specific combos.

---

### BUG-T2: Overextension filter wider in polarity engine than legacy engine
**Status: ✅ RESOLVED (2026-04-06)**

Resolved by per-strategy `entry_gates` config. Trend has `"overextension": false`,
so the gate never fires regardless of TF. Both engines now check gates via the
`entry_gates` block rather than hardcoded TF conditionals.

---

## 2. BUY DIP Strategy

### BUG-D1: Cross-TF exit is architectural fiction
**Status: ✅ FIXED (2026-04-06)**
**File:** `apps/dashboard/strategy.py`

`exit_tf` is now documented as a config intent marker only. The engine always uses
the entry TF's exit params and data. Buy Dip configured with `entry_tf: "1D"` and
`exit_tf: "1D"` (same). Code comment at relevant line documents this explicitly.

---

### BUG-D2: `build_dashboard.py` ignores `entry_tf`/`exit_tf` when computing events
**Status: ✅ FIXED (2026-04-06)**

Same fix as D1. Build pipeline always passes `tf` (entry TF) to the engine, which
uses it for all parameter lookup. Consistent with screener behaviour.

---

### BUG-D3: Exit combos ignored in "all" mode and JS fallback simulation
**Status: ⚠️ PARTIALLY FIXED**
**File:** `apps/dashboard/static/chart_builder.js`

- "All" mode: **FIXED** — now reads from pre-computed `position_events_by_strategy`.
- JS fallback simulation: **STILL OPEN** — still only reads `cc.c3`/`cc.c4`, never
  reads `exit_combos`. Practical risk is low (only triggers on stale assets), but Buy
  Dip exits in fallback mode will use entry KPIs instead of the correct exit KPIs.

---

### BUG-D4: SMA20>SMA200 gate may block valid dip-buy entries
**Status: ✅ SAFE (2026-04-06)**

Gate is now opt-in per strategy via `entry_gates` config. Buy Dip has
`"sma20_gt_sma200": false` — gate does not fire. Design intent is preserved.

---

## 3. SWING Strategy

### BUG-S1: No `entry_tf` defined — strategy fires on all 5 timeframes
**Status: ⏹️ N/A — Strategy Removed**

Swing strategy removed. Replaced by Arch-A (pullback in uptrend).

---

### BUG-S2: Swing P&L computed on all TFs inflates/deflates metrics
**Status: ⏹️ N/A — Strategy Removed**

---

## 4. STOOF Strategy

### BUG-ST1: Chart shows Trend events, not Stoof
**Status: ✅ FIXED (2026-04-06)**
**File:** `apps/dashboard/static/chart_builder.js`

Fallback to legacy `position_events` is now restricted to `strategy === "trend"` only.
Stoof reads from `position_events_by_strategy["stoof"]` exclusively.

---

### BUG-ST2: No position events computed for Stoof
**Status: ✅ FIXED (2026-04-06)**
**File:** `apps/dashboard/build_dashboard.py`

`compute_stoof_position_events()` is now called in the build pipeline and stored in
`position_events_by_strategy["stoof"]`.

---

### BUG-ST3: No P&L metrics for Stoof anywhere in the system
**Status: ✅ FIXED (2026-04-06)**
**File:** `apps/dashboard/serve_dashboard.py`

`_compute_pnl_summary` now has a dedicated Stoof path that calls
`compute_stoof_position_events` directly.

---

## 5. P&L Calculation Bugs (All Strategies)

### BUG-PL1: C4 trades double-weighted in P&L tab
**Status: ✅ FIXED (2026-04-06)**
**File:** `apps/dashboard/serve_dashboard.py`

`ret_pct` is now stored unweighted in `compute_position_events`. `_compute_pnl_summary`
applies the 1.5× C4 weight exactly once at aggregation. No more 2.25× inflation.

---

### BUG-PL2: C4 trades double-weighted in in-chart equity curve
**Status: ✅ FIXED (2026-04-06)**
**File:** `apps/dashboard/static/chart_builder.js`

`t.ret` (from `ev.ret_pct`) is now unweighted. JS applies `weight = t.scaled ? 1.5 : 1.0`
exactly once when accumulating `cumRet`.

---

### BUG-PL3: P&L tab is not strategy-aware — always shows legacy Trend results
**Status: ✅ FIXED (2026-04-06)**
**File:** `apps/dashboard/serve_dashboard.py`

`_compute_pnl_summary(group, tf, strategy=)` now accepts a strategy parameter.
`/api/pnl-summary` passes the selected strategy from the request, routing to the
appropriate engine (polarity, Stoof, or legacy).

---

### BUG-PL4: Screener top-level `l12m_pnl` is from a 5th unnamed strategy
**Status: ⚠️ STILL OPEN**
**File:** `apps/dashboard/screener_builder.py:133–134`

`compute_position_status` and `compute_trailing_pnl` still use `combo_kpis_by_tf`
(a config section separate from the 4 named strategies). The screener CSV's `l12m_pnl`
column does not correspond to any of `{trend, dip_buy, arch_a, stoof}`.

**Consequence:** The screener's primary P&L ranking metric is a baseline/unnamed
strategy. Named strategy P&L lives only in `strat_statuses[key].l12m_pnl`.

---

## 6. Cross-Cutting Issues

### BUG-CC1: v5 entry gates applied inconsistently across strategies
**Status: ✅ RESOLVED (2026-04-06)**

All v5 gates (SMA20>SMA200, volume spike, overextension, SR break) are now defined
in per-strategy `entry_gates` config blocks. Each strategy opts in or out explicitly:
- Trend: most gates enabled
- Buy Dip / Arch-A / Stoof: all gates disabled (by design)

---

### BUG-CC2: Three separate trade simulation paths produce different results
**Status: ⚠️ PARTIALLY FIXED**

- Python build pipeline and Python screener are now aligned.
- JS client fallback still exists as a backward-compat path (triggers only on stale assets).
  Fallback diverges from build pipeline on: exit combo logic, entry gate logic.
- Low practical risk but eliminates perfect auditability.

---

## Summary Table

| # | Strategy | Severity | Status | Short Description |
|---|----------|----------|--------|-------------------|
| T1 | Trend | High | ✅ FIXED | `combos_by_tf` ignored in build pipeline |
| T2 | Trend | Low | ✅ RESOLVED | Overextension gate — now opt-in per strategy |
| D1 | Buy Dip | High | ✅ FIXED | Cross-TF exit not implemented — now documented as intent marker |
| D2 | Buy Dip | Medium | ✅ FIXED | Build ignores `exit_tf` — now consistent with screener |
| D3 | Buy Dip | Medium | ⚠️ PARTIAL | `exit_combos` ignored in JS fallback only |
| D4 | Buy Dip | Medium | ✅ SAFE | SMA gate disabled for Buy Dip in config |
| S1 | Swing | High | ⏹️ N/A | Strategy removed |
| S2 | Swing | Medium | ⏹️ N/A | Strategy removed |
| ST1 | Stoof | High | ✅ FIXED | Chart no longer falls back to Trend events |
| ST2 | Stoof | High | ✅ FIXED | Position events now computed and stored |
| ST3 | Stoof | Medium | ✅ FIXED | P&L tab now has Stoof-specific path |
| PL1 | All (P&L tab) | High | ✅ FIXED | C4 weight applied once (was 2.25×) |
| PL2 | All (chart) | High | ✅ FIXED | In-chart equity curve also fixed |
| PL3 | All (P&L tab) | High | ✅ FIXED | P&L tab is now strategy-aware |
| PL4 | All (screener) | Medium | ⚠️ OPEN | Screener `l12m_pnl` from unnamed 5th strategy |
| CC1 | All | Medium | ✅ RESOLVED | Gates now per-strategy opt-in |
| CC2 | All | Medium | ⚠️ PARTIAL | JS fallback still diverges from build pipeline |

---

## Remaining Open Items

### Must-fix
- **BUG-PL4** — Decide whether to unify screener `l12m_pnl` with a named strategy or
  explicitly document the unnamed baseline strategy in config.

### Low-risk / deferred
- **BUG-D3 / CC2** — JS fallback `exit_combos` gap. Eliminate the JS fallback entirely
  for polarity strategies (always use pre-computed server-side events), which fixes both.

---
_Last verified against code: 2026-04-06_

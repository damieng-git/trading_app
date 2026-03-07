# Strategy Audit — All 4 Strategies
_Audited: 2026-03-06_

This document covers every confirmed bug, design gap, and inconsistency found across
the four strategies: **Trend**, **Buy Dip**, **Swing**, and **Stoof**.

---

## Reference: How the Pipeline Works

```
config.json (strategy_setups)
       |
       +-- build_dashboard.py
       |     -- compute_polarity_position_events()  →  position_events_by_strategy[skey]
       |     -- compute_position_events()           →  position_events  (legacy, "Trend" only)
       |     -- both written into per-symbol JSON asset files
       |
       +-- screener_builder.py
       |     -- compute_position_status()           →  top-level signal_action, l12m_pnl
       |     -- compute_polarity_position_status()  →  strat_statuses[skey]
       |
       +-- serve_dashboard.py  /api/pnl-summary
       |     -- _compute_pnl_summary()              →  P&L tab data
       |
       +-- chart_builder.js (frontend)
             -- reads position_events_by_strategy[activeStrat] for polarity strategies
             -- falls back to position_events (legacy) otherwise
```

---

## 1. TREND Strategy

### BUG-T1: `combos_by_tf` ignored when computing position events
**File:** `apps/dashboard/build_dashboard.py:1306`
**Severity:** High

```python
combos = sdef.get("combos", {})   # ← always flat combos
```

The backend always uses the strategy's flat top-level `combos` dict. The per-timeframe
`combos_by_tf` block (which has different KPI sets for 4H, 1D, 1W, 2W, 1M) is never read.

**Effect:** All Trend position events on all TFs use the same combos:
- C3: NW Smoother + DEMA + cRSI
- C4: NW Smoother + Stoch_MTM + cRSI + Volume+MA20

...instead of the TF-specific combos defined in `combos_by_tf`, e.g.:
- 1D C3 should be: NW Smoother + Madrid Ribbon + Volume+MA20
- 4H C3 should be: NW Smoother + DEMA + Stoch_MTM

The frontend JS does correctly read `combos_by_tf` (chart_builder.js:722-724), creating a
mismatch: the chart heatmap/score uses the correct TF combo, but trade markers come from
wrong combos computed server-side.

**Contrast:** `compute_polarity_position_status()` in strategy.py correctly resolves
`combos_by_tf` first, but this function is only used by the screener, not by build_dashboard.

**Fix needed:** In build_dashboard.py, resolve `combos_by_tf[tf]` before falling back to
flat `combos`, matching the logic already in `compute_polarity_position_status`.

---

### BUG-T2: Overextension filter wider in polarity engine than legacy engine
**File:** `apps/dashboard/strategy.py:472`
**Severity:** Low-Medium

Legacy `compute_position_events`: overextension filter applied only on `tf == "1W"`.
Polarity `compute_polarity_position_events`: applied on `tf in ("1D", "1W")`.

This means Trend on 1D has an extra entry gate that the legacy engine (used for Stoof
fallback and P&L tab) does not. Makes Trend's actual entry count lower than the comparison
baseline shown in the P&L tab.

---

## 2. BUY DIP Strategy

### BUG-D1: Cross-TF exit is architectural fiction
**File:** `apps/dashboard/strategy.py:651–683`
**Severity:** High (design gap)

The config defines:
```json
"entry_tf": "1D",
"exit_tf": "1W"
```

The stated intent ("1W-governed exit") implies the exit logic should use 1W bar data
and KPI states. However, `compute_polarity_position_status` passes `exit_tf` as the `tf`
parameter to `compute_polarity_position_events`, but **the `df` being passed is the 1D
dataframe** (from the current TF loop). This means:

- 1W exit params (T=2, M=20) are applied to 1D data
- T=2 bars means full invalidation after just **2 daily bars** (should be 4)
- M=20 bars checkpoint triggers after **20 daily bars** (~1 month, not 20 weeks)

There is no mechanism to fetch and join 1W KPI states for exit checking. The `exit_kpis`
field only swaps which indicator set is checked, not which timeframe's data is used.

**Fix needed:** Either (a) implement true cross-TF exit by passing both 1D and 1W dfs to
the engine, or (b) document that "1W-governed" means "uses 1W-style exit KPIs on 1D data"
and configure the exit params accordingly using entry TF params.

---

### BUG-D2: `build_dashboard.py` ignores `entry_tf`/`exit_tf` when computing events
**File:** `apps/dashboard/build_dashboard.py:1317–1320`
**Severity:** Medium

```python
raw = compute_polarity_position_events(
    df_full, kpi_st,
    s_c3_kpis, s_c3_pols,
    s_c4_kpis, s_c4_pols,
    tf,              # ← always the current TF, ignores exit_tf
    exit_kpis=ex_kpis, exit_pols=ex_pols,
)
```

The build pipeline always passes the current iteration's `tf` (e.g. "1D") to the engine.
The `exit_tf="1W"` in the config is completely ignored here. This is inconsistent with
what `compute_polarity_position_status` does (which passes `exit_tf` to the engine).

Result: position events stored in the JSON asset use 1D params (T=4, M=40), while the
screener status computation uses 1W params (T=2, M=20) — the same strategy shows different
trade boundaries depending on whether you're looking at the chart or the screener.

---

### BUG-D3: Exit combos ignored in "all" mode and JS fallback simulation
**File:** `apps/dashboard/static/chart_builder.js:1087–1135`
**Severity:** Medium

When `_activeStrat === "all"`, the client-side trade simulation loop does not read
`exit_combos` from the strategy definition. It uses only the entry combo KPIs for exit
checking. Buy Dip's distinct exit KPI set (NW Smoother + ADX & DI + Stoch_MTM) is silently
dropped in this mode.

Same issue in the fallback JS simulation (line 964–1038): uses `combo3kpis/combo4kpis`
for all exit checks, never reading `exit_combos`.

---

### BUG-D4: SMA20>SMA200 gate may block valid dip-buy entries
**File:** `apps/dashboard/strategy.py:466–469`
**Severity:** Medium (design mismatch)

The polarity engine applies the SMA20>SMA200 structural gate on 1D and 1W. Buy Dip is
designed to catch oversold dips where ADX & DI and WT_LB are bearish. In a valid dip
scenario, SMA20 may be crossing below SMA200 precisely when the entry is most attractive.
The gate that was designed for bullish trend-following strategies filters out the core
use case of dip buying.

---

## 3. SWING Strategy

### BUG-S1: No `entry_tf` defined — strategy fires on all 5 timeframes
**File:** `apps/dashboard/configs/config.json` (Swing definition)
**Severity:** High

Swing has no `entry_tf` field. In `compute_polarity_position_status` and
`compute_polarity_trailing_pnl`, this defaults to:
```python
entry_tf = setup.get("entry_tf", tf)   # → tf (whatever TF is currently processed)
```

Since `entry_tf == tf` is always true, Swing position status and trailing P&L are
computed for all 5 timeframes (4H, 1D, 1W, 2W, 1M). This means:

- Screener shows Swing signals on 4H and 1D charts where the 1W combos are applied
  to shorter timeframe data → spurious/meaningless signals
- Build pipeline generates Swing position events for every TF
- `strat_statuses["swing"]` is populated for all TFs in screener rows

**Fix needed:** Add `"entry_tf": "1W"` to the Swing config. Both `compute_polarity_position_status`
and `compute_polarity_trailing_pnl` already have the guard:
```python
if entry_tf != tf:
    return flat_result / empty
```
...so this single config change will restrict Swing to 1W only.

---

### BUG-S2: Swing P&L computed on all TFs inflates/deflates metrics
**Severity:** Medium (consequence of BUG-S1)

Since Swing runs on all TFs, the `l12m_pnl` inside `strat_statuses["swing"]` is
independent per TF. The screener shows Swing's P&L on 4H rows using 1W combos applied
to 4H bar intervals — those numbers are not meaningful.

---

## 4. STOOF Strategy

### BUG-ST1: Chart shows Trend events, not Stoof
**File:** `apps/dashboard/static/chart_builder.js:953–963`
**Severity:** High (visible, confusing)

When Stoof is selected as active strategy:
- `isPolStrat` is `false` (Stoof is `entry_type: "threshold"`)
- `_useStratEvents` is `false` — no Stoof-specific events exist
- Code falls through to: `else if (data.position_events && ...)` → renders legacy Trend events

The user sees C3/C4 entry/exit markers from the legacy Trend engine while the Stoof
score/heatmap is displayed. This is misleading and creates the illusion that Stoof has
C3/C4 entries, when it doesn't.

---

### BUG-ST2: No position events computed for Stoof
**File:** `apps/dashboard/build_dashboard.py:1303–1305`
**Severity:** High

```python
if sdef.get("entry_type") != "polarity_combo":
    continue   # ← Stoof is skipped entirely
```

No trade simulation is ever run for Stoof. There are no stored position events, no chart
markers specific to Stoof, and no Stoof-specific P&L.

**What Stoof should show:** Entries when the count of bullish KPIs among the 10 Stoof KPIs
crosses the threshold of 7. Currently undefined/unimplemented.

---

### BUG-ST3: No P&L metrics for Stoof anywhere in the system
**Severity:** Medium

- Screener top-level `l12m_pnl` uses the legacy engine (not Stoof threshold logic)
- `strat_statuses["stoof"]` is never populated (filtered out by entry_type check)
- P&L tab uses legacy engine regardless of strategy selection
- There is no `compute_stoof_position_events` function or equivalent

---

## 5. P&L Calculation Bugs (All Strategies)

### BUG-PL1: C4 trades double-weighted in P&L tab
**File:** `apps/dashboard/serve_dashboard.py:806–808`
**Severity:** High (corrupts P&L numbers)

`compute_position_events` stores `ret_pct` already multiplied by `weight` (1.5x for C4):
```python
weight = 1.5 if scaled else 1.0
ret_pct = (((xp - entry_price) / entry_price - cost) * 100 * weight   # ← 1.5x baked in
```

Then `_compute_pnl_summary` re-applies the weight:
```python
pnls = [e["ret_pct"] for e in closed]               # already 1.5x for C4
weighted_pnls = [p * (1.5 if e.get("scaled") else 1.0) ...]   # 1.5x applied again → 2.25x
total_ret = sum(weighted_pnls)
```

And for individual trades sent to the frontend:
```python
"ret": round(e["ret_pct"] * w, 2)   # 2.25x again
```

**Effect:** Every C4 trade's P&L contribution is 2.25× what it should be in the P&L tab
aggregate stats (Return, Sharpe, equity curve, best/worst, profit factor).

---

### BUG-PL2: C4 trades double-weighted in in-chart equity curve
**File:** `apps/dashboard/static/chart_builder.js:1616–1627`
**Severity:** High

When using pre-computed events (polarity strategies):
```js
const ret = ev.ret_pct;    // already includes 1.5x from Python
...
cumRet += t.ret * weight;  // weight = 1.5 for C4 → 2.25x total
```

For strategies using legacy events (`data.position_events`), the same double-weight applies.
The unrealised P&L calculation during a trade (`(close[i] - entryPrice) / entryPrice * weight`)
is computed correctly since it doesn't use `ret_pct`, but the cumulative realised return is wrong.

---

### BUG-PL3: P&L tab is not strategy-aware — always shows legacy Trend results
**File:** `apps/dashboard/serve_dashboard.py:761,795`
**Severity:** High

`_compute_pnl_summary` always calls `compute_position_events` (the legacy bullish-only
engine) with `combo_kpis_by_tf` combos. There is no way for the P&L tab to show results
for Buy Dip, Swing, or Stoof. The currently selected strategy has no effect on the P&L tab.

---

### BUG-PL4: Screener top-level `l12m_pnl` is from a 5th unnamed strategy
**File:** `apps/dashboard/screener_builder.py:133–134`
**Severity:** Medium

```python
pos_status = compute_position_status(df, st, c3_kpis, c4_kpis, tf)
trailing_pnl = compute_trailing_pnl(df, st, c3_kpis, c4_kpis, tf)
```

`c3_kpis`/`c4_kpis` come from `combo_kpis_by_tf` (a separate config section, not any of
the 4 `strategy_setups`). These are effectively a standalone unnamed strategy used as the
screener's primary signal and P&L metric. The named strategies' P&L values live in
`strat_statuses[key].l12m_pnl` only.

This creates confusion: the screener's `l12m_pnl` column (exported in CSV too) does not
correspond to any of the 4 strategies visible in the dashboard.

---

## 6. Cross-Cutting Issues

### BUG-CC1: v5 entry gates applied inconsistently across strategies
**Severity:** Medium

The v5 gates (SMA20>SMA200, volume spike 1.5×, 1W overextension) are:
- Applied in `compute_position_events` (legacy): SMA on 1D/1W, overext on 1W only
- Applied in `compute_polarity_position_events` (polarity): SMA on 1D/1W, overext on 1D+1W
- Applied in JS "all" mode simulation: SMA on 1D/1W, overext on 1W only (matches legacy)

These gates make sense for bullish trend-following (Trend) but are questionable for
Buy Dip (designed to enter when momentum is weak) and irrelevant by design for Stoof
(threshold-based) and arguably for Swing (1W only, SMA gate may be overly restrictive
at weekly level).

---

### BUG-CC2: Three separate trade simulation paths produce different results
**Severity:** Medium

For any given symbol+TF, trades can be computed by:
1. **Python build pipeline** (`compute_polarity_position_events`) → stored in JSON assets
2. **Python screener** (`compute_polarity_position_status` with `scan_start=n-500`) → screener status
3. **JS client fallback** (if asset is missing or Stoof selected) → client-side simulation

These three paths use the same exit logic but can diverge due to:
- `scan_start=n-500` truncation in screener (may miss earlier open position)
- Different overextension filter scope (1W-only vs 1D+1W)
- JS fallback uses flat combos from `data.combo_3_kpis` (may differ from strategy combos)
- JS fallback ignores `exit_kpis` entirely

---

## Summary Table

| # | Strategy | Severity | Category | Short Description |
|---|----------|----------|----------|-------------------|
| T1 | Trend | High | Wrong combos | `combos_by_tf` ignored in build pipeline |
| T2 | Trend | Low | Inconsistency | Overextension gate wider in polarity engine |
| D1 | Buy Dip | High | Design gap | Cross-TF exit not implemented — 1W params on 1D data |
| D2 | Buy Dip | Medium | Inconsistency | Build ignores `exit_tf`, screener uses it |
| D3 | Buy Dip | Medium | Missing logic | `exit_combos` ignored in JS "all" mode and fallback |
| D4 | Buy Dip | Medium | Design mismatch | SMA gate blocks valid dip entries |
| S1 | Swing | High | Wrong scope | No `entry_tf` → fires on all 5 TFs |
| S2 | Swing | Medium | Data quality | Swing P&L on non-1W TFs is meaningless |
| ST1 | Stoof | High | Wrong display | Chart shows Trend events, not Stoof |
| ST2 | Stoof | High | Missing | No position events computed for Stoof |
| ST3 | Stoof | Medium | Missing | No P&L metrics for Stoof anywhere |
| PL1 | All (P&L tab) | High | Wrong numbers | C4 trades double-weighted (2.25× actual) |
| PL2 | All (chart) | High | Wrong numbers | In-chart equity curve also double-weighted for C4 |
| PL3 | All (P&L tab) | High | Wrong display | P&L tab always shows legacy Trend, ignores selection |
| PL4 | All (screener) | Medium | Confusion | Screener `l12m_pnl` is from unnamed 5th strategy |
| CC1 | All | Medium | Inconsistency | v5 entry gates applied selectively/wrongly per strategy |
| CC2 | All | Medium | Inconsistency | 3 simulation paths diverge (build / screener / JS) |

---

## Recommended Fix Order

### Phase 1 — High-impact, low-risk (no architecture change)
1. **BUG-S1**: Add `"entry_tf": "1W"` to Swing config (1-line fix)
2. **BUG-T1**: Fix `build_dashboard.py` to resolve `combos_by_tf[tf]` before flat combos
3. **BUG-PL1/PL2**: Fix double-weight: either remove weight from `ret_pct` in strategy.py,
   or remove re-weighting from `_compute_pnl_summary` and `buildFigureFromData`
4. **BUG-ST1**: Suppress Trend event fallback when Stoof is active in chart_builder.js

### Phase 2 — Moderate effort, targeted fixes
5. **BUG-PL3**: Make P&L tab strategy-aware (pass selected strategy to `/api/pnl-summary`,
   use `compute_polarity_position_events` for polarity strategies)
6. **BUG-D2**: Make build pipeline consistent with screener on `exit_tf` usage
7. **BUG-D3**: Apply `exit_combos` in JS "all" mode and fallback simulation
8. **BUG-CC2**: Eliminate JS fallback simulation for polarity strategies
   (always use pre-computed server-side events)

### Phase 3 — Architecture decisions required
9. **BUG-D1**: Define what "cross-TF exit" actually means and implement it properly
   (true 1W bar gating vs. same-TF exit with 1W-style params)
10. **BUG-D4**: Decide whether SMA gate applies to Buy Dip; if not, add per-strategy
    gate config or exclude dip_buy from SMA check
11. **BUG-ST2/ST3**: Design and implement Stoof position model (threshold onset detection,
    exit logic, P&L accounting)
12. **BUG-PL4**: Decide whether to unify screener `l12m_pnl` with named strategy P&L,
    or document the unnamed screener strategy explicitly

---
_Generated by strategy audit — no code was modified_

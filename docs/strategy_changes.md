# Strategy Changes — Phase 1 / 2 / 3

This document records every code change made during the strategy audit and fix effort.
See `docs/strategy_audit.md` for the original bug catalogue and design rationale.

---

## Files Modified

| File | Bugs Fixed |
|---|---|
| `apps/dashboard/configs/config.json` | BUG-S1, BUG-D1, BUG-D4 |
| `apps/dashboard/strategy.py` | BUG-PL1, BUG-D1, BUG-D2, BUG-D4, BUG-ST2 (new functions) |
| `apps/dashboard/build_dashboard.py` | BUG-T1, BUG-D4, BUG-ST2 |
| `apps/dashboard/screener_builder.py` | BUG-ST3, BUG-PL4 |
| `apps/dashboard/serve_dashboard.py` | BUG-PL3 |
| `apps/dashboard/static/chart_builder.js` | BUG-ST1, BUG-PL2, BUG-CC2, BUG-D3 |
| `apps/dashboard/static/dashboard_pnl.js` | BUG-PL3 |

---

## Phase 1 — Strategy Definition Correctness

### BUG-T1 — Trend ignored `combos_by_tf` in build pipeline
**File:** `apps/dashboard/build_dashboard.py`

**Before:** `combos = sdef.get("combos", {})` — always used the flat fallback combos, ignoring the per-TF override.

**After:**
```python
_cbytf = sdef.get("combos_by_tf", {})
combos = _cbytf.get(tf) or sdef.get("combos", {})
```
Trend now uses the correct KPI set per timeframe (e.g. 1D uses NW+Madrid+Volume, 4H uses NW+DEMA+Stoch_MTM).

---

### BUG-S1 — Swing strategy missing `entry_tf`/`exit_tf`
**File:** `apps/dashboard/configs/config.json`

**Before:** Swing had no `entry_tf`/`exit_tf` fields.

**After:** Added `"entry_tf": "1W"` and `"exit_tf": "1W"` to the swing setup so the engine selects the correct exit parameters (T=2, M=20) instead of defaulting to an undefined timeframe.

---

### BUG-D1 — Buy Dip cross-TF exit applied wrong exit params
**File:** `apps/dashboard/configs/config.json`, `apps/dashboard/strategy.py`

**Config change:** Changed dip_buy `"exit_tf": "1W"` → `"exit_tf": "1D"`. Updated description to clarify that `exit_tf` is informational — the engine always runs on entry-TF data and params.

**Strategy change (`compute_polarity_trailing_pnl`, `compute_polarity_position_status`):**
- Changed `exit_tf = setup.get("exit_tf", tf)` → engine now always uses the caller's `tf` for lookback and engine dispatch.
- Exit params (`T`, `M`, `K`) are always sourced from `EXIT_PARAMS[tf]` (entry TF), not from `exit_tf`.

---

### BUG-D2 — `compute_polarity_position_status` used `exit_tf` for engine call
**File:** `apps/dashboard/strategy.py`

**Before:** `compute_polarity_position_events(df, st, ..., tf=exit_tf, ...)`

**After:** Engine always called with `tf=tf` (entry TF). `exit_tf` is no longer passed to the engine.

---

### BUG-D4 — Buy Dip and Swing had entry gates active unintentionally
**File:** `apps/dashboard/configs/config.json`, `apps/dashboard/strategy.py`, `apps/dashboard/build_dashboard.py`

**Config change:** Added `entry_gates` block to both `dip_buy` and `swing`:
```json
"entry_gates": {
  "sma20_gt_sma200": false,
  "volume_spike": false,
  "overextension": false
}
```
These strategies should not require bullish SMA trend or volume confirmation — they work on oversold/momentum setups independently.

**Strategy change:** `compute_polarity_position_events` and `compute_polarity_position_status` now accept and respect `entry_gates: dict | None`. Each gate defaults to `True` (active) if not present, so existing Trend behavior is unchanged.

**Build change:** `build_dashboard.py` reads `_gates = sdef.get("entry_gates")` and passes `entry_gates=_gates` to `compute_polarity_position_events`.

---

## Phase 2 — P&L Calculation Fixes

### BUG-PL1 — `ret_pct` stored with C4 weight baked in (double-weight bug)
**File:** `apps/dashboard/strategy.py`

**Before (both engines):**
```python
ret_pct = (exit_price / entry_price - 1 - cost) * 100 * weight  # weight=1.5 for C4
```

**After:**
```python
ret_pct = (exit_price / entry_price - 1 - cost) * 100  # weight-free; consumers apply it
```

All consumers (`compute_trailing_pnl`, `compute_polarity_trailing_pnl`, `serve_dashboard.py` summary, `chart_builder.js` simulateTrades) already applied `* (scaled ? 1.5 : 1.0)` separately, so removing weight from the source eliminates the 2.25× effective weight on C4 trades.

---

### BUG-PL2 — `simulateTrades` in `chart_builder.js` applied weight to `ret`
**File:** `apps/dashboard/static/chart_builder.js`

**Before:** `const ret = ep > 0 ? ((xp - ep) / ep - cost) * 100 * weight : 0`

**After:** `const ret = ep > 0 ? ((xp - ep) / ep - cost) * 100 : 0`

Weight is applied downstream when accumulating into the running P&L, not inside the trade return calculation.

---

### BUG-PL3 — P&L tab always showed legacy strategy P&L regardless of selected strategy
**File:** `apps/dashboard/serve_dashboard.py`, `apps/dashboard/static/dashboard_pnl.js`

**Serve change:**
- `_compute_pnl_summary(group, tf)` → `_compute_pnl_summary(group, tf, strategy="legacy")`
- Routes internally: polarity strategies call `compute_polarity_position_events`, stoof calls `compute_stoof_position_events`, default calls `compute_position_events`.
- API endpoint reads `pnl_strategy = qs.get("strategy", ["legacy"])[0]` and passes it through; cache key is `f"{group}|{tf}|{pnl_strategy}"`.

**JS change (`dashboard_pnl.js`):**
- Added `_pnlCacheStrategy` variable in `buildPnlTab`.
- Reads `window.currentStrategy` and appends `&strategy=...` to the API URL so the server returns strategy-specific P&L data.

---

### BUG-PL4 — Screener `l12m_pnl` column used unnamed fallback combo, not any named strategy
**File:** `apps/dashboard/screener_builder.py`

**Before:** The top-level `trailing_pnl` used `combo_kpis_by_tf` (an unnamed config key that doesn't correspond to any strategy setup). Trend's real P&L was buried in `strat_statuses["trend"]` but never promoted.

**After:** After the strategy loop, if `"trend"` is present in `strat_statuses`, overwrite the top-level screener `trailing_pnl` with Trend's l12m values:
```python
if "trend" in strat_statuses:
    _ts = strat_statuses["trend"]
    trailing_pnl = {
        "l12m_pnl": _ts["l12m_pnl"],
        "l12m_trades": _ts["l12m_trades"],
        "l12m_hit_rate": _ts["l12m_hit_rate"],
    }
```

---

## Phase 3 — Stoof Strategy Fixes

### BUG-ST1 — Stoof chart showed Trend C3/C4 events instead of Stoof events
**File:** `apps/dashboard/static/chart_builder.js`

**Before:** The event-rendering cascade fell through to `data.position_events` (Trend pre-computed events) when `isPolStrat` was false (which Stoof is).

**After:** Added `!isStoof` guard so Trend events are only used when the active strategy is actually Trend:
```js
} else if (!isStoof && data.position_events && ...) {
```

Same guard applied in `simulateTrades`: Stoof events are sourced from `_peByStrat2["stoof"]`; the Trend fallback is only used when `!_isStoof2`.

---

### BUG-ST2 — Stoof position events never computed or stored in JSON assets
**File:** `apps/dashboard/strategy.py`, `apps/dashboard/build_dashboard.py`

**Strategy additions:**
- `compute_stoof_position_events(df, st, stoof_kpis, threshold, tf, *, scan_start=None)` — threshold-onset entry detection (bullish count ≥ threshold), ATR stop (K × ATR14), two-stage exit (lenient: count < threshold; strict: count ≤ threshold-2), M-bar checkpoint trailing stop.
- `compute_stoof_trailing_pnl(df, st, stoof_kpis, threshold, tf)` — L12M P&L for Stoof using the same event engine.

**Build change:** Added Stoof event computation block after the polarity strategy loop:
```python
stoof_kpis = [k for k in get_kpi_trend_order() if k in df.columns]
stoof_events = compute_stoof_position_events(df, st, stoof_kpis, threshold=7, tf=tf)
pos_events_by_strategy["stoof"] = stoof_events
```
Events are stored in the per-symbol JSON asset under `position_events_by_strategy.stoof`.

---

### BUG-ST3 — Stoof had no P&L in screener
**File:** `apps/dashboard/screener_builder.py`

**Before:** The strategy loop only handled `entry_type == "polarity_combo"`. Stoof (`entry_type == "threshold"`) was skipped entirely, leaving it with no `strat_statuses` entry and no l12m P&L.

**After:** Added `elif entry_type == "threshold":` branch that calls `compute_stoof_trailing_pnl` and stores the result in `strat_statuses[skey]`.

---

### BUG-CC2 — "all" strategy mode re-simulated client-side even when server events existed
**File:** `apps/dashboard/static/chart_builder.js`

**Before:** In "all" mode, `allKpiZ` was always used for client-side re-simulation, overwriting pre-computed server events.

**After:** Client-side re-simulation only runs when server has not provided pre-computed events:
```js
if (_activeStrat === "all" && Object.keys(allKpiZ).length && Object.keys(_peByStrat).length === 0) {
```
When server events exist, they are used directly — no re-simulation.

---

### BUG-D3 — "all" mode exit check ignored `exit_combos` for Buy Dip
**File:** `apps/dashboard/static/chart_builder.js`

**Before:** In "all" mode loop, exit checking always used the entry KPIs (`_sAk/_sAp`). Buy Dip has a separate `exit_combos` set but it was never consulted.

**After:** Added exit_combos support in the "all" mode loop:
- Reads `sdef.exit_combos` → derives `_sExKpis`/`_sExPols`.
- When `_sExKpis` is non-empty, exit checking uses those KPIs instead of `_sAk/_sAp`.
- On C4 scale-up, exit KPIs are updated to the C4 entry KPIs if no dedicated exit_combos exist.

---

## Summary

| Bug | Category | Severity | Status |
|---|---|---|---|
| BUG-T1 | Trend combos_by_tf ignored | High | Fixed |
| BUG-S1 | Swing missing entry/exit TF | Medium | Fixed |
| BUG-D1 | Buy Dip cross-TF exit params | High | Fixed |
| BUG-D2 | Polarity status used exit_tf | High | Fixed |
| BUG-D4 | Unwanted entry gates on Dip/Swing | Medium | Fixed |
| BUG-PL1 | ret_pct double-weighted | Critical | Fixed |
| BUG-PL2 | simulateTrades applied weight twice | Critical | Fixed |
| BUG-PL3 | P&L tab strategy-unaware | High | Fixed |
| BUG-PL4 | Screener l12m used wrong strategy | High | Fixed |
| BUG-ST1 | Stoof showed Trend events | High | Fixed |
| BUG-ST2 | Stoof events never computed | High | Fixed |
| BUG-ST3 | Stoof no screener P&L | Medium | Fixed |
| BUG-CC2 | "all" mode re-sim discarded server events | Medium | Fixed |
| BUG-D3 | exit_combos ignored in "all" mode | Medium | Fixed |

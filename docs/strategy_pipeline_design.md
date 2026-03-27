# Strategy Pipeline Design
**Date:** 2026-03-17
**Status:** Implemented. Full build verified on staging 2026-03-17 (186 symbols × 5 TFs).
**Context:** Audit revealed that strategies are computed by mixed engines and follow divergent JS render paths. This document defines the target single-engine, single-render-path design and the gaps that must be closed to reach it.

---

## 1. Design Goals

1. **One engine per `entry_type`** — all strategies of the same type go through the same Python function.
2. **Asset is the single source of truth** — JS never recomputes positions or combo states. If the asset is stale, the UI says so.
3. **Config-driven, not code-driven** — adding a new strategy requires only a `config.json` edit (and a new engine function only if it introduces a new `entry_type`).
4. **Zero strategy-specific branches in JS** — chart_builder reads `position_events_by_strategy[activeStrategy]` regardless of which strategy is active.

---

## 2. Target Pipeline (Logigram)

```
╔══════════════════════════════════════════════════════════════════╗
║                    config.json                                   ║
║  strategy_setups:                                                ║
║    dip_buy  → entry_type: polarity_combo, combos, entry_gates    ║
║    swing    → entry_type: polarity_combo, combos_by_tf           ║
║    trend    → entry_type: polarity_combo, combos_by_tf           ║
║    stoof    → entry_type: threshold, score_kpis, exit_threshold  ║
║    new_xyz  → entry_type: ???  (add engine once, reuse forever)  ║
╚══════════════════╦═══════════════════════════════════════════════╝
                   │  single config read at startup
                   ▼
╔══════════════════════════════════════════════════════════════════╗
║              build_dashboard.py  (dispatcher only)               ║
║                                                                  ║
║  kpi_states ← enrichment pipeline  (1 pass, shared by ALL)      ║
║                                                                  ║
║  for each strategy in config.strategy_setups:                    ║
║    entry_type == "polarity_combo"                                ║
║      └─► compute_polarity_position_events(kpi_states, sdef)     ║
║    entry_type == "threshold"                                     ║
║      └─► compute_stoof_position_events(kpi_states, sdef)        ║
║    entry_type == "new_type"   ← add ONE dispatch line here       ║
║      └─► compute_new_type_events(kpi_states, sdef)              ║
║                                                                  ║
║  → position_events_by_strategy[skey]  (every strategy)          ║
║  → c3_states_by_strategy[skey]        (every strategy)          ║
╚══════════════════╦═══════════════════════════════════════════════╝
                   │  write once per symbol × TF
                   ▼
╔══════════════════════════════════════════════════════════════════╗
║           JSON asset  (single source of truth)                   ║
║                                                                  ║
║  kpi:        { z, kpis }          ← raw KPI timeline            ║
║  sma20_vals, sma200_vals          ← price overlays              ║
║                                                                  ║
║  position_events_by_strategy: {                                  ║
║    dip_buy: [...],  swing: [...],                                ║
║    trend:   [...],  stoof: [...],  new_xyz: [...]                ║
║  }                                                               ║
║                                                                  ║
║  c3_states_by_strategy: {                                        ║
║    dip_buy: { c3:[bool,...], c4:[bool,...] },                    ║
║    swing:   { c3:[...],      c4:[...]      },  ...               ║
║  }                                                               ║
║                                                                  ║
║  ✗  NO  position_events          (legacy field — to be removed)  ║
║  ✗  NO  combo_3_kpis/combo_4_kpis  (legacy — to be removed)     ║
╚══════════════════╦═══════════════════════════════════════════════╝
                   │  read once on symbol load
                   ▼
╔══════════════════════════════════════════════════════════════════╗
║          chart_builder.js  (pure renderer — zero logic)          ║
║                                                                  ║
║  activeStrategy ← UI selector (trend/dip_buy/swing/stoof/all)   ║
║                                                                  ║
║  events = asset.position_events_by_strategy[activeStrategy]      ║
║  c3     = asset.c3_states_by_strategy[activeStrategy]            ║
║                                                                  ║
║  if (!events) → banner: "asset stale — click Rebuild"           ║
║  else         → render trades, combo row, score bar              ║
║                                                                  ║
║  ✗  NO client-side position reconstruction                       ║
║  ✗  NO comboBool() fallback                                      ║
║  ✗  NO hardcoded entry gates                                      ║
║  ✗  NO strategy-specific if/else branches                        ║
╚══════════════════════════════════════════════════════════════════╝
```

---

## 3. Adding a New Strategy

In the target design, adding a strategy is **config-only** unless it introduces a new `entry_type`.

### Case A — same `entry_type` as an existing strategy (most common)

```
1. config.json   → add a new block under strategy_setups with the desired combos,
                   entry_gates, exit_combos, timeframe, weights
2. rebuild assets → python -m trading_dashboard dashboard build
3. JS             → zero changes required
```

### Case B — new `entry_type`

```
1. strategy.py          → add compute_<type>_position_events(df, kpi_states, sdef)
2. build_dashboard.py   → add 1 dispatch line in the strategy loop
3. config.json          → add strategy_setup block with entry_type: "<type>"
4. rebuild assets
5. JS                   → zero changes required
```

### What never changes when adding a strategy

- `chart_builder.js` — reads `position_events_by_strategy[activeStrategy]` generically
- `data_exporter.py` — already writes all strategies from the dict
- `screener_builder.py` — already iterates `strategy_setups` generically
- `dashboard.js` — strategy pills are built from `strategy_setups` keys at runtime
- `dashboard_screener.js` — `STRAT_PRIO` built at init from `STRATEGY_SETUPS.setups` filtered by `badge_prio`; `strat_any` filter iterates `STRAT_PRIO` generically

### Badge display config (required fields per strategy)

| Field | Type | Purpose |
|---|---|---|
| `badge_prio` | int | Display priority in Action column (lower = shown first when multiple active). Omit to hide from Action badge entirely. |
| `badge_label` | str | Short label shown in badge, e.g. `"D"`, `"St"`. Falls back to strategy key if omitted. |
| `color` | hex str | Badge tint colour. |

---

## 4. Engine Responsibilities

### `compute_polarity_position_events(df, kpi_states, sdef, tf)`
- Used by: `dip_buy`, `swing`, `trend`
- Entry logic: C3 onset (all KPIs match expected polarity) + configurable entry gates
- Exit logic: ATR stop, full C3 invalidation within T bars, 2/N KPIs turning after T bars, M-bar checkpoint trailing stop
- Per-strategy config via `sdef`: `combos`, `combos_by_tf`, `entry_gates`, `exit_combos`, `exit_params`

### `compute_stoof_position_events(df, kpi_states, sdef, tf)`
- Used by: `stoof`
- Entry logic: score threshold (N of M KPIs bullish) + MACD_BL mandatory gate
- Exit logic: score drops below exit threshold OR ATR stop OR any 1 score KPI turns red
- Per-strategy config via `sdef`: `score_kpis`, `entry_threshold`, `exit_threshold`, `scale_kpi`

### `compute_c3_states_by_strategy(df, kpi_states, strategy_setups, tf)`
- Shared utility, not an engine
- Produces per-bar boolean arrays for C3/C4 used by the combo heatmap row in the chart
- Called once after all position events are computed, for all strategies in one pass

---

## 5. Current State vs Target

| Component | Target | Current State | Gap |
|---|---|---|---|
| Python engines | 1 per entry_type | `compute_position_events` (legacy) still runs alongside polarity engine for trend | Legacy engine must be removed |
| JSON asset | `position_events_by_strategy` only | Both `position_events` (legacy) and `position_events_by_strategy` written | Remove legacy field write |
| JS position rendering | Reads pre-computed events only | Fallback simulation at `chart_builder.js:1029–1103` can reconstruct client-side | Remove fallback blocks |
| JS "all" mode | Reads `position_events_by_strategy` for each strategy | Re-simulates trades at `chart_builder.js:1109–1212`, missing per-strategy gates | Replace with pre-computed read |
| Per-strategy entry gates | Config-driven, respected everywhere | Python respects them; JS fallback hardcodes gates always-on | Remove JS fallback |
| Stale asset UX | Banner: "rebuild required" | Silent wrong data (legacy Trend events shown) | Add stale-asset banner |
| `c3_states_by_strategy` | Used by combo heatmap row | Computed and exported but never read in JS | Wire up in JS or remove |
| Score bar weights | Same rule for all strategies | Legacy uses `kpi_weights`; polarity strategies use equal weight | Standardise |
| Screener `STRAT_PRIO` | Config-driven from `badge_prio` | Hardcoded 3-entry JS array (dip_buy/swing/trend); stoof missing | ✓ Closed 2026-03-17 |

---

## 6. Gaps Closed (2026-03-17)

### P1 — Legacy engine removed from build pipeline ✓
**Files:** `build_dashboard.py`
- `compute_position_events()` call removed; `pos_events` variable eliminated
- `position_events=` argument removed from `export_symbol_data_json()`
- `trend` fully handled by `compute_polarity_position_events` via `strategy_setups`
- Asset verified: `position_events` field absent, `position_events_by_strategy` contains all strategies

### P2 — JS fallback simulation removed ✓
**File:** `chart_builder.js`
- ~75-line single-strategy client-side reconstruction block removed
- ~103-line "all" mode re-simulation block removed
- Replaced with 3-case dispatch: pre-computed events → render; "all" overlay → loop `_peByStrat`; missing → `_showStaleToast()`
- "all" overlay no longer references legacy `data.position_events`

### P3 — `c3_states_by_strategy` wired in JS ✓
**File:** `chart_builder.js` (implemented in §28, prior to this change)
- Combo heatmap row reads `c3_states_by_strategy[activeStrategy].c3/.c4` from asset
- Falls back to `comboBool()` only for combo heatmap row on pre-§28 stale assets (acceptable — the row is a visualisation, not a trade)

### P4 — Score bar weights standardised ✓
**File:** `chart_builder.js`
- All strategies now use `kpi_weights[k] ?? 1` — no strategy-specific branch
- Resolved: polarity/stoof strategies previously used equal weight while legacy path used config weights

### P5 — Screener `STRAT_PRIO` made config-driven ✓
**Files:** `apps/dashboard/configs/config.json`, `apps/dashboard/static/dashboard_screener.js`

**Problem:** `STRAT_PRIO` in `dashboard_screener.js` was a hardcoded 3-entry array `[dip_buy, swing, trend]`.
Stoof was absent from this list, so even though `strat_statuses.stoof.signal_action = "HOLD"` was correctly
stored in `screener_summary.json`, the Action column badge fell through to the v6 fallback and showed "—".

**Fix:**
- Added `badge_prio` (int) and `badge_label` (str) to each strategy in `config.json → strategy_setups`
- `STRAT_PRIO` is now built at init from `STRATEGY_SETUPS.setups`, filtered by `badge_prio`, sorted ascending
- `strat_any` screener filter now iterates `STRAT_PRIO` instead of a hardcoded key list
- Adding a new strategy to the Action badge = set `badge_prio` + `badge_label` in `config.json`. No JS change.

**Current badge priorities:**

| Strategy | `badge_prio` | `badge_label` |
|---|---|---|
| dip_buy | 1 | D |
| swing | 2 | S |
| trend | 3 | T |
| stoof | 4 | St |

---

### P6 — Entry gates unified across scan and screener ✓
**Date:** 2026-03-18
**Files:** `apps/dashboard/configs/config.json`, `apps/screener/scan_enrichment.py`, `apps/screener/scan_strategy.py`, `apps/dashboard/strategy.py`

**Problem:** Two separate config fields (`scan_filters` and `entry_gates`) controlled entry conditions for the same strategies, but with different values and different code paths. `scan_filters` was read only by the scan pipeline; `entry_gates` only by the screener's position engine. They were inconsistent:

| Strategy | `scan_filters` (scan) | `entry_gates` (screener) |
|---|---|---|
| dip_buy | all false | all false (aligned, but `sr_break` missing) |
| swing | sma20=T, vol=T, sr_break=T | all false — **completely misaligned** |
| trend | sma20=T, vol=T, sr_break=T | not set → defaults all True (including overextension) |
| stoof | `{}` | not set |

The screener's position engine defaulted unset gate keys to `True` (opt-out model), so any strategy without explicit `entry_gates` had all gates silently enabled. Specifically: `trend` had no `entry_gates`, so overextension was always on in the screener but never checked in the scan. This caused scan results (e.g., OSIS on 1W) to not appear as ENTRY in the screener's strategy column.

**Fix:**
- Removed `scan_filters` from all strategies in `config.json` — it is now dead config
- `entry_gates` is the single source of truth for both pipelines, with 4 explicit keys per strategy: `sma20_gt_sma200`, `volume_spike`, `sr_break`, `overextension`
- All gate defaults changed from `True` → `False` in `strategy.py` (opt-in model — a missing key no longer silently enables a gate)
- `sr_break` gate added to `strategy.py` position engine (was scan-only; now both pipelines enforce it)
- `overextension` gate added to `scan_enrichment.py` `check_quality_gates_raw` / `check_quality_gates` (was screener-only; now both pipelines enforce it)
- `scan_strategy.py` reads `entry_gates` instead of `scan_filters` everywhere

**Resulting gate config:**

| Strategy | `sma20_gt_sma200` | `volume_spike` | `sr_break` | `overextension` |
|---|---|---|---|---|
| dip_buy | false | false | false | false |
| swing | true | true | true | false |
| trend | true | true | true | false |
| stoof | false | false | false | false |

**Invariant going forward:** scan and screener will produce identical entry signals on the last bar for any strategy, because they read the same gate config and execute equivalent gate logic.

---

## 7. Related Documents

- `docs/strategy_audit.md` — per-strategy bug list (T1–CC2), audited 2026-03-06
- `docs/architecture_audit.md` — 30 architecture recommendations, audited 2026-03-11
- `CLAUDE.md` — strategy engine overview, entry/exit logic summary, config schema

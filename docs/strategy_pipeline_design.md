# Strategy Pipeline Design
**Date:** 2026-03-17
**Status:** Target architecture — not yet fully implemented
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

---

## 6. Gaps to Close (Ordered by Impact)

### P1 — Remove legacy engine from build pipeline
**Files:** `build_dashboard.py`, `data_exporter.py`
- Remove the `compute_position_events()` call (legacy bullish-only engine)
- Stop writing `position_events` (flat, non-strategy-scoped) to the JSON asset
- Ensure `trend` is fully handled by `compute_polarity_position_events` via `strategy_setups`

### P2 — Remove JS fallback simulation
**File:** `chart_builder.js:1029–1103` and `1109–1212`
- Delete both blocks
- If `position_events_by_strategy[activeStrategy]` is missing or empty, show a "asset stale — click Rebuild" banner
- The "all" strategy overlay must read from `position_events_by_strategy`, not re-simulate

### P3 — Wire up `c3_states_by_strategy` in JS
**File:** `chart_builder.js`
- Replace the current combo heatmap row logic (which reads `combo_3_kpis`/`combo_4_kpis`) with `c3_states_by_strategy[activeStrategy].c3` and `.c4`
- Remove `combo_3_kpis`/`combo_4_kpis` from the JSON asset export once JS is updated

### P4 — Standardise score bar weights
**Files:** `chart_builder.js`, `templates.py`
- Decide on one rule: either always use `kpi_weights` from config, or always use equal weights
- Apply consistently for all strategies in both the chart score bar and the screener score column

---

## 7. Related Documents

- `docs/strategy_audit.md` — per-strategy bug list (T1–CC2), audited 2026-03-06
- `docs/architecture_audit.md` — 30 architecture recommendations, audited 2026-03-11
- `CLAUDE.md` — strategy engine overview, entry/exit logic summary, config schema

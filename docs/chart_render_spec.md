# Chart Tab — Render Specification

**Scope:** Defines exactly what the browser renders on the Chart/Strategy view for any active strategy.
**Companion doc:** `strategy_pipeline_design.md` — covers data flow (Python → JSON asset). This doc covers rendering (JSON asset → browser).
**Last updated:** 2026-04-06 (corrected against implementation)

---

## Overview

The chart view is split into two sub-tabs: **Strategy** and **Chart (Indicators)**. Both share the same price panel (row 1) and are driven by the same strategy selector. Switching between them shows or hides different lower panels without reloading data.

---

## Strategy Selector

A dropdown present on every tab. Lists all strategies from `config.json → strategy_setups`, plus an "All Strategies" overlay mode.

- Changing it triggers a full chart rebuild for the active symbol and timeframe.
- Selection is persisted across symbol navigation, tab switches, and page reload.
- Changing strategy syncs the screener filter: `polarity_combo` strategies map to their matching screener filter via a hardcoded map (`dip_buy→strat_dip`, `trend→strat_trend`); any unlisted polarity strategy defaults to `strat_active`; non-polarity strategies reset the filter to `all`.
- The selector is synchronised across all tabs — changing it on any tab changes it everywhere.

---

## Entry Definition Bar

A single line rendered below the toolbar describing the active strategy's entry conditions. Always visible on the chart/strategy view. Updates on timeframe change.

**All strategies use the prefix `Regime:` followed by their specific conditions.**

| `entry_type` | Format |
|---|---|
| `polarity_combo` | `Regime: KPI-A ↑ · KPI-B ↑ · KPI-C ↑ \| Scale: C3 + KPI-D ↑` |
| `threshold` | `Regime: MACD-BL ↑ + ≥5/9 score \| Scale: C3 + WT-MTF ↑` |
| gate (has `ribbon_label`) | `ribbon_label` verbatim from `config.json` — must itself begin with `Regime:` |

The `| Scale:` segment is omitted when the strategy has no C4 (scale-up). Gate strategies that define `ribbon_label` in config are responsible for including the `Regime:` prefix in that string.

---

## (a) Strategy Sub-Tab

Shows **price + trade history + performance**. The oscillator panel, KPI heatmaps, and indicator dropdown are hidden. The x-axis uses the same default display window as the Chart sub-tab (180 bars on 1D, 52 on 1W, 26 on 2W, 18 on 1M); zoom out to see full trade history.

---

### Panel 1 — Price Chart

Full-height candlestick. Strategy overlays rendered on top:

- **Position shading:** coloured background rect spanning entry bar to exit bar per trade.
  - Win = strategy colour at low opacity.
  - Loss = red.
  - Strategies with C4 scale-up shade the pre-scale segment at lower opacity and the post-scale segment at higher opacity.
- **Entry marker:** upward triangle below the low of the entry bar. Colour = strategy colour. Hover shows: strategy name, entry price, initial ATR stop, date.
- **Scale-up marker:** orange upward triangle at the scale bar. Only rendered for strategies with C4 defined.
- **Exit marker:** downward triangle above the high of the exit bar. Colour = strategy colour (win) or red (loss). Hover shows: return %, exit reason (`ATR stop` / `CE exit` / `KPI invalidation` / `Open`), hold in bars, entry price/date, exit price/date.
- **ATR stop-loss trail:** dotted red line from entry bar to exit bar per trade, tracing the trailing stop level bar by bar.
- **SMA200 + SMA20 lines:** rendered on 1D and 1W timeframes whenever the symbol has ≥200 bars of history — regardless of the active strategy's `entry_gates` config. Greyed lines at low opacity.

---

### Panel 2 — P&L / Equity Curve

Cumulative equity curve built from all completed trades of the active strategy on the displayed timeframe. Positioned directly below the price panel.

- Green fill area above zero, red fill area below zero.
- Equity curve line: green if cumulative return is positive, red if negative.
- **Per-trade P&L bars:** centred on each trade's midpoint bar, width proportional to hold duration. Bar height = trade return %. Hover shows: entry price, exit price, return %, hold duration expressed adaptively (days / weeks / months based on timeframe).
- **Statistics annotation** (top-right of panel): **Total Return**, **Hit Rate %**, **N trades**, **Avg return per trade**, **Max DD**. Recalculates dynamically on zoom/pan to reflect only trades whose exit falls within the visible x-axis window.

---

### Panel 3 — Strategy Score Bar

Bar chart showing the net bullish score per bar for the active strategy's KPI set.

- Bar height = sum of weighted bullish KPI scores minus sum of weighted bearish KPI scores, using `kpi_weights` from `config.json`.
- Colour: green (net bullish), red (net bearish), grey (zero).
- Y-axis range is padded to fit the number of KPIs in the strategy's set.
- For threshold strategies (e.g. Stoof): a horizontal line is drawn at the configured entry threshold value.
- **KPI source by strategy type:**
  - `polarity_combo` strategies: reads KPI list from `combos_by_tf` / `combos` in `STRATEGY_SETUPS` (baked into the shell at build time), **not** from `strategy_kpis[key]` in the JSON asset.
  - `threshold` strategies (e.g. Stoof): reads from `strategy_kpis[key]` in the asset.
  - `gate` strategies (e.g. Pullback-A): reads from `strategy_kpis[key]` in the asset. Score bar label = `<strategy label> Score`.
  - All other strategies: falls back to the generic trend KPI list — **silent bug if a new strategy is added without `strategy_kpis[key]` in the asset**; must be caught by the Score bar check in the render checklist below.

---

## (b) Chart (Indicators) Sub-Tab

Shows **price + oscillators + KPI heatmaps**. The P&L panel and score bar are hidden. The indicator dropdown is visible and active.

---

### KPI Filter Dropdown

A set of grouped dropdowns controlling which indicators are plotted on the price chart (row 1) and the oscillator panel (row 3).

**The dropdown is filtered to the active strategy's KPI set. Indicators that do not belong to the active strategy must not appear.**

The KPI set shown here covers **all KPIs relevant to the strategy** — regime gates, entry trigger, and exit indicators — not just the entry combo KPIs. The Regime Ribbon (panel 5) is the right place to show entry-only conditions; the heatmap and dropdown show the full picture.

Filter rules by `entry_type`:

| `entry_type` | What is shown |
|---|---|
| `polarity_combo` | Indicators whose KPI name appears in the strategy's C3/C4 combo KPIs for the active timeframe (entry-specific by definition) |
| `threshold` | All indicators registered in `registry.py` with `strategies=["<key>"]` |
| gate | All indicators registered in `registry.py` with `strategies=["<key>"]` — includes regime gates, entry trigger, and any exit indicator relevant to the strategy |
| All Strategies | All indicators shown, unfiltered |

Additional rules:

- KPIs that exist only in the heatmap (no plottable trace on row 1 or row 3) appear in the dropdown with a disabled checkbox, visually distinguished (greyed) to indicate heatmap-only status.
- Checked/unchecked state persists across symbol navigation and tab switches, but resets when the active strategy changes.

Each item in the dropdown shows:

- **State dot:** green (bullish), red (bearish), grey (neutral) — sourced from `kpi_states` for the active symbol/timeframe.
- **Indicator display name.**
- **Checkbox** to toggle visibility on the chart.
- **★ star** for indicators that are part of the entry combo (C3/C4 KPIs).

---

### Panel 1 — Price Chart

Same candlestick as the Strategy sub-tab. In the Chart sub-tab:

- Strategy trade overlays (position shading, entry/exit markers, stop trail) are **hidden**.
- Only indicator-driven overlays toggled via the dropdown are shown (e.g. SuperTrend band, Chandelier Exit line, Bollinger Bands).
- Only overlays belonging to the active strategy's indicator set are available to toggle.

---

### Panel 2 — Oscillators

Sub-chart indicators (RSI, Stochastic, MACD, etc.) for the active strategy. Toggled via the indicator dropdown within the strategy's allowed set. Not regime-driven.

---

### Panel 3 — KPI Trend Heatmap

Continuous bull / bear / neutral state per KPI per bar, colour-encoded.

- One row per KPI in the active strategy's KPI list.
- Colour: green (bull `1`), red (bear `-1`), grey (neutral `0`).
- **Shows all KPIs relevant to the strategy** — regime gates, entry trigger, exit indicators — not just entry combo KPIs. The Regime Ribbon (panel 5) is the dedicated place for entry-only conditions.
- **Every strategy must supply its own KPI list via `registry.py`.** This panel must never show the generic trend KPIs when a non-trend strategy is active.
- KPI source by `entry_type`: `polarity_combo` → `combos_by_tf` (entry KPIs only, by definition); `threshold` and `gate` → `strategy_kpis[key]` from the JSON asset (all registered KPIs for the strategy).

---

### Panel 4 — KPI Breakout Signals (dot heatmap)

One row per KPI in the active strategy's KPI list. Each dot marks a state transition (bullish/bearish onset) for that KPI on that bar.

- Only KPIs belonging to the active strategy are rendered. KPIs from other strategies are filtered out.
- KPI row order matches the KPI Trend Heatmap (panel 3) for visual alignment.
- Same "all strategy KPIs" scope as panel 3 — regime gates, entry trigger, and exit indicators all appear here.

---

### Panel 5 — Regime Ribbon

A narrow horizontal band at the top of the lower panel showing when the strategy's entry regime is active — all prerequisite conditions met, independent of whether the trade trigger fired on that bar.

- Active bar = highlighted in the strategy colour. Inactive bar = dark/flat.
- The regime will be active on **more bars than there are trade entries**. The trigger (e.g. MACD histogram crossover onset, C3 KPI onset) is a subset of the regime window. A wide regime window with sparse entry markers is expected and correct.

**Label format: `Regime: <conditions>` — mandatory for every strategy.**

| `entry_type` | Label source |
|---|---|
| `polarity_combo` | Auto-built: `Regime: KPI-A · KPI-B · KPI-C` |
| `threshold` | Auto-built: `Regime: MACD-BL + ≥5/9 score` |
| gate (`ribbon_label` in config) | Verbatim from `config.json → ribbon_label` — must begin with `Regime:` |

- If the strategy has a scale-up condition (C4), a second band is rendered below the C3 regime band labelled `Scale: <C4 conditions>`.
- If `c3_states_by_strategy[key]` is absent from the asset, the ribbon is not rendered and no fallback reconstruction is performed.

---

## Asset Contract — What Every Strategy Must Supply

For both sub-tabs to render correctly, each strategy must provide all of the following in the JSON asset:

| Field | Drives | Produced by |
|---|---|---|
| `position_events_by_strategy[key]` | Trade markers, shading, stop trail, P&L curve, stats | `strategy.py` engine |
| `c3_states_by_strategy[key].c3` | Regime ribbon — active/inactive per bar | `compute_c3_states_by_strategy()` |
| `c3_states_by_strategy[key].c4` | Scale-up regime band (`null` if no C4) | same |
| `strategy_kpis[key]` | Breakout filter · heatmap rows · KPI dropdown (and score bar for `threshold` strategies) | `registry.py` + `build_dashboard.py` |
| `STRATEGY_SETUPS.setups[key].combos_by_tf` | Score bar KPI list for `polarity_combo` strategies (baked into shell; independent of `strategy_kpis`) | `config.json` → shell template |
| `config.ribbon_label` | Regime band label text (gate strategies only; must begin with `Regime:`) | `config.json` |

**Missing field behaviour:**

| Missing field | Effect |
|---|---|
| `position_events_by_strategy[key]` | Stale-asset toast shown; no trade overlays rendered |
| `c3_states_by_strategy[key]` | Regime ribbon row not rendered; no fallback |
| `strategy_kpis[key]` | Heatmap, breakout filter, KPI dropdown, and score bar (for `threshold` and `gate` strategies) silently fall back to generic trend KPIs. Score bar for `polarity_combo` strategies is unaffected (uses shell-baked `combos_by_tf`). Silent bug — must be caught by the Score bar and heatmap checks below. |
| `ribbon_label` (gate strategy) | Regime ribbon label renders as empty string |

---

## Adding a New Strategy — Render Checklist

Before a new strategy is considered complete, verify every item:

- [ ] `position_events_by_strategy[key]` written for all active timeframes
- [ ] `c3_states_by_strategy[key].c3` computed and written (`.c4 = null` if no scale-up)
- [ ] At least one `IndicatorDef` registered in `registry.py` with `strategies=["<key>"]` and correct `kpi_type`. **This is the only step that populates `strategy_kpis[key]` in the asset.** Without it the heatmap, breakout panel, and score bar silently show generic trend KPIs.
- [ ] `strategy_kpis[key]` present and non-empty in the asset — confirmed by checking the build log for `strategy_kpis['<key>'] is empty` warnings after rebuild.
- [ ] If gate `entry_type`: `ribbon_label` defined in `config.json` and begins with `Regime:`
- [ ] If `polarity_combo` or `threshold`: confirm the auto-built regime label renders with `Regime:` prefix
- [ ] **KPI dropdown check:** switch to Chart sub-tab with this strategy active — confirm only this strategy's indicators appear in the dropdown, and no indicators from other strategies are visible
- [ ] **Regime ribbon check:** switch to Chart sub-tab — confirm the ribbon is active on a broader set of bars than the entry markers on the price chart
- [ ] **Score bar check:** switch to Strategy sub-tab — confirm the score bar reflects this strategy's KPI set, not the generic trend KPIs

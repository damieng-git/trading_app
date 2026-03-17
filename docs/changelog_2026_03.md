# Dashboard Changelog — March 2026

This document covers all UX, strategy, and scan system changes made in the `claude/update` branch.

---

## 1. Screener: "All Strategies" filter

**File:** `apps/dashboard/templates.py`, `apps/dashboard/static/dashboard_screener.js`

**Change:** Added an **All** strategy filter button to the screener tab, before the existing strategy buttons.

**Logic:** When "All" is selected, the screener shows any symbol that has ENTRY or HOLD status on *any* strategy (`dip_buy`, `swing`, `trend`, `stoof`).

---

## 2. Stock list selector moved to sidebar

**File:** `apps/dashboard/templates.py`, `apps/dashboard/static/dashboard.css`

**Change:** The stock group dropdown (Damien / Stefan / Scan List / Watchlist / Benchmark) was moved from the top bar into the sidebar, above the "Filter symbol" input box. It now uses `#sidebarGroupSelector` and is full-width within the sidebar.

**Applies to:** Chart tab and Strategy tab (both use the sidebar).

---

## 3. Indicator panel UX overhaul

**Files:** `apps/dashboard/templates.py`, `apps/dashboard/static/dashboard.js`, `apps/dashboard/static/dashboard.css`

### 3a. Dimension tabs

A row of tab pills is rendered above the chip strip (`#indicatorDimTabs`). Tabs:

| Tab | Behavior |
|---|---|
| **All** | Shows all indicators, stacked in groups per dimension |
| **Trend** | Only Trend indicators |
| **Momentum** | Only Momentum indicators |
| **Relative Strength** | Only RS indicators |
| **Breakout** | Only Breakout indicators |
| **Risk / Exit** | Only Risk / Exit indicators |
| **★ Selected** | Only currently active indicators (appears when ≥1 chip is on) |

Active tab is persisted to `localStorage` (`indicatorDimTab`).

### 3b. Grouped layout on "All" tab

When the "All" tab is active, `#indicatorStrip` switches to `flex-direction: column` (`dim-grouped` class). Each dimension renders as:

```
[TREND]
  [chip] [chip] [chip]
────
[MOMENTUM]
  [chip] [chip] [chip] [chip]
```

When a specific dim tab is active, chips render as a flat horizontal row (no header, no grouping).

### 3c. Yellow ring on selected chips

Selected chips (`.chip.on`) get `box-shadow: 0 0 0 2px #eab308` — a yellow outer ring.

### 3d. Dimming of unselected chips

When any chip is selected, unselected chips in the strip get `opacity: 0.4`. Hovering over a dimmed chip restores full opacity.

### 3e. Count badge on toggle

The **Indicators** button shows `(N)` when N chips are active, e.g. `Indicators (3) ▼`.

---

## 4. C3/C4 bars for stoof strategy in chart tab

**File:** `apps/dashboard/static/chart_builder.js`

**Change:** When the stoof strategy is selected in the chart tab, C3 and C4 heatmap rows are now computed and rendered, same as other strategies.

**Logic:**
- `C3 row` = `MACD_BL` green AND score ≥ threshold/total (e.g. 5/9)
- `C4 row` = C3 + `WT_MTF` green

Labels shown:
- C3: `MACD Band Light + ≥5/9 score`
- C4: `C3 + WaveTrend MTF`

Also fixed: the threshold line in the stoof chart now uses the configured `threshold` value from `config.json` instead of a hardcoded `7`.

---

## 5. Strategy definition bar above chart

**File:** `apps/dashboard/static/dashboard.js`, `apps/dashboard/templates.py`, `apps/dashboard/static/dashboard.css`

**Change:** Added `#strategyDef` div above `#indicatorWrap` in the chart tab. When switching strategies, it shows a concise definition of C3 and C4 conditions, e.g.:

```
C3: NW Smoother ↑ · Madrid Ribbon ↑ · Volume > MA20    C4: C3 + GK Trend Ribbon ↑
```

For stoof (threshold type), it shows:
```
C3: MACD Band Light green + 5/9 score    C4: C3 + WaveTrend MTF
```

The bar updates on init and on every strategy change.

---

## 6. Symbol list reorganization

**Files:** `apps/dashboard/configs/lists/`, `trading_dashboard/symbols/manager.py`, `apps/dashboard/static/dashboard.js`, `apps/dashboard/configs/config.json`

### 6a. Group renames and reorder

| Old | New |
|---|---|
| `portfolio` | `damien` |
| `stefan` | `stefan` (moved below Damien in sidebar) |

Sidebar group order: **Damien → Stefan → Scan List → Watchlist → Benchmark** (previously Portfolio then mixed order).

### 6b. Strategy-specific lists removed

Removed: `portfolio.csv`, `dip_buy.csv`, `entry_stocks.csv`, `swing.csv`, `trend.csv`.

Kept: `damien.csv` (content = former portfolio), `stefan.csv`, `watchlist.csv`, `benchmark.csv`.

### 6c. Unified scan list

Added `scan_list.csv` — single file that holds the union of all strategy scan results. Previously, each strategy wrote to its own CSV.

### 6d. SymbolManager update

`trading_dashboard/symbols/manager.py`: `_EXCLUSIVE_GROUPS` updated from `{"portfolio", "watchlist"}` → `{"damien", "watchlist"}`.

---

## 7. Scan system overhaul

**Files:** `apps/screener/scan_strategy.py`, `apps/screener/scan_enrichment.py`

### 7a. Stoof threshold scanning added

Previously: stoof scanning returned an error ("not implemented").

Now:
- `run_scan(strategy="stoof", tf="2W"|"1M")` fully works
- Lean pre-filter: checks `MACD_BL` green on raw OHLCV (necessary condition for C3)
- Full validation: `_validate_stoof_on_enriched()` reads enriched Parquet, computes score from 9 score KPIs, checks C3 onset (MACD_BL green AND score ≥ threshold AND was not true on prior bar)
- `run_scan_all_strategies()` includes stoof when `tf ∈ active_tfs` (2W, 1M)

### 7b. BUG-11 fixed: quality gate before enrichment

Previously: `compute_scan_indicators()` (expensive) ran before `check_quality_gates()` (cheap). Symbols that failed gates were still fully enriched.

Now: `check_quality_gates_raw()` runs on raw OHLCV before enrichment. `check_quality_gates_raw` computes SMA20/SMA200/Vol_MA20 inline without pre-computed indicator columns.

Applied in:
- `_scan_symbol()` (single-strategy scan)
- `_scan_symbol_all_strategies()` (multi-strategy scan — also skips strategies that fail their gate before computing enrichment at all)

### 7c. BUG-13 fixed: run_scan_all_tf captured empty results

Previously: `run_scan_all_tf()` iterated over `run_scan_all_strategies()` generator and tried to capture results from the `"done"` event's `by_strategy` key, which only contained counts `{key: int}` — so it created empty lists: `{k: [] for k in by_strategy}`.

Fix: `run_scan_all_strategies()` now includes a `"results"` key in the `"done"` event with the actual validated symbol lists. `run_scan_all_tf()` reads `event["results"]`.

### 7d. 4H excluded from scanning

4H was never a useful scan timeframe (used only for entry timing confirmation after a 1D signal is found). Now explicitly rejected:
- `run_scan()`: returns error if `tf == "4H"`
- `run_scan_all_strategies()`: returns error if `tf == "4H"`
- `run_scan_all_tf()`: tfs list = `["1D", "1W", "2W", "1M"]` (already excluded 4H, now with comments)

### 7e. Duplicate `_load_strategy_csv` removed

A stale second definition of `_load_strategy_csv` (lines ~565-579) referenced per-strategy CSV paths and was silently shadowing the correct first definition. Removed.

### 7f. CLI supports `--tf all` and `--strategy all`

```bash
# Single TF, single strategy
python -m apps.screener.scan_strategy --strategy trend --tf 1D

# Single TF, all strategies
python -m apps.screener.scan_strategy --strategy all --tf 1D

# All TFs, single strategy
python -m apps.screener.scan_strategy --strategy trend --tf all

# All TFs, all strategies
python -m apps.screener.scan_strategy --strategy all --tf all
```

### 7g. MACD_BL added to lean enrichment

`apps/screener/scan_enrichment.py`:
- Added `MACD_BL` block in `compute_scan_indicators()`: computes MACD with Band Light params (`fast=15, slow=23, signal=5, EMA signal`), writes `MACD_BL`, `MACD_BL_signal`, `MACD_BL_hist`
- Added `"MACD_BL": 50` to `KPI_SCAN_MIN_BARS`
- Added `check_quality_gates_raw()` — quality gate check on raw OHLCV (used before enrichment)

### 7h. Scan now writes to unified scan_list.csv

All strategy results (trend, swing, dip_buy, stoof) are merged into a single `scan_list.csv`. The dashboard's "Scan List" group reads from this file.

---

## 8. serve_dashboard.py scan dispatch

**File:** `apps/dashboard/serve_dashboard.py` (audit confirmed, no changes needed)

- `timeframe="all"` → `run_scan_all_tf()`
- `timeframe != "all"` → `run_scan_all_strategies(tf, ...)`
- `_skip_refresh=True` in inner calls from `run_scan_all_tf`; single refresh at the end

---

---

## 9. Indicator selector — dropdown redesign

**Files:** `apps/dashboard/templates.py`, `apps/dashboard/static/dashboard.js`, `apps/dashboard/static/dashboard.css`

Replaced the chip strip + dimension tabs with **one custom dropdown per indicator family**:

```
[Trend 8 ▾]  [Momentum 12 ▾]  [Relative Strength 2 ▾]  [Breakout 3 ▾]  [Risk / Exit 4 ▾]
```

- **Trigger button** shows dimension name + total indicator count (grey). When indicators are active in that family, the count turns into a **yellow badge** and the border goes gold.
- **Dropdown panel** lists every indicator in the family with: state dot (green/red/grey), name, checkbox. Combo KPIs marked with ★.
- **Selected rows** get a yellow left-bar highlight + bold text.
- Opening a dropdown closes any previously open one. Click outside closes all.
- **"Clear all"** dashed button appears at end of row when any indicator is active — clears all selections in one click.
- Selections and chart visibility apply instantly without closing the dropdown.
- The **"Indicators (N) ▾"** toggle still shows total active count and collapses/expands the whole row.
- Removed old CSS: `.chip`, `.dim-tab`, `.dim-group`, `.dim-sep`, `.dim-chips`, `.dim-header`.

---

## 10. Chart layout defaults reset

**Files:** `apps/dashboard/static/dashboard.js`, `apps/dashboard/static/dashboard.css`, `apps/dashboard/templates.py`

### 10a. LocalStorage key bumped → fresh defaults

`LS_KEY` bumped from `v1_2` → `v1_3`. This clears all users' saved dashboard state on first load after the update, resetting to clean defaults:

| Setting | Default |
|---|---|
| Timeframe | **1W** |
| Stock group | **All** |
| Screener strategy filter | **All** |
| Chart strategy | trend |

### 10b. Chart height reduced

`#chartUpper` `min-height` reduced from `600px` → `420px`, giving more vertical space to the oscillator section without scrolling.

### 10c. Oscillator open by default

The oscillator panel now starts **expanded** (was collapsed). The toggle text starts as `Oscillators ▼` instead of `Oscillators ▶`. Users can still collapse it by clicking the toggle.

---

---

## 11. Daily scan cron job

**Scheduled via:** `crontab -e` (root user, system crontab)

Runs every day at **6:00 AM** server time — all strategies × all timeframes (1D/1W/2W/1M):

```
0 6 * * *  TRADING_APP_ROOT=/root/damiverse_apps/trading_app_test \
           /root/damiverse_apps/trading_app/.venv/bin/python \
           -m apps.screener.scan_strategy --strategy all --tf all \
           >> /root/damiverse_apps/trading_app_test/logs/scan_cron.log 2>&1
```

- Output (stdout + stderr) is appended to `logs/scan_cron.log`
- On completion, the scan automatically triggers a dashboard refresh (writes `scan_list.csv` → calls `python -m trading_dashboard dashboard refresh` in background)
- To view or edit: `crontab -l` / `crontab -e`
- To check recent log: `tail -100 /root/damiverse_apps/trading_app_test/logs/scan_cron.log`
- To disable: `crontab -r` (removes all) or `crontab -e` and delete the line

---

---

## 12. Scan: pre-scan dashboard refresh + open-position filter

**Files:** `apps/screener/scan_strategy.py`, `apps/dashboard/serve_dashboard.py`, `apps/dashboard/static/dashboard.js`

### What changed

**Phase 1 — dashboard stock refresh (new)**
Before scanning the universe, all dashboard symbols are now incrementally re-enriched: new OHLCV bars are downloaded and indicators are recomputed. This ensures the feature store is current at scan time, so the position status check uses fresh data.

- `_refresh_dashboard_stocks()` — calls `enrich_symbols(cfg.symbols)`, which handles incremental download + indicator recomputation via `IncrementalUpdater`.
- Phase 1 runs once per scan invocation. In `run_scan_all_tf` it runs before the TF loop (not once per TF).

**Open-position filter (new)**
After C3 onset is confirmed on enriched data, symbols that are already in an open position for the same strategy/TF are dropped from results.

- `_filter_open_positions(symbols, strat_def, tf)` — loads each symbol's enriched parquet, calls `compute_polarity_position_status`, drops any symbol where `signal_action != "FLAT"`.
- Only applies to `polarity_combo` strategies. Threshold (stoof) strategies are not filtered (no positional state equivalent).
- Only dashboard stocks are checked; universe-only stocks pass through unconditionally.

**`_skip_enrich` flag**
`run_scan` and `run_scan_all_strategies` both accept `_skip_enrich: bool = False`. Set to `True` to bypass Phase 1 (used by `run_scan_all_tf` which runs Phase 1 once at the top level).

**Scan tab UI**
The "Scan complete" message now shows: `N signal(s) found · X/Y stocks refreshed (Z failed)`.

### Motivation

Previously, the scan could re-flag a symbol already in an open position when C3 cycled off and back on mid-hold (e.g. due to a volatile volume KPI). The position filter prevents this. The pre-scan refresh ensures position status is computed from current data, not a potentially stale feature store.

---

---

## 13. Chart tab UX: indicator panel, strategy description, layout

**Files:** `apps/dashboard/templates.py`, `apps/dashboard/static/dashboard.js`, `apps/dashboard/static/dashboard.css`

### 13a. Foldable indicator panel removed

The "Indicators ▼" toggle button and collapsible `#indicatorWrap` wrapper were removed. The indicator dropdown row (`#indicatorDropdowns`) is now always visible directly in the top bar area — no extra click required.

### 13b. Indicator dropdown row (new implementation in source)

`buildIndicatorPanel()` in `dashboard.js` was rewritten to build one `.ind-dd-wrap` per dimension family (Trend, Momentum, Relative Strength, Breakout, Risk/Exit). Each family button shows its total indicator count. When indicators are active in a family the button gets a yellow border and the count becomes a yellow badge. Opening a dropdown closes any other open one; clicking outside closes all.

A **"Clear all"** dashed button appears at the end of the row when any indicator is active.

### 13c. Strategy description bar (`#strategyDef` + `#strategyDefBar`)

Added `_updateStrategyDef(key)` to `dashboard.js` (inside `initStrategyDropdown()` IIFE, exposed as `window._updateStrategyDef`).

- Shows concise C3 / C4 condition text with polarity arrows: `↑` (green, bullish required) or `↓` (red, bearish required) next to each KPI name.
- `#strategyDef` renders in the top indicator area (above the dropdowns).
- `#strategyDefBar` renders between the oscillator panel and `#chartTs` (horizontal aggregator bars).
- Both update on strategy change and TF change. Hidden when strategy is "all" or unset.

### 13d. `#strategyDefBar` position

`#strategyDefBar` is placed in the HTML directly after `#oscWrap` and before `#chartLower` + `#chartTs`.

### 13e. Chart heights reduced

| Element | Before | After |
|---|---|---|
| `#chartUpper` min-height | 600px | 380px |
| `#chartOsc` min-height | 200px | 220px |

### 13f. Oscillator open by default

The oscillator panel starts expanded. Toggle text initialises as `Oscillators ▼`.

### 13g. `LS_KEY` bumped → v1_3

`LS_KEY` bumped from `td_dash_shell_state_v1_2` → `v1_3`. Resets all saved dashboard state to clean defaults on first load (timeframe: 1W, group: All, screener strategy: All, chart strategy: trend).

---

## 14. Chart tab top bar: Stock List selector removed

**File:** `apps/dashboard/templates.py`

The **Stock List** filter group (dropdown + label) was removed from the chart tab's `tab-filter-bar`. The top bar now shows only **Strategy** and **Timeframe**.

The stock group selector is available in the sidebar (see §15).

---

## 15. Sidebar: full-width stock group selector

**Files:** `apps/dashboard/templates.py`, `apps/dashboard/static/dashboard.css`

`_build_sidebar()` now renders `#sidebarGroupSelector` as the first element in the sidebar, above the filter input. The selector uses the standard `.tab-group-dropdown` / `.tab-group-trigger` (yellow background, full width of sidebar).

CSS added: `#sidebarGroupSelector .tab-group-dropdown` is `display: block; width: 100%` so the trigger stretches to fill the sidebar panel.

---

## 16. Heatmap regime bar labels: "Entry C3" / "Entry C4"

**File:** `apps/dashboard/static/chart_builder.js`

The C3 and C4 combo heatmap rows (row 7, above TrendScore) now use labels **"Entry C3"** and **"Entry C4"** instead of "C3" / "C4". Applies to both threshold (stoof) and polarity-combo strategies.

---

---

## 17. Screener: "All" button for Strategy filter

**File:** `apps/dashboard/templates.py`

Added an **All** button at the start of the Strategy filter group in the screener tab (before Dip Buy / Swing / Trend / Stoof). Uses the existing `strat_any` filter logic already present in `dashboard_screener.js`: shows any symbol with an active ENTRY or HOLD signal on *any* strategy.

---

## 18. Stoof exit rule overhaul

**File:** `apps/dashboard/strategy.py`, `apps/dashboard/configs/config.json`

### Old exit logic
- ATR stop (always active)
- Score ≤ `exit_threshold` (3 by default) — required 2+ KPIs to turn red
- M-bar checkpoint: exit if score < threshold at checkpoint

### New exit logic
- ATR stop (unchanged — price safety net)
- **MACD_BL turns red** → immediate exit (`"MACD_BL exit"`)
- **Any 1 score KPI turns red** → immediate exit (score < threshold = 5)
- M-bar checkpoint: now only trails the stop forward (no longer exits at checkpoint)

### Effect
The exit condition is now the exact inverse of the C3 entry condition: positions stay open precisely as long as C3 is green. This aligns the screener (ENTRY/HOLD states) with the chart display — any stock showing C3 green in the chart should also appear as ENTRY or HOLD in the screener.

---

---

## 19. Scan system: date_added tracking + open-position filter for all strategies

**Files:** `apps/screener/scan_strategy.py`, `apps/dashboard/screener_builder.py`, `apps/dashboard/build_dashboard.py`, `apps/dashboard/templates.py`, `apps/dashboard/static/dashboard_scan.js`, `apps/dashboard/static/dashboard.css`, `apps/dashboard/configs/lists/scan_list.csv`

### 19a. scan_list.csv gains date_added column

`scan_list.csv` now has two columns: `ticker,date_added`.

- `_write_scan_list(symbols, prev_dates)` — writes `date_added = today` for new symbols; preserves existing `date_added` for symbols already in the list.
- `_load_scan_list()` — now returns `dict[str, str]` (ticker → date_added) instead of `list[str]`. Callers updated accordingly.
- `_write_strategy_csv()` — now loads prev_dates and passes them through to preserve existing dates.

### 19b. Open-position filter extended to all strategy types

`_filter_open_positions()` previously only applied to `polarity_combo` strategies (stoof/threshold was unconditionally passed through).

Now it applies to **all** strategies:
- `polarity_combo`: uses `compute_polarity_position_status()` — unchanged
- `threshold` (stoof): uses `compute_stoof_position_status()` — new

Symbols not present in the dashboard symbol list still pass through unconditionally.

### 19c. Stoof strategies now filtered in run_scan_all_strategies

In `run_scan_all_strategies()`, the stoof validation block previously called `_validate_stoof_on_enriched()` but skipped `_filter_open_positions()`. The filter call is now applied after stoof validation, same as combo strategies.

### 19d. Raw-passed and filtered-open counts tracked throughout

After C3/stoof validation and before/after `_filter_open_positions`, counts are tracked:
- `raw_passed` = symbols confirmed by C3/stoof onset detection (before open-position filter)
- `filtered_open` = symbols removed because they have an active open position

These are:
- Emitted in the `"done"` SSE event from `run_scan()` and `run_scan_all_strategies()`
- Written to `scan_log.jsonl` via `_append_scan_log(raw_passed=N, filtered_open=M)`
- Displayed in the scan tab UI (see §19f)

### 19e. date_added injected into screener rows

`build_screener_rows()` accepts a new `scan_date_map: dict | None` parameter. Each screener row for a symbol in the scan list gets `scan_date_added = scan_date_map[sym]`.

Both `build_screener_rows` call sites in `build_dashboard.py` now load and pass `scan_date_map` from `_load_scan_list()`.

### 19f. Scan tab: stats bar + date_added column

**Stats bar (`#scanPassStats`)** — rendered above the New Signals table by `_renderScanStats()`:
- Reads the most recent entry from `/api/scan-log`
- Shows pill badges: `✓ N passed gate` · `⊘ M filtered (open pos)` · `⊕ T in list` · `+A added` · `−R removed`
- Called via `_loadScanLog` each time the scan tab opens

**Date Added column** — added as the last column in the New Signals table:
- Shows `r.scan_date_added` (ISO date string, e.g. `2026-03-17`)
- Falls back to `—` for stocks added before this change

**CSS** — added `.scan-pass-stats`, `.scan-stat-pill` and variant classes (`.scan-stat-pass`, `.scan-stat-filtered`, `.scan-stat-total`, `.scan-stat-added`, `.scan-stat-removed`, `.scan-stat-ts`) to `dashboard.css`.

---

## 20. Daily scan cron job: corrected to use scan_strategy.py

**Scheduled via:** `crontab -e` (root user)

The cron was previously calling `python -m trading_dashboard screener run` which routes to `daily_screener.py` (broad universe lean-enrichment scanner). This was wrong — all scan logic, open-position filtering, and `scan_list.csv` updates live in `scan_strategy.py`.

**Updated cron entry:**
```
0 6 * * * cd /root/damiverse_apps/trading_app_test && TRADING_APP_ROOT=/root/damiverse_apps/trading_app_test /root/damiverse_apps/trading_app/.venv/bin/python -m apps.screener.scan_strategy --strategy all --tf all >> /root/damiverse_apps/trading_app_test/logs/scan_cron.log 2>&1
```

`--strategy all --tf all` routes to `run_scan_all_tf()` which:
1. Incrementally re-enriches all dashboard stocks (new bars only)
2. Downloads universe OHLCV for each TF (1D, 1W, 2W, 1M)
3. Runs all strategies in a single enrichment pass per TF
4. Filters open positions for all strategy types
5. Writes `scan_list.csv` with `date_added` preserved
6. Appends to `scan_log.jsonl` with pass/filter counts
7. Triggers a background dashboard refresh

---

## 21. Nav tab reorder + emoji labels

**File:** `apps/dashboard/templates.py`

**Change:** Reordered the top navigation tabs and added emoji labels to all items.

New order: 🔍 Screener · ⚡ Charts · ♟️ Strategy · 💰 P&L · 📡 Scan · 💡 Info

---

## 22. Scan Phase 1: TTL-gated refresh (fixes "stuck on refreshing" bug)

**File:** `apps/screener/scan_strategy.py`

### Problem

Every time the Scan button was clicked, Phase 1 (`_refresh_dashboard_stocks`) unconditionally downloaded and re-enriched all ~190 dashboard symbols before the actual scan could start. With `_BATCH_CHUNK_SIZE=50` and `_BATCH_TIMEOUT_S=300`, this triggered 4 daily-download batches + 4 hourly-download batches, totalling 5–20+ minutes. Because the generator is blocked during this phase, the progress bar sat frozen at the first emitted event ("Refreshing dashboard stocks…") with no updates — making the scan appear hung.

### Fix

**TTL check in `_refresh_dashboard_stocks`**

Added `_SCAN_REFRESH_TTL_HOURS = 4.0`. Before calling `enrich_symbols`, the function now checks the `{sym}_1D.parquet` mtime for each dashboard symbol. Symbols enriched within the last 4 hours are skipped. If all symbols are fresh, the function returns immediately with `{"all_fresh": True}` and Phase 1 completes in milliseconds.

Only truly stale symbols (parquet missing or older than 4h) are passed to `enrich_symbols`. This means:
- **Back-to-back scans**: Phase 1 is instant (data just refreshed).
- **After a Full Refresh**: Phase 1 is instant.
- **First scan of the day**: only symbols with data > 4h old are re-downloaded (usually all of them, but this is the expected slow path).

**Improved progress messages**

- Initial message changed from `"Refreshing dashboard stocks…"` → `"Checking dashboard stocks…"` (completes fast in the common case).
- Completion message: `"Dashboard stocks up to date"` (all fresh) or `"Refreshed N stocks"` (stale symbols updated).
- Applied to all three scan entry points: `run_scan`, `run_scan_all_strategies`, `run_scan_all_tf`.

**Fixed pct=0 regression**

In `run_scan_all_strategies`, after Phase 1 ended at `pct=8`, the "Scanning N symbols…" message incorrectly reset the progress bar back to `pct=0`. Fixed to use `pct=_dl_pct_base` (8).

---

## 23. All TFs scan: union write bug + per-TF log baseline fix

**File:** `apps/screener/scan_strategy.py`

### Bug

When the Scan button was used with "All TFs" selected, `run_scan_all_tf` called `run_scan_all_strategies` four times (1D → 1W → 2W → 1M). Each call ended with `_write_scan_list(sorted(all_confirmed), ...)`, which opens `scan_list.csv` in **`"w"` (replace) mode**. As a result:

- 1D scan writes its signals → 1W scan loads those as `prev_dates`, then **overwrites** with only 1W signals → 1D signals lost
- Same pattern for 2W and 1M → after all four TFs, `scan_list.csv` contains **only 1M signals**

Additionally, `prev_list` (used for `added`/`removed` in `scan_log.jsonl`) was reloaded from the file inside each inner call, so it reflected what the *previous TF wrote* rather than the pre-run state. The log diffs were wrong for every TF after 1D.

### Fix

Added `_skip_write: bool = False` and `_prev_scan_dates: dict[str, str] | None = None` parameters to `run_scan_all_strategies`:

- **`_skip_write=True`**: skips the `_write_scan_list()` call inside the inner function; the caller handles the final write.
- **`_prev_scan_dates`**: caller passes in the pre-run scan_list snapshot so the log diff uses the correct baseline for all TF entries.

In `run_scan_all_tf`:
1. `prev_scan_dates = _load_scan_list()` is called **once** before the TF loop.
2. Each inner `run_scan_all_strategies` call receives `_skip_write=True, _prev_scan_dates=prev_scan_dates`.
3. After all 4 TFs complete, `run_scan_all_tf` computes the **union** of all TF results and calls `_write_scan_list` once with the full set.

Per-TF `scan_log.jsonl` entries are still written (appended) inside each inner call — one entry per TF per run — but now the `added`/`removed` diffs correctly reflect the state *before the current run started*.

---

## 24. All TFs scan: hybrid download (2 passes instead of 4)

**File:** `apps/screener/scan_strategy.py`

### Problem

The previous `run_scan_all_tf` called `run_scan_all_strategies` once per TF, each calling `_download_batch` independently. That meant **4 separate yfinance downloads** per batch: 1D (450 days), 1W (700 days), 2W (1500 days), 1M (3650 days). More critically, the bar depths were insufficient:

| TF | Old period | Bars produced | Min needed | Result |
|---|---|---|---|---|
| 1W | 700 days → native `1wk` | ~100 weeks | 202 | ❌ swing/trend silently skipped |
| 1M | 3650 days → native `1mo` | ~120 months | 202 | ❌ swing/trend silently skipped |

Both swing and trend require 202+ bars (cRSI, GK Trend). With only 100 weekly or 120 monthly bars, `_scan_symbol_all_strategies` fell through the `len(df) < max_min_bars` guard, returning zero signals for every symbol on those TFs — silently.

### Fix — hybrid download

`run_scan_all_tf` now downloads **two intervals per batch** instead of four:

1. **Daily at 3650 days** (`interval="1d"`) — covers 1D, 1W, 2W via in-memory resampling
2. **Monthly at 7300 days** (`interval="1mo"`) — native 1M bars

Resampling functions (already used elsewhere):
- `_resample_to_1w(df_1d)` — pandas `"W-FRI"` rule
- `_resample_to_2w(df_1d)` — pandas `"2W-FRI"` rule

Result bar depths:

| TF | Source | Bars produced | Min needed | Result |
|---|---|---|---|---|
| 1D | daily 3650d | ~2511 | 235 | ✅ |
| 1W | resample from daily | ~522 | 202 | ✅ fixed |
| 2W | resample from daily | ~262 | 202 | ✅ |
| 1M | monthly 7300d | ~240 | 202 | ✅ fixed |

### WTD/MTD note

Both native yfinance `1wk`/`1mo` and pandas-resampled weekly/monthly produce an **incomplete current-period bar** mid-period (week-to-date / month-to-date). This is not a new limitation introduced by the hybrid approach — native downloads behaved identically.

### New constants

```python
_HYBRID_DAILY_DAYS = 3650   # daily download for 1D/1W/2W derivation
_HYBRID_MONTHLY_DAYS = 7300  # native monthly download for 1M
```

### Test results (2026-03-17, 10 large-cap symbols: AAPL MSFT ASML NVDA AMD META GOOGL AMZN TSLA NFLX)

- All TFs processed: 1D, 1W, 2W, 1M
- Bar counts confirmed correct: 1D=2511, 1W=522, 2W=262, 1M=240
- 0 signals across all TFs and strategies — **correct** given current market conditions; quality gates (SMA20>SMA200, volume spike, SR break) and C3 onset logic correctly rejected all large-cap names that are not in a fresh entry setup in mid-March 2026
- Total elapsed: ~200s for 10 symbols (2 download passes per batch)

---

## 25. Standalone 1W/1M scans: bar-depth fix (`_TF_DOWNLOAD_DAYS`)

**File:** `apps/screener/scan_strategy.py`

### Problem

`run_scan` and `run_scan_all_strategies` used `_TF_DOWNLOAD_DAYS` to decide how many calendar days of history to request from yfinance. The values for `1W` and `1M` were too small:

| TF | Old value | Bars produced | Min needed |
|---|---|---|---|
| `1W` | 700 days → native `1wk` | ~100 bars | 202 |
| `1M` | 3650 days → native `1mo` | ~120 bars | 202 |

Swing and trend strategies require 202 bars (cRSI, DEMA). With too few bars, every symbol silently failed the `len(df) < max_min_bars` guard and was skipped.

### Fix

```python
_TF_DOWNLOAD_DAYS = {
    "1W": 3650,   # ~521 native weekly bars  (was 700 → ~100)
    "1M": 7300,   # ~240 native monthly bars (was 3650 → ~120)
}
```

`1W` and `1M` still use native yfinance intervals (`1wk`, `1mo`) — no resampling. The fix only extends how far back in history is requested. Consistent with the hybrid all-TF scan which gives the same bar counts via resampling.

---

## 26. Scan progress bar: allow closing mid-scan

**File:** `apps/dashboard/static/dashboard.js`

### Change

The close (`×`) button on the scan progress bar was hidden while the scan was running (`closeBtn.classList.add("hidden")` inside `showBar()`). Changed to `classList.remove("hidden")` so the button is always visible.

Clicking it mid-scan disconnects from the SSE stream (the scan continues server-side) and hides the bar. The scan result is still written to `scan_list.csv` in the background.

---

## 27. Stoof chart: false C3 entry caused by score inflation

**Files:** `apps/dashboard/static/chart_builder.js`

### Bug

The chart's TrendScore bar is computed from ALL 11 stoof KPIs (MACD_BL + WT_MTF + 9 score KPIs). The C3 detection used this same total as the threshold comparison:

```javascript
// WRONG — before fix
c3Active = tsValues.map((score, i) => _reqRow[i] === 1 && score >= _stoofThresh);
```

When MACD_BL (+1) and WT_MTF (+1) were both bullish, they inflated `tsValues` by 2. A symbol with only 3 real score KPIs bullish would show `tsValues = 5 >= threshold (5)` → false C3 entry displayed.

The position engine and scan (`strategy.py`, `scan_strategy.py`) correctly excluded MACD_BL and WT_MTF from the score, so the chart was the only component showing the false signal.

**Example:** TGT/1M — chart showed stoof entry at 2026-01-31; position engine and scan correctly computed score=4/5, no entry.

### Fix

Compute a separate `_stoofScoreCount` array counting only the 9 score KPIs (state === 1, excluding `_reqKpi` and `_c4Kpi`). Use that for the threshold check.

---

## 28. Single source of truth for C3/C4 chart rendering

**Files:** `apps/dashboard/strategy.py`, `apps/dashboard/build_dashboard.py`, `apps/dashboard/data_exporter.py`, `apps/dashboard/static/chart_builder.js`

### Problem

C3/C4 entry condition logic was implemented in two places:

1. **`strategy.py`** (Python) — used by screener, scan validation, position engine
2. **`chart_builder.js`** (JavaScript) — used by the strategy tab chart

The stoof bug in §27 was a direct consequence: two independent implementations drifted. Adding any new strategy would require parallel JS reimplementation with no automated consistency check.

### Fix

Added `compute_c3_states_by_strategy(df, kpi_st, strategy_setups, tf, plot_offset)` to `strategy.py`. It returns `{strategy_key: {"c3": [bool, ...], "c4": [bool, ...]}}` — per-bar boolean arrays for every strategy, computed with the same logic as the position engine:

- `polarity_combo` strategies: AND of all C3 KPIs at their expected polarity
- `threshold` strategies (stoof): `required_kpi == 1 AND score_kpis_bullish >= threshold`

`build_dashboard.py` calls this function and passes the result to `data_exporter.py`, which includes `c3_states_by_strategy` in every per-symbol asset payload.

`chart_builder.js` now reads C3/C4 directly from the asset:

```javascript
const _serverC3 = (data.c3_states_by_strategy || {})[_activeStrat];
if (_serverC3) {
  c3Active = _serverC3.c3 || null;
  c4Active = _serverC3.c4 || null;
} else {
  // Fallback for assets built before this feature
  if (!isStoof) {
    c3Active = comboBool(combo3kpis, combo3pols);
    c4Active = combo4kpis.length ? comboBool(combo4kpis, combo4pols) : null;
  }
}
```

All strategy-specific C3/C4 logic is removed from JS.

### Adding a new strategy

Implement the position logic in `strategy.py`. Add the appropriate `entry_type` branch to `compute_c3_states_by_strategy`. Run a Full Refresh. Chart, screener, and scan are automatically consistent — no JS changes needed.

### Deployment note

Requires a **Full Refresh** (not just UI Refresh) because `c3_states_by_strategy` is baked into per-symbol `.js.gz` assets at build time.

---

## 29. Score bar: hidden for "all" strategy; Stoof score bar fix

**File:** `apps/dashboard/static/chart_builder.js`

### Problem

Two bugs in the score bar (Row 4, TrendScore/StoofScore):

1. **"all" strategy shows a score bar it shouldn't.** When `currentStrategy === "all"`, none of `isPolStrat` / `isStoof` is set, so `scoreSlice` falls through to the default `trend` slice. The score bar renders with label "TrendScore" using all trend KPIs — confusing since the "all" view is a multi-strategy overlay, not a single-strategy score.

2. **Stoof score bar missing on stale assets.** The score bar requires `stoofSlice.kk.length > 0`, which in turn requires `data.strategy_kpis["stoof"]` to be populated. This field was added to `data_exporter.py` in §28. Assets built before that change contain no `strategy_kpis`, so `stoofKpiNames = []` → `stoofSlice` is empty → bar is silently skipped.

### Fix

Added `!_isAllStrats` guard to the score bar render condition (`chart_builder.js` line 836):

```js
// Before
if (scoreSlice.kk.length && scoreSlice.zz.length) {

// After
if (!_isAllStrats && scoreSlice.kk.length && scoreSlice.zz.length) {
```

The Stoof score bar issue resolves automatically after a **Full Refresh** (rebuilds per-symbol assets with the `strategy_kpis` field).

---

## 30. PLANNED: Single source of truth — strategy pipeline unification

**Status:** Not yet implemented. Documented here for tracking.

### Problem

A full pipeline audit (March 2026) found that strategies do not follow a consistent data path from Python → asset → chart. Specific issues:

#### Issue A: "trend" is computed by two engines simultaneously

Every dashboard build runs **both**:
- `compute_position_events()` — legacy engine, hardcoded bullish-only logic, hardcoded SMA/volume/overextension entry gates. Output stored in `position_events` (top-level asset field).
- `compute_polarity_position_events()` — polarity-aware engine, configurable entry gates, configurable exit combos. Output stored in `position_events_by_strategy["trend"]`.

JS picks between them depending on context. These two engines can produce different signals for the same symbol/TF.

#### Issue B: C3/C4 combo row has a JS fallback that only works for polarity strategies

When `c3_states_by_strategy` is absent from an asset (stale build):
- Polarity strategies (trend/dip_buy/swing): fall back to JS `comboBool()` — combo row still renders.
- Stoof: fallback is explicitly blocked (`if (!isStoof)`) — combo row silently disappears.

The fallback is a second implementation of entry signal logic in JS. It cannot be configured per strategy and diverges from the Python engine over time.

#### Issue C: JS position-reconstruction fallback (lines ~1028–1107 in chart_builder.js)

When `position_events_by_strategy` is empty, JS reconstructs trade positions client-side. It hardcodes the legacy engine's entry gates and cannot reflect per-strategy config. This path is mostly dormant but still active and diverges silently.

#### Issue D: Score bar weights inconsistent across strategies

| Strategy | Weight rule |
|---|---|
| Legacy "trend" (fallback path) | Weighted by `kpi_weights` from config |
| Polarity strategies (trend/dip_buy/swing) | Unweighted (all KPIs = 1) |
| Stoof | Unweighted (all KPIs = 1) |

#### Issue E: `position_events` legacy field is a second source of truth

The asset contains both `position_events` (legacy "trend") and `position_events_by_strategy`. The legacy field is used for the "all" overlay and as fallback. Two representations of the same strategy exist in the same asset.

### Planned fix

1. **Delete `compute_position_events()` from the build pipeline.** Fold "trend" fully into `compute_polarity_position_events()`. Stop writing `position_events` to the asset.

2. **Delete the JS `comboBool()` fallback and position-reconstruction fallback.** All C3 states and position events must come from the asset. A missing or stale asset should surface a visible "rebuild required" message rather than silently computing something different.

3. **Align score bar weights.** Use unweighted (all KPIs = 1) for all strategies — consistent and transparent.

4. **Clean up "all" overlay.** Source exclusively from `position_events_by_strategy`; remove any reference to the legacy `position_events` field.

### Files that will change

| File | Change |
|---|---|
| `apps/dashboard/build_dashboard.py` | Remove legacy `compute_position_events` call; remove `position_events` from export args |
| `apps/dashboard/data_exporter.py` | Remove `position_events` parameter |
| `apps/dashboard/strategy.py` | `compute_position_events` can be kept for reference but removed from build path |
| `apps/dashboard/static/chart_builder.js` | Remove `comboBool` fallback; remove JS position-reconstruction fallback; fix "all" overlay to use `position_events_by_strategy` only; align score weights |
| `apps/dashboard/screener_builder.py` | Verify legacy `trailing_pnl` is fully overridden by polarity path; remove legacy call if safe |

### Deployment note

Requires a **Full Refresh** after changes land — all per-symbol assets must be rebuilt to drop the legacy `position_events` field and ensure `c3_states_by_strategy` + `position_events_by_strategy` are present for every strategy.

---

## Files changed summary

| File | Change type |
|---|---|
| `apps/dashboard/templates.py` | Screener All filter, sidebar group selector, #indicatorDimTabs, #strategyDef, strat_any All btn §17, scanPassStats §19, nav tab reorder + emoji §21 |
| `apps/dashboard/static/dashboard.js` | Dim tabs, grouped chip layout, yellow ring, dimming, badge, strategy def bar |
| `apps/dashboard/static/dashboard.css` | dim-tab styles, dim-grouped column layout, chip.on yellow ring, has-selection dimming, scan-pass-stats §19 |
| `apps/dashboard/static/dashboard_screener.js` | strat_any filter logic |
| `apps/dashboard/static/dashboard_scan.js` | _renderScanStats, date_added column §19 |
| `apps/dashboard/static/chart_builder.js` | Stoof C3/C4 heatmap rows, threshold line fix, Entry C3/C4 labels; stoof score inflation fix §27; server-driven C3/C4 rendering §28; suppress score bar for "all" strategy §29 |
| `apps/dashboard/configs/lists/scan_list.csv` | Migrated to ticker,date_added format §19 |
| `apps/dashboard/configs/lists/` | damien.csv added, old CSVs removed |
| `apps/dashboard/configs/config.json` | Group references updated, stoof description updated |
| `trading_dashboard/symbols/manager.py` | _EXCLUSIVE_GROUPS: portfolio→damien |
| `apps/screener/scan_strategy.py` | Full scan rewrite: stoof, BUG-11/13, 4H guard, CLI §7; pre-scan refresh, open-pos filter §12; date_added, all-strategy filter, raw/filtered counts §19; TTL-gated Phase 1, pct regression fix §22; All TFs union write + log baseline fix §23; hybrid download + bar-depth fix §24; standalone 1W/1M bar-depth fix §25 |
| `apps/screener/scan_enrichment.py` | MACD_BL lean computation, check_quality_gates_raw |
| `apps/dashboard/screener_builder.py` | scan_date_map parameter, scan_date_added in rows §19 |
| `apps/dashboard/build_dashboard.py` | Pass scan_date_map to build_screener_rows at both call sites §19; compute_c3_states_by_strategy call §28 |
| `apps/dashboard/data_exporter.py` | c3_states_by_strategy field §28 |
| `apps/dashboard/strategy.py` | Stoof exit: MACD_BL red or any 1 KPI red §18; compute_c3_states_by_strategy §28 |
| `apps/dashboard/static/dashboard.js` | Dropdown selector, Clear button, LS key bump v1_3 §9-10; scan progress close button always visible §26 |
| `apps/dashboard/static/dashboard.css` (§9-10, §13) | Dropdown styles, chart height, sidebar group selector, removed chip/dim-tab CSS |
| `apps/dashboard/templates.py` (§9-10, §13-15) | #indicatorDropdowns, oscillator open, sidebar group selector, Stock List removed from topbar |

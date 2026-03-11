# Architecture & Software Audit
**Date:** 2026-03-11
**Scope:** `trading_app_test` — full codebase review
**Goals:** single source of truth, lean files, fast compute, low energy, scalability

---

## File Size Overview

| File | LOC | Status |
|---|---|---|
| `apps/dashboard/static/dashboard.js` | 2,746 | 🔴 Split needed |
| `apps/dashboard/serve_dashboard.py` | 1,887 | 🔴 Split needed |
| `apps/dashboard/build_dashboard.py` | 1,830 | 🔴 Split needed |
| `apps/dashboard/static/chart_builder.js` | 1,742 | 🔴 Split needed |
| `apps/dashboard/static/dashboard.css` | 1,792 | 🟡 Borderline |
| `apps/dashboard/figures.py` | 1,373 | 🟡 Borderline |
| `apps/dashboard/templates.py` | 1,274 | 🟡 Borderline |
| `apps/dashboard/strategy.py` | 1,118 | 🟡 Borderline |
| `apps/screener/scan_strategy.py` | 798 | 🟢 OK |

---

## 30 Recommendations

### 🔴 Phase 1 — Critical (correctness + single source of truth)

---

#### #1 — `strategy.py` reads `config.json` directly instead of using `config_loader`
**File:** `apps/dashboard/strategy.py:23-36`
**Problem:** Re-opens and re-parses `config.json` from disk using its own hardcoded `_CONFIG_PATH`. Does not use `config_loader.py` at all. Called hundreds of times during a full build — each call hits disk.
**Fix:** Accept a `BuildConfig` or `exit_params` dict as a function parameter. Call sites pass the already-loaded config. Eliminates duplicate disk I/O, a second config path resolution, and drift risk.

---

#### #2 — Exit params defined in 3 places
**Files:** `config.json` (source of truth), `strategy.py` (hardcoded fallback), `templates.py:109` (hardcoded defaults embedded in HTML shell)
**Problem:** If `config.json` is updated, the HTML shell's embedded exit params are stale until `rebuild-ui` is run explicitly.
**Fix:** One definition in `config.json`, loaded once via `config_loader`, passed to both `strategy.py` and `templates.py`. Remove all hardcoded fallback dicts.

---

#### #3 — Timeframes scattered across 10+ files
**Files:** `strategy.py`, `figures_indicators.py`, `scan_strategy.py`, `templates.py`, `serve_dashboard.py`, `enrichment.py`, `config.json`
**Problem:** `config_loader.TIMEFRAME_REGISTRY` exists but most modules ignore it. Hardcoded `"4H"`, `"1D"`, `"1W"`, `"2W"`, `"1M"` strings are sprinkled everywhere. Adding or removing a timeframe requires edits in 10+ locations.
**Fix:** `TIMEFRAME_REGISTRY` in `config_loader.py` is already the right home — export it properly and make it the only place that lists valid timeframes. All other files import from it.

---

#### #4 — `config.json` changes require a full server restart
**File:** `serve_dashboard.py:88-111`
**Problem:** Config is loaded once at module import time. No hot-reload. Every config change requires `systemctl restart`.
**Fix:** Add a `/api/reload-config` endpoint, or detect file mtime change on each request, that re-reads `config.json` in-process without restarting. At minimum, document this explicitly at each module-level config load site.

---

#### #5 — `SymbolManager` instantiated ~8 times across `serve_dashboard.py`
**File:** `apps/dashboard/serve_dashboard.py` (multiple CRUD route handlers)
**Problem:** Each CRUD route creates a new `SymbolManager.from_lists_dir(...)`, reading all CSV files from disk on every request.
**Fix:** One module-level `_SM` singleton with a lock. Add an `invalidate()` method called after any write. Reading becomes O(1) instead of O(files × symbols).

---

#### #6 — Screener payload duplicated: embedded in HTML and served via API
**Files:** `templates.py` (embeds `screener_summary` into HTML shell), `serve_dashboard.py` `/api/screener-data` (serves the same data on demand)
**Problem:** Large JSON payload (100-200 KB) in the HTML shell on every page load, then refetched via API. Two sources for the same data.
**Fix:** Remove the embedded payload from the HTML shell. JS fetches `/api/screener-data` on startup. Reduces initial HTML size significantly and eliminates the dual-source problem.

---

#### #7 — `BuildPaths` attributes have no type-safe contract
**File:** `apps/dashboard/config_loader.py:187-201`
**Problem:** `BuildPaths` is a frozen dataclass with no runtime attribute validation. We already hit one `AttributeError` (`feature_store_enriched_dir`) from a missing attribute. Any new function using `resolve_paths()` can silently reference the wrong name.
**Fix:** Add `__post_init__` validation that all paths resolve under `TRADING_APP_ROOT`. Enforce `mypy` on `config_loader.py`. Consider named accessors with explicit docstrings over bare dataclass fields.

---

### 🟠 Phase 2 — Architecture & File Splits

---

#### #8 — `build_dashboard.py` (1,830 LOC) should be split into 4 modules
**Current ownership:** CLI entrypoint + pipeline orchestration + per-symbol export + dashboard refresh + figure export + hash/health utilities + FX rates
**Fix:**
- `pipeline.py` — `run_stock_export()`, `run_refresh_dashboard()` (the two top-level phases)
- `asset_exporter.py` — `_export_one_data()`, figure task dispatch, `ThreadPoolExecutor` logic
- `fx.py` — FX rate fetching and caching
- `build_dashboard.py` — keep only `main()` as a thin CLI entrypoint (~100 LOC)

---

#### #9 — `serve_dashboard.py` (1,887 LOC) should be split into 3 modules
**Current ownership:** HTTP routing + 4 SSE state machines + all API handlers + CRUD + rate limiting + path resolution
**Fix:**
- `server.py` — `main()` + `Handler` class routing only (thin)
- `background_tasks.py` — `_ScanState`, `_RefreshState`, `_RebuildUiState`, `_EnrichState`, `_is_any_task_running()`
- `api_handlers.py` — all `_handle_*` methods (screener, CRUD, trades, FX, groups)

---

#### #10 — `dashboard.js` (2,746 LOC) should be split into 4 files
**Current ownership:** DOM init, theme, SSE progress bar, symbol navigation, chart rendering, annotations, URL hash state, keyboard shortcuts, figure cache, group management, indicator toggles, strategy filter, FX currency switching
**Fix:**
- `dashboard_core.js` — DOM init, theme, localStorage, URL hash state, keyboard shortcuts
- `dashboard_chart.js` — figure cache, chart rendering, indicator toggles, annotations
- `dashboard_nav.js` — symbol navigation, group management, strategy/TF pills
- `dashboard.js` — thin top-level `init()` that wires everything

---

#### #11 — `chart_builder.js` (1,742 LOC) should be split
**Problem:** One giant `buildChart()` function with all 40+ indicator trace builders inline.
**Fix:** Extract indicator trace builders into a `chart_traces.js` file (keyed by indicator name). `chart_builder.js` becomes layout/axis/shape assembly only. Each indicator's trace definition is co-located with its label in `SHORT_LABELS`.

---

#### #12 — `dashboard.css` (1,792 LOC) should be split into logical layers
**Fix:**
- `variables.css` — all CSS custom properties (current lines 1-180)
- `layout.css` — grid, topbar, panels, sidebars
- `components.css` — buttons, badges, pills, modals, dropdowns
- `chart.css` — chart-specific overrides, indicator state colours
- `themes.css` — dark/light overrides only

Import all via a single `@import` chain in `dashboard.css`. Zero functional change, instant navigability.

---

#### #13 — `figures.py`, `figures_indicators.py`, `figures_layout.py` have unclear ownership
**Problem:** Three figure files with no enforced boundary. `figures.py` imports from both others, making the dependency direction ambiguous.
**Fix:** Define explicit ownership: `figures_layout.py` owns axis/layout config; `figures_indicators.py` owns per-indicator subplots; `figures.py` owns top-level assembly only. Remove any cross-imports going in the wrong direction.

---

#### #14 — JS files share state via `window` with no formal contract
**Files:** `dashboard.js` exposes `currentStrategy`, `_connectSSE`, `_isEurMode`, `_toEur`, `Dashboard.*` onto `window`. Other files read from `window` without any interface definition.
**Problem:** Silent failures if load order changes. No way to know what a file depends on without reading all of it.
**Fix:** Use a single `window.App` namespace object with documented properties. Each file reads/writes only its own slice. Alternatively, move to ES modules with explicit `import`/`export`.

---

### 🟡 Phase 3 — Compute & Performance

---

#### #15 — ProcessPoolExecutor worker count capped at 8 regardless of CPU count
**Files:** `build_dashboard.py:553`, `build_dashboard.py:1745`
**Problem:** On a 16-core machine, half the CPU sits idle during enrichment.
**Fix:** `min(max(8, os.cpu_count() or 4), len(tasks))`. Also skip the pool entirely when `len(tasks) < 4` — pool startup overhead (~500ms) exceeds the benefit for tiny workloads.

---

#### #16 — Worker processes re-import all indicator modules on every spawn
**File:** `build_dashboard.py` (ProcessPoolExecutor for `_enrich_one_task`)
**Problem:** Each worker is a fresh fork. First task triggers a full import of `enrichment.py` + all 40 indicator modules. Repeated for each of the 8 workers.
**Fix:** Use the `initializer=` parameter of `ProcessPoolExecutor` to pre-warm workers with a single import. Saves ~0.5s per worker at spawn time.

---

#### #17 — Plotly JSON assets regenerated on every build regardless of data changes
**File:** `build_dashboard.py` (`_export_one_data()`)
**Problem:** Every full build writes all 197 symbols × 5 TF × ~20 KB = ~20 MB of JSON even when only 5 symbols changed data.
**Fix:** Before writing, compute `md5(json_bytes)` and compare to the existing file. Skip the write if identical (store hash in a sidecar `.md5` file or read the existing file). For incremental refreshes this skips the majority of writes.

---

#### #18 — `ThreadPoolExecutor(max_workers=1)` created and destroyed per download batch
**File:** `trading_dashboard/data/downloader.py:171, 239`
**Problem:** Every batch creates and destroys a thread pool with a single worker purely for timeout support. ~13 pools created per full download, each with ~100ms overhead.
**Fix:** Use a reusable executor across all batches, or replace with `signal.alarm`-based timeout on Linux (simpler, zero thread overhead).

---

#### #19 — `IncrementalUpdater.needs_update()` is time-based, not bar-date-based
**File:** `trading_dashboard/data/incremental.py:111-123`
**Problem:** Checks "when was this last updated" not "does the last bar predate today." A symbol refreshed at 23:59 that already had today's data would still trigger a re-download the next morning even if markets haven't opened.
**Fix:** Compare `last_bar` date in `incremental_meta.json` against the last expected trading day (simple weekday + market-close check). Skip download only if `last_bar >= last_trading_day`. More accurate and avoids unnecessary yfinance calls.

---

#### #20 — SSE `_event_log` is unbounded — memory leak over time
**Files:** `serve_dashboard.py:191, 322, 434, 535` (one per state machine)
**Problem:** Each state machine appends events forever. A full scan (600 symbols) generates 600+ progress events. After 100 scans: ~60,000 entries (~18 MB) permanently in memory.
**Fix:** Replace `list` with `collections.deque(maxlen=1000)`. Subscribers using `get_events_since(idx)` already handle partial snapshots — they only need the tail, not full history.

---

#### #21 — All symbol DataFrames loaded into memory simultaneously during refresh
**File:** `build_dashboard.py:1724-1735`
**Problem:** `all_data_refresh` loads all 600 symbols × 5 TFs into a single dict before processing. Peak RAM: ~600 MB just for DataFrames.
**Fix:** Stream processing — load, enrich, export, and `del` each symbol one at a time. Only the current symbol lives in memory. Peak RAM drops from ~600 MB to ~5 MB.

---

#### #22 — `download_daily_batch` always downloads from `START_DATE` (full history)
**File:** `trading_dashboard/data/downloader.py:154-214`
**Problem:** Even for incremental updates, the full historical range is requested from yfinance. `merge_new_bars` deduplicates afterwards, but the download itself transfers years of redundant data.
**Fix:** Pass `start=last_bar_date - 5_days` when the symbol already has raw history (use `incremental_meta.json`). Fall back to full history only for new symbols with no existing data.

---

### 🟢 Phase 4 — Code Quality & Maintainability

---

#### #23 — Magic number `28.2` hardcoded as `max_trend_score` in `templates.py:113`
**Problem:** `max_trend_score = sum(...) if _kpi_w else 28.2` — the fallback is the sum of current KPI weights. If weights change in `config.json`, this fallback silently produces wrong scores.
**Fix:** Remove the fallback constant. If `_kpi_w` is empty, set `max_trend_score = None` and handle in JS. Never hardcode a derived computed value.

---

#### #24 — Hardcoded chart pixel heights in `dashboard.js:85`
**Problem:** `_chartHeights = { chartUpper: 500, chartPnl: 160, ... }` — layout pixel values buried in JS. Changing chart proportions requires a JS edit.
**Fix:** Move to CSS custom properties (`--chart-upper-h: 500px`, etc.). JS reads them via `_css("--chart-upper-h")`. All chart sizing becomes a single CSS file edit.

---

#### #25 — `linreg` in `_base.py:152` uses `raw=False` in `.apply()`
**File:** `trading_dashboard/indicators/_base.py:152`
**Problem:** `series.rolling(...).apply(lambda w: ..., raw=False)` passes each window as a Series (slower), then immediately calls `.to_numpy()` inside the lambda — converting back to what `raw=True` would have given directly.
**Fix:** `raw=True`. Single-character change, measurable speedup for long series across 40+ indicators.

---

#### #26 — `enrichment_is_current()` not used on all code paths
**File:** `trading_dashboard/data/store.py:270-283`
**Problem:** The hash-based enrichment skip (`raw_hash + config_hash` comparison) exists and works, but is not called before every enrichment invocation. Some paths re-enrich unconditionally.
**Fix:** Audit all call sites of `translate_and_compute_indicators()`. Ensure `enrichment_is_current()` is checked before every call during a full build. This is the highest-value caching layer in the entire pipeline.

---

#### #27 — `_RateLimiter` IP history grows unbounded
**File:** `apps/dashboard/serve_dashboard.py` (`_RateLimiter` class)
**Problem:** `_requests: dict[str, list[float]]` stores per-IP timestamps. New IPs are added but old ones are never pruned — even after their sliding window has long expired.
**Fix:** In `is_allowed()`, after cleaning old timestamps for an IP, if the resulting list is empty: `del self._requests[ip]`. Zero extra overhead, zero memory leak.

---

#### #28 — No `requirements.txt` lockfile or pinned dependencies
**Problem:** One breaking `yfinance` or `pandas` release away from a full outage. `pip install` on a fresh machine may install incompatible versions.
**Fix:** Run `pip freeze > requirements.lock` and commit it. Add a startup check that validates key package versions (at minimum `yfinance`, `pandas`, `plotly`). Long-term: move to `pyproject.toml` with version pins.

---

#### #29 — Screener scan uses `_MAX_WORKERS = 1` (fully sequential)
**File:** `apps/screener/scan_strategy.py:58`
**Problem:** With 600 symbols, the scan downloads data for each symbol one at a time. This is the dominant bottleneck for scan duration.
**Fix:** Raise to `min(4, cpu_count())` with a semaphore to respect yfinance rate limits. Even 2 parallel workers halves scan time. Batch the downloads (already available via `download_daily_batch`) rather than per-symbol calls.

---

#### #30 — Build timing uses `print()` instead of structured logging
**File:** `apps/dashboard/build_dashboard.py` (multiple `print(f"[timing] ...")` statements)
**Problem:** Timing data is not captured in a queryable format. Cannot track build time regressions over time. Mixes with stdout in journalctl.
**Fix:** Replace with `logger.info("phase=%s elapsed=%.1fs symbols=%d", ...)` using structured key-value pairs. With the existing JSON log formatter in `serve_dashboard.py`, these become filterable via `journalctl -o json | jq 'select(.phase)'`. Enables build time monitoring.

---

## Priority Matrix

| Phase | # | Issue | Effort | Impact |
|---|---|---|---|---|
| 🔴 1 | #1 | `strategy.py` reads config directly | Small | High |
| 🔴 1 | #2 | Exit params in 3 places | Small | High |
| 🔴 1 | #3 | Timeframes in 10+ files | Medium | High |
| 🔴 1 | #4 | No config hot-reload | Medium | Medium |
| 🔴 1 | #5 | SymbolManager instantiated 8× | Small | Medium |
| 🔴 1 | #6 | Screener payload in HTML + API | Small | High |
| 🔴 1 | #7 | BuildPaths no type safety | Small | Medium |
| 🔴 1 | #20 | SSE event log memory leak | Small | High |
| 🟠 2 | #8 | Split `build_dashboard.py` | Large | High |
| 🟠 2 | #9 | Split `serve_dashboard.py` | Large | High |
| 🟠 2 | #10 | Split `dashboard.js` | Large | High |
| 🟠 2 | #11 | Split `chart_builder.js` | Medium | Medium |
| 🟠 2 | #12 | Split `dashboard.css` | Small | Medium |
| 🟠 2 | #13 | Clarify figures.py ownership | Small | Medium |
| 🟠 2 | #14 | JS global state via `window` | Medium | Medium |
| 🟡 3 | #15 | ProcessPool worker count | Small | Medium |
| 🟡 3 | #16 | Worker process pre-warming | Small | Medium |
| 🟡 3 | #17 | Skip unchanged Plotly assets | Medium | High |
| 🟡 3 | #18 | ThreadPoolExecutor per batch | Small | Low |
| 🟡 3 | #19 | Bar-date vs time-based freshness | Medium | High |
| 🟡 3 | #21 | Stream processing (reduce peak RAM) | Large | High |
| 🟡 3 | #22 | Incremental yfinance download range | Medium | High |
| 🟢 4 | #23 | Magic number `28.2` in templates | Tiny | Medium |
| 🟢 4 | #24 | Hardcoded chart heights in JS | Small | Low |
| 🟢 4 | #25 | `linreg raw=True` | Tiny | Low |
| 🟢 4 | #26 | `enrichment_is_current()` coverage | Small | High |
| 🟢 4 | #27 | RateLimiter memory leak | Tiny | Medium |
| 🟢 4 | #28 | Lockfile / pinned deps | Small | High |
| 🟢 4 | #29 | Screener scan parallelism | Small | High |
| 🟢 4 | #30 | Structured timing logs | Small | Medium |

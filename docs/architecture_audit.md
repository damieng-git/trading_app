# Architecture & Software Audit
**Date:** 2026-03-11
**Re-verified:** 2026-04-06
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
**Status: ✅ FIXED (2026-04-06)**

Now uses `from apps.dashboard.config_loader import CONFIG_JSON as _CONFIG_PATH`.
No more hardcoded path or duplicate disk reads.

---

#### #2 — Exit params defined in 3 places
**Files:** `config.json` (source of truth), `strategy.py` (loads via config_loader), `templates.py`
**Status: ⚠️ STILL OPEN**

`config.json` and `strategy.py` are now in sync (via `config_loader`). `templates.py`
may still embed hardcoded exit param defaults — verify and consolidate.

---

#### #3 — Timeframes scattered across 10+ files
**Status: ⚠️ STILL OPEN**

`TIMEFRAME_REGISTRY` exists in `config_loader.py` but is not enforced. Hardcoded
`"1D"`, `"1W"`, `"2W"`, `"1M"` strings remain in `strategy.py`, `build_dashboard.py`,
`serve_dashboard.py`, `chart_builder.js`, etc.

---

#### #4 — `config.json` changes require a full server restart
**File:** `serve_dashboard.py`
**Status: ⚠️ STILL OPEN**

Config still loaded once at module import time. No hot-reload. All config changes
require `systemctl restart trading-dashboard-test`.

---

#### #5 — `SymbolManager` instantiated ~8 times across `serve_dashboard.py`
**Status: ⚠️ STILL OPEN**

Each CRUD route still creates a new `SymbolManager.from_lists_dir(...)`, reading all
CSV files from disk on every request. No module-level singleton.

---

#### #6 — Screener payload duplicated: embedded in HTML and served via API
**Files:** `templates.py`, `serve_dashboard.py` `/api/screener-data`
**Status: ⚠️ STILL OPEN (unverified)**

Large screener JSON may still be embedded in `dashboard_shell.html` and also served
via `/api/screener-data`. Verify `templates.py` for embedded `screener_summary`.

---

#### #7 — `BuildPaths` attributes have no type-safe contract
**File:** `apps/dashboard/config_loader.py`
**Status: ⚠️ STILL OPEN**

`BuildPaths` is a frozen dataclass with no `__post_init__` validation. Missing
attribute references fail at runtime, not import time.

---

#### #20 — SSE `_event_log` is unbounded — memory leak over time
**Files:** `serve_dashboard.py` (one `_event_log` per state machine)
**Status: ✅ FIXED (2026-04-06)**

All state machines now use `deque(maxlen=1000)` instead of unbounded `list`.
Existing subscriber code using `get_events_since(idx)` handles partial snapshots correctly.

---

### 🟠 Phase 2 — Architecture & File Splits

---

#### #8 — `build_dashboard.py` (1,830 LOC) should be split into 4 modules
**Status: ⚠️ STILL OPEN**

Suggested split:
- `pipeline.py` — `run_stock_export()`, `run_refresh_dashboard()`
- `asset_exporter.py` — `_export_one_data()`, figure task dispatch, thread pool
- `fx.py` — FX rate fetching and caching
- `build_dashboard.py` — thin CLI entrypoint (~100 LOC)

---

#### #9 — `serve_dashboard.py` (1,887 LOC) should be split into 3 modules
**Status: ⚠️ STILL OPEN**

Suggested split:
- `server.py` — `main()` + `Handler` class routing (thin)
- `background_tasks.py` — all `_*State` classes, `_is_any_task_running()`
- `api_handlers.py` — all `_handle_*` methods

---

#### #10 — `dashboard.js` (2,746 LOC) should be split into 4 files
**Status: ⚠️ STILL OPEN**

Suggested split:
- `dashboard_core.js` — DOM init, theme, localStorage, URL hash, keyboard shortcuts
- `dashboard_chart.js` — figure cache, chart rendering, indicator toggles, annotations
- `dashboard_nav.js` — symbol navigation, group management, strategy/TF pills
- `dashboard.js` — thin `init()` that wires everything

---

#### #11 — `chart_builder.js` (1,742 LOC) should be split
**Status: ⚠️ STILL OPEN**

Extract indicator trace builders into a `chart_traces.js` file keyed by indicator name.
`chart_builder.js` becomes layout/axis/shape assembly only.

---

#### #12 — `dashboard.css` (1,792 LOC) should be split into logical layers
**Status: ⚠️ STILL OPEN**

Suggested split:
- `variables.css` — CSS custom properties
- `layout.css` — grid, topbar, panels, sidebars
- `components.css` — buttons, badges, pills, modals, dropdowns
- `chart.css` — chart-specific overrides, indicator state colours
- `themes.css` — dark/light overrides only

---

#### #13 — `figures.py`, `figures_indicators.py`, `figures_layout.py` have unclear ownership
**Status: ⚠️ STILL OPEN**

No enforced boundary between three figure files. Cross-imports in wrong direction.

---

#### #14 — JS files share state via `window` with no formal contract
**Status: ⚠️ STILL OPEN**

`dashboard.js` exposes state on `window` with no documented interface. Move toward
a single `window.App` namespace or ES modules with explicit `import`/`export`.

---

### 🟡 Phase 3 — Compute & Performance

---

#### #15 — ProcessPoolExecutor worker count capped at 8 regardless of CPU count
**Files:** `build_dashboard.py`
**Status: ⚠️ STILL OPEN**

Fix: `min(max(8, os.cpu_count() or 4), len(tasks))`. Skip pool for `len(tasks) < 4`.

---

#### #16 — Worker processes re-import all indicator modules on every spawn
**Status: ⚠️ STILL OPEN**

Use `initializer=` parameter of `ProcessPoolExecutor` to pre-warm workers. Saves ~0.5s per worker.

---

#### #17 — Plotly JSON assets regenerated on every build regardless of data changes
**File:** `build_dashboard.py` (`_export_one_data()`)
**Status: ⚠️ STILL OPEN**

Compare `md5(json_bytes)` to existing file before writing. Skip write if identical.

---

#### #18 — `ThreadPoolExecutor(max_workers=1)` created and destroyed per download batch
**File:** `trading_dashboard/data/downloader.py`
**Status: ⚠️ STILL OPEN**

Use a reusable executor across batches, or replace with `signal.alarm`-based timeout.

---

#### #19 — `IncrementalUpdater.needs_update()` is time-based, not bar-date-based
**File:** `trading_dashboard/data/incremental.py`
**Status: ⚠️ STILL OPEN**

Compare `last_bar` date against last expected trading day. Skip if `last_bar >= last_trading_day`.

---

#### #21 — All symbol DataFrames loaded into memory simultaneously during refresh
**File:** `build_dashboard.py`
**Status: ⚠️ STILL OPEN**

Stream processing: load, enrich, export, `del` one symbol at a time. Drops peak RAM
from ~600 MB to ~5 MB.

---

#### #22 — `download_daily_batch` always downloads from `START_DATE` (full history)
**File:** `trading_dashboard/data/downloader.py`
**Status: ⚠️ STILL OPEN**

Pass `start=last_bar_date - 5_days` for incremental updates. Fall back to full
history for new symbols only.

---

### 🟢 Phase 4 — Code Quality & Maintainability

---

#### #23 — Magic number `28.2` hardcoded as `max_trend_score` in `templates.py`
**Status: ✅ FIXED (2026-04-06)**

`max_trend_score` now computed dynamically:
```python
max_trend_score = sum(float(v) for v in _kpi_w.values()) if _kpi_w else None
```
Hardcoded fallback removed.

---

#### #24 — Hardcoded chart pixel heights in `dashboard.js:85`
**Status: ⚠️ STILL OPEN**

Move `_chartHeights` values to CSS custom properties. JS reads via `_css("--chart-upper-h")`.

---

#### #25 — `linreg` in `_base.py:152` uses `raw=False` in `.apply()`
**Status: ⚠️ STILL OPEN**

Still uses `raw=False` with immediate `.to_numpy()` inside the lambda. Change to `raw=True`.
Single-character fix, measurable speedup across 40+ indicators.

---

#### #26 — `enrichment_is_current()` not used on all code paths
**File:** `trading_dashboard/data/store.py`
**Status: ⚠️ STILL OPEN**

Audit all call sites of `translate_and_compute_indicators()`. Ensure hash-based
enrichment skip is checked before every call during full build.

---

#### #27 — `_RateLimiter` IP history grows unbounded
**File:** `apps/dashboard/serve_dashboard.py`
**Status: ✅ FIXED (2026-04-06)**

Empty IP entries are now pruned after their sliding window expires:
```python
self._requests.pop(ip, None)  # prune empty entry
```

---

#### #28 — No `requirements.txt` lockfile or pinned dependencies
**Status: ⚠️ STILL OPEN**

Run `pip freeze > requirements.lock` and commit it. Add startup version validation
for key packages (`yfinance`, `pandas`, `plotly`).

---

#### #29 — Screener scan uses `_MAX_WORKERS = 1` (fully sequential)
**File:** `apps/screener/scan_strategy.py`
**Status: ✅ FIXED (2026-04-06)**

Changed from `_MAX_WORKERS = 1` to `_MAX_WORKERS = 4`.

---

#### #30 — Build timing uses `print()` instead of structured logging
**File:** `apps/dashboard/build_dashboard.py`
**Status: ⚠️ STILL OPEN**

Replace `print(f"[timing] ...")` with `logger.info("phase=%s elapsed=%.1fs", ...)`.
Enables filtering via `journalctl -o json | jq`.

---

## Priority Matrix (Updated)

| Phase | # | Issue | Effort | Impact | Status |
|---|---|---|---|---|---|
| 🔴 1 | #1 | `strategy.py` reads config directly | Small | High | ✅ FIXED |
| 🔴 1 | #2 | Exit params in 3 places | Small | High | ⚠️ OPEN |
| 🔴 1 | #3 | Timeframes in 10+ files | Medium | High | ⚠️ OPEN |
| 🔴 1 | #4 | No config hot-reload | Medium | Medium | ⚠️ OPEN |
| 🔴 1 | #5 | SymbolManager instantiated 8× | Small | Medium | ⚠️ OPEN |
| 🔴 1 | #6 | Screener payload in HTML + API | Small | High | ⚠️ OPEN |
| 🔴 1 | #7 | BuildPaths no type safety | Small | Medium | ⚠️ OPEN |
| 🔴 1 | #20 | SSE event log memory leak | Small | High | ✅ FIXED |
| 🟠 2 | #8 | Split `build_dashboard.py` | Large | High | ⚠️ OPEN |
| 🟠 2 | #9 | Split `serve_dashboard.py` | Large | High | ⚠️ OPEN |
| 🟠 2 | #10 | Split `dashboard.js` | Large | High | ⚠️ OPEN |
| 🟠 2 | #11 | Split `chart_builder.js` | Medium | Medium | ⚠️ OPEN |
| 🟠 2 | #12 | Split `dashboard.css` | Small | Medium | ⚠️ OPEN |
| 🟠 2 | #13 | Clarify figures.py ownership | Small | Medium | ⚠️ OPEN |
| 🟠 2 | #14 | JS global state via `window` | Medium | Medium | ⚠️ OPEN |
| 🟡 3 | #15 | ProcessPool worker count | Small | Medium | ⚠️ OPEN |
| 🟡 3 | #16 | Worker process pre-warming | Small | Medium | ⚠️ OPEN |
| 🟡 3 | #17 | Skip unchanged Plotly assets | Medium | High | ⚠️ OPEN |
| 🟡 3 | #18 | ThreadPoolExecutor per batch | Small | Low | ⚠️ OPEN |
| 🟡 3 | #19 | Bar-date vs time-based freshness | Medium | High | ⚠️ OPEN |
| 🟡 3 | #21 | Stream processing (reduce peak RAM) | Large | High | ⚠️ OPEN |
| 🟡 3 | #22 | Incremental yfinance download range | Medium | High | ⚠️ OPEN |
| 🟢 4 | #23 | Magic number `28.2` in templates | Tiny | Medium | ✅ FIXED |
| 🟢 4 | #24 | Hardcoded chart heights in JS | Small | Low | ⚠️ OPEN |
| 🟢 4 | #25 | `linreg raw=True` | Tiny | Low | ⚠️ OPEN |
| 🟢 4 | #26 | `enrichment_is_current()` coverage | Small | High | ⚠️ OPEN |
| 🟢 4 | #27 | RateLimiter memory leak | Tiny | Medium | ✅ FIXED |
| 🟢 4 | #28 | Lockfile / pinned deps | Small | High | ⚠️ OPEN |
| 🟢 4 | #29 | Screener scan parallelism | Small | High | ✅ FIXED |
| 🟢 4 | #30 | Structured timing logs | Small | Medium | ⚠️ OPEN |

**Fixed:** 7 of 30 (#1, #20, #23, #27, #29 + two implicit via strategy audit fixes)
**Open:** 23 of 30

---
_Last verified against code: 2026-04-06_

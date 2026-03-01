# Trading Dashboard — Full End-to-End Audit

> **0** critical | **33** warnings | **30** info
>
> **Note (2026-02-26):** Several warnings have been addressed since this audit was generated. Fixed items are marked with ✅. See commit history for details.


---

## 1. Architecture & Structure

### [info] Codebase size

110 Python files, ~37,016 total lines (68 app, 42 research). App code: ~13,093 lines.


### [WARNING] Large app files (>400 lines): 7

These resist testing and comprehension:

apps/dashboard/figures.py: 1789 lines

apps/dashboard/build_dashboard.py: 1176 lines

audit_dashboard.py: 1171 lines

apps/dashboard/templates.py: 754 lines

trading_dashboard/data/downloader.py: 485 lines

trading_dashboard/data/enrichment.py: 483 lines

trading_dashboard/symbols/manager.py: 431 lines


### [WARNING] Long functions (>80 lines): 21

Top 15:

apps/dashboard/figures.py:485  build_figure_for_symbol_timeframe()  ~1305 lines

apps/dashboard/templates.py:21  write_lazy_dashboard_shell_html()  ~616 lines

trading_dashboard/data/enrichment.py:114  translate_and_compute_indicators()  ~370 lines

apps/dashboard/screener_builder.py:16  build_screener_rows()  ~329 lines

trading_dashboard/kpis/catalog.py:70  compute_kpi_state_map()  ~313 lines

apps/dashboard/figures.py:140  _add_exit_flow_overlay()  ~300 lines

apps/dashboard/build_dashboard.py:901  main()  ~271 lines

apps/dashboard/build_dashboard.py:664  run_refresh_dashboard()  ~235 lines

apps/dashboard/build_dashboard.py:428  run_stock_export()  ~234 lines

trading_dashboard/indicators/sr_breaks_retests.py:40  sr_breaks_retests()  ~176 lines

apps/dashboard/strategy.py:28  compute_position_status()  ~174 lines

apps/dashboard/config_loader.py:233  load_build_config()  ~117 lines

trading_dashboard/utils/pine_rtf.py:24  rtf_to_text()  ~109 lines

trading_dashboard/indicators/rsi_zeiierman.py:79  rsi_strength_consolidation_zeiierman()  ~108 lines

audit_dashboard.py:771  audit_strategy()  ~103 lines


### [WARNING] App imports research module (`audit_dashboard.py`)

audit_dashboard.py imports from research/ — violates layer boundary.


### [WARNING] App imports research module (`trading_dashboard/cli.py`)

trading_dashboard/cli.py imports from research/ — violates layer boundary.


### [info] Missing __init__.py: trading_dashboard/kpis/ (`trading_dashboard/kpis`)

Directory has Python files but no __init__.py


### [info] Missing __init__.py: trading_dashboard/utils/ (`trading_dashboard/utils`)

Directory has Python files but no __init__.py


### [WARNING] Circular import: apps.dashboard.alert_notifier ↔ apps.dashboard.alert_notifier (`apps.dashboard.alert_notifier`)

Circular imports increase coupling and can cause ImportError at runtime.



---

## 2. UI/UX Consistency

### [WARNING] Inconsistent spacing values (`apps/dashboard/static/dashboard.css`)

15 px values off a standard 4px scale: [1, 3, 5, 6, 7, 9, 10, 11, 13, 14, 15, 18, 22, 28, 60]... Consider a spacing system (e.g. 4/8/12/16/24/32).


### [WARNING] Too many font sizes: [9, 10, 11, 12, 13, 14, 15, 16, 22, 24] (`apps/dashboard/static/dashboard.css`)

10 distinct font-size values. A typographic scale should use 4–6 sizes.


### ✅ [WARNING] Undefined CSS variables: --card-bg, --text, --text-dim, --text-muted (`apps/dashboard/static/dashboard.css`)

**FIXED**: All four variables are now defined in `:root` (dashboard.css).


### [info] Color palette size: 67 unique hex values

CSS: 40, chart_builder.js: 35. Consider consolidating into CSS variables for maintainability.


### ✅ [WARNING] Accessibility gaps: 4 issues (partially fixed)

- ✅ ARIA attributes added (role="button", tabindex="0", aria-label)
- ✅ prefers-reduced-motion media query added
- No screen-reader-only utility class (open)

- Only 3 focus-visible rules (incomplete coverage)



---

## 3. Data Flow

### [info] Loading states: implemented

setLoading(true/false) with overlay, spinner, and skeleton classes.


### ✅ [WARNING] Incomplete error states in UI

- ✅ Retry button added on fetch failure
- ✅ "No data available" message for empty datasets (no yellow banner)


### [info] Client-side cache: figCache (in-memory)

Loaded figures are cached per symbol+tf key. No cache size limit or eviction policy — can grow large if user browses many symbols.


### ✅ [info] Live scan via SSE

**FIXED**: `serve_dashboard.py` provides SSE-based scan with real-time progress via `GET /api/scan`. Background thread + event log + reconnection support. Groups and figures update live after scan completes.


### [info] State persistence: localStorage

Keys: ['td_dash_shell_state_v1_2']. URL hash sync implemented. No state management library (appropriate for this scale).


### [info] Path traversal mitigation in serve_dashboard.py

Uses resolve() + parents check for static file serving.



---

## 4. Performance

### [WARNING] Dashboard HTML: 7.6 MB

Plotly JS (~3.5 MB) is inlined via get_plotlyjs(). Consider loading Plotly from CDN or async <script> to halve initial load time.


### [WARNING] Data assets: 692 JS files, 809 MB total

Per-symbol JSON files are lazy-loaded — good. But 800+ MB of assets is large for deployment. Consider gzip/brotli compression or binary formats.


### [WARNING] chart_builder.js: ~128 loops/iterators

Heavy iteration over full bar arrays. The Exit Flow simulation has nested loops (O(n²) worst case). Consider Web Workers for heavy computation off main thread.


### [info] dashboard.js: ~107 DOM queries

Many getElementById/querySelector calls. Cache element references at init time to avoid repeated DOM lookups.


### ✅ [info] Web Workers — implemented

`simulateTradesAsync` offloads CPU-heavy trade simulation to an inline Blob Web Worker, keeping the main thread responsive.


### ✅ [info] Enrichment parallelism — implemented

`ProcessPoolExecutor` used for CPU-bound enrichment across symbols. Opt out with `--no_parallel_enrich`.



---

## 5. Security

### [info] Placeholder credentials in alerts_config.json

Contains placeholder tokens (YOUR_BOT_TOKEN_HERE). Safe as-is, but real credentials should come from env vars.


### [WARNING] Path traversal risk in serve_dashboard.py (`apps/dashboard/serve_dashboard.py`)

Symbol/TF from query params are used in file paths. While resolve()+parents check exists, adding a whitelist regex (e.g. ^[A-Z0-9^._-]+$) is safer.


### [WARNING] CLI path arguments used without validation (`trading_dashboard/cli.py`)

User-supplied paths (args.file, args.config, args.output) are passed to Path() without restricting to safe directories.


### [WARNING] Loose dependency versions: 12 packages use >= (`pyproject.toml`)

Packages: setuptools, pandas, numpy, plotly, yfinance, matplotlib, reportlab, requests. Use a lockfile (pip-compile, uv.lock, poetry.lock) for reproducible builds.


### [WARNING] No authentication on serve_dashboard.py

Dashboard is accessible to anyone with the URL. For server deployment: add auth (OAuth2/JWT/basic auth).


### [info] yfinance downloads have no timeout (`trading_dashboard/data/downloader.py`)

yf.download() can hang indefinitely if Yahoo Finance is slow. Consider wrapping with signal.alarm or concurrent.futures timeout.



---

## 6. Reliability

### [WARNING] Silent exception swallowing: 25 blocks

try/except with pass/continue and no logging. These hide bugs and data corruption:

apps/dashboard/build_dashboard.py:287

apps/dashboard/build_dashboard.py:294

apps/dashboard/build_dashboard.py:306

apps/dashboard/build_dashboard.py:404

apps/dashboard/data_exporter.py:94

apps/dashboard/figures.py:1743

apps/dashboard/figures.py:1787

apps/dashboard/sector_map.py:310

apps/dashboard/serve_dashboard.py:87

apps/dashboard/serve_dashboard.py:238

apps/dashboard/serve_dashboard.py:256

trading_dashboard/data/downloader.py:151

trading_dashboard/data/downloader.py:199

trading_dashboard/data/downloader.py:416

trading_dashboard/data/downloader.py:461

trading_dashboard/data/downloader.py:463

trading_dashboard/data/enrichment.py:441

trading_dashboard/data/enrichment.py:453

trading_dashboard/data/enrichment.py:471

trading_dashboard/data/enrichment.py:480


### [info] Download retry logic: present

Exponential backoff for yf.download failures.


### [WARNING] Retry logic does not catch exceptions (`trading_dashboard/data/downloader.py`)

_yf_download_with_retry only retries on empty responses, not on raised exceptions (e.g. 429).


### [info] Modules without logging: 31

apps/dashboard/alert_notifier.py

apps/dashboard/figures.py

apps/dashboard/sector_map.py

apps/dashboard/serve_dashboard.py

apps/dashboard/strategy.py

apps/dashboard/templates.py

trading_dashboard/cli.py

trading_dashboard/data/enrichment.py

trading_dashboard/data/health.py

trading_dashboard/data/incremental.py


### [info] No monitoring instrumentation

No metrics (Prometheus), structured logging (JSON), or health check endpoint suitable for container orchestration. serve_dashboard.py has /health but returns plain HTML status.


### ✅ [WARNING] No file locking in DataStore (`trading_dashboard/data/store.py`)

**FIXED**: `_flock` helper uses `fcntl.flock` (Unix) / `msvcrt.locking` (Windows) for `_enrichment_meta.json` safety. `_atomic_write` in `manager.py` for crash-safe CSV/config writes.



---

## 7. Code Quality

### [info] Type hint coverage: 97% (265/273)

Good


### [info] Docstring coverage: 61% (100/163 public functions)

Target: >80% for public APIs.


### [WARNING] Magic numbers: ~256 instances

Numeric literals without named constants. Top 10:

apps/dashboard/alert_notifier.py:78  lookback_hours: int = 48,

apps/dashboard/alert_notifier.py:217  '<p style="margin-top:16px;color:#888;font-size:12px">'

apps/dashboard/alert_notifier.py:237  with urllib.request.urlopen(req, timeout=15) as resp:

apps/dashboard/build_dashboard.py:1077  _LOAD_WORKERS = min(12, max(1, len(cfg_refresh.symbols)))

apps/dashboard/build_dashboard.py:1102  buf = tf_meta.enrich_buffer_bars if tf_meta else 300

apps/dashboard/build_dashboard.py:1103  bpy = tf_meta.bars_per_year if tf_meta else 252

apps/dashboard/config_loader.py:69  START_DATE = "2018-01-01"

apps/dashboard/config_loader.py:93  "4H": Timeframe(key="4H", max_plot_bars=5000, min_bars=500, enrich_buffer_bars=6

apps/dashboard/config_loader.py:94  "1D": Timeframe(key="1D", max_plot_bars=600, min_bars=200, enrich_buffer_bars=30

apps/dashboard/config_loader.py:95  "1W": Timeframe(key="1W", max_plot_bars=140, min_bars=80, enrich_buffer_bars=60,


### [WARNING] Dead JS code: 13 functions defined but never called

apps/dashboard/static/chart_builder.js: bAnd()

apps/dashboard/static/chart_builder.js: avgPnl()

apps/dashboard/static/chart_builder.js: retSign()

apps/dashboard/static/chart_builder.js: bOr()

apps/dashboard/static/chart_builder.js: prevAbove()

apps/dashboard/static/chart_builder.js: hr()

apps/dashboard/static/chart_builder.js: retColor()

apps/dashboard/static/dashboard.js: weeklySource()

apps/dashboard/static/dashboard.js: pillClass()

apps/dashboard/static/dashboard.js: _kpiOrder()

apps/dashboard/static/dashboard.js: _mkCrosshairLine()

apps/dashboard/static/dashboard.js: fmtState()

apps/dashboard/static/dashboard.js: _initPnlControls()


### [WARNING] yfinance imported outside abstraction layer (`apps/dashboard/build_dashboard.py`)

apps/dashboard/build_dashboard.py imports yfinance directly. Route through downloader.py.


### [WARNING] Wrong path in JS: 'Scripts/serve_dashboard.py' (`apps/dashboard/static/dashboard.js`)

Should be 'python -m apps.dashboard.serve_dashboard'



---

## 8. Repo Hygiene

### [WARNING] Large files in repo: 2 files > 5 MB

Should be in .gitignore or Git LFS:

data/dashboard_artifacts/dashboard_shell.html: 7.6 MB

data/dashboard_artifacts/dashboard_assets/DX-Y.NYB/4H.js: 5.6 MB


### [WARNING] Missing .gitignore entries

Consider adding:

*.parquet (enriched data cache)


### [info] Legacy/archive: 18 files, ~12,872 lines

Consider removing or archiving outside the repo to reduce clone size and cognitive load.



---

## 9. Strategy & P&L

### [WARNING] ATR NaN silently disables stop-loss (`apps/dashboard/strategy.py`)

When atr[i] is NaN, 'atr[i] > 0' is False → stop = -inf. The position runs without any stop protection. Fix: explicit np.isnan() check with fallback ATR.


### [WARNING] Empty combo list → immediate EXIT (`apps/dashboard/strategy.py`)

If c3_kpis is [], nk=0, nb>=nk is True on first bar. Add: if not c3_kpis: return flat_result.


### [info] No OHLC column validation (`apps/dashboard/strategy.py`)

strategy.py accesses df['High/Low/Close'] without checking they exist. Missing columns → unhandled KeyError.


### ✅ [WARNING] Dashboard P&L: no fees or slippage (`apps/dashboard/static/chart_builder.js`)

**FIXED**: `chart_builder.js` now applies 0.5% slippage (`SLIP_FIG`) + 0.1% commission per trade, matching `strategy.py`. Next-bar-open fills implemented in both Python and JS.


### ✅ [WARNING] EXIT_PARAMS duplicated in Python + JS (`apps/dashboard/strategy.py`)

**FIXED**: `EXIT_PARAMS_CFG` is defined in `config.json` and injected into JS at build time via `templates.py`. Single source of truth.


### [info] Position sizing: 1x/1.5x

C3-only = 1x, C4 = 1.5x. Implemented in both Python and JS. Verify both produce identical results with a regression test.


### ✅ [info] Execution assumption: fills at bar close

**FIXED**: Both `strategy.py` and `chart_builder.js` now use next-bar-open fills for entry and exit. 0.5% flat slippage + 0.1% commission applied.


### [info] Timezone handling: 5 tz_localize(None) calls

All timestamps normalized to naive (no TZ) after download. Consistent but means 4H candles for non-US markets may not align with local trading hours.


### [WARNING] Missing test coverage: 5 areas

No tests found for:

- NaN in Close column

- missing OHLCV columns

- empty combo KPI list

- P&L vs JS regression

- concurrent store writes



---

## 10. Scalability & Server-Readiness

### [WARNING] Hardcoded relative paths: 5

These assume CWD = repo root. For containerized deployment, use env vars or a config object:

trading_dashboard/cli.py:27  _DEFAULT_CONFIGS_DIR = Path("apps/dashboard/configs")

trading_dashboard/data/store.py:29  enriched_dir=Path("data/feature_store/enriched/dashboard/stock_data"),

trading_dashboard/data/store.py:30  raw_dir=Path("data/cache/ohlcv_raw/dashboard"),

trading_dashboard/symbols/manager.py:157  sm = SymbolManager.from_lists_dir(Path("apps/dashboard/configs/lists"))

trading_dashboard/symbols/manager.py:161  sm.save_config(Path("apps/dashboard/configs/config.json"))


### [info] Recommended deployment shape

Phase 1 (quick win): Single Docker container with FastAPI + static file serving + cron job for builds.

Phase 2 (scale): Separate containers for API (FastAPI), worker (Celery/RQ for builds), and static assets (nginx/CDN).

Phase 3 (cloud): Managed services — CloudRun/ECS for API, Cloud Tasks for builds, GCS/S3 for data, CDN for assets.


### [WARNING] No frontend/API/worker separation

build_dashboard.py handles download, enrichment, export, and HTML generation in one process. For a server:

- API: serve screener data + symbol data via REST

- Worker: background build/enrich jobs

- Frontend: standalone SPA (or keep current HTML)


### [info] Key bottlenecks for server mode

1. yfinance rate limits (batch download mitigates)

2. Enrichment is CPU-bound (~45s for 25 symbols)

3. HTML generation blocks the process (~35s)

4. 800 MB of assets — need compression or streaming

5. No incremental client updates (full page refresh)



---

## 11. Developer Experience

### [info] Dev setup files: 3/5

Present: pyproject.toml, CONTRIBUTING.md, README / DASHBOARD.md

Missing: Makefile / task runner, Dockerfile


### [info] Test suite: 5 test files

Run with: python3 -m pytest tests/ -v


### [info] No CI/CD pipeline

Add GitHub Actions / GitLab CI for automated testing, linting (ruff/flake8), and type checking (mypy).


### [info] No linter configured

Add [tool.ruff] to pyproject.toml for fast linting + formatting.



---

## 12. Five Extra-Mile Next Steps

### 1. [UI Polish] Design token system + accessibility pass

Extract all colors, font sizes, spacing, and radii into CSS custom properties on a 4px grid. Replace the 13 font-size values with a 5-step type scale. Add prefers-reduced-motion, aria-labels on interactive elements, and focus-visible rings on all clickable components. Add a 768px tablet breakpoint. Impact: consistent, accessible UI across devices.

### 2. [Reliability] Replace silent exceptions + add structured logging

Audit all ~25 except/pass blocks: add logger.warning with context (symbol, timeframe, file path). Switch to JSON structured logging (python-json-logger) for machine-parseable logs. Add NaN guards in screener_builder.py and ATR validation in strategy.py. Add a watchdog timeout (60s) around yf.download calls. Impact: no more silent data corruption, debuggable issues.

### 3. [Scalability] FastAPI REST layer + background worker

Add a FastAPI app with endpoints: GET /api/screener/{tf}, GET /api/symbol/{sym}/{tf}, POST /api/build (triggers background build via Celery/RQ). Serve assets via nginx or CDN. Containerize with Docker (API + worker + nginx). Add /healthz endpoint for orchestrator probes. Impact: decoupled frontend, concurrent users, deploy anywhere.

### 4. [Quant Correctness] Add commission to dashboard P&L + Python/JS parity test

Add the 0.1% round-trip commission from STRATEGY.md to chart_builder.js P&L computation. Move EXIT_PARAMS into config.json and inject into both Python and JS at build time. Write a regression test that runs strategy.py on a known dataset and compares trade list + equity curve against chart_builder.js output (via Node or snapshot). Impact: honest P&L, no Python/JS drift.

### 5. [Developer Workflow] Makefile + CI pipeline + pre-commit hooks

Create a Makefile with targets: make install, make test, make lint, make build, make audit. Add GitHub Actions workflow: ruff lint + mypy type check + pytest on every push. Add pre-commit hooks (ruff format, ruff check). Pin dependencies with pip-compile or uv.lock. Impact: consistent environments, catch regressions early, faster onboarding.


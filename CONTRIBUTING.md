# Contributing to Trading Dashboard

## Prerequisites

- Python 3.11+
- Install in development mode:

```bash
pip install -e ".[dev]"
```

## Project Layout

See `CLAUDE.md` for the authoritative package structure and data flow.

## Running Tests

```bash
# All tests
python3 -m pytest tests/ -v

# With coverage
python3 -m pytest tests/ --cov=trading_dashboard --cov=apps --cov-report=term-missing

# Single file
python3 -m pytest tests/test_strategy.py -v
```

## Adding a New Indicator

1. Create `trading_dashboard/indicators/your_indicator.py`
2. Subclass `IndicatorDefinition` from `_base.py`
3. Register it in `registry.py` with dimension and KPI name
4. Add default parameters to `apps/dashboard/configs/indicator_config.json`
5. Run tests: `python3 -m pytest tests/test_indicators.py -v`

## Adding a New KPI

1. Add the KPI rule to `trading_dashboard/kpis/rules.py`
2. Register the KPI in `trading_dashboard/kpis/catalog.py`
3. Update `kpi_weights` in `apps/dashboard/configs/config.json`
4. Run tests to verify: `python3 -m pytest tests/ -v`

## Build Modes

| Mode | Command | Use When |
|------|---------|----------|
| `build` | `python3 -m trading_dashboard dashboard build` | Weekly full refresh |
| `refresh` | `python3 -m trading_dashboard dashboard refresh` | Config change, no new data |
| `rebuild-ui` | `python3 -m trading_dashboard dashboard rebuild-ui` | UI/template changes only |
| `re-enrich` | `python3 -m trading_dashboard dashboard re-enrich` | After indicator code changes |
| `export` | `python3 -m trading_dashboard dashboard export` | Data only, no HTML |

Useful flags:
- `--force-recompute` — bypass enrichment cache (content-hash skip)
- `--skip-figures` — screener-only rebuild, no charts

## Performance Notes

- **Batch downloading**: `downloader.py` provides `download_daily_batch` / `download_hourly_batch` which pass all tickers to a single `yf.download()` call (chunked at 50). This is ~7x faster than per-symbol downloads. `build_dashboard.py` uses these by default.
- **Content-hash caching**: enriched parquet files store MD5 fingerprints of raw data + indicator config. Unchanged data skips enrichment automatically.
- **Vectorize loops**: prefer NumPy/Pandas vectorized operations over Python loops in indicators. Use `pandas.ewm()` for exponential smoothing.
- **Parallel I/O**: `sector_map.py` uses `ThreadPoolExecutor` for yfinance API calls. CPU-bound enrichment uses `ProcessPoolExecutor` (opt out with `--no_parallel_enrich`).
- **Gzip assets**: every JS asset is also written as `.gz` companion for pre-compressed serving.
- **Web Worker**: `simulateTradesAsync()` offloads trade simulation to an inline Blob Web Worker, keeping the UI thread responsive during P&L computation.
- **DOM caching**: `dashboard.js` caches 9 frequently accessed DOM elements in a `DOM` object to avoid repeated `getElementById` calls.

## Reliability

- **Download retry**: `_yf_download_with_retry` retries up to 3× with exponential backoff; `_download_with_timeout` enforces a 60s timeout per download.
- **File locking**: `_flock` in `store.py` uses `fcntl.flock` (Unix) / `msvcrt.locking` (Windows) for metadata safety.
- **Atomic writes**: `_atomic_write` in `manager.py` writes to tempfile then `os.replace` for crash-safe CSV/config updates.
- **Structured logging**: all `except: pass` blocks log via `logger.debug()` with context — never silently swallow errors.
- **Data guards**: ATR NaN checks, empty combo guards, int(NaN) safety, OHLC column validation — all added to prevent runtime crashes.
- **Scan safety**: `_ScanState` singleton prevents concurrent scans, stores event log for reconnection.

## CSS & UI Guidelines

- **Design tokens**: use `--space-{xs..xl}` for spacing and `--font-{xs..xl}` for font sizes (defined in `:root`)
- **Colors**: use semantic CSS variables (`--success`, `--danger`, `--warning`, `--info`, `--text`, `--text-muted`, `--card-bg`, etc.) — never hardcode hex values for colors that already have a variable
- **Accessibility**: all interactive elements must have `role="button"`, `tabindex="0"`, and `aria-label`
- **Responsive**: test at 768px and 480px breakpoints; use `@media (prefers-reduced-motion: reduce)` for animation-sensitive users

## Documentation

| Document | Content |
|----------|---------|
| `CLAUDE.md` | Architecture, data flow, CLI reference, deployment |
| `docs/strategy_pipeline_design.md` | Strategy engine design, adding new strategies |
| `docs/chart_render_spec.md` | Chart tab render contract |
| `docs/architecture_audit.md` | Open improvement backlog |
| `docs/changelog.md` | Full change history |
| `docs/screener.md` | Screener pipeline, scan architecture, detection logic |

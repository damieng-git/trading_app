# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (editable + dev deps)
pip install -e ".[dev]"

# Lint
ruff check trading_dashboard/ apps/ tests/

# Type check
mypy trading_dashboard/ apps/ --ignore-missing-imports

# Run all tests
pytest tests/ -v --cov=trading_dashboard --cov=apps --cov-report=term-missing

# Run a single test file
pytest tests/test_strategy.py -v

# Run a single test by name
pytest tests/test_indicators.py::test_ema -v
```

## Dashboard CLI

All commands are run from the project root as `python -m trading_dashboard <cmd>`.

```bash
# Full pipeline: download OHLCV → compute indicators → build dashboard
python -m trading_dashboard dashboard build

# Refresh dashboard from cached OHLCV (no yfinance download)
python -m trading_dashboard dashboard refresh

# Rebuild UI only (fastest — skip indicator recomputation)
python -m trading_dashboard dashboard rebuild-ui

# Serve the generated dashboard locally
python -m apps.dashboard.serve_dashboard

# Daily screener (C3/C4 signal scan)
python -m trading_dashboard screener run
python -m trading_dashboard screener run --cached --no-dashboard

# Symbol management
python -m trading_dashboard symbols list
python -m trading_dashboard symbols add AAPL --group watchlist
python -m trading_dashboard symbols sync
```

## Architecture

### Data flow

```
yfinance → downloader.py → OHLCV parquet cache
                         → enrichment.py (computes all indicators)
                         → feature store: data/feature_store/enriched/<dataset>/stock_data/<SYM>_<TF>.parquet
                         → build_dashboard.py → Plotly JSON assets + dashboard_shell.html
```

### Package structure

- **`trading_dashboard/`** — core library
  - `indicators/` — Pine Script → Python indicator implementations. Each file is a self-contained indicator. `_base.py` provides shared primitives (sma, ema, rma, rsi, atr, etc.). `registry.py` defines KPI display order.
  - `kpis/` — KPI state computation (`catalog.py`) and bull/bear rules (`rules.py`). Converts indicator output to binary 1/0/-1 states.
  - `data/` — OHLCV downloading (`downloader.py`), incremental updates (`incremental.py`), feature store (`store.py`), enrichment pipeline (`enrichment.py`).
  - `symbols/` — Symbol group management (`manager.py`); reads from `apps/dashboard/configs/lists/*.csv`.
  - `cli.py` — Argparse CLI entry point.

- **`apps/dashboard/`** — dashboard application layer
  - `build_dashboard.py` — orchestrates the full build pipeline.
  - `config_loader.py` — loads `configs/config.json`; defines all path constants (`DASHBOARD_SHELL_HTML`, `FEATURE_STORE_ENRICHED_DIR`, etc.) and `BuildConfig`/`BuildPaths` dataclasses.
  - `strategy.py` — **Entry v5 + Exit Flow v4** position engine. `compute_position_events()` is the single source of truth for all entry/exit logic. `compute_polarity_position_events()` handles mixed-polarity strategies.
  - `screener_builder.py` — builds screener rows from enriched data + KPI states.
  - `figures.py` / `figures_indicators.py` / `figures_layout.py` — Plotly chart construction.
  - `templates.py` — HTML shell generation for the lazy-load dashboard.
  - `configs/config.json` — runtime config: symbols, timeframes, KPI weights, exit params, strategy setups.

- **`apps/screener/`** — standalone daily screener using the same indicator/KPI stack.

### Strategy engine

The core trading logic lives in `apps/dashboard/strategy.py`. Two engines:

1. **`compute_position_events`** — bullish-only combo (C3 = all KPIs bullish, C4 = scale-up). Entry gates: C3 onset, SMA20 > SMA200 (1D/1W), volume spike 1.5× MA20 within 5 bars, 1W overextension ≤15%.
2. **`compute_polarity_position_events`** — polarity-aware engine for mixed-polarity strategies (each KPI checked against its expected state rather than always bullish).

Exit logic: ATR stop (K × ATR14), full invalidation within T bars, 2/N KPIs turning after T bars, M-bar checkpoint trailing stop.

Strategy setups are configured in `config.json` under `strategy_setups` and dispatched in `screener_builder.py` and chart renderers.

### KPI system

Indicators output numeric columns into enriched DataFrames. `kpis/catalog.py` maps those columns to binary KPI states (1 = bull, -1 = bear, 0 = neutral). KPI weights in `config.json` are used to compute a weighted trend score for screener ranking.

### Timeframes

5 timeframes: `4H`, `1D`, `1W`, `2W`, `1M`. 4H is resampled from 1H yfinance data; 2W/1M are resampled from 1D. Weekly resampling anchors to Friday (`W-FRI`).

### Dashboard output

`data/dashboard_artifacts/dashboard_shell.html` is a lazy-loading shell. Per-symbol Plotly JSON assets live in `data/dashboard_artifacts/dashboard_assets/`. The shell fetches assets on demand to keep initial load fast.

### Configuration

`apps/dashboard/configs/config.json` is the primary runtime config. Symbol lists are CSV files in `apps/dashboard/configs/lists/`. `indicator_config.json` controls which indicators are active and their parameters.

## Deployment & Server Management

### Server layout

| | Production | Staging |
|---|---|---|
| Repo | `trading_app` | `trading_app_test` (this repo) |
| Port | 8050 | 8051 |
| URL | `http://46.224.149.54/` | `http://46.224.149.54/test/` |
| Systemd service | `trading-dashboard` | `trading-dashboard-test` |
| Data root | `trading_app/data/` | `trading_app_test/data/` |

Nginx config: `/etc/nginx/sites-enabled/trading-dashboard` (not in git — must be recreated manually if server is rebuilt).
- `/test/*` → strips prefix → proxies to 8051 (staging)
- `/api/*`, `/fig/*` → proxies to 8050 (prod)
- `/*` → proxies to 8050 (prod)

**Critical:** the `/test/` location block must include `proxy_buffering off`, `proxy_cache off`, `proxy_set_header Connection ''`, and `proxy_read_timeout 86400s` — otherwise SSE streams (scan, refresh, rebuild-ui) are buffered by nginx and appear to hang in the browser.

Both services use `/root/damiverse_apps/trading_app/.venv/bin/python` (`trading_app_test` has no venv of its own).

### Process management — systemd only

Both servers are managed exclusively by **systemd**. Do NOT use `pm2` or manual `python3` invocations.

```bash
systemctl restart trading-dashboard        # restart prod
systemctl restart trading-dashboard-test   # restart staging
systemctl status trading-dashboard         # check prod status
journalctl -u trading-dashboard-test -f    # tail staging logs
```

Do NOT `kill <pid>` directly — systemd will immediately respawn the process (`Restart=always`). Always use `systemctl stop/restart`.

### When to restart vs. click UI Refresh

| Change type | Action |
|---|---|
| `apps/dashboard/static/*.js` or `*.css` | Click **UI Refresh** in the dashboard |
| `apps/dashboard/templates.py` | Click **UI Refresh** in the dashboard |
| `apps/dashboard/serve_dashboard.py` | `systemctl restart trading-dashboard-test` |
| `configs/config.json` | `systemctl restart trading-dashboard-test` |

The **UI Refresh** button (in the dashboard toolbar) calls `/api/rebuild-ui` on the server, which regenerates `dashboard_shell.html` from the current JS/CSS/templates without re-downloading data (~5 min).

### CLI rebuild-ui — always set TRADING_APP_ROOT

`config_loader.py` resolves the data root from the `TRADING_APP_ROOT` env var. When running CLI commands manually, always set it explicitly or the output will go to the wrong path:

```bash
# Staging rebuild (correct)
TRADING_APP_ROOT=/root/damiverse_apps/trading_app_test python3 -m trading_dashboard dashboard rebuild-ui

# Production rebuild (correct — only after git pull in trading_app)
TRADING_APP_ROOT=/root/damiverse_apps/trading_app python3 -m trading_dashboard dashboard rebuild-ui
```

The systemd services already set `TRADING_APP_ROOT` correctly — this only affects manual terminal runs.

### Promoting staging → production

```bash
# 1. Pull changes into prod repo
cd /root/damiverse_apps/trading_app && git pull

# 2. Restart prod server if serve_dashboard.py changed
systemctl restart trading-dashboard

# 3. Rebuild prod shell (JS/CSS/templates changes)
TRADING_APP_ROOT=/root/damiverse_apps/trading_app python3 -m trading_dashboard dashboard rebuild-ui
# or click UI Refresh in the prod dashboard (once the endpoint is available in prod)
```

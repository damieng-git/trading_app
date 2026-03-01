# Trading Dashboard — Master Documentation

> Single source of truth for architecture, data flow, UI structure, and reproducibility.
> Last updated: 2026-03-01

---

## 1. Architecture Overview

```
yfinance API
     │
     ▼
┌─────────────────────────────────────────────────────────┐
│  BUILD PIPELINE  (Python)                               │
│                                                         │
│  downloader.py ─► enrichment.py ─► store.py (parquet)   │
│       │                                                 │
│       ▼                                                 │
│  sector_map.py ──► sector_map.json (identity + funds.)  │
│       │                                                 │
│       ▼                                                 │
│  build_dashboard.py  (orchestrator)                     │
│       │                                                 │
│       ├─► data_exporter.py  ──► JS asset files (JSON)   │
│       ├─► screener_builder.py ──► screener payload       │
│       └─► templates.py ──► dashboard_shell.html          │
└─────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────┐
│  LOCAL SERVER  (serve_dashboard.py)                     │
│                                                         │
│  ThreadingHTTPServer on port 8050                       │
│       ├─ GET  /dashboard_shell.html  (static HTML)      │
│       ├─ GET  /fig?symbol=X&tf=Y     (live Plotly JSON)  │
│       ├─ GET  /api/scan              (SSE scan stream)   │
│       ├─ GET  /api/refresh           (SSE refresh stream) │
│       ├─ GET  /api/scan/status       (task state check)  │
│       ├─ GET  /api/groups            (symbol groups)     │
│       ├─ POST /api/move              (move ticker)       │
│       ├─ POST /api/delete            (delete ticker+data)│
│       ├─ _ScanState singleton        (background thread) │
│       └─ _RefreshState singleton     (background thread) │
│            └─ event log + subscriber model for SSE       │
└─────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────┐
│  CLIENT-SIDE  (Browser)                                 │
│                                                         │
│  dashboard_shell.html                                   │
│       ├─ chart_builder.js  (Plotly figure construction)  │
│       ├─ dashboard.js      (UI: tabs, sidebar, screener) │
│       └─ dashboard.css     (theming + layout)            │
│                                                         │
│  In server mode: figures loaded via /fig endpoint        │
│  In static mode: figures loaded from JS asset files      │
│  All charts rendered client-side via Plotly.react()      │
└─────────────────────────────────────────────────────────┘
```

### Key Design Decisions

1. **Client-side rendering** — Plotly figures are built in the browser from raw data JSON (not pre-rendered server-side). This enables dynamic hover, zoom sync, and indicator toggling without rebuilding HTML.
2. **Single metadata file** — `sector_map.json` is the single source of truth for identity (name, sector, industry), geography, ETF benchmarks, and fundamentals (P/E, market cap, analyst rating, etc.).
3. **Incremental updates** — `DataStore` uses parquet with TTL-based caching. Only stale symbols trigger yfinance downloads.
4. **Content-hash enrichment skip** — enriched parquet files store MD5 fingerprints of the raw OHLCV data and `indicator_config.json`. On subsequent runs, enrichment is skipped if both hashes match, avoiding redundant indicator recomputation.
5. **Batch downloading** — `download_daily_batch` / `download_hourly_batch` send all tickers to yfinance in a single `yf.download()` call (chunked at 50). This is ~7x faster than per-symbol sequential downloads (benchmarked: 4.7s vs 33.9s for 25 indices).
6. **Offline modes** — `refresh_dashboard` and `rebuild_ui` modes never call yfinance; they work entirely from cached data.
7. **EXIT_PARAMS in config** — strategy exit parameters (T, M, K per timeframe) are stored in `config.json` and injected into both the Python strategy engine and the client-side JS chart builder, ensuring a single source of truth.
8. **Gzip companion assets** — every JS asset file is also written as `.gz` for servers that support pre-compressed static files.
9. **Web Worker for trade simulation** — `simulateTrades` is duplicated into an inline Blob Web Worker (`simulateTradesAsync`), offloading CPU-heavy P&L computation from the main thread.
10. **Parallel enrichment** — `ProcessPoolExecutor` for CPU-bound indicator computation across symbols (opt out with `--no_parallel_enrich`).
11. **Background scan/refresh with reconnection** — Scan and Refresh run in `_ScanState`/`_RefreshState` singletons (background threads), not inside the HTTP handler. All progress events are stored in an event log. SSE clients can disconnect and reconnect (e.g. page refresh) without losing progress. The server replays all past events on reconnection. Only one task (scan or refresh) can run at a time.
12. **Atomic file writes** — Group CSVs and config are written via `_atomic_write` (write to tempfile, then `os.replace`) to prevent corruption on crash.
13. **Cross-platform file locking** — `_flock` helper in `store.py` uses `fcntl.flock` on Unix, `msvcrt.locking` on Windows, no-op otherwise.
14. **Auto-cleanup of stale data** — When the screener refreshes `entry_stocks.csv`, tickers dropped from the new results (and not in any other group) have their enriched parquets and chart assets automatically deleted to save storage.
15. **Portfolio/watchlist dedup guard** — `SymbolManager.add_symbols` and `move_symbol` enforce mutual exclusivity between `portfolio` and `watchlist` groups: adding/moving to one automatically removes the symbol from the other.
16. **Stock deletion with data purge** — `POST /api/delete` removes a ticker from its group CSV and, if the ticker is no longer in any group, deletes enriched data files and chart assets.

---

## 2. File Map

### Build Pipeline

| File | Role |
|------|------|
| `apps/dashboard/build_dashboard.py` | Orchestrator: download, enrich, export, render |
| `apps/dashboard/config_loader.py` | Loads `config.json`, resolves paths, defines `BuildConfig` |
| `apps/dashboard/data_exporter.py` | Converts enriched DataFrames → JSON for client-side charts |
| `apps/dashboard/screener_builder.py` | Computes screener rows (TrendScore, combos, deltas, fundamentals) |
| `apps/dashboard/strategy.py` | Entry v6 + Exit Flow v4 position engine (onset, SMA20>SMA200, vol spike, ATR stop, P&L) |
| `apps/dashboard/sector_map.py` | Fetches/caches symbol metadata + fundamentals from yfinance |
| `apps/dashboard/templates.py` | Generates `dashboard_shell.html` (embeds CSS, JS, data payloads) |
| `apps/dashboard/figures.py` | KPI timeline matrix + figure assembly (imports from `figures_layout` and `figures_indicators`) |
| `apps/dashboard/figures_layout.py` | Layout helpers: JSON sanitization, safe Plotly serialization |
| `apps/dashboard/figures_indicators.py` | Indicator overlays: Entry v6 + Exit Flow v4 overlay, combo shading, KPI timeline |
| `apps/dashboard/refresh_dashboard.py` | Shortcut: calls `build_dashboard.main(--mode refresh_dashboard)` |
| `apps/dashboard/serve_dashboard.py` | Local HTTP server: static HTML, `/fig` endpoint, `/api/scan` + `/api/refresh` (SSE), `/api/groups`, `/api/move`, `/api/delete` |
| `apps/dashboard/alert_notifier.py` | Signal alerts (email/webhook, optional) |
| `apps/dashboard/signal_logger.py` | Logs combo/trend signals to disk |
| `apps/dashboard/stock_export.py` | Stock export utilities |

### Data Layer

| File | Role |
|------|------|
| `trading_dashboard/data/downloader.py` | yfinance OHLCV download (per-symbol + batch), 1H→4H / 1D→1W resampling |
| `trading_dashboard/data/enrichment.py` | Runs all registered indicators on raw OHLCV |
| `trading_dashboard/data/store.py` | Parquet read/write with TTL caching + content-hash enrichment metadata |
| `trading_dashboard/data/incremental.py` | Incremental update logic (append new bars) |
| `trading_dashboard/data/health.py` | Data quality checks (missing bars, gaps) |

### Indicators

| File | Role |
|------|------|
| `trading_dashboard/indicators/registry.py` | Indicator registry, dimensions, KPI ordering |
| `trading_dashboard/indicators/_base.py` | Base class for indicator definitions |
| `trading_dashboard/indicators/*.py` | Individual indicator implementations (25 files) |
| `trading_dashboard/kpis/catalog.py` | KPI state computation (bull/bear/neutral per bar) |
| `trading_dashboard/kpis/rules.py` | KPI rule definitions |

### Client-Side

| File | Role |
|------|------|
| `apps/dashboard/static/chart_builder.js` | Builds Plotly figures from data JSON (6-row subplot layout) |
| `apps/dashboard/static/dashboard.js` | UI controller: tabs, sidebar, screener, crosshair, theme |
| `apps/dashboard/static/dashboard.css` | All styling: layout, theming (dark/light), components |

### Configuration

| File | Role |
|------|------|
| `apps/dashboard/configs/config.json` | Master config: timeframes, KPI weights, combos, exit params |
| `apps/dashboard/configs/lists/*.csv` | Symbol groups: one CSV per group (filename stem = group name) |
| `apps/dashboard/configs/indicator_config.json` | Indicator parameters (lengths, multipliers, etc.) |
| `apps/dashboard/configs/sector_map.json` | Symbol metadata + fundamentals (auto-populated from yfinance) |
| `apps/dashboard/configs/symbol_display_overrides.json` | Manual display name overrides (e.g. index names) |
| `apps/dashboard/configs/alerts_config.json` | Alert notification channels (optional) |

### Daily Screener

| File | Role |
|------|------|
| `apps/screener/daily_screener.py` | Screener pipeline: universe → download → lean enrich → C3/C4 active status → rank → inject into dashboard (auto-purge stale data) |
| `apps/screener/lean_enrichment.py` | Computes only the 5 indicators needed for 1D C3/C4 detection + SMA200 |
| `apps/screener/universe.py` | Loads universe CSV, applies quality filters (price, volume, market cap, data length) |
| `apps/screener/_build_universe.py` | Generates `universe.csv` from NASDAQ screener API (US) + hardcoded EU index lists |
| `apps/screener/configs/universe.csv` | Ticker universe (~3,800 US+EU stocks, regenerated via `_build_universe.py`) |
| `apps/screener/configs/screener_results.csv` | Output: tickers with combo type, entry bar, trend score (regenerated each run) |

### CLI

| File | Role |
|------|------|
| `trading_dashboard/cli.py` | CLI entry points (`dashboard build/refresh/rebuild-ui/re-enrich`, `symbols add/remove/sync`, `screener run/seed-universe`) |
| `trading_dashboard/__main__.py` | `python -m trading_dashboard` entry point |
| `trading_dashboard/symbols/manager.py` | Symbol list management (add, remove, move between groups, portfolio/watchlist dedup guard) |

### Output Artifacts

| Path | Content |
|------|---------|
| `data/dashboard_artifacts/dashboard_shell.html` | The dashboard HTML file |
| `data/dashboard_artifacts/dashboard_assets/` | Per-symbol JS data files (lazy-loaded) |
| `data/feature_store/enriched/dashboard/stock_data/` | Enriched parquet files (dashboard only) |
| `data/feature_store/enriched/dashboard/ohlcv_raw/` | Raw OHLCV cache (dashboard only) |
| `data/dashboard_artifacts/daily_screener.json` | Screener results: C3/C4 hits, metadata, filters applied |
| `apps/screener/configs/screener_results.csv` | Screener tickers with combo type, entry bar, trend score |

### Data Separation: Dashboard vs Research

The dashboard and research backtesting use **separate datasets**:

| Scope | dataset_name | Data location | Symbols | Purpose |
|-------|-------------|---------------|---------|---------|
| **Dashboard** | `dashboard` | `data/feature_store/enriched/dashboard/` | ~644 (from 5 groups) | Live screener + charts |
| **Research** | `sample_300` | `research/data/feature_store/enriched/sample_300/` | 300 (curated backtest universe) | KPI optimization, backtesting |

**Dashboard groups** (defined in `configs/lists/*.csv` — CSVs are the single source of truth):

| CSV file | Group name | Content | Symbols |
|----------|-----------|---------|---------|
| `portfolio.csv` | Portfolio | Actively held positions | ~24 |
| `entry_stocks.csv` | Entry Stocks | Auto-generated by the daily screener (C3/C4 combo hits) | ~519 |
| `watchlist.csv` | Watchlist | Candidates under monitoring | 0 |
| `benchmark.csv` | Benchmark | Indices + sector ETFs | ~46 |
| `stoof.csv` | Stoof | Shared list (Stefan + ETFs) | ~55 |

**Group dropdown order**: All | Portfolio | Entry Stocks | Watchlist | — | Benchmark | Stoof

**Rule**: `config.json` → `dataset_name` must be `"dashboard"`. Group definitions in `config.json`'s `symbol_groups` key are ignored — CSVs override them. Research scripts point to `research/data/` independently and never write to `data/feature_store/`.

---

## 3. Build Modes

| Mode | Command | Downloads | Computes Indicators | Generates UI | Use Case |
|------|---------|-----------|--------------------|--------------| ---------|
| `all` | `--mode all` | Yes | Yes | Yes | Weekly full refresh |
| `stock_export` | `--mode stock_export` | Yes | Yes | No | Data only, no HTML |
| `refresh_dashboard` | `--mode refresh_dashboard` | No | Yes (from cache) | Yes | Rebuild after config change |
| `rebuild_ui` | `--mode rebuild_ui` | No | No (reuse cached) | Yes | UI-only changes (fastest, ~55s) |
| `re_enrich` | `--mode re_enrich` | No | Yes (force recompute) | Yes | After indicator code changes |

Additional flags:
- `--export_phase download|compute` — run only download or compute phase
- `--force_recompute_indicators` — bypass cached enriched data
- `--indicator_config <path>` — use alternate indicator params
- `--skip_figures` — skip chart generation (screener-only rebuild)
- `--no_parallel_enrich` — disable ProcessPoolExecutor for enrichment (sequential fallback)

---

## 4. Data Flow

### 4.1 Download Phase

```
yfinance API  (batch: one yf.download() call per chunk of 50 tickers)
  │
  ├─ 1H candles (24 months) ──► resample to 4H
  ├─ 1D candles (24 months) ──► used as-is
  ├─ 1D candles ──────────────► resample to 1W  (W-FRI)
  ├─ 1D candles ──────────────► resample to 2W  (2W-FRI, bi-weekly)
  └─ 1D candles ──────────────► resample to 1M  (ME, month-end)
  │
  ▼
  Raw OHLCV parquet (data/feature_store/enriched/<dataset>/ohlcv_raw/)
```

**Batch download**: `build_dashboard.py` resolves all ticker symbols, then calls
`download_daily_batch` and `download_hourly_batch` (from `downloader.py`) which
pass the full list to `yf.download(tickers, ...)` in chunks of 50. This yields a
MultiIndex DataFrame that is split per ticker. The batch approach is ~7x faster
than per-symbol sequential downloads. Retry logic with exponential backoff
handles transient rate-limit errors.

### 4.2 Enrichment Phase

```
Raw OHLCV (per symbol, per TF)
  │
  ▼
  enrichment.py: run all registered indicators
  │  ├─ NW Smoother, Bollinger, ATR, SuperTrend, ...
  │  ├─ KPI state columns (bull=1, bear=-1, neutral=0, N/A=-2)
  │  └─ Breakout event detection
  │
  ▼
  Enriched parquet (data/feature_store/enriched/<dataset>/stock_data/)
```

### 4.3 Metadata Phase

```
yf.Ticker(sym).info  (single call per symbol)
  │
  ├─ Identity: name, sector, industry
  ├─ Geography: geo (US/EU/OTHER), national_index
  ├─ ETF mapping: sector_etf, industry_etf, benchmark_etf
  └─ Fundamentals: market_cap, trailing_pe, forward_pe, beta,
  │    dividend_yield, recommendation, target_price, ...
  │
  ▼
  sector_map.json  (cached, refreshed on --mode all)
```

### 4.4 Export Phase

```
Enriched parquet + sector_map.json
  │
  ├─► data_exporter.py ──► JS asset files (per symbol per TF)
  │     { x: [...], c: {Open:[...], Close:[...], NW_LuxAlgo_trend:[...], ...}, kpi: {...} }
  │
  ├─► screener_builder.py ──► SCREENER payload (embedded in HTML)
  │     { rows_by_tf: {1W: [...], 1D: [...], 4H: [...]}, by_symbol: {...} }
  │
  └─► templates.py ──► dashboard_shell.html
        Embeds: CSS, chart_builder.js, dashboard.js, SCREENER, SYMBOL_DISPLAY,
        SYMBOL_GROUPS, KPI_KEYS, DIMENSION_MAP, RUN_META, DATA_HEALTH
```

---

## 5. Dashboard UI Structure

### 5.1 Tabs

| Tab | Sub-tab | Content |
|-----|---------|---------|
| **Screener** | — | Data table with all symbols, filters, sorting |
| **Strategy** | strategy | Price chart (SR Breaks + Combo only) + P&L chart + TrendScore bars |
| **Charts** | chart | Price chart (all indicators) + Oscillator panel + KPI heatmaps |
| **Info** | — | Fundamentals, analyst ratings, sector comparison |
| **P&L** | — | Aggregate equity curve, drawdown, per-symbol trade breakdown |

### 5.2 Chart Subplot Layout (6 rows)

Built by `chart_builder.js`, split by `dashboard.js` into separate `<div>` containers:

| Row | Axis | Container | Content | Visible On |
|-----|------|-----------|---------|------------|
| 1 | y/x | `#chartUpper` | Price chart (candlestick + all indicator overlays) | Both |
| 2 | y2/x2 | `#chartPnl` | P&L bars + equity curve + stat banner | Strategy |
| 3 | y3/x3 | `#chartOsc` | Oscillator panel (RSI, MACD, etc.) | Charts |
| 4 | y4/x4 | `#chartTs` | TrendScore vertical bar chart | Strategy |
| 5 | y5/x5 | `#chartLower` (part 1) | KPI Breakout dot matrix | Charts |
| 6 | y6/x6 | `#chartLower` (part 2) | KPI Trend heatmap | Charts |

Height shares: Price 30%, P&L 8%, Oscillator ~25%, TrendScore 5%, KPI ~32%.

### 5.3 Per-Tab Filter Bars

Each content tab (Screener, Charts/Strategy, P&L) has its own **filter bar** with a Group dropdown and TF selector (4H, D, W). All filter bars share the same global `currentGroup` and `currentTF` state — changing either on any tab immediately syncs to all other tabs.

- **Top bar** contains only: tab selector, theme toggle, Scan button, Refresh button
- **Group dropdown**: All | Portfolio | Entry Stocks | Watchlist | — | Benchmark | Stoof
- **TF selector**: 4H | D | W (one always active)

The filter bars use the CSS classes `.tab-filter-bar`, `.tab-group-dropdown`, `.tab-group-trigger`, `.tab-tf-selector`, `.tab-tf-btn`.

### 5.4 Sidebar (Charts/Strategy tabs)

- **Search**: filter by ticker or display name
- **Sort buttons**: A-Z, % Chg, Score, Combo (filter)
- **Symbol list**: display name + sparkline + % change + combo badge (C3/C4)

### 5.5 Indicator Panel (Charts tab only)

Foldable panel below the topbar. Checkboxes grouped by dimension:
- **Trend**: NW Smoother, TuTCI, MA Ribbon, Madrid Ribbon, Donchian Ribbon, DEMA, Ichimoku, GK Trend, Impulse Trend
- **Momentum**: WT_LB, SQZMOM_LB, Stoch_MTM, CM_Ult_MacD_MFT, cRSI, ADX & DI, GMMA, RSI Zeiierman, OBVOSC_LB
- **Relative Strength**: Mansfield RS, SR Breaks
- **Breakout**: BB 30, NW Envelope (MAE), NW Envelope (STD), NW Envelope (Repainting)
- **Risk / Exit**: SuperTrend, UT Bot Alert, CM_P-SAR

Each chip shows a colored dot (green=bull, red=bear, grey=neutral) based on the current symbol's KPI state.

### 5.6 Strategy Tab Behavior

When Strategy tab is active:
- Only **Price**, **SR Breaks**, and **Combo Signal** traces are visible on the price chart
- Combo shapes (green vrects) are shown
- P&L chart and TrendScore bars are visible
- Oscillator and KPI panels are hidden
- Indicator panel is hidden

### 5.7 Charts Tab Behavior

When Charts tab is active:
- All selected indicator traces are visible on the price chart
- Combo shapes are hidden
- Oscillator panel and KPI panels are visible
- Indicator panel is visible (foldable)
- P&L chart and TrendScore bars are hidden

### 5.8 P&L Tab

Aggregate P&L analysis across all symbols in the active group/timeframe:

- **Filter bar**: own group dropdown + TF selector (synced with other tabs)
- **Equity curve**: cumulative return chart across all symbols
- **Drawdown chart**: peak-to-trough drawdown visualization
- **Per-symbol table**: sortable by return, hit rate, trades, avg P&L, max drawdown
- **Commission**: 0.1% per trade deducted from returns (matching Python strategy engine)

Switching group or timeframe (on any tab) auto-invalidates the P&L cache and triggers a rebuild.

### 5.9 Accessibility & Responsiveness

- **ARIA attributes**: `role="button"`, `tabindex="0"`, and `aria-label` on all interactive elements (theme toggle, nav tabs)
- **Reduced motion**: `@media (prefers-reduced-motion: reduce)` disables animations and transitions
- **Responsive breakpoints**: 768px (tablet) and 480px (mobile) with adjusted layout and font sizes
- **Design tokens**: CSS custom properties for spacing (`--space-xs` to `--space-xl`) and font scale (`--font-xs` to `--font-xl`)
- **Dark/light theming**: all colors use CSS variables (`--text`, `--text-muted`, `--text-dim`, `--card-bg`, `--success`, `--danger`, `--warning`, `--info`, etc.)

---

## 6. Screener Structure

### 6.1 Columns

| # | Key | Label | Sortable | Source |
|---|-----|-------|----------|--------|
| 1 | `symbol` | Name | Yes | Display name from `sector_map` |
| 2 | `_ticker` | Ticker | Yes | Raw ticker symbol (hover shows sector + industry) |
| 3 | `market_cap` | Mkt Cap | Yes | From fundamentals |
| 4 | `_price` | Price | Yes | Last close + colored % delta for current TF underneath |
| 5 | `_recommendation` | Analysts | Yes | Analyst consensus (Buy/Hold/Sell) |
| 6 | `_conv10` | TrendScore | No | 13-bar conviction mini bar chart + conviction % |
| 7 | `_confluence` | Traffic Light | No | Multi-timeframe trend dots |
| 8 | `_action_tf` | Action | Yes | Signal state per TF: E1.5 (gold), E1 (green), HLD (blue), EXT (red), — (flat). Hover shows bar count. |
| 9 | `_vs_bench` | TrendScore vs Bench | No | Combined: `S`+delta (sector) and `M`+delta (market). Hover shows benchmark names. |
| 10 | `pe_vs_sector` | P/E vs Sec | Yes | % premium/discount vs sector ETF P/E |
| 11 | `_move_group` | Group | — | Dropdown to move ticker between groups + ✕ delete button |

### 6.2 Action Column — Signal Badges

Each badge in the Action column shows the signal state for one timeframe:

| Badge | Color | Signal | Hover |
|-------|-------|--------|-------|
| E1.5 | Gold (combo-c4) | Entry 1.5x or Scale | "Entry/Scale 1.5x Nb" |
| E1 | Green (combo-c3) | Entry 1x | "Entry 1x Nb" |
| HLD | Blue (info) | Hold | "Hold Nb" |
| EXT | Red (danger) | Exit (incl. recent flat ≤2b) | "Exit Nb" |
| — | Muted | Flat / no position | "Flat" |

**Price column** shows last close price with the delta percentage for the current timeframe underneath (green if positive, red if negative, e.g. "+1.2% D").

**Action sort order** (descending, aggregated across all timeframes with TF weighting):

| Priority | Signal per TF | Score per TF |
|----------|---------------|--------------|
| 1 | E1.5 / Scale | (n - idx) * 1000 + 500 |
| 2 | E1 (Entry 1x) | (n - idx) * 1000 + 400 |
| 3 | Hold | (n - idx) * 1000 + 200 |
| 4 | Exit | (n - idx) * 1000 + 100 |
| 5 | Flat | 0 |

### 6.3 Combo Column — Position-Aware Display

The Combo column only shows combo timing when the stock is **in an active position** (ENTRY, SCALE, or HOLD). When the position is flat (exited or never entered), it shows "FLAT" regardless of historical combo data.

| Position State | Combo Data | Combo Column Display |
|----------------|-----------|---------------------|
| ENTRY / SCALE | combo on current bar | **C3** or **C4** badge (colored) |
| HOLD | combo N bars ago | **N bars** (amber if ≤ 3, muted otherwise) |
| EXIT / FLAT | any | **FLAT** (muted) |

Sorting (descending): active combos (most recent first) → FLAT stocks at the bottom.

### 6.4 Filter Pills

| Group | Filter | Logic |
|-------|--------|-------|
| — | All | No filter |
| Trend | Bullish | `trend_score > 0` |
| Trend | Bearish | `trend_score < 0` |
| Trend | Strong (≥5) | `|trend_score| >= 5` |
| Trend | Improving | `trend_delta > 0` |
| Signal | Combo | Any combo active (C3/C4) |
| Signal | New Combo | Combo appeared on latest bar |
| Signal | Recent (≤3) | Combo within last 3 bars |
| Analyst | Buy Rating | Recommendation is Buy or Strong Buy |

### 6.5 Data Computation (`screener_builder.py`)

For each symbol × timeframe:
1. Compute KPI state map (bull/bear/neutral per KPI per bar)
2. Calculate weighted TrendScore
3. Detect C3/C4 combo signals (TF-specific → global)
4. Compute "bars since last combo" (scan up to 200 bars back)
5. Calculate breakout score, trend delta, conviction
6. Extract fundamentals from `sector_map.json` (recommendation, market_cap, trailing_pe)
7. After all symbols: compute cross-symbol deltas (vs sector ETF, vs national index, P/E vs sector)

---

## 7. Data Schemas

### 7.1 `config.json`

```json
{
  "symbols": ["NVDA", "BNP.PA", ...],
  "timeframes": ["4H", "1D", "1W", "2W", "1M"],
  "dashboard_mode": "lazy_static",
  "max_plot_bars_per_tf": {"4H": 5000, "1D": 600, "1W": 140},
  "plot_lookback_months": 24,
  "cache_ttl_hours": 0,
  "dataset_name": "dashboard",
  "kpi_weights": {"Nadaraya-Watson Smoother": 3, "BB 30": 2, ...},
  "combo_3_kpis": ["Nadaraya-Watson Smoother", "Madrid Ribbon", "Volume + MA20"],
  "combo_4_kpis": ["Nadaraya-Watson Smoother", "Madrid Ribbon", "GK Trend Ribbon", "cRSI"],
  "combo_kpis_by_tf": {"4H": {"combo_3": ["NWSm","DEMA","Stoch_MTM"], "combo_4": [...]}, "1W": {"combo_3": ["NWSm","DEMA","cRSI"], "combo_4": ["NWSm","Stoch_MTM","cRSI","Volume + MA20"]}},
  "stoch_mtm_thresholds": {"overbought": 40, "oversold": -40},
  "exit_params": {
    "4H": {"T": 4, "M": 48, "K": 4.0},
    "1D": {"T": 4, "M": 40, "K": 4.0},
    "1W": {"T": 2, "M": 20, "K": 4.0}
  }
}
```

**Notes:**
- `symbols` is the merged list from all group CSVs. Updated automatically by `SymbolManager`.
- `symbol_groups` key is legacy — ignored if `configs/lists/*.csv` files exist (CSVs take precedence).
- `dashboard_mode`: `"lazy_static"` (pre-built JS assets) or `"lazy_server"` (on-the-fly from parquet).

### 7.2 `sector_map.json` (per symbol)

```json
{
  "NVDA": {
    "name": "NVIDIA Corporation",
    "sector": "Technology",
    "industry": "Semiconductors",
    "geo": "US",
    "national_index": "^GSPC",
    "sector_etf": "XLK",
    "industry_etf": "SMH",
    "benchmark_etf": "XLK",
    "fundamentals": {
      "market_cap": 3200000000000,
      "trailing_pe": 55.2,
      "forward_pe": 32.1,
      "peg_ratio": null,
      "price_to_book": 45.3,
      "profit_margins": 0.55,
      "return_on_equity": 1.15,
      "gross_margins": 0.73,
      "earnings_growth": 0.80,
      "revenue_growth": 1.22,
      "dividend_yield": 0.02,
      "beta": 1.65,
      "52w_high": 195.0,
      "52w_low": 75.0,
      "short_pct_float": 0.012,
      "recommendation": "buy",
      "target_price": 180.0,
      "num_analysts": 45,
      "country": "United States",
      "currency": "USD",
      "debt_to_equity": 29.1,
      "free_cashflow": 60000000000,
      "total_revenue": 130000000000
    }
  }
}
```

Notes:
- ETFs and indices have empty `sector`/`industry` (expected — yfinance doesn't classify them)
- `fundamentals` refreshed on every `--mode all` run via `refresh_fundamentals=True`
- Display names fall back to `symbol_display_overrides.json` for manual corrections

### 7.3 `indicator_config.json`

```json
{
  "WT_LB": {"params": {"n1": 8, "n2": 25}},
  "PSAR": {"params": {"start": 0.02, "increment": 0.02, "maximum": 0.2}},
  "ATR": {"params": {"length": 14, "smoothing": "RMA", "mult": 1.5}},
  "BB": {"params": {"length": 20, "mult": 3, "ma_type": "SMA"}},
  "SuperTrend": {"params": {"periods": 12, "multiplier": 3}},
  ...
}
```

### 7.4 Data Asset Format (per symbol × TF)

Exported by `data_exporter.py`, loaded lazily by `chart_builder.js`:

```json
{
  "symbol": "NVDA",
  "timeframe": "1W",
  "display_name": "NVIDIA Corporation",
  "x": ["2024-01-05T00:00:00", ...],
  "c": {
    "Open": [120.5, ...],
    "High": [125.3, ...],
    "Low": [118.2, ...],
    "Close": [123.8, ...],
    "Volume": [45000000, ...],
    "NW_LuxAlgo_trend": [122.1, ...],
    "BB_upper": [130.5, ...],
    ...
  },
  "kpi": {
    "kpi_names": ["NW Smoother", "cRSI", ...],
    "kpi_z": [[1, 1, -1, ...], ...],
    "kpi_custom": [["Bull crossover", ...], ...]
  },
  "combo_3_kpis": ["Nadaraya-Watson Smoother", "Madrid Ribbon", "Volume + MA20"],
  "combo_4_kpis": [...],
  "kpi_weights": {"Nadaraya-Watson Smoother": 3, ...}
}
```

### 7.5 Screener Payload (embedded in HTML)

```json
{
  "rows_by_tf": {
    "1W": [
      {
        "symbol": "NVDA", "name": "NVIDIA Corporation", "tf": "1W",
        "sector": "Technology", "industry": "Semiconductors", "geo": "US",
        "trend_score": 18.4, "trend_delta": 6,
        "combo_3": true, "combo_4": false,
        "last_combo_bars": 0,
        "signal_action": "HOLD", "bars_held": 11, "combo_bars": 11,
        "c4_scaled": false,
        "recommendation": "buy", "market_cap": 3200000000000,
        "trailing_pe": 55.2, "pe_vs_sector": 55.6,
        "sector_ts_delta": 4.2, "market_ts_delta": 8.1,
        "kpi_states": {"Nadaraya-Watson Smoother": 1, "cRSI": 1, ...},
        "spark": [0.1, 0.15, 0.3, ...],
        "conv10": [0.5, 0.6, 0.4, ...],
        ...
      }
    ]
  },
  "by_symbol": {"NVDA": {"1W": {...}, "1D": {...}, "4H": {...}}}
}
```

### 7.6 Daily Screener Output (`daily_screener.json`)

```json
{
  "generated_utc": "2026-02-26T08:23:15+00:00",
  "timeframe": "1D",
  "universe_size": 3800,
  "after_filters": 3720,
  "elapsed_seconds": 85.5,
  "filters_applied": {
    "min_dollar_volume": 2000000,
    "min_price": 5,
    "min_market_cap": 300000000,
    "min_bars": 250,
    "sma_gate": "SMA20 > SMA200",
    "vol_spike": "1.5x N=5",
    "sr_break_prefilter": "N=10",
    "onset_only": true,
    "geo": ["US", "EU"]
  },
  "c3_total_found": 199,
  "c4_total_found": 12,
  "c3_hits": [
    {"symbol": "ALB", "combo_3_bar": 0, "trend_score": 7.1, ...}
  ],
  "c4_hits": [
    {"symbol": "ENGI.PA", "combo_4_bar": 0, "trend_score": 7.1, ...}
  ]
}
```

---

## 8. KPI System

### 8.1 KPI Trend Order (23 indicators)

These are evaluated per bar and produce a state: bull (1), bear (-1), neutral (0), or N/A (-2).

| # | KPI | Dimension | Weight |
|---|-----|-----------|--------|
| 1 | Nadaraya-Watson Smoother | Trend | 3 |
| 2 | TuTCI | Trend | 1 |
| 3 | MA Ribbon | Trend | 1 |
| 4 | Madrid Ribbon | Trend | 1 |
| 5 | Donchian Ribbon | Trend | 1 |
| 6 | DEMA | Trend | 1 |
| 7 | Ichimoku | Trend | 1 |
| 8 | GK Trend Ribbon | Trend | 1 |
| 9 | Impulse Trend | Trend | 1 |
| 10 | WT_LB | Momentum | 1 |
| 11 | SQZMOM_LB | Momentum | 1 |
| 12 | Stoch_MTM | Momentum | 1 |
| 13 | CM_Ult_MacD_MFT | Momentum | 1 |
| 14 | cRSI | Momentum | 1 |
| 15 | ADX & DI | Momentum | 1 |
| 16 | GMMA | Momentum | 1 |
| 17 | RSI Zeiierman | Momentum | 1 |
| 18 | OBVOSC_LB | Momentum | 1 |
| 19 | Mansfield RS | Relative Strength | 1 |
| 20 | SR Breaks | Relative Strength | 1 |
| 21 | SuperTrend | Risk / Exit | 1 |
| 22 | UT Bot Alert | Risk / Exit | 1 |
| 23 | CM_P-SAR | Risk / Exit | 1 |

### 8.2 KPI Breakout Order (4 indicators)

| # | KPI | Dimension |
|---|-----|-----------|
| 1 | BB 30 | Breakout |
| 2 | NW Envelope (MAE) | Breakout |
| 3 | NW Envelope (STD) | Breakout |
| 4 | NW Envelope (Repainting) | Breakout |

### 8.3 Combo Signals

A combo fires when ALL KPIs in the list are simultaneously bullish (state = 1).

| Level | Default KPIs | Typical Hit Rate (1W) |
|-------|-------------|----------------------|
| C3 | NWSm + DEMA + Stoch_MTM (4H) / NWSm + Madrid + Vol>MA (1D) / NWSm + DEMA + cRSI (1W) | 63–89% |
| C4 | NWSm + Madrid + GKTr + cRSI (4H/1D) / NWSm + Stoch + cRSI + Vol>MA (1W) | ~70–88% |

Combos are configurable per timeframe (`combo_kpis_by_tf`) in `config.json`.

### 8.4 Entry Gate Filters (v5)

Before a C3 onset opens a position, it must pass all v6 entry gates (implemented in `apps/dashboard/strategy.py`):

| Filter | Timeframes | Condition | Purpose |
|--------|-----------|-----------|---------|
| **Onset-only** | All | C3 must transition FALSE→TRUE | Only fresh entries, not continuations (Phase 13: PF 7.5 vs 3.3) |
| **SMA20 > SMA200** | 1D, 1W | `SMA20[i] >= SMA200[i]` | Structural uptrend gate (Phase 14: +0.8pp HR, +0.7 PF vs Close>SMA200) |
| **Vol spike 1.5×** | All | `Vol >= 1.5× Vol_MA20` within last 5 bars | Momentum confirmation (Phase 14: +2.7pp HR, PF 7.1→8.1) |
| **Overextension** | 1W only | `Close[i] <= 1.15 × Close[i-5]` | Blocks entries at peak of sharp rallies (worst trade -38.6% → -26.7%) |

Constants in `strategy.py`: `_OVEREXT_LOOKBACK = 5`, `_OVEREXT_PCT = 15.0`, `_VOL_SPIKE_MULT = 1.5`, `_VOL_SPIKE_LOOKBACK = 5`.

The daily screener adds an additional pre-filter: **SR Break N=10** (computed on raw OHLCV before lean enrichment). This is not applied in the dashboard position tracker or chart overlay.

### 8.5 TrendScore

Weighted sum of all trend KPI states: `sum(weight[k] * state[k])` for all KPIs where state is 1 or -1.

---

## 9. State Management (Client-Side)

### 9.1 Persisted State

Stored in `localStorage` and URL hash:

| Key | Type | Default |
|-----|------|---------|
| `symbol` | string | First symbol |
| `tf` | string | `"1W"` |
| `tab` | string | `"screener"` |
| `group` | string | `"all"` |
| `theme` | string | `"dark"` |
| `indicators` | string[] | `[]` (none selected) |
| `sidebarSort` | string | `"name"` |
| `screenerFilter` | string | `"all"` |
| `screenerSortKey` | string | `"_signal_action"` |
| `screenerSortDir` | string | `"desc"` |

Note: `group` and `tf` are shared across all tabs. Changing either on any tab's filter bar updates the global state and syncs all other filter bars.

### 9.2 URL Hash Format

`#symbol=NVDA&tf=1W&tab=strategy&group=portfolio&theme=dark`

### 9.3 Keyboard Shortcuts

| Key | Action |
|-----|--------|
| S | Switch to Screener tab |
| C | Switch to Charts tab |
| B | Switch to Strategy tab |
| I | Switch to Info tab |
| P | Switch to P&L tab |
| D | Toggle dark/light theme |
| / | Focus symbol search |
| ↑/↓ | Navigate symbol list |
| ←/→ | Previous/next symbol |
| 1/2/3/4/5 | Switch timeframe (4H/1D/1W/2W/1M) |

---

## 10. Sector & Geography System

### 10.1 Geography Detection

Based on ticker suffix:
- `.PA`, `.DE`, `.AS`, `.MI`, `.MC`, `.SW`, `.VI`, `.L`, `.CO`, `.OL`, `.HE`, `.WA`, `.BR`, `.LS`, `.IR` → **EU**
- No dot, or `.TO` → **US**
- `.SS`, `.BO`, `.T`, `.SA` → **OTHER**

### 10.2 National Index Mapping

| Suffix | Index | Name |
|--------|-------|------|
| `.PA` | `^FCHI` | CAC 40 |
| `.DE` | `^GDAXI` | DAX 40 |
| `.AS` | `^AEX` | AEX Amsterdam |
| `.L` | `^FTSE` | FTSE 100 |
| `.MI` | `FTSEMIB.MI` | FTSE MIB |
| `.SW` | `^SSMI` | SMI Zurich |
| (US) | `^GSPC` | S&P 500 |

### 10.3 Sector ETF Mapping

Each sector maps to a US and EU ETF benchmark:

| Sector | US ETF | EU ETF |
|--------|--------|--------|
| Technology | XLK | TNO.PA |
| Financial Services | XLF | IUFS.L |
| Healthcare | XLV | HLT.PA |
| Industrials | XLI | IUIS.L |
| Consumer Cyclical | XLY | IUCD.L |
| Consumer Defensive | XLP | IUCS.L |
| Energy | XLE | IUES.L |
| Utilities | XLU | IUUS.L |
| Materials | XLB | — |
| Real Estate | XLRE | — |
| Communication | XLC | — |

### 10.4 Fundamentals (22 fields)

Fetched from `yf.Ticker().info` — zero extra API cost (same call as identity).
Refreshed on every `--mode all` run. Coverage: ~65% for stocks, partial for ETFs, minimal for indices.

---

## 11. Reproducibility Checklist

If the dashboard breaks, follow this sequence to rebuild from scratch.

### Full Rebuild from Zero

```bash
# 0. Install dependencies
pip install -e ".[dev]"

# 1. Full rebuild (downloads data + computes everything + generates UI)
python -m apps.dashboard.build_dashboard --mode all

# 2. Start the local server
python3 -m apps.dashboard.serve_dashboard
# → Dashboard available at http://localhost:8050
```

### Targeted Rebuilds

```bash
# If indicators changed but data is fresh
python -m apps.dashboard.build_dashboard --mode re_enrich

# If only UI/template code changed (CSS, JS, templates.py)
python -m apps.dashboard.build_dashboard --mode rebuild_ui

# If only screener/config changed (no indicator recompute)
python -m apps.dashboard.build_dashboard --mode refresh_dashboard

# If you need to force-refresh all indicator columns
python -m apps.dashboard.build_dashboard --mode all --force_recompute_indicators

# If you need to refresh fundamentals only
python -c "
from apps.dashboard.sector_map import fetch_sector_map, load_sector_map
sm = load_sector_map()
fetch_sector_map(list(sm.keys()), refresh_fundamentals=True)
"
```

### Daily Screener

```bash
# Full run: scan universe → detect C3/C4 → build dashboard for hits
python -m trading_dashboard screener run

# Scan only, skip dashboard build
python -m trading_dashboard screener run --no-dashboard

# From the dashboard UI: click ⚙ Scan (new entries) or ⟳ Refresh (re-download all)
# → Runs the full pipeline via SSE with real-time progress
```

### Critical Files (do not delete)

| File | Why |
|------|-----|
| `configs/lists/*.csv` | All symbol groups (portfolio, entry_stocks, watchlist, benchmark, stoof) |
| `configs/config.json` | Timeframes, combo definitions, exit params, KPI weights |
| `configs/sector_map.json` | All metadata + fundamentals (expensive to rebuild — one yfinance API call per symbol) |
| `configs/indicator_config.json` | Indicator parameters (lengths, multipliers, etc.) |
| `configs/symbol_display_overrides.json` | Manual display name overrides for indices/ETFs |
| `data/feature_store/enriched/*/stock_data/*.parquet` | Enriched data (hours to recompute for ~644 symbols × 3 TFs) |
| `data/feature_store/enriched/*/ohlcv_raw/*.parquet` | Raw OHLCV cache (re-download from yfinance if deleted) |

### Environment

- Python 3.11+
- Key packages: `yfinance>=0.2.31`, `pandas>=2.1`, `plotly>=5.18`, `numpy>=1.26`, `numba>=0.59`, `pyarrow>=14.0`
- No database required — everything is file-based (parquet + JSON + CSV)
- **Static mode** (`lazy_static`): the HTML file + JS assets are self-contained; no server needed for viewing
- **Server mode** (`lazy_server`): `serve_dashboard.py` generates figures on-the-fly from parquet; enables scan, group management, and live figure updates

### Process to Add a New Symbol Manually

```bash
# Via CLI
python -m trading_dashboard symbols add AAPL --group portfolio

# Then rebuild
python -m apps.dashboard.build_dashboard --mode all
```

Or use the dashboard's Group dropdown on each screener row to move tickers between groups.

---

## 12. Server & Scan Pipeline

### 12.1 Local Server (`serve_dashboard.py`)

Run: `python3 -m apps.dashboard.serve_dashboard`

Serves the dashboard on `http://localhost:8050` with these endpoints:

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Redirect to `/dashboard_shell.html` |
| GET | `/dashboard_shell.html` | Serves the dashboard HTML |
| GET | `/fig?symbol=X&tf=Y` | Returns Plotly figure JSON (computed on-the-fly from enriched parquet) |
| GET | `/api/scan` | SSE stream: runs screener with real-time progress (or reconnects to existing scan) |
| GET | `/api/refresh` | SSE stream: re-downloads + re-enriches all symbols, then rebuilds dashboard (with progress) |
| GET | `/api/scan/status` | Returns `{"scan_running": bool, "refresh_running": bool}` — used by frontend to auto-reconnect on page load |
| GET | `/api/groups` | Returns all symbol groups and their tickers |
| POST | `/api/move` | Move a ticker between groups (persists to CSV) |
| POST | `/api/delete` | Remove a ticker from a group (or all groups) + purge enriched data and chart assets |
| GET | `/health` | Status check |

**Figure caching**: The `_Caches` singleton caches loaded DataFrames and rendered Plotly JSON by `(symbol, tf)` key, keyed to the parquet file's mtime. Thread-safe via `threading.Lock`.

**Input validation**: Symbol parameters are validated against `^[A-Z0-9^._-]{1,20}$`. POST body limited to 64 KB.

### 12.2 Scan & Refresh Pipelines

Both scan and refresh run as **background threads** via singleton state objects (`_ScanState` and `_RefreshState`), not inside the HTTP handler. This enables:

- **Page refresh without losing progress**: all events are stored in an in-memory event log. When a new SSE client connects, it receives a full replay of past events, then streams new ones live.
- **Mutual exclusion**: only one task (scan or refresh) can run at a time. `start()` returns `False` if the other is active.
- **Auto-reconnect on page load**: the frontend calls `/api/scan/status` on init; if either task is running, it automatically opens the correct SSE connection.

#### Scan Pipeline (`_ScanState`)

**Pipeline phases (8 total):**

| Phase | # | Pct range | What happens |
|-------|---|-----------|-------------|
| init | 0 | 0% | Bootstrap |
| download | 1 | 0-36% | Screener: batch OHLCV download (1D only, 50-ticker chunks) |
| filter | 2 | 36-37% | Screener: quality filters (price, volume, market cap) |
| enrich | 3 | 37-51% | Screener: lean enrichment + C3/C4 detection |
| detect | 4 | 51-60% | Screener: ranking, saving results |
| inject | 5 | 62% | Write combo tickers to `entry_stocks.csv` |
| build | 6 | 70% | Full dashboard build (`--mode all`: download 3 TFs + enrich + HTML) |
| validate | 7 | 92% | Verify data files exist for every new ticker |

**SSE event types:**

| Event | When | UI behavior |
|-------|------|------------|
| `progress` | During pipeline | Updates progress bar, label, detail, ETA |
| `complete` | All phases succeeded, all tickers have data | Green bar, refreshes groups |
| `failed` (severity: `partial`) | Build succeeded but some tickers missing data | Amber bar, shows `X/Y tickers ready`, still refreshes groups |
| `failed` (severity: `critical`) | Build crashed or screener failed | Red bar, shows error message |

#### Refresh Pipeline (`_RefreshState`)

Triggered by the **⟳ Refresh** button. Re-downloads and re-enriches all symbols currently in the dashboard CSV lists, then rebuilds the dashboard HTML.

| Phase | Pct range | What happens |
|-------|-----------|-------------|
| init | 0% | Bootstrap |
| download | 5-30% | Batch yfinance download (daily + hourly) for all symbols |
| enrich | 30-90% | Per-symbol×TF indicator enrichment (parallel, with per-task progress) |
| rebuild | 92% | Screener summary + chart assets + HTML generation |

Uses the same SSE event types (`progress`, `complete`, `failed`) and the same progress bar UI as scan. The `on_progress` callback in `run_stock_export()` emits per-symbol enrichment detail (e.g. `"AAPL 1D (42/850)"`).

### 12.3 Dashboard Modes

| Mode | `dashboard_mode` | Figure source | Use case |
|------|-----------------|--------------|----------|
| `lazy_server` | Server generates figures on-the-fly from parquet | `/fig?symbol=X&tf=Y` | Development, live editing |
| `lazy_static` | Pre-built JS asset files loaded by browser | `dashboard_assets/X/Y.js` | Deployment, offline viewing |

Default: `lazy_static` (set in `config_loader.py`; override in `config.json` → `dashboard_mode`).

---

## 13. Reliability & Error Handling

### 13.1 Download Resilience

- **Retry with backoff**: `_yf_download_with_retry` retries up to 3 times with exponential delays (10s, 20s, 40s) on both empty results and exceptions
- **Download timeout**: single-ticker downloads have a 60-second thread-based timeout via `_download_with_timeout`
- **Ticker cache**: resolved yfinance ticker symbols are cached to `configs/ticker_cache.json`, avoiding redundant API probes on subsequent runs

### 13.2 Data Validation

- **OHLC column check**: `compute_position_status` validates that `High`, `Low`, `Close` columns exist before running the strategy engine
- **ATR NaN guard**: all ATR access points check for `np.isnan()` with a 5% fallback stop price
- **Empty combo guard**: early return when combo KPI lists are empty
- **int(NaN) safety**: KPI state extraction in `screener_builder.py` checks `pd.isna()` before `int()` conversion

### 13.3 Logging & Observability

- All 25+ previously silent `except: pass` blocks now log via `logger.debug()` with context describing the failed operation
- Each module uses `logging.getLogger(__name__)` for structured log output

### 13.4 Concurrency Safety

- **File locking**: `_flock` helper in `store.py` uses `fcntl.flock` on Unix, `msvcrt.locking` on Windows, silent no-op otherwise — prevents concurrent write corruption of `_enrichment_meta.json`
- **Atomic file writes**: `_atomic_write` in `manager.py` writes to a tempfile then `os.replace` for crash-safe CSV and config updates
- **Thread-safe caches**: `_Caches` in `serve_dashboard.py` uses `threading.Lock` for `df_by_key` and `fig_by_key`
- **Task concurrency**: `_ScanState` and `_RefreshState` singletons prevent parallel tasks via `threading.Lock` + mutual exclusion

### 13.5 Client-Side Graceful Degradation

- Missing symbol assets return `null` from `loadFig()` (no alert/crash) and display a user-friendly "No data available" message
- **Retry button**: fetch failures show a retry button that clears the cache entry and re-attempts loading
- **DOM cache**: 9 frequently accessed elements are cached in a `DOM` object to avoid repeated `getElementById` calls

---

## 14. Execution Model

### 14.1 Trade Fills

- **Entry**: fills at next bar's Open (not the signal bar's Close)
- **Exit**: fills at next bar's Open when possible (signal bar Close for open positions)
- **Slippage**: `SLIPPAGE = 0.5%` flat, applied on both entry and exit
- **Commission**: `COMMISSION = 0.1%` per trade (round-trip)
- **Total cost per trade**: 0.6% (slippage + commission)

Both Python (`strategy.py`) and JS (`chart_builder.js` + Web Worker) use identical values.

### 14.2 P&L Computation

- **Weighted returns**: C4 trades apply `1.5x` weight via `_wret(t)` helper
- **Risk-adjusted metrics**: Sharpe, Calmar, Expectancy, W/L ratio
- **Benchmark overlay**: SPY buy-and-hold (dotted grey line) on aggregate equity curve
- **Position sizing view**: C3 (1x) vs C4 (1.5x) breakdown with weighted P&L contribution
- **Drill-down**: click a row in the per-symbol table to see multi-TF equity curves

### 14.3 Alert Pipeline

`alert_runner.py`: standalone CLI pipeline (download → enrich → screener → alert).

```bash
python -m apps.dashboard.alert_runner                # full run
python -m apps.dashboard.alert_runner --scan-only    # skip download
python -m apps.dashboard.alert_runner --dry-run      # preview without sending
```

Pipeline run log: `data/dashboard_artifacts/alert_files/pipeline_runs.jsonl`

### 14.4 Templates Payload

`templates.py` embeds these globals into the HTML shell `<script>` block:

| Variable | Content |
|----------|---------|
| `FIG_SOURCE` | `"server"` or `"static"` |
| `ASSETS_DIR` | Path to JS asset files |
| `SYMBOLS` | All symbols across all groups |
| `SYMBOL_GROUPS` | Group → ticker list mapping |
| `TIMEFRAMES` | `["4H", "1D", "1W", "2W", "1M"]` |
| `KPI_KEYS` | Ordered KPI names |
| `SCREENER` | Full screener payload (rows_by_tf, by_symbol) |
| `EXIT_PARAMS_CFG` | Per-TF T/M/K values |
| `MAX_TREND_SCORE` | Maximum possible TrendScore |
| `DIMENSION_MAP` | KPI → dimension mapping |
| `DIMENSION_ORDER` | Dimension display order |
| `SYMBOL_META` | Metadata from sector_map.json |
| `SYMBOL_DISPLAY` | Display name overrides |
| `SYMBOL_TO_ASSET` | Ticker → asset filename mapping |
| `RUN_META` | Build timestamp, version, counts |
| `DATA_HEALTH` | Data quality flags |
| `DEFAULT_SYMBOL` | First symbol |
| `DEFAULT_TF` | Default timeframe |
| `FX_TO_EUR` | Currency → EUR exchange rates |
| `SYMBOL_CURRENCIES` | Symbol → currency code mapping |

---

## 15. Add Ticker to Watchlist

### 15.1 Overview

Users can add new tickers to the watchlist directly from the screener toolbar via the **[+ Add]** button. The modal supports a **staging queue** — users can search for multiple tickers, add them to a queue, then confirm and enrich all at once.

### 15.2 Flow

1. User clicks **[+ Add]** → modal opens
2. User types ticker symbol, company name, or ETF name → clicks **Search**
3. Server resolves via `yf.Search()` (fuzzy matching for stocks and ETFs across all global exchanges), with suffix brute-force fallback (`.PA`, `.DE`, `.L`, `.MI`, `.AS`, `.SW`, `.TO`, `.ST`, `.MC`, `.IR`, `.HE`, `.OL`, `.CO`, `.IS`, `.VI`, `.WA`, `.SA`, `.HK`, `.AX`, `.SI`, `.T`, `.KS`, `.KQ`, `.MX`, `.NS`, `.BO`)
4. Up to 8 results shown with name, sector, exchange, currency, quoteType, and price
5. User clicks **[+ Add]** on each desired result — tickers queue into a staging list (displayed as chips with ✕ remove)
6. User can search again and add more tickers to the queue
7. User clicks **Confirm & Enrich** → server writes all tickers to `watchlist.csv`, then runs `enrich_symbols()` in background
8. SSE progress bar shows enrichment status ("Enriching 2/5…")
9. On completion: screener JSON rebuilt, `_reloadLiveData()` fires, all new tickers appear in screener with full data

### 15.3 API

| Method | Endpoint | Body | Response |
|--------|----------|------|----------|
| POST | `/api/resolve-ticker` | `{ "query": "MSCI World" }` | `{ "results": [...] }` |
| POST | `/api/add-symbol` | `{ "ticker": "AAPL", "group": "watchlist" }` | `{ "ok": true }` |
| POST | `/api/enrich-symbols` | `{ "tickers": ["AAPL","MSFT"], "group": "watchlist" }` | `{ "ok": true }` (starts SSE background task) |
| GET | `/api/enrich` | — | SSE stream: progress/complete/failed events |

---

## 16. EUR Price Toggle

### 16.1 Overview

A toggle button **[Local / EUR]** in the top bar converts all screener prices to EUR using cached FX rates.

### 16.2 Implementation

- **Build time**: `_fetch_fx_rates_and_currencies()` in `build_dashboard.py` downloads FX rates for all unique currencies via yfinance (`{CCY}EUR=X` pairs).
- **Payload**: `FX_TO_EUR` dict (e.g. `{"USD": 0.92, "GBP": 1.17, ...}`) and `SYMBOL_CURRENCIES` dict (e.g. `{"AAPL": "USD", "MC.PA": "EUR", ...}`) embedded in HTML.
- **Client-side**: JS `_toEur(price, symbol)` multiplies by FX rate. No backend re-enrichment — purely a display transformation.
- **Screener**: Prices and deltas in the screener table are converted to EUR when toggle is active.
- **Plotly charts**: When EUR toggle is active, OHLCV candlestick data and price-axis traces are scaled by the FX rate before rendering. Toggle click invalidates figure cache and re-renders.
- **State**: Toggle persisted in `localStorage` (`showEur` key).
- **GBp handling**: British pence (GBp) rate = GBP rate / 100.

---

## 17. P&L Trade Tracker

### 17.1 Overview

Manual trade recording integrated into the P&L tab as sub-tabs: **[Backtest]** (existing P&L) and **[My Trades]**.

### 17.2 Data Layer

- **Database**: SQLite at `data/trades.db`
- **Module**: `apps/dashboard/trades.py`

**Schema** (`trades` table):

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT PK | 12-char hex UUID |
| symbol | TEXT | Ticker symbol |
| timeframe | TEXT | Trading timeframe |
| direction | TEXT | `long` or `short` |
| entry_date | TEXT | ISO date |
| entry_price | REAL | Entry price |
| size | REAL | Position size multiplier |
| exit_date | TEXT | ISO date (null if open) |
| exit_price | REAL | Exit price (null if open) |
| status | TEXT | `open` or `closed` |
| stop_price | REAL | Stop-loss price |
| notes | TEXT | User notes |
| currency | TEXT | Price currency |
| created_at | TEXT | Creation timestamp |

### 17.3 API

| Method | Endpoint | Body | Response |
|--------|----------|------|----------|
| GET | `/api/trades` | `?status=open&symbol=AAPL` | `[{trade}, ...]` |
| POST | `/api/trades` | `{ symbol, entry_price, entry_date, ... }` | `{ ok, trade }` |
| POST | `/api/trades/close` | `{ id, exit_price, exit_date }` | `{ ok, trade }` |
| POST | `/api/trades/update` | `{ id, stop_price, size, notes }` | `{ ok, trade }` |
| POST | `/api/trades/delete` | `{ id }` | `{ ok }` |
| GET | `/api/trades/stats` | — | `{ total, wins, losses, win_rate, ... }` |

### 17.4 UI

- **Open Positions table**: Symbol, TF, Direction, Entry Price, Date, Size, Stop, Unrealized P&L (from screener's `last_close`), Notes, Close/Delete buttons
- **Closed Trades table**: Symbol, TF, Direction, Entry, Exit, P&L %, Dates, Notes, Delete button
- **Enter Trade modal**: pre-fills current symbol + price from screener
- **Close Trade modal**: pre-fills current price from screener
- **Stats bar**: Trades count, Win Rate, Total P&L, Expectancy, Avg Win, Avg Loss
- **Equity Curve**: Plotly line chart of cumulative P&L % over closed trades

---

## 18. P&L Backtest (Server-Side Bulk Endpoint)

### 18.1 Overview

The P&L Backtest tab now loads via a single server-side API call instead of fetching individual figures per symbol. This reduces load time from ~30s to ~1-3s.

### 18.2 API

| Method | Endpoint | Query Params | Response |
|--------|----------|-------------|----------|
| GET | `/api/pnl-summary` | `group=stoof&tf=1D` | JSON with `portfolio`, `per_symbol`, `all_trades` |

### 18.3 Response Shape

```json
{
  "portfolio": {
    "total_return": 4157.03,
    "total_trades": 941,
    "win_rate": 49.3,
    "avg_gain": 12.5,
    "avg_loss": -6.2,
    "max_dd": -85.3,
    "profit_factor": 2.1,
    "sharpe": 3.2,
    "best": 45.2,
    "worst": -28.1,
    "equity_curve": { "dates": [...], "values": [...] }
  },
  "per_symbol": [
    { "symbol": "AAPL", "name": "Apple Inc.", "trades": 12, "return": 45.2, "hit_rate": 66.7, ... }
  ],
  "all_trades": [
    { "symbol": "AAPL", "entry": "2024-01-15", "exit": "2024-03-20", "ret": 12.3, "hold": 45, "label": "C3", "reason": "ATR stop" }
  ]
}
```

### 18.4 Server-Side Computation

- Uses `compute_position_events()` from `strategy.py` (same single source of truth as charts)
- Loads enriched Parquet via `_Caches.load_df()` (disk-mtime caching)
- Computes KPI state maps, runs position engine, aggregates portfolio stats
- Supports group filtering and per-timeframe analysis
- `all_trades` capped at 500 most recent for payload efficiency

---

## 19. Scan Optimization

### 19.1 Before

After the screener found combo hits, scan called `_build_main(["--mode", "all"])` to rebuild the entire dashboard (~130s).

### 19.2 After

1. Screener runs and finds C3/C4 hits
2. `inject_screener_groups()` writes tickers to `entry_stocks.csv`
3. **Diff**: scan checks which tickers already have enriched Parquet files
4. **Batch enrich**: only truly new tickers go through `enrich_symbols()` (~5-10s per batch)
5. Screener JSON is rebuilt incrementally
6. Scan time drops from ~130s to ~40-70s

### 19.3 Batch Enrichment Pipeline (`enrich_symbols()`)

Located in `build_dashboard.py`. Steps:

1. Resolve yfinance tickers
2. Download daily + hourly OHLCV in batch
3. Resample to all 5 TFs (4H, 1D, 1W, 2W, 1M)
4. Compute indicators per TF
5. Save enriched Parquet files
6. Update `sector_map.json` with fundamentals
7. Rebuild `screener_summary.json` (merges new + existing enriched data)
8. Report progress via callback (used by SSE)

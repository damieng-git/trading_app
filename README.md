# Trading indicators dashboard

Converts Pine Script indicators into Python, computes them on multi-timeframe OHLCV data (1D, 1W, 2W, 1M), and generates a standalone lazy-load Plotly dashboard with a daily screener.

## What it produces

- `data/dashboard_artifacts/dashboard_shell.html` — interactive dashboard (lazy-load shell)
- `data/dashboard_artifacts/dashboard_assets/` — per-symbol Plotly JSON assets
- `data/feature_store/enriched/<dataset>/stock_data/<SYMBOL>_<TF>.parquet` — enriched OHLCV + indicator columns

## Commands

```bash
# Full build (download + compute + dashboard)
python -m trading_dashboard dashboard build

# Refresh dashboard from cached data (no yfinance download)
python -m trading_dashboard dashboard refresh

# UI-only rebuild (fastest — skips indicator recomputation)
python -m trading_dashboard dashboard rebuild-ui

# Serve dashboard
python -m apps.dashboard.serve_dashboard

# Daily screener
python -m trading_dashboard screener run
```

## Symbol management

```bash
python -m trading_dashboard symbols list
python -m trading_dashboard symbols add AAPL --group watchlist
python -m trading_dashboard symbols sync
```

## Docs

| Document | Content |
|----------|---------|
| `CLAUDE.md` | Architecture, data flow, CLI reference, deployment |
| `CONTRIBUTING.md` | Developer guide: adding indicators, KPIs, CSS guidelines |
| `docs/strategy_pipeline_design.md` | Strategy engine design and adding new strategies |
| `docs/chart_render_spec.md` | Chart tab render contract |
| `docs/architecture_audit.md` | Open improvement backlog |
| `docs/changelog.md` | Full change history |

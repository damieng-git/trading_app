# Trading indicators dashboard (4H / 1D / 1W / 2W / 1M)

This project converts Pine Script indicators into Python, computes them on multi-timeframe OHLCV data (4H, 1D, 1W, 2W, 1M), and generates a standalone Plotly dashboard.

## What it produces

- `data/dashboard_artifacts/dashboard_shell.html`: interactive dashboard (lazy-load shell)
- `data/dashboard_artifacts/dashboard_assets/`: per-symbol Plotly JSON assets
- `data/feature_store/enriched/<dataset>/stock_data/<SYMBOL>_<TF>.parquet`: enriched OHLCV + computed indicator columns
- `docs/pine_to_python_mapping.md`: Pine → Python mapping and limitations

## Run

```bash
# Full build (download + compute + dashboard)
python -m trading_dashboard dashboard build

# Refresh dashboard from cached data (no yfinance)
python -m trading_dashboard dashboard refresh

# UI-only rebuild (fastest — skip indicator recomputation)
python -m trading_dashboard dashboard rebuild-ui

# Serve dashboard via local HTTP server
python -m apps.dashboard.serve_dashboard
```

## Symbol management

```bash
python -m trading_dashboard symbols list
python -m trading_dashboard symbols add AAPL --group watchlist
python -m trading_dashboard symbols sync
```

## Notes

- Data is downloaded via `yfinance`:
  - daily (`1d`) then optionally resampled to 1W (`W-FRI`)
- If a symbol is not found, the script tries common exchange suffixes (e.g. `.PA`).

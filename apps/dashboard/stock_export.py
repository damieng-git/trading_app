"""
stock_export.py

Thin entrypoint for:
  - downloading OHLCV via yfinance
  - computing enriched indicator/KPI columns
  - writing enriched CSVs to output_data/stock_data/

This runs ONLY the export phase of `build_dashboard.py` (no dashboard HTML/assets).
"""

from __future__ import annotations

import sys

from .build_dashboard import main


if __name__ == "__main__":
    raise SystemExit(main(["--mode", "stock_export", *sys.argv[1:]]))


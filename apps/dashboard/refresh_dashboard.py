"""
refresh_dashboard.py

Thin entrypoint for generating dashboard outputs from cached enriched CSVs:
  - mapping docs + README
  - screener summary + alerts
  - dashboard shell / static assets / monolithic HTML depending on config

This runs ONLY the refresh phase of `build_dashboard.py` and does NOT call yfinance.
"""

from __future__ import annotations

import sys

from .build_dashboard import main

if __name__ == "__main__":
    raise SystemExit(main(["--mode", "refresh_dashboard", *sys.argv[1:]]))


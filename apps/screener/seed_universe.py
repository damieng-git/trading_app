#!/usr/bin/env python3
"""Seed the screener universe CSV from Wikipedia index constituent tables.

Sources:
  - S&P 500:       https://en.wikipedia.org/wiki/List_of_S%26P_500_companies
  - S&P 400:       https://en.wikipedia.org/wiki/List_of_S%26P_400_companies
  - FTSE 100:      https://en.wikipedia.org/wiki/FTSE_100_Index
  - FTSE 250:      https://en.wikipedia.org/wiki/FTSE_250_Index
  - DAX:           https://en.wikipedia.org/wiki/DAX
  - CAC 40:        https://en.wikipedia.org/wiki/CAC_40
  - SMI:           https://en.wikipedia.org/wiki/Swiss_Market_Index
  - AEX:           https://en.wikipedia.org/wiki/AEX_index
  - IBEX 35:       https://en.wikipedia.org/wiki/IBEX_35
  - FTSE MIB:      https://en.wikipedia.org/wiki/FTSE_MIB
  - OMX Stockholm: https://en.wikipedia.org/wiki/OMX_Stockholm_30
  - OMX Copenhagen:https://en.wikipedia.org/wiki/OMX_Copenhagen_25
  - OMX Helsinki:  https://en.wikipedia.org/wiki/OMX_Helsinki_25
  - BEL 20:        https://en.wikipedia.org/wiki/BEL_20
  - OBX:           https://en.wikipedia.org/wiki/OBX_Index
  - WIG 20:        https://en.wikipedia.org/wiki/WIG20
  - ATX:           https://en.wikipedia.org/wiki/Austrian_Traded_Index

Output: apps/screener/configs/universe.csv

Usage:
    python3 -m apps.screener.seed_universe
"""

from __future__ import annotations

import io
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests

_OUT_PATH = Path(__file__).resolve().parent / "configs" / "universe.csv"

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
})

_DELAY = 5  # seconds between Wikipedia requests


def _fetch_tables(url: str, **kwargs) -> list[pd.DataFrame]:
    """Fetch URL with retry, parse HTML tables."""
    for attempt in range(5):
        try:
            resp = _SESSION.get(url, timeout=30)
            if resp.status_code == 200:
                return pd.read_html(io.StringIO(resp.text), **kwargs)
            if resp.status_code in (403, 429):
                delay = _DELAY * (2 ** attempt)
                print(f"  Rate-limited ({resp.status_code}), retrying in {delay}s...")
                time.sleep(delay)
                continue
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  Request failed: {e}", file=sys.stderr)
            if attempt < 4:
                time.sleep(_DELAY * (2 ** attempt))
    return []


def _clean(raw: str) -> str:
    t = str(raw or "").strip()
    t = re.sub(r"\s+", " ", t).split(" ")[0]
    t = t.replace("–", "-").replace("—", "-")
    return t.strip()


def _find_ticker_col(df: pd.DataFrame) -> str | None:
    for c in df.columns:
        cl = str(c).lower()
        if any(kw in cl for kw in ["ticker", "symbol", "epic", "code"]):
            return c
    return None


def _extract_us_tickers(url: str, source: str) -> list[dict]:
    """Extract US tickers from S&P Wikipedia tables."""
    tables = _fetch_tables(url, match="Symbol")
    if not tables:
        tables = _fetch_tables(url)
    if not tables:
        return []
    df = tables[0]
    col = _find_ticker_col(df) or df.columns[0]
    out = []
    for _, row in df.iterrows():
        t = _clean(row[col])
        if t and not t.startswith("^") and len(t) <= 6:
            t = t.replace(".", "-")
            out.append({"ticker": t, "source_index": source, "geo": "US"})
    return out


def _extract_eu_tickers(url: str, source: str, suffix: str, match: str | None = None) -> list[dict]:
    """Extract EU tickers from a Wikipedia index table and apply yfinance suffix."""
    kwargs = {"match": match} if match else {}
    tables = _fetch_tables(url, **kwargs)
    if not tables:
        return []

    # Find the table most likely to be the constituents (largest with a ticker-like column)
    best_df = None
    best_col = None
    for t in tables:
        col = _find_ticker_col(t)
        if col is not None and len(t) > (len(best_df) if best_df is not None else 0):
            best_df = t
            best_col = col
    if best_df is None:
        best_df = max(tables, key=len) if tables else pd.DataFrame()
        best_col = _find_ticker_col(best_df)
    if best_df is None or best_df.empty:
        return []
    if best_col is None:
        best_col = best_df.columns[1] if len(best_df.columns) > 1 else best_df.columns[0]

    out = []
    for _, row in best_df.iterrows():
        t = _clean(row[best_col])
        if not t or t.startswith("^") or len(t) > 10:
            continue
        # Already has a suffix (e.g., "SAP.DE")
        if "." in t and not t.startswith("."):
            out.append({"ticker": t, "source_index": source, "geo": "EU"})
        else:
            out.append({"ticker": f"{t}{suffix}", "source_index": source, "geo": "EU"})
    return out


# ── Source definitions ───────────────────────────────────────────────────

_US_SOURCES = [
    ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "sp500"),
    ("https://en.wikipedia.org/wiki/List_of_S%26P_400_companies", "sp400"),
]

_EU_SOURCES = [
    ("https://en.wikipedia.org/wiki/FTSE_100_Index", "ftse100", ".L", "Ticker"),
    ("https://en.wikipedia.org/wiki/FTSE_250_Index", "ftse250", ".L", None),
    ("https://en.wikipedia.org/wiki/DAX", "dax", ".DE", None),
    ("https://en.wikipedia.org/wiki/CAC_40", "cac40", ".PA", None),
    ("https://en.wikipedia.org/wiki/Swiss_Market_Index", "smi", ".SW", None),
    ("https://en.wikipedia.org/wiki/AEX_index", "aex", ".AS", None),
    ("https://en.wikipedia.org/wiki/IBEX_35", "ibex35", ".MC", "Ticker"),
    ("https://en.wikipedia.org/wiki/FTSE_MIB", "ftsemib", ".MI", None),
    ("https://en.wikipedia.org/wiki/OMX_Stockholm_30", "omxs30", ".ST", None),
    ("https://en.wikipedia.org/wiki/OMX_Copenhagen_25", "omxc25", ".CO", None),
    ("https://en.wikipedia.org/wiki/OMX_Helsinki_25", "omxh25", ".HE", None),
    ("https://en.wikipedia.org/wiki/BEL_20", "bel20", ".BR", None),
    ("https://en.wikipedia.org/wiki/OBX_Index", "obx", ".OL", None),
    ("https://en.wikipedia.org/wiki/WIG20", "wig20", ".WA", None),
    ("https://en.wikipedia.org/wiki/Austrian_Traded_Index", "atx", ".VI", None),
]


def seed_universe(output_path: Path | None = None) -> pd.DataFrame:
    """Fetch all index constituents, deduplicate, and write CSV."""
    out = output_path or _OUT_PATH
    all_records: list[dict] = []

    for url, source in _US_SOURCES:
        label = source.upper()
        print(f"Fetching {label}...")
        tickers = _extract_us_tickers(url, source)
        print(f"  → {len(tickers)} tickers")
        all_records.extend(tickers)
        time.sleep(_DELAY)

    for url, source, suffix, match in _EU_SOURCES:
        label = source.upper()
        print(f"Fetching {label}...")
        tickers = _extract_eu_tickers(url, source, suffix, match)
        print(f"  → {len(tickers)} tickers")
        all_records.extend(tickers)
        time.sleep(_DELAY)

    df = pd.DataFrame(all_records)
    df = df.drop_duplicates(subset=["ticker"], keep="first")
    df = df.sort_values("ticker").reset_index(drop=True)

    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)

    n_us = len(df[df["geo"] == "US"])
    n_eu = len(df[df["geo"] == "EU"])
    print(f"\nUniverse CSV written: {out}")
    print(f"  Total unique tickers: {len(df)}")
    print(f"  US: {n_us}  |  EU: {n_eu}")

    return df


if __name__ == "__main__":
    seed_universe()

"""Universe loading, geo filtering, and stock quality gates.

Loads the curated universe CSV and applies filters to produce a list of
tradeable tickers suitable for daily C3/C4 screening.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Dict, List

import pandas as pd

logger = logging.getLogger(__name__)

_CONFIGS_DIR = Path(__file__).resolve().parent / "configs"
_DEFAULT_UNIVERSE_CSV = _CONFIGS_DIR / "universe.csv"

# ── Quality filter defaults ─────────────────────────────────────────────────

DEFAULT_MIN_PRICE = 5.0
DEFAULT_MIN_DOLLAR_VOLUME = 2_000_000  # 20-day avg Close × Volume
DEFAULT_MIN_MARKET_CAP = 300_000_000
DEFAULT_MIN_BARS = 250
DEFAULT_ALLOWED_GEO = {"US", "EU"}


def load_universe(
    path: Path | None = None,
    *,
    allowed_geo: set[str] | None = None,
) -> pd.DataFrame:
    """Load universe CSV and filter to allowed geographies.

    Expected CSV columns: ticker, source_index, geo
    Returns a DataFrame with at least ``ticker`` and ``geo`` columns.
    """
    csv_path = path or _DEFAULT_UNIVERSE_CSV
    if not csv_path.exists():
        raise FileNotFoundError(f"Universe CSV not found: {csv_path}")

    df = pd.read_csv(csv_path, dtype=str).dropna(subset=["ticker"])
    df["ticker"] = df["ticker"].str.strip()
    df = df[df["ticker"].str.len() > 0].copy()
    df = df.drop_duplicates(subset=["ticker"], keep="first")

    if allowed_geo:
        geo = allowed_geo
        if "geo" in df.columns:
            df["geo"] = df["geo"].str.strip().str.upper()
            df = df[df["geo"].isin(geo)].copy()

    logger.info("Universe loaded: %d tickers from %s", len(df), csv_path.name)
    return df.reset_index(drop=True)


def apply_quality_filters(
    tickers: List[str],
    ohlcv_map: Dict[str, pd.DataFrame],
    *,
    sector_map: Dict[str, dict] | None = None,
    min_price: float = DEFAULT_MIN_PRICE,
    min_dollar_volume: float = DEFAULT_MIN_DOLLAR_VOLUME,
    min_market_cap: float = DEFAULT_MIN_MARKET_CAP,
    min_bars: int = DEFAULT_MIN_BARS,
) -> List[str]:
    """Filter tickers by price, volume, market cap, and data sufficiency.

    Returns the subset of *tickers* that pass all gates.
    """
    sector_map = sector_map or {}
    passed: List[str] = []

    for sym in tickers:
        df = ohlcv_map.get(sym)
        if df is None or df.empty:
            continue

        if len(df) < min_bars:
            continue

        close = df["Close"].dropna()
        if close.empty:
            continue

        last_close = float(close.iloc[-1])
        if not math.isfinite(last_close) or last_close < min_price:
            continue

        # 20-day average dollar volume
        if "Volume" in df.columns:
            tail = df.tail(20)
            avg_dv = (tail["Close"] * tail["Volume"]).mean()
            if math.isfinite(avg_dv) and avg_dv < min_dollar_volume:
                continue
        else:
            continue  # no volume data → skip

        # Market cap from sector_map (cached yfinance .info)
        meta = sector_map.get(sym, {})
        fund = meta.get("fundamentals", {}) if isinstance(meta, dict) else {}
        mcap = fund.get("market_cap")
        if mcap is not None:
            try:
                if float(mcap) < min_market_cap:
                    continue
            except (TypeError, ValueError):
                pass
        # If market_cap is missing, let it through (data may not be cached yet)

        passed.append(sym)

    logger.info("Quality filters: %d / %d passed", len(passed), len(tickers))
    return passed


def exclude_indices_and_leveraged(tickers: List[str]) -> List[str]:
    """Remove index tickers (^PREFIX) and common leveraged/inverse ETF patterns."""
    import re
    _leveraged_re = re.compile(r"^(3[A-Z]{2,}|TQQQ|SQQQ|SPXU|UPRO|SDS|SSO|QLD|UDOW|SDOW)", re.IGNORECASE)
    out = []
    for t in tickers:
        if t.startswith("^"):
            continue
        if _leveraged_re.match(t):
            continue
        out.append(t)
    return out

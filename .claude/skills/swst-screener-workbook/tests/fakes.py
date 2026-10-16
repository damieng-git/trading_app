"""Offline fakes shared by the dashboard tests. No network, no yfinance objects."""

import datetime as _dt
import os
import sys
import zlib

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import enrich as E  # noqa: E402

SUFFIX_CCY = {".L": "GBp", ".ST": "SEK", ".DE": "EUR", ".PA": "EUR"}


def _synth_history(last_price, ret_1y, currency, n=800, seed=0):
    raw_last = last_price * (100.0 if currency in ("GBp", "GBX") else 1.0)
    r = ret_1y if ret_1y is not None else 0.0
    raw_start = raw_last / (1.0 + r) if (1.0 + r) != 0 else raw_last
    rng = np.random.default_rng(seed)
    close = np.linspace(raw_start * 0.9, raw_last, n) * (1 + 0.01 * rng.standard_normal(n))
    close[-253] = raw_last / (1.0 + r)
    close[-1] = raw_last
    idx = pd.date_range(end=_dt.date(2026, 9, 19), periods=n, freq="D")
    return pd.DataFrame({"close": close, "volume": np.full(n, 2_000_000.0)}, index=idx)


def fake_fetch_for(frame, calls=None):
    """symbol -> Snapshot echoing each row's currency/price/1y return (so the
    cross-check passes) with ticker-seeded, deterministic fundamentals.
    ``calls`` (a list) records every symbol fetched."""
    by_symbol = {}
    for _, row in frame.iterrows():
        sym = E.map_symbol(row["ticker"], row["country"], row["currency"])
        if sym is None:
            continue
        suffix = "." + sym.rsplit(".", 1)[1] if "." in sym else ""
        ccy = SUFFIX_CCY.get(suffix, "USD")
        seed = zlib.crc32(sym.encode())
        price = row["price_local"] if pd.notna(row["price_local"]) else 50.0
        ret = row["ret_1y"] if pd.notna(row["ret_1y"]) else None
        info = {
            "currency": ccy,
            "financialCurrency": {"GBp": "GBP"}.get(ccy, ccy),
            "trailingEps": 3.0, "trailingPE": 10.0 + seed % 25,
            "forwardEps": 3.2, "forwardPE": 9.0 + seed % 20,
            "priceToSalesTrailing12Months": 1.0 + seed % 7,
            "enterpriseToEbitda": 5.0 + seed % 15,
            "priceToBook": 0.8 + (seed % 20) / 10.0,
            "returnOnEquity": ((seed % 30) - 5) / 100.0,
            "marketCap": 1.0e10, "freeCashflow": 4.0e8,
            "beta": 0.6 + (seed % 14) / 10.0,
            "totalCash": 8.0e8, "totalDebt": 1.0e8, "operatingCashflow": 6.0e8,
            "sector": "Financial Services" if row["industry"] == "Banks" else "Other",
            "industry": "Fake",
            # name + consensus (targets in the quote's raw units, so GBp names are x100)
            "longName": f"{row['ticker']} Holdings (fake)",
            "recommendationKey": "buy", "recommendationMean": 2.0,
            "numberOfAnalystOpinions": 5 + seed % 10,
            "targetMeanPrice": price * 1.1 * (100.0 if ccy == "GBp" else 1.0),
            "targetLowPrice": price * 0.9 * (100.0 if ccy == "GBp" else 1.0),
            "targetHighPrice": price * 1.3 * (100.0 if ccy == "GBp" else 1.0),
            "profitMargins": 0.12, "revenueGrowth": 0.05, "debtToEquity": 40.0,
        }
        by_symbol[sym] = E.Snapshot(
            symbol=sym, history=_synth_history(price, ret, ccy, seed=seed % 1000),
            info=info,
            calendar={"earnings": _dt.date(2026, 10, 29), "ex_div": None})

    def fetch(symbol):
        if calls is not None:
            calls.append(symbol)
        return by_symbol.get(symbol, E.Snapshot(
            symbol=symbol, history=pd.DataFrame(columns=["close", "volume"]), info={},
            ok=False, calendar={"earnings": None, "ex_div": None}))
    return fetch

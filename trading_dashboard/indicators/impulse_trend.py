"""
Impulse Trend Levels [BOSWaves] — Adaptive EMA bands with impulse decay.

Port of TradingView script: https://www.tradingview.com/v/cmA33Ppg/
"""

from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd

from ._base import ema as _ema
from ._base import sma as _sma


def impulse_trend_levels(
    df: pd.DataFrame,
    trend_length: int = 19,
    impulse_lookback: int = 5,
    decay_rate: float = 0.99,
    mad_length: int = 20,
    band_min: float = 1.5,
    band_max: float = 1.9,
) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """
    Impulse Trend Levels.

    Returns (basis, upper, lower, trend).
    trend: 1 = bullish, -1 = bearish.
    """
    trend_length = int(trend_length)
    impulse_lookback = int(impulse_lookback)
    decay_rate = float(decay_rate)
    mad_length = int(mad_length)
    band_min = float(band_min)
    band_max = float(band_max)

    close = df["Close"].astype(float)
    basis = _ema(close, trend_length)

    mean_val = _sma(close, mad_length)
    mad = _sma((close - mean_val).abs(), mad_length)

    n = len(close)
    impulse = np.zeros(n)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    trend = np.ones(n, dtype=int)

    for i in range(n):
        m = float(mad.iat[i]) if pd.notna(mad.iat[i]) else 0.0
        b = float(basis.iat[i]) if pd.notna(basis.iat[i]) else np.nan
        c = float(close.iat[i]) if pd.notna(close.iat[i]) else np.nan

        if np.isnan(b) or np.isnan(c) or m == 0:
            upper[i] = np.nan
            lower[i] = np.nan
            continue

        if i >= impulse_lookback:
            prev_c = float(close.iat[i - impulse_lookback])
            raw_imp = (c - prev_c) / m if m > 0 else 0.0
        else:
            raw_imp = 0.0

        if abs(raw_imp) > 1.0:
            impulse[i] = abs(raw_imp)
        else:
            impulse[i] = impulse[i - 1] * decay_rate if i > 0 else 0.0

        freshness = min(impulse[i] / 2.0, 1.0)
        band_mult = band_max - (band_max - band_min) * freshness

        upper[i] = b + m * band_mult
        lower[i] = b - m * band_mult

        if i == 0:
            trend[i] = 1
            continue

        prev_trend = trend[i - 1]
        if prev_trend == -1 and c > upper[i]:
            trend[i] = 1
        elif prev_trend == 1 and c < lower[i]:
            trend[i] = -1
        else:
            trend[i] = prev_trend

    idx = df.index
    return (
        basis,
        pd.Series(upper, index=idx, name="ITL_upper"),
        pd.Series(lower, index=idx, name="ITL_lower"),
        pd.Series(trend, index=idx, name="ITL_trend"),
    )

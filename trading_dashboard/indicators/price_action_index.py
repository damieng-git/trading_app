"""
Price Action Index (PAI).

Translated from Pine Script "Combined Band Light" script 10.

Combines a centered stochastic oscillator with a volatility (dispersion)
measure.  Z = P * V where P = (stoch - 50)/50 and V = stoch(stdev).
Z >= 0 = bullish, Z < 0 = bearish (binary, no neutral state).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ._base import sma, stdev, highest, lowest


def _stochastic(src: pd.Series, high: pd.Series, low: pd.Series, length: int) -> pd.Series:
    """Standard stochastic oscillator (0–100)."""
    h = highest(high, length)
    l = lowest(low, length)
    rng = (h - l).replace(0.0, np.nan)
    return ((src - l) / rng) * 100.0


def price_action_index(
    df: pd.DataFrame,
    *,
    stoch_length: int = 20,
    smooth: int = 3,
    dispersion_length: int = 20,
) -> pd.Series:
    """
    Compute the Price Action Index.

    Returns
    -------
    pd.Series — Z score (positive = bullish, negative = bearish).
    """
    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    # P = centered stochastic: (SMA(stoch, smooth) - 50) / 50  → [-1, +1]
    raw_stoch = _stochastic(close, high, low, stoch_length)
    stoch_smooth = sma(raw_stoch, smooth)
    p = (stoch_smooth - 50.0) / 50.0

    # V = stochastic of price dispersion (stdev)
    sd = stdev(close, dispersion_length)
    v = _stochastic(sd, sd, sd, stoch_length)

    z = p * v
    return z

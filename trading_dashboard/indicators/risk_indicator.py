"""
Risk Indicator.

Translated from Pine Script "Combined Band Light" script 7.

Computes a normalised risk score based on the log deviation of price from its
SMA, power-scaled by bar index.  Normalised to the running all-time high/low
of that score, yielding a 0–1 oscillator.  <0.2 = low risk (bullish),
>0.8 = high risk (bearish).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ._base import sma


def risk_indicator(
    close: pd.Series,
    *,
    sma_period: int = 50,
    power_factor: float = 0.395,
    initial_atl: float = 2.5,
) -> pd.Series:
    """
    Compute the risk indicator.

    Returns
    -------
    pd.Series — normalised risk score (0–1).
    """
    close_arr = close.to_numpy(dtype=float)
    sma_arr = sma(close, sma_period).to_numpy(dtype=float)

    n = len(close_arr)
    result = np.full(n, np.nan, dtype=float)
    ath = 0.0
    atl = initial_atl

    for i in range(n):
        if np.isnan(close_arr[i]) or np.isnan(sma_arr[i]) or sma_arr[i] <= 0 or close_arr[i] <= 0:
            continue

        bar_idx = i + 1  # 1-based to avoid log(0)
        average = (np.log(close_arr[i]) - np.log(sma_arr[i])) * (bar_idx ** power_factor)

        if average > ath:
            ath = average
        if average < atl:
            atl = average

        denom = ath - atl
        if denom > 0:
            result[i] = (average - atl) / denom
        else:
            result[i] = 0.5

    return pd.Series(result, index=close.index)

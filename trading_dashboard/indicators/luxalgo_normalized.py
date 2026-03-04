"""
LuxAlgo-style multi-scale normalized oscillator.

Translated from Pine Script "Combined Band Light" scripts 6 and 8.

For each lookback from 4..length, compute (src - min)/(max - min) and average
them, yielding a 0–100 oscillator.  Pre-smoothed with SMA, post-smoothed with
SMA.  Low (<20) = oversold, high (>80) = overbought.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ._base import highest, lowest, sma


def luxalgo_normalized(
    close: pd.Series,
    *,
    length: int = 14,
    presmooth: int = 10,
    postsmooth: int = 10,
) -> pd.Series:
    """
    Compute the LuxAlgo multi-scale normalised oscillator.

    Returns
    -------
    pd.Series — the post-smoothed oscillator (0–100 range).
    """
    src = sma(close, presmooth)

    # Multi-scale normalization: for each window from 4..length,
    # (src - min_of_window) / (max_of_window - min_of_window), averaged.
    n = len(src)
    arr = src.to_numpy(dtype=float)
    accum = np.zeros(n, dtype=float)
    count = 0

    for win in range(4, length + 1):
        s_max = highest(src, win).to_numpy(dtype=float)
        s_min = lowest(src, win).to_numpy(dtype=float)
        rng = s_max - s_min
        rng[rng == 0] = np.nan
        accum += (arr - s_min) / rng
        count += 1

    if count == 0:
        return pd.Series(np.nan, index=close.index)

    norm = (accum / count) * 100.0
    norm_series = pd.Series(norm, index=close.index)
    result = sma(norm_series, postsmooth)

    return result

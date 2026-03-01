"""
GK Trend Ribbon SWING + PREPARE HUD — Zero-lag EMA with ATR bands.

Port of TradingView script: https://www.tradingview.com/v/xCtlJHSI/
"""

from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd

from ._base import ema as _ema, atr as _atr


def gk_trend_ribbon(
    df: pd.DataFrame,
    length: int = 200,
    band_mult: float = 2.0,
    atr_length: int = 21,
    confirm_bars: int = 2,
) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """
    GK Trend Ribbon.

    Returns (zl_line, upper_band, lower_band, trend).
    trend: 1 = bullish, -1 = bearish, 0 = neutral.
    """
    length = int(length)
    confirm_bars = max(1, min(3, int(confirm_bars)))
    close = df["Close"].astype(float)

    lag = max(0, (length - 1) // 2)
    if lag > 0:
        src = close + (close - close.shift(lag))
    else:
        src = close.copy()

    zl = _ema(src, length)
    atr_val = _atr(df, atr_length, smoothing="RMA")

    upper = zl + atr_val * float(band_mult)
    lower = zl - atr_val * float(band_mult)

    zl_rising = zl > zl.shift(1)
    zl_falling = zl < zl.shift(1)

    above_upper = close > upper
    below_lower = close < lower

    bull_confirmed = above_upper.copy()
    bear_confirmed = below_lower.copy()
    for shift in range(1, confirm_bars):
        bull_confirmed = bull_confirmed & above_upper.shift(shift).fillna(False).infer_objects(copy=False)
        bear_confirmed = bear_confirmed & below_lower.shift(shift).fillna(False).infer_objects(copy=False)

    bull_cond = bull_confirmed & zl_rising
    bear_cond = bear_confirmed & zl_falling

    n = len(df)
    trend = np.zeros(n, dtype=int)
    for i in range(n):
        if bull_cond.iat[i]:
            trend[i] = 1
        elif bear_cond.iat[i]:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1] if i > 0 else 0

    idx = df.index
    return (
        pd.Series(zl, index=idx, name="GK_zl"),
        pd.Series(upper, index=idx, name="GK_upper"),
        pd.Series(lower, index=idx, name="GK_lower"),
        pd.Series(trend, index=idx, name="GK_trend"),
    )

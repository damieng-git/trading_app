"""
Breakout Targets [AlgoAlpha] — Pivot-based consolidation detection + breakout signals.

Port of TradingView script: https://www.tradingview.com/v/JY7Rjrsi/
"""

from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd

from ._base import atr as _atr, ema as _ema, wma as _wma


def breakout_targets(
    df: pd.DataFrame,
    range_period: int = 99,
    atr_period: int = 14,
    sl_mult: float = 5.0,
    tp1_mult: float = 0.5,
    tp2_mult: float = 1.0,
    tp3_mult: float = 1.5,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Breakout Targets.

    Returns (breakout_signal, range_high, range_low).
    breakout_signal: 1 = bullish breakout, -1 = bearish breakout, 0 = no signal.
    """
    range_period = int(range_period)
    atr_period = int(atr_period)
    half = max(range_period // 2, 2)

    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    body = (close - df["Open"].astype(float)).abs()

    body_wma = _wma(body, range_period)
    body_ema = _ema(body, range_period)

    consolidation = body_wma < body_ema

    n = len(df)
    rng_hi = np.full(n, np.nan)
    rng_lo = np.full(n, np.nan)
    signal = np.zeros(n, dtype=int)

    in_range = False
    box_top = np.nan
    box_bot = np.nan

    for i in range(half, n):
        if not pd.notna(consolidation.iat[i]):
            continue

        prev_consol = bool(consolidation.iat[i - 1]) if pd.notna(consolidation.iat[i - 1]) else False
        curr_consol = bool(consolidation.iat[i])

        if not prev_consol and curr_consol:
            lookback = min(half, i)
            box_top = float(high.iloc[i - lookback:i + 1].max())
            box_bot = float(low.iloc[i - lookback:i + 1].min())
            in_range = True

        if in_range and curr_consol:
            rng_hi[i] = box_top
            rng_lo[i] = box_bot
            box_top = max(box_top, float(high.iat[i]))
            box_bot = min(box_bot, float(low.iat[i]))

        c = float(close.iat[i])
        if in_range and not np.isnan(box_top) and not np.isnan(box_bot):
            if c > box_top:
                signal[i] = 1
                in_range = False
            elif c < box_bot:
                signal[i] = -1
                in_range = False

        if not curr_consol and not in_range:
            rng_hi[i] = np.nan
            rng_lo[i] = np.nan

    idx = df.index
    return (
        pd.Series(signal, index=idx, name="BT_signal"),
        pd.Series(rng_hi, index=idx, name="BT_range_high"),
        pd.Series(rng_lo, index=idx, name="BT_range_low"),
    )

"""
CCI + Choppiness Index + Bollinger Band %R composite.

Translated from Pine Script "Combined Band Light" scripts 4 and 9.
Two param variants: v1 (CCI=18, Chop=14, BB=20) and v2 (CCI=90, Chop=24, BB=10).

The three sub-indicators are averaged and EMA-smoothed into a single 0–100ish
oscillator.  Low values (<25) suggest oversold, high values (>65) overbought.
"""
from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd

from ._base import ema, highest, lowest, sma, stdev
from ._base import hlc3 as _hlc3


def cci_chop_bb(
    df: pd.DataFrame,
    *,
    cci_length: int = 18,
    chop_length: int = 14,
    bb_length: int = 20,
    bb_mult: float = 2.0,
    smooth: int = 10,
) -> Tuple[pd.Series, pd.Series]:
    """
    Compute the CCI+Chop+BB composite oscillator.

    Returns
    -------
    (raw, smoothed) — the raw average and EMA-smoothed composite.
    """
    close = df["Close"]
    src = _hlc3(df)

    # --- CCI ---
    ma = sma(src, cci_length)
    # Pine's ta.dev computes mean absolute deviation, but the CCI formula
    # traditionally uses mean absolute deviation.  The Pine indicator divides
    # by 0.015 * ta.dev which matches the standard CCI definition.
    # We replicate: CCI = (src - SMA) / (0.015 * MAD), then halve it.
    mad = src.rolling(window=cci_length, min_periods=cci_length).apply(
        lambda w: np.mean(np.abs(w - np.mean(w))), raw=True
    )
    mad = mad.replace(0.0, np.nan)
    cci = ((src - ma) / (0.015 * mad)) / 2.0

    # --- Choppiness Index ---
    # Uses ATR(1) which is just true_range
    prev_close = close.shift(1)
    tr = pd.concat([
        (df["High"] - df["Low"]).abs(),
        (df["High"] - prev_close).abs(),
        (df["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr_sum = tr.rolling(window=chop_length, min_periods=chop_length).sum()
    atr_high = highest(tr, chop_length)
    atr_low = lowest(tr, chop_length)
    hl_diff = (atr_high - atr_low).replace(0.0, np.nan)
    chop = 100.0 * np.log10(atr_sum / hl_diff) / np.log10(float(chop_length))

    # --- BB percentile (0–80 range) ---
    basis = sma(close, bb_length)
    dev = bb_mult * stdev(close, bb_length)
    upper = basis + dev
    lower = basis - dev
    bb_range = (upper - lower).replace(0.0, np.nan)
    bbr = ((close - lower) / bb_range) * 80.0

    # --- Composite ---
    raw = (chop + cci + bbr) / 3.0
    smoothed = ema(raw, smooth)

    return raw, smoothed

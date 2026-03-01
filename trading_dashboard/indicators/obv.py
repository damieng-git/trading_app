from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd

from ._base import ema


def obv_oscillator(df: pd.DataFrame, length: int = 20) -> Tuple[pd.Series, pd.Series]:
    """
    OBVOSC_LB.rtf translation.
    Returns:
      obv, obv_osc = obv - ema(obv, length)
    """
    length = int(length)
    ch = df["Close"].diff()
    signed_vol = np.where(ch > 0, df["Volume"], np.where(ch < 0, -df["Volume"], 0.0))
    obv = pd.Series(signed_vol, index=df.index, dtype=float).cumsum()
    obv_ema = ema(obv, length)
    return obv, (obv - obv_ema)


def obv_oscillator_dual_ema(
    df: pd.DataFrame, short_length: int = 1, long_length: int = 20
) -> Tuple[pd.Series, pd.Series]:
    """
    OBV oscillator using dual-EMA crossover (Band Light script 3).
    Returns:
      obv, obv_osc = ema(obv, short) - ema(obv, long)
    """
    ch = df["Close"].diff()
    signed_vol = np.where(ch > 0, df["Volume"], np.where(ch < 0, -df["Volume"], 0.0))
    obv = pd.Series(signed_vol, index=df.index, dtype=float).cumsum()
    obv_short = ema(obv, int(short_length))
    obv_long = ema(obv, int(long_length))
    return obv, (obv_short - obv_long)

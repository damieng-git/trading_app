from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd

from ._base import ema, highest, lowest, sma


def stoch_momentum_index(
    df: pd.DataFrame,
    a: int = 10,
    b: int = 3,
    c: int = 10,
    smooth_period: int = 5,
) -> Tuple[pd.Series, pd.Series]:
    """
    Stoch_MTM.rtf translation.
    Returns SMI_smoothed and EMA signal (length=c).
    """
    a = int(a)
    b = int(b)
    c = int(c)
    smooth_period = int(smooth_period)

    ll = lowest(df["Low"], a)
    hh = highest(df["High"], a)
    diff = hh - ll
    rdiff = df["Close"] - (hh + ll) / 2.0

    avgrel = ema(rdiff, b)
    avgdiff = ema(diff, b)
    smi = (avgrel / (avgdiff / 2.0).replace(0.0, np.nan)) * 100.0
    smi = smi.fillna(0.0)
    smi_smoothed = sma(smi, smooth_period)
    ema_signal = ema(smi_smoothed, c)
    return smi_smoothed, ema_signal

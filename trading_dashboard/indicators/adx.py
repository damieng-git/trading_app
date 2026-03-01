from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd

from ._base import rma, sma


def adx_di(df: pd.DataFrame, length: int = 14) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    ADX & DI.rtf translation (Pine v4 script).
    - DI+/DI- use Wilder smoothing (RMA-like update).
    - ADX is SMA(DX, len) in the script.
    """
    length = int(length)
    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [
            (df["High"] - df["Low"]).abs(),
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    dm_plus = (df["High"] - df["High"].shift(1)).where(
        (df["High"] - df["High"].shift(1)) > (df["Low"].shift(1) - df["Low"]), 0.0
    )
    dm_plus = dm_plus.where(dm_plus > 0, 0.0)

    dm_minus = (df["Low"].shift(1) - df["Low"]).where(
        (df["Low"].shift(1) - df["Low"]) > (df["High"] - df["High"].shift(1)), 0.0
    )
    dm_minus = dm_minus.where(dm_minus > 0, 0.0)

    str_ = rma(tr, length)
    sdm_plus = rma(dm_plus, length)
    sdm_minus = rma(dm_minus, length)

    di_plus = (sdm_plus / str_.replace(0.0, np.nan)) * 100.0
    di_minus = (sdm_minus / str_.replace(0.0, np.nan)) * 100.0
    dx = (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0.0, np.nan) * 100.0
    adx = sma(dx, length)
    return adx, di_plus, di_minus

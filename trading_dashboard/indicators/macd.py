from __future__ import annotations

from typing import Tuple

import pandas as pd

from ._base import ema, sma


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    MACD using EMA fast/slow and SMA signal to match CM_Ult_MacD_MFT.rtf.
    """
    fast_ma = ema(series, fast)
    slow_ma = ema(series, slow)
    macd_line = fast_ma - slow_ma
    signal_line = sma(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

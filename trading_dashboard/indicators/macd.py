from __future__ import annotations

from typing import Literal, Tuple

import pandas as pd

from ._base import ema, sma

SignalMA = Literal["SMA", "EMA"]


def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    signal_ma: SignalMA = "SMA",
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    MACD with configurable signal-line smoothing.

    The v6 CM_Ult_MacD_MFT uses SMA for the signal line.
    Pine's ta.macd (used by Band Light) uses EMA for the signal line.
    """
    fast_ma = ema(series, fast)
    slow_ma = ema(series, slow)
    macd_line = fast_ma - slow_ma
    signal_line = ema(macd_line, signal) if signal_ma == "EMA" else sma(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

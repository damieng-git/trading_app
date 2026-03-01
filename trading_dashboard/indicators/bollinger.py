from __future__ import annotations

from typing import Literal, Tuple

import pandas as pd

from ._base import ema, rma, sma, stdev, vwma, wma


def bollinger_bands(
    series: pd.Series,
    length: int = 20,
    mult: float = 2.0,
    ma_type: Literal["SMA", "EMA", "SMMA (RMA)", "WMA", "VWMA"] = "SMA",
    volume: pd.Series | None = None,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    BB 30.rtf logic (basis MA is selectable).
    """
    length = int(length)
    if ma_type == "SMA":
        basis = sma(series, length)
    elif ma_type == "EMA":
        basis = ema(series, length)
    elif ma_type == "SMMA (RMA)":
        basis = rma(series, length)
    elif ma_type == "WMA":
        basis = wma(series, length)
    elif ma_type == "VWMA":
        if volume is None:
            raise ValueError("VWMA selected but volume is None")
        basis = vwma(series, volume, length)
    else:
        raise ValueError(f"Unsupported BB MA type: {ma_type}")

    dev = float(mult) * stdev(series, length)
    upper = basis + dev
    lower = basis - dev
    return basis, upper, lower

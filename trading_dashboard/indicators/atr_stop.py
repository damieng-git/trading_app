from __future__ import annotations

import pandas as pd

from ._base import AtrSmoothing, atr


def atr_stop_loss_finder(df: pd.DataFrame, length: int = 14, smoothing: AtrSmoothing = "RMA", mult: float = 1.5) -> pd.DataFrame:
    """
    ATR.rtf translation: outputs ATR*m plus stop levels based on High/Low.
    """
    a = atr(df, length=length, smoothing=smoothing) * float(mult)
    short_stop = a + df["High"]
    long_stop = df["Low"] - a
    return pd.DataFrame({"ATR_mult": a, "ATR_short_stop": short_stop, "ATR_long_stop": long_stop}, index=df.index)

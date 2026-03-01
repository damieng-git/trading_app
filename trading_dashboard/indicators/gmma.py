from __future__ import annotations

import pandas as pd

from ._base import ema


def gmma(df: pd.DataFrame) -> pd.DataFrame:
    """
    GMMA.rtf: 12 EMAs with fixed periods.
    """
    periods = [3, 5, 8, 10, 12, 15, 30, 35, 40, 45, 50, 60]
    out: dict[str, pd.Series] = {}
    for p in periods:
        out[f"GMMA_ema_{p}"] = ema(df["Close"], p)
    return pd.DataFrame(out, index=df.index)

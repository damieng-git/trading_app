from __future__ import annotations

import pandas as pd

from ._base import sma


def ma_ribbon(
    df: pd.DataFrame,
    lengths: tuple[int, ...] = (20, 50, 100, 200),
) -> pd.DataFrame:
    """
    MA Ribbon: 4 SMAs at the given lengths.

    Default daily lengths are (20, 50, 100, 200).
    For weekly, use (4, 10, 20, 40) to preserve equivalent real-time spans.
    """
    c = df["Close"]
    L = lengths if len(lengths) == 4 else (20, 50, 100, 200)
    return pd.DataFrame(
        {
            "MA_Ribbon_ma1": sma(c, L[0]),
            "MA_Ribbon_ma2": sma(c, L[1]),
            "MA_Ribbon_ma3": sma(c, L[2]),
            "MA_Ribbon_ma4": sma(c, L[3]),
        },
        index=df.index,
    )

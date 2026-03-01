from __future__ import annotations

import numpy as np
import pandas as pd

from ._base import highest, lowest


def turtle_trade_channels(df: pd.DataFrame, length: int = 20, exit_length: int = 10) -> pd.DataFrame:
    """
    TuTCI.rtf translation (core channel lines).
    """
    length = int(length)
    exit_length = int(exit_length)
    upper = highest(df["High"], length)
    lower = lowest(df["Low"], length)
    sup = highest(df["High"], exit_length)
    sdown = lowest(df["Low"], exit_length)

    high_break = (df["High"] >= upper.shift(1)).fillna(False)
    low_break = (df["Low"] <= lower.shift(1)).fillna(False)

    bs_high = pd.Series(np.nan, index=df.index, dtype=float)
    bs_low = pd.Series(np.nan, index=df.index, dtype=float)
    cnt_h = np.nan
    cnt_l = np.nan
    for i in range(len(df)):
        if high_break.iat[i]:
            cnt_h = 0
        elif not np.isnan(cnt_h):
            cnt_h += 1
        if low_break.iat[i]:
            cnt_l = 0
        elif not np.isnan(cnt_l):
            cnt_l += 1
        bs_high.iat[i] = cnt_h
        bs_low.iat[i] = cnt_l

    cond = bs_high <= bs_low
    k1 = lower.where(cond, upper)
    k2 = sdown.where(cond, sup)

    return pd.DataFrame(
        {
            "TuTCI_upper": upper,
            "TuTCI_lower": lower,
            "TuTCI_trend": k1,
            "TuTCI_exit": k2,
        },
        index=df.index,
    )

from __future__ import annotations

import numpy as np
import pandas as pd

from ._base import atr, ema


def ut_bot_alert(
    df: pd.DataFrame,
    a: float = 1.0,
    c: int = 10,
) -> pd.DataFrame:
    """
    UT Bot Alert.rtf translation (Heikin-Ashi option ignored; uses Close).
    """
    c = int(c)
    src = df["Close"].astype(float)
    xatr = atr(df, length=c, smoothing="RMA")
    nloss = float(a) * xatr

    ts = pd.Series(np.nan, index=df.index, dtype=float)
    pos = pd.Series(0.0, index=df.index, dtype=float)

    for i in range(len(df)):
        prev_ts = ts.iat[i - 1] if i > 0 else 0.0
        prev_src = src.iat[i - 1] if i > 0 else np.nan
        curr_src = src.iat[i]
        curr_nloss = nloss.iat[i]

        if i == 0 or np.isnan(prev_ts):
            prev_ts = 0.0

        if (curr_src > prev_ts) and (prev_src > prev_ts):
            ts.iat[i] = max(prev_ts, curr_src - curr_nloss)
        elif (curr_src < prev_ts) and (prev_src < prev_ts):
            ts.iat[i] = min(prev_ts, curr_src + curr_nloss)
        else:
            ts.iat[i] = (curr_src - curr_nloss) if (curr_src > prev_ts) else (curr_src + curr_nloss)

        prev_pos = pos.iat[i - 1] if i > 0 else 0.0
        prev_ts2 = ts.iat[i - 1] if i > 0 else 0.0
        if (prev_src < prev_ts2) and (curr_src > prev_ts2):
            pos.iat[i] = 1.0
        elif (prev_src > prev_ts2) and (curr_src < prev_ts2):
            pos.iat[i] = -1.0
        else:
            pos.iat[i] = prev_pos

    ema1 = ema(src, 1)
    above = (ema1 > ts) & (ema1.shift(1) <= ts.shift(1))
    below = (ts > ema1) & (ts.shift(1) <= ema1.shift(1))
    buy = (src > ts) & above
    sell = (src < ts) & below

    return pd.DataFrame(
        {
            "UT_trailing_stop": ts,
            "UT_pos": pos.astype(int),
            "UT_buy": buy.fillna(False),
            "UT_sell": sell.fillna(False),
        },
        index=df.index,
    )

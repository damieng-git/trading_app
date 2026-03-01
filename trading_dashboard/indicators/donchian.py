from __future__ import annotations

import numpy as np
import pandas as pd

from ._base import highest, lowest


def donchian_trend_ribbon(df: pd.DataFrame, dlen: int = 20, depth: int = 7) -> pd.DataFrame:
    dlen = int(dlen)
    depth = int(depth)
    close = df["Close"]
    out: dict[str, pd.Series] = {}

    def _trend_for_len(L: int) -> pd.Series:
        hh = highest(df["High"], L)
        ll = lowest(df["Low"], L)
        trend = pd.Series(np.nan, index=df.index, dtype=float)
        for i in range(len(df)):
            if i == 0:
                trend.iat[i] = 0.0
                continue
            prev = trend.iat[i - 1]
            if np.isnan(prev):
                prev = 0.0
            if close.iat[i] > hh.shift(1).iat[i]:
                trend.iat[i] = 1.0
            elif close.iat[i] < ll.shift(1).iat[i]:
                trend.iat[i] = -1.0
            else:
                trend.iat[i] = prev
        return trend.astype(int)

    out["Donchian_maintrend"] = _trend_for_len(dlen)
    for k in range(depth):
        L = max(2, dlen - k)
        out[f"Donchian_trend_{L}"] = _trend_for_len(L)

    return pd.DataFrame(out, index=df.index)

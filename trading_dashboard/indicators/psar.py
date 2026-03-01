from __future__ import annotations

import numpy as np
import pandas as pd


def parabolic_sar(df: pd.DataFrame, start: float = 0.02, increment: float = 0.02, maximum: float = 0.2) -> pd.Series:
    """
    Parabolic SAR implementation compatible with Pine's `sar(start, inc, max)`.
    Returns the SAR value series.
    """
    hi = df["High"].astype(float).to_numpy()
    lo = df["Low"].astype(float).to_numpy()
    cl = df["Close"].astype(float).to_numpy()
    n = len(df)
    if n == 0:
        return pd.Series(dtype=float, index=df.index)

    sar = np.full(n, np.nan, dtype=float)
    af = float(start)
    inc = float(increment)
    af_max = float(maximum)

    long = True
    if n >= 2 and cl[1] < cl[0]:
        long = False

    ep = hi[0] if long else lo[0]
    sar[0] = lo[0] if long else hi[0]

    for i in range(1, n):
        prev_sar = sar[i - 1]
        if np.isnan(prev_sar):
            prev_sar = sar[i - 1] = lo[i - 1] if long else hi[i - 1]

        sar_i = prev_sar + af * (ep - prev_sar)

        if long:
            if i >= 2:
                sar_i = min(sar_i, lo[i - 1], lo[i - 2])
            else:
                sar_i = min(sar_i, lo[i - 1])
        else:
            if i >= 2:
                sar_i = max(sar_i, hi[i - 1], hi[i - 2])
            else:
                sar_i = max(sar_i, hi[i - 1])

        if long:
            if lo[i] < sar_i:
                long = False
                sar_i = ep
                ep = lo[i]
                af = float(start)
            else:
                if hi[i] > ep:
                    ep = hi[i]
                    af = min(af + inc, af_max)
        else:
            if hi[i] > sar_i:
                long = True
                sar_i = ep
                ep = hi[i]
                af = float(start)
            else:
                if lo[i] < ep:
                    ep = lo[i]
                    af = min(af + inc, af_max)

        sar[i] = sar_i

    return pd.Series(sar, index=df.index, name="PSAR")

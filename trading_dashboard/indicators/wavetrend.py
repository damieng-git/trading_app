from __future__ import annotations

from typing import Tuple

import pandas as pd

from ._base import ema, hlc3, sma


def wavetrend_lazybear(df: pd.DataFrame, n1: int = 10, n2: int = 21) -> Tuple[pd.Series, pd.Series, pd.Series]:
    ap = hlc3(df)
    esa = ema(ap, n1)
    d = ema((ap - esa).abs(), n1)
    denom = 0.015 * d
    denom = denom.where(denom.abs() > 1e-12, 1e-12)
    ci = (ap - esa) / denom
    tci = ema(ci, n2)
    wt1 = tci
    wt2 = sma(wt1, 4)
    hist = wt1 - wt2
    return wt1, wt2, hist

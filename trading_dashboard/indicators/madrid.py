from __future__ import annotations

import numpy as np
import pandas as pd

from ._base import ema, sma


def madrid_ma_ribbon_state(df: pd.DataFrame, exponential: bool = True) -> pd.DataFrame:
    c = df["Close"]
    ma_fn = ema if exponential else sma
    lens = list(range(5, 101, 5))

    ma05 = ma_fn(c, 5)
    ma100 = ma_fn(c, 100)
    above = (ma05 > ma100).fillna(False)

    out: dict[str, pd.Series] = {"MMARB_ma05": ma05, "MMARB_ma100": ma100}
    for L in lens:
        maL = ma_fn(c, L)
        slope_up = (maL.diff() >= 0).fillna(False)
        state = np.where(
            above & slope_up,
            2,
            np.where(
                above & ~slope_up,
                -2,
                np.where((~above) & slope_up, 1, -1),
            ),
        )
        out[f"MMARB_state_{L:03d}"] = pd.Series(state, index=df.index, dtype=int)

    return pd.DataFrame(out, index=df.index)

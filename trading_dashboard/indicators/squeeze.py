from __future__ import annotations

import numpy as np
import pandas as pd

from ._base import highest, linreg, lowest, sma, stdev, true_range


def squeeze_momentum_lazybear(
    df: pd.DataFrame,
    length: int = 20,
    mult: float = 2.0,
    length_kc: int = 20,
    mult_kc: float = 1.5,
    use_true_range: bool = True,
) -> pd.DataFrame:
    """
    SQZMOM_LB.rtf translation.
    """
    length = int(length)
    length_kc = int(length_kc)

    src = df["Close"]
    basis = sma(src, length)
    dev = float(mult_kc) * stdev(src, length)
    upper_bb = basis + dev
    lower_bb = basis - dev

    ma = sma(src, length_kc)
    rng = true_range(df) if use_true_range else (df["High"] - df["Low"])
    rangema = sma(rng, length_kc)
    upper_kc = ma + rangema * float(mult_kc)
    lower_kc = ma - rangema * float(mult_kc)

    sqz_on = (lower_bb > lower_kc) & (upper_bb < upper_kc)
    sqz_off = (lower_bb < lower_kc) & (upper_bb > upper_kc)
    no_sqz = (~sqz_on.fillna(False)) & (~sqz_off.fillna(False))

    hh = highest(df["High"], length_kc)
    ll = lowest(df["Low"], length_kc)
    m1 = (hh + ll) / 2.0
    m2 = sma(df["Close"], length_kc)
    osc_src = src - ((m1 + m2) / 2.0)
    val = linreg(osc_src, length_kc)

    prev = val.shift(1).fillna(0.0)
    val_gt_0 = (val > 0).fillna(False)
    val_gt_prev = (val > prev).fillna(False)
    val_lt_prev = (val < prev).fillna(False)

    lime = "#00e676"
    green = "#22c55e"
    red = "#ef4444"
    maroon = "#7f1d1d"

    bcolor = np.where(
        val_gt_0 & val_gt_prev,
        lime,
        np.where(val_gt_0, green, np.where(val_lt_prev, red, maroon)),
    )

    blue = "#2962FF"
    black = "#0b0f19"
    gray = "#9ca3af"
    scolor = np.where(no_sqz.to_numpy(), blue, np.where(sqz_on.fillna(False).to_numpy(), black, gray))

    return pd.DataFrame(
        {
            "SQZ_val": val,
            "SQZ_on": sqz_on.fillna(False),
            "SQZ_off": sqz_off.fillna(False),
            "SQZ_no": no_sqz.fillna(False),
            "SQZ_bcolor": pd.Series(bcolor, index=df.index, dtype=object),
            "SQZ_scolor": pd.Series(scolor, index=df.index, dtype=object),
        },
        index=df.index,
    )

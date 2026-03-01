from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd

from ._base import rma, sma, true_range


def supertrend(
    df: pd.DataFrame,
    periods: int = 10,
    multiplier: float = 3.0,
    change_atr_method: bool = True,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    SuperTrend.rtf translation (Pine v4 script).
    Returns:
      st_line, trend (1/-1), atr_used
    """
    periods = int(periods)
    src = (df["High"] + df["Low"]) / 2.0  # hl2

    tr = true_range(df)
    atr_sma = sma(tr, periods)
    atr_wilder = rma(tr, periods)
    atr_used = atr_wilder if change_atr_method else atr_sma

    # Base bands
    base_up = src - (float(multiplier) * atr_used)
    base_dn = src + (float(multiplier) * atr_used)

    close = df["Close"].astype(float)
    n = len(df)
    up = np.full(n, np.nan, dtype=float)
    dn = np.full(n, np.nan, dtype=float)
    trend = np.full(n, 1, dtype=int)

    # Pine uses nz(up[1], up) and nz(dn[1], dn) which, combined with the := assignments,
    # makes the up/dn bands *recursive* (they trail). This must be computed sequentially.
    for i in range(n):
        bu = float(base_up.iat[i]) if pd.notna(base_up.iat[i]) else np.nan
        bd = float(base_dn.iat[i]) if pd.notna(base_dn.iat[i]) else np.nan

        if i == 0:
            up[i] = bu
            dn[i] = bd
            trend[i] = 1
            continue

        # Previous adjusted bands (Pine: up1/dn1)
        up1 = up[i - 1] if not np.isnan(up[i - 1]) else bu
        dn1 = dn[i - 1] if not np.isnan(dn[i - 1]) else bd

        prev_close = float(close.iat[i - 1]) if pd.notna(close.iat[i - 1]) else np.nan
        curr_close = float(close.iat[i]) if pd.notna(close.iat[i]) else np.nan

        # up := close[1] > up1 ? max(up, up1) : up
        if not np.isnan(prev_close) and not np.isnan(up1) and prev_close > up1:
            up[i] = np.nanmax([bu, up1])
        else:
            up[i] = bu

        # dn := close[1] < dn1 ? min(dn, dn1) : dn
        if not np.isnan(prev_close) and not np.isnan(dn1) and prev_close < dn1:
            dn[i] = np.nanmin([bd, dn1])
        else:
            dn[i] = bd

        # trend flip logic (uses dn1/up1 from previous adjusted bands)
        prev_trend = trend[i - 1]
        if prev_trend == -1 and (not np.isnan(curr_close)) and (not np.isnan(dn1)) and (curr_close > dn1):
            trend[i] = 1
        elif prev_trend == 1 and (not np.isnan(curr_close)) and (not np.isnan(up1)) and (curr_close < up1):
            trend[i] = -1
        else:
            trend[i] = prev_trend

    up_s = pd.Series(up, index=df.index, name="SuperTrend_up")
    dn_s = pd.Series(dn, index=df.index, name="SuperTrend_dn")
    trend_s = pd.Series(trend, index=df.index, name="SuperTrend_trend")
    st_line = up_s.where(trend_s == 1, dn_s)
    return st_line, trend_s, atr_used

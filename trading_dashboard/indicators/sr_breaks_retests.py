"""
SR Breaks and Retests [ChartPrime] — Python translation.

Detects support/resistance levels at high-volume pivot points, tracks their
breakouts and retests, and signals when S becomes R (or R becomes S).

Pine source: docs/pinescripts/SR Breaks and Retests/
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import atr as calc_atr, highest, lowest


def _delta_volume(df: pd.DataFrame) -> pd.Series:
    """Signed delta volume: +volume on green candles, -volume on red.
    Matches Pine's upAndDownVolume() which uses a sticky `isBuyVolume` var."""
    close = df["Close"].astype(float).values
    open_ = df["Open"].astype(float).values
    vol = df["Volume"].astype(float).values
    n = len(df)

    green = close > open_
    red = close < open_
    direction = np.where(green, 1, np.where(red, -1, 0))
    # Forward-fill zeros (doji bars keep previous direction) — sticky state
    nz = np.nonzero(direction)[0]
    if len(nz) == 0:
        return pd.Series(vol, index=df.index, dtype=float)
    for i in range(n):
        if direction[i] == 0:
            direction[i] = direction[i - 1] if i > 0 else 1
    sign = np.where(direction >= 0, 1.0, -1.0)
    return pd.Series(vol * sign, index=df.index, dtype=float)


def sr_breaks_retests(
    df: pd.DataFrame,
    lookback: int = 20,
    vol_len: int = 2,
    box_width: float = 1.0,
    atr_len: int = 200,
) -> pd.DataFrame:
    """
    Compute SR Breaks and Retests indicator.

    Returns DataFrame with columns:
        SR_support       - current support level (pivot low with high +volume)
        SR_resistance    - current resistance level (pivot high with high -volume)
        SR_support_lo    - lower edge of support zone (support - ATR*width)
        SR_resistance_hi - upper edge of resistance zone (resistance + ATR*width)
        SR_break_res     - bool: resistance breakout (bullish)
        SR_break_sup     - bool: support breakdown (bearish)
        SR_res_holds     - bool: resistance holds (bearish)
        SR_sup_holds     - bool: support holds (bullish)
        SR_res_is_sup    - bool: resistance has flipped to act as support
        SR_sup_is_res    - bool: support has flipped to act as resistance
        SR_state         - int: 1=bullish, -1=bearish, 0=neutral
    """
    n = len(df)
    _COLS = [
        "SR_support", "SR_resistance", "SR_support_lo", "SR_resistance_hi",
        "SR_break_res", "SR_break_sup", "SR_res_holds", "SR_sup_holds",
        "SR_res_is_sup", "SR_sup_is_res", "SR_state",
    ]
    has_volume = (
        "Volume" in df.columns
        and df["Volume"].notna().any()
        and (df["Volume"].astype(float) > 0).any()
    )
    if n < lookback * 2 + 1 or not has_volume:
        empty = pd.DataFrame(index=df.index)
        for col in _COLS:
            empty[col] = np.nan
        return empty

    close = df["Close"].astype(float).values
    high = df["High"].astype(float).values
    low = df["Low"].astype(float).values

    dv = _delta_volume(df)
    dv_vals = dv.values
    dv_scaled = dv / 2.5
    vol_hi = highest(dv_scaled, vol_len).values
    vol_lo = lowest(dv_scaled, vol_len).values

    atr_len_safe = min(atr_len, n - 1) if n > 1 else 1
    atr_val = calc_atr(df, length=atr_len_safe, smoothing="RMA")
    withd = (atr_val * box_width).values

    # Pine uses ta.pivothigh(src=close, lb, rb) and ta.pivotlow(src=close, lb, rb).
    # Pivot at position i is confirmed at bar i+lookback.
    is_pivot_high = np.full(n, False)
    is_pivot_low = np.full(n, False)
    close_s = pd.Series(close)
    for j in range(1, lookback + 1):
        left = close_s.shift(j).to_numpy()
        right = close_s.shift(-j).to_numpy()
        if j == 1:
            ph_mask = (close >= left) & (close >= right)
            pl_mask = (close <= left) & (close <= right)
        else:
            ph_mask &= (close >= left) & (close >= right)
            pl_mask &= (close <= left) & (close <= right)
    valid = np.zeros(n, dtype=bool)
    valid[lookback:n - lookback] = True
    is_pivot_high = ph_mask & valid
    is_pivot_low = pl_mask & valid

    # Sequential state machine (matches Pine's bar-by-bar processing)
    sup_level = np.full(n, np.nan)
    sup_lo = np.full(n, np.nan)
    res_level = np.full(n, np.nan)
    res_hi = np.full(n, np.nan)
    break_res = np.full(n, False, dtype=bool)
    break_sup = np.full(n, False, dtype=bool)
    res_holds = np.full(n, False, dtype=bool)
    sup_holds = np.full(n, False, dtype=bool)
    res_is_sup = np.full(n, False, dtype=bool)
    sup_is_res = np.full(n, False, dtype=bool)

    cur_sup = np.nan
    cur_sup_lo = np.nan
    cur_res = np.nan
    cur_res_hi = np.nan
    _res_is_sup = False
    _sup_is_res = False

    for i in range(n):
        prev_sup = cur_sup
        prev_sup_lo = cur_sup_lo
        prev_res = cur_res
        prev_res_hi = cur_res_hi

        pivot_bar = i - lookback
        if 0 <= pivot_bar < n:
            w = withd[i]  # may be NaN; keep as NaN to match Pine behaviour

            if is_pivot_low[pivot_bar]:
                dv_i = dv_vals[i]
                vh_i = vol_hi[i] if not np.isnan(vol_hi[i]) else 0.0
                if dv_i > vh_i:
                    cur_sup = close[pivot_bar]
                    cur_sup_lo = cur_sup - w if not np.isnan(w) else np.nan

            if is_pivot_high[pivot_bar]:
                dv_i = dv_vals[i]
                vl_i = vol_lo[i] if not np.isnan(vol_lo[i]) else 0.0
                if dv_i < vl_i:
                    cur_res = close[pivot_bar]
                    cur_res_hi = cur_res + w if not np.isnan(w) else np.nan

        sup_level[i] = cur_sup
        sup_lo[i] = cur_sup_lo
        res_level[i] = cur_res
        res_hi[i] = cur_res_hi

        # Crossover/crossunder checks — NaN level means no signal (matches Pine)
        if i > 0:
            if (not np.isnan(cur_res_hi)) and (not np.isnan(prev_res_hi)):
                if low[i] > cur_res_hi and low[i - 1] <= prev_res_hi:
                    break_res[i] = True

            if (not np.isnan(cur_res)) and (not np.isnan(prev_res)):
                if high[i] < cur_res and high[i - 1] >= prev_res:
                    res_holds[i] = True

            if (not np.isnan(cur_sup)) and (not np.isnan(prev_sup)):
                if low[i] > cur_sup and low[i - 1] <= prev_sup:
                    sup_holds[i] = True

            if (not np.isnan(cur_sup_lo)) and (not np.isnan(prev_sup_lo)):
                if high[i] < cur_sup_lo and high[i - 1] >= prev_sup_lo:
                    break_sup[i] = True

        if break_res[i]:
            _res_is_sup = True
        elif res_holds[i]:
            _res_is_sup = False
        if break_sup[i]:
            _sup_is_res = True
        elif sup_holds[i]:
            _sup_is_res = False

        res_is_sup[i] = _res_is_sup
        sup_is_res[i] = _sup_is_res

    state = np.zeros(n, dtype=int)
    cur_state = 0
    for i in range(n):
        if break_res[i] or sup_holds[i]:
            cur_state = 1
        elif break_sup[i] or res_holds[i]:
            cur_state = -1
        state[i] = cur_state

    return pd.DataFrame(
        {
            "SR_support": pd.Series(sup_level, index=df.index),
            "SR_resistance": pd.Series(res_level, index=df.index),
            "SR_support_lo": pd.Series(sup_lo, index=df.index),
            "SR_resistance_hi": pd.Series(res_hi, index=df.index),
            "SR_break_res": pd.Series(break_res, index=df.index),
            "SR_break_sup": pd.Series(break_sup, index=df.index),
            "SR_res_holds": pd.Series(res_holds, index=df.index),
            "SR_sup_holds": pd.Series(sup_holds, index=df.index),
            "SR_res_is_sup": pd.Series(res_is_sup, index=df.index),
            "SR_sup_is_res": pd.Series(sup_is_res, index=df.index),
            "SR_state": pd.Series(state, index=df.index),
        },
        index=df.index,
    )

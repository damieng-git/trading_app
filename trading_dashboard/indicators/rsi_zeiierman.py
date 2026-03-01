from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd

from ._base import rma, rsi_wilder, true_range, wma


def _kf(series: pd.Series, alpha: float) -> pd.Series:
    """Kalman-style filter: equivalent to EMA with the given alpha."""
    return series.ewm(alpha=float(alpha), adjust=False).mean()


def rsi_simple_sma(src: pd.Series, length: int) -> pd.Series:
    length = int(length)
    delta = src.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.rolling(window=length, min_periods=length).mean()
    avg_loss = loss.rolling(window=length, min_periods=length).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi = rsi.where(~avg_loss.eq(0.0), 100.0)
    return rsi


def core_dmi(df: pd.DataFrame, length: int, adx_len: int) -> Tuple[pd.Series, pd.Series, pd.Series]:
    length = int(length)
    adx_len = int(adx_len)
    tr = true_range(df)
    up_move = df["High"] - df["High"].shift(1)
    down_move = df["Low"].shift(1) - df["Low"]
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    sm_tr = rma(tr, length)
    sm_plus = rma(plus_dm, length)
    sm_minus = rma(minus_dm, length)

    plus_di = np.where(sm_tr == 0, 0.0, 100.0 * sm_plus / sm_tr.replace(0.0, np.nan))
    minus_di = np.where(sm_tr == 0, 0.0, 100.0 * sm_minus / sm_tr.replace(0.0, np.nan))
    plus_di = pd.Series(plus_di, index=df.index, dtype=float)
    minus_di = pd.Series(minus_di, index=df.index, dtype=float)

    di_sum = plus_di + minus_di
    dx = np.where(di_sum == 0, 0.0, 100.0 * (plus_di - minus_di).abs() / di_sum.replace(0.0, np.nan))
    dx = pd.Series(dx, index=df.index, dtype=float).fillna(0.0)
    adx = rma(dx, adx_len)
    return plus_di, minus_di, adx


def dmi_simple_sum(df: pd.DataFrame, length: int) -> Tuple[pd.Series, pd.Series, pd.Series]:
    length = int(length)
    hi = df["High"].astype(float)
    lo = df["Low"].astype(float)
    cl = df["Close"].astype(float)

    prev_close = cl.shift(1)
    tr = pd.concat([(hi - lo).abs(), (hi - prev_close).abs(), (lo - prev_close).abs()], axis=1).max(axis=1)

    up_move = hi - hi.shift(1)
    down_move = lo.shift(1) - lo
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    sum_tr = tr.rolling(window=length, min_periods=length).sum()
    sum_plus = plus_dm.rolling(window=length, min_periods=length).sum()
    sum_minus = minus_dm.rolling(window=length, min_periods=length).sum()

    plus_di = 100.0 * (sum_plus / sum_tr.replace(0.0, np.nan))
    minus_di = 100.0 * (sum_minus / sum_tr.replace(0.0, np.nan))
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    adx = dx
    return plus_di, minus_di, adx


def rsi_strength_consolidation_zeiierman(
    df: pd.DataFrame,
    rsi_length: int = 14,
    dmi_length: int = 14,
    adx_smoothing: int = 14,
    filter_strength: float = 0.1,
) -> pd.DataFrame:
    rsi_length = int(rsi_length)
    dmi_length = int(dmi_length)
    adx_smoothing = int(adx_smoothing)
    filter_strength = float(filter_strength)

    close = df["Close"].astype(float)

    rsi_manual = _kf(rsi_simple_sma(close, rsi_length), filter_strength)
    rsi_inbuilt = _kf(rsi_wilder(close, rsi_length), filter_strength)
    rsi = (rsi_manual + rsi_inbuilt) / 2.0

    p_d, m_d, a_d = dmi_simple_sum(df, dmi_length)
    p_c, m_c, a_c = core_dmi(df, dmi_length, adx_smoothing)

    a = _kf((a_d + a_c) / 2.0, filter_strength)
    p = _kf((p_d + p_c) / 2.0, filter_strength)
    m = _kf((m_d + m_c) / 2.0, filter_strength)

    strength = np.where(a > 20.0, (a - 20.0) / 3.0, 0.0)
    strength = pd.Series(strength, index=df.index, dtype=float).fillna(0.0)
    rsi_strength = rsi - strength
    rsi_strength = rsi_strength.where(~(rsi > 50.0), rsi + strength)

    i_rsi_wma5 = wma(rsi_inbuilt, 5)
    c_rsi_wma5 = wma(rsi_manual, 5)

    condition_met = a_c.fillna(np.inf) <= 20.0
    in_zone = False
    zone_high = np.nan
    zone_low = np.nan
    last_high = np.nan
    last_low = np.nan
    last_end_i = None
    fired = False

    zone_high_s = np.full(len(df), np.nan, dtype=float)
    zone_low_s = np.full(len(df), np.nan, dtype=float)
    breakout_up = np.full(len(df), False, dtype=bool)
    breakout_dn = np.full(len(df), False, dtype=bool)
    breakout_level = np.full(len(df), np.nan, dtype=float)

    hi = df["High"].to_numpy(dtype=float)
    lo = df["Low"].to_numpy(dtype=float)
    cl = df["Close"].to_numpy(dtype=float)

    for i in range(len(df)):
        if bool(condition_met.iat[i]):
            if not in_zone:
                in_zone = True
                zone_high = hi[i]
                zone_low = lo[i]
                fired = False
                last_high = np.nan
                last_low = np.nan
                last_end_i = None
            else:
                zone_high = max(zone_high, hi[i])
                zone_low = min(zone_low, lo[i])
            zone_high_s[i] = zone_high
            zone_low_s[i] = zone_low
        else:
            if in_zone:
                in_zone = False
                last_high = zone_high
                last_low = zone_low
                last_end_i = i - 1 if i > 0 else 0
                zone_high = np.nan
                zone_low = np.nan

            if (last_end_i is not None) and (not fired) and (i > last_end_i):
                prev_close = cl[i - 1] if i > 0 else np.nan
                if not np.isnan(last_high) and (prev_close <= last_high) and (cl[i] > last_high):
                    fired = True
                    breakout_up[i] = True
                    breakout_level[i] = last_high
                if not fired and (not np.isnan(last_low)) and (prev_close >= last_low) and (cl[i] < last_low):
                    fired = True
                    breakout_dn[i] = True
                    breakout_level[i] = last_low

    bullish = (p > m).fillna(False)

    return pd.DataFrame(
        {
            "Zei_rsi": rsi,
            "Zei_rsi_strength": rsi_strength,
            "Zei_adx_core": a_c,
            "Zei_adx": a,
            "Zei_di_plus": p,
            "Zei_di_minus": m,
            "Zei_bullish": bullish,
            "Zei_rsi_inbuilt_wma5": i_rsi_wma5,
            "Zei_rsi_manual_wma5": c_rsi_wma5,
            "Zei_zone_high": pd.Series(zone_high_s, index=df.index),
            "Zei_zone_low": pd.Series(zone_low_s, index=df.index),
            "Zei_breakout_up": pd.Series(breakout_up, index=df.index),
            "Zei_breakout_down": pd.Series(breakout_dn, index=df.index),
            "Zei_breakout_level": pd.Series(breakout_level, index=df.index),
        },
        index=df.index,
    )

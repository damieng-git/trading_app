"""
WaveTrend MTF Signal [PlungerMen] — filtered extreme-zone composite.

Combines WaveTrend (extreme zone detection + cross), MTF MACD (momentum
confirmation from a faster timeframe), and centered RSI into a single
signal generator.

Based on the TradingView PineScript:
  "MACD_CM_MTF_RSI_WaveTrend [PlungerMen] - filtered extremes"

When mtf_close is provided (e.g. 4H close prices while computing on 1M),
the MACD is evaluated on that faster timeframe and aligned back.
When mtf_close is None, MACD uses the same timeframe as WT (fallback).
"""
from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def _rsi(series: pd.Series, length: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1.0 / length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / length, min_periods=length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _align_mtf_to_slow(
    mtf_series: pd.Series,
    slow_index: pd.DatetimeIndex,
) -> pd.Series:
    """For each slow-TF bar, take the last available fast-TF value at or before that date."""
    mtf = mtf_series.sort_index().dropna()
    if mtf.empty:
        return pd.Series(np.nan, index=slow_index, dtype=float)
    # Normalize both indices to datetime64[ns] to avoid merge dtype mismatch
    slow_idx = pd.DatetimeIndex(slow_index).astype("datetime64[ns]")
    mtf_idx = pd.DatetimeIndex(mtf.index).astype("datetime64[ns]")
    aligned = pd.merge_asof(
        pd.DataFrame({"_key": 1}, index=slow_idx),
        pd.DataFrame({"val": mtf.values}, index=mtf_idx),
        left_index=True,
        right_index=True,
        direction="backward",
    )
    return pd.Series(aligned["val"].values, index=slow_index, dtype=float)


def wt_mtf_signal(
    df: pd.DataFrame,
    mtf_close: pd.Series | None = None,
    *,
    wt_channel_len: int = 27,
    wt_average_len: int = 21,
    macd_fast: int = 15,
    macd_slow: int = 26,
    macd_signal_len: int = 12,
    rsi_len: int = 16,
    ob_level1: float = 60.0,
    os_level1: float = -60.0,
    min_bars_in_extreme: int = 2,
    confirm_window: int = 1,
    min_spread: float = 1.5,
    cooldown_bars: int = 8,
    use_rsi_filter: bool = True,
    use_macd_filter: bool = True,
) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """
    Compute WaveTrend MTF Signal.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV DataFrame for the main (slow) timeframe.
    mtf_close : pd.Series, optional
        Close prices from a faster timeframe for MTF MACD.
        If None, uses same-TF close for MACD.

    Returns
    -------
    wt1 : pd.Series — WaveTrend fast line
    wt2 : pd.Series — WaveTrend slow line
    signal : pd.Series — +1 (BUY), -1 (SELL), 0 (neutral)
    rsi_centered : pd.Series — RSI - 50
    """
    close = df["Close"].astype(float)
    high = df["High"].astype(float) if "High" in df.columns else close
    low = df["Low"].astype(float) if "Low" in df.columns else close
    idx = df.index
    n = len(df)

    # ── WaveTrend ──
    hlc3 = (high + low + close) / 3
    esa = _ema(hlc3, wt_channel_len)
    dev = _ema((hlc3 - esa).abs(), wt_channel_len)
    ci = np.where(dev != 0, (hlc3 - esa) / (0.015 * dev), 0.0)
    ci = pd.Series(ci, index=idx, dtype=float)
    wt1 = _ema(ci, wt_average_len)
    wt2 = _sma(wt1, 4)

    # ── RSI (centered) ──
    rsi_centered = _rsi(close, rsi_len) - 50.0

    # ── MACD (from MTF if available, otherwise same-TF) ──
    if mtf_close is not None and len(mtf_close) > macd_slow:
        mtf_close = mtf_close.astype(float)
        mtf_macd = _ema(mtf_close, macd_fast) - _ema(mtf_close, macd_slow)
        mtf_signal_line = _ema(mtf_macd, macd_signal_len)
        mtf_bull_raw = (mtf_macd >= mtf_signal_line).astype(float)
        macd_bull = _align_mtf_to_slow(mtf_bull_raw, idx).fillna(0).astype(bool)
    else:
        same_macd = _ema(close, macd_fast) - _ema(close, macd_slow)
        same_signal = _ema(same_macd, macd_signal_len)
        macd_bull = same_macd >= same_signal

    # ── Convert to numpy for signal loop ──
    wt1_arr = wt1.to_numpy(float, na_value=np.nan)
    wt2_arr = wt2.to_numpy(float, na_value=np.nan)
    rsi_arr = rsi_centered.to_numpy(float, na_value=np.nan)
    macd_bull_arr = np.asarray(macd_bull, dtype=bool)
    signal_arr = np.zeros(n, dtype=np.int8)

    deep_os_bars = 0
    deep_ob_bars = 0
    last_bull_bar = -cooldown_bars - 1
    last_bear_bar = -cooldown_bars - 1

    for i in range(1, n):
        w1, w2 = wt1_arr[i], wt2_arr[i]
        w1_prev, w2_prev = wt1_arr[i - 1], wt2_arr[i - 1]
        if np.isnan(w1) or np.isnan(w2) or np.isnan(w1_prev) or np.isnan(w2_prev):
            deep_os_bars = 0
            deep_ob_bars = 0
            continue

        in_deep_os = w1 < os_level1 and w2 < os_level1
        in_deep_ob = w1 > ob_level1 and w2 > ob_level1
        deep_os_bars = deep_os_bars + 1 if in_deep_os else 0
        deep_ob_bars = deep_ob_bars + 1 if in_deep_ob else 0

        bull_cross = w1 > w2 and w1_prev <= w2_prev
        bear_cross = w1 < w2 and w1_prev >= w2_prev

        # Check cross within confirm window
        bull_cross_recent = bull_cross
        bear_cross_recent = bear_cross
        if confirm_window > 0:
            for lookback in range(1, confirm_window + 1):
                j = i - lookback
                if j < 1:
                    break
                wj1, wj2 = wt1_arr[j], wt2_arr[j]
                wj1p, wj2p = wt1_arr[j - 1], wt2_arr[j - 1]
                if not np.isnan(wj1) and not np.isnan(wj2) and not np.isnan(wj1p) and not np.isnan(wj2p):
                    if wj1 > wj2 and wj1p <= wj2p:
                        bull_cross_recent = True
                    if wj1 < wj2 and wj1p >= wj2p:
                        bear_cross_recent = True

        # Slope
        bull_slope = w1 > w1_prev and w2 >= w2_prev
        bear_slope = w1 < w1_prev and w2 <= w2_prev

        # Spread
        bull_spread = (w1 - w2) >= min_spread
        bear_spread = (w2 - w1) >= min_spread

        # RSI filter
        rsi_val = rsi_arr[i]
        rsi_prev = rsi_arr[i - 1] if i > 0 else np.nan
        rsi_bull_ok = True
        rsi_bear_ok = True
        if use_rsi_filter and not np.isnan(rsi_val) and not np.isnan(rsi_prev):
            rsi_bull_ok = rsi_val < 0 and rsi_val > rsi_prev
            rsi_bear_ok = rsi_val > 0 and rsi_val < rsi_prev

        # MACD filter
        macd_bull_ok = not use_macd_filter or macd_bull_arr[i]
        macd_bear_ok = not use_macd_filter or not macd_bull_arr[i]

        # Composite signals
        raw_bull = (bull_cross_recent and in_deep_os and deep_os_bars >= min_bars_in_extreme
                    and bull_slope and bull_spread and rsi_bull_ok and macd_bull_ok)
        raw_bear = (bear_cross_recent and in_deep_ob and deep_ob_bars >= min_bars_in_extreme
                    and bear_slope and bear_spread and rsi_bear_ok and macd_bear_ok)

        # Onset (not true on previous bar — simplified: first bar of raw signal)
        wt1_arr[i - 1] < os_level1 and wt2_arr[i - 1] < os_level1
        wt1_arr[i - 1] > ob_level1 and wt2_arr[i - 1] > ob_level1

        # Cooldown
        bull_cooldown_ok = (i - last_bull_bar) > cooldown_bars
        bear_cooldown_ok = (i - last_bear_bar) > cooldown_bars

        if raw_bull and bull_cooldown_ok:
            signal_arr[i] = 1
            last_bull_bar = i
        elif raw_bear and bear_cooldown_ok:
            signal_arr[i] = -1
            last_bear_bar = i

    signal = pd.Series(signal_arr, index=idx, dtype=np.int8)
    return wt1, wt2, signal, rsi_centered

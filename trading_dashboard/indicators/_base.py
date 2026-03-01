"""
Indicator implementations (pandas/numpy).

This package is the shared indicator library used by:
- apps/dashboard (runtime dashboards)
- research (optimisation/harness)
- trading_dashboard/screener (market scanning)
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd


def sma(series: pd.Series, length: int) -> pd.Series:
    length = int(length)
    return series.rolling(window=length, min_periods=length).mean()


def ema(series: pd.Series, length: int) -> pd.Series:
    """
    TradingView-like EMA:
    - Uses adjust=False which matches the recursive EMA form.
    """
    length = int(length)
    return series.ewm(span=length, adjust=False, min_periods=length).mean()


def wma(series: pd.Series, length: int) -> pd.Series:
    """
    Weighted moving average (linear weights 1..length), similar to Pine's ta.wma.
    """
    length = int(length)
    w = np.arange(1, length + 1, dtype=float)

    def _apply(x: np.ndarray) -> float:
        return float(np.dot(x, w) / w.sum())

    return series.rolling(window=length, min_periods=length).apply(lambda x: _apply(x.to_numpy()), raw=False)


def vwma(price: pd.Series, volume: pd.Series, length: int) -> pd.Series:
    """
    Volume-weighted moving average, similar to Pine's ta.vwma.
    """
    length = int(length)
    pv = price * volume
    num = pv.rolling(window=length, min_periods=length).sum()
    den = volume.rolling(window=length, min_periods=length).sum()
    return num / den.replace(0.0, np.nan)


def stdev(series: pd.Series, length: int) -> pd.Series:
    length = int(length)
    return series.rolling(window=length, min_periods=length).std(ddof=0)


def highest(series: pd.Series, length: int) -> pd.Series:
    length = int(length)
    return series.rolling(window=length, min_periods=length).max()


def lowest(series: pd.Series, length: int) -> pd.Series:
    length = int(length)
    return series.rolling(window=length, min_periods=length).min()


def true_range(df: pd.DataFrame) -> pd.Series:
    """
    True Range, matching Pine's tr(true).
    """
    prev_close = df["Close"].shift(1)
    hl = (df["High"] - df["Low"]).abs()
    hc = (df["High"] - prev_close).abs()
    lc = (df["Low"] - prev_close).abs()
    return pd.concat([hl, hc, lc], axis=1).max(axis=1)


AtrSmoothing = Literal["RMA", "SMA", "EMA", "WMA"]


def atr(df: pd.DataFrame, length: int = 14, smoothing: AtrSmoothing = "RMA") -> pd.Series:
    """
    ATR with selectable smoothing (RMA/SMA/EMA/WMA) to mirror the ATR.rtf script.
    """
    tr = true_range(df)
    length = int(length)
    if smoothing == "RMA":
        return rma(tr, length)
    if smoothing == "SMA":
        return sma(tr, length)
    if smoothing == "EMA":
        return ema(tr, length)
    if smoothing == "WMA":
        return wma(tr, length)
    raise ValueError(f"Unsupported ATR smoothing: {smoothing}")


def dema(series: pd.Series, length: int) -> pd.Series:
    length = int(length)
    e1 = ema(series, length)
    e2 = ema(e1, length)
    return 2.0 * e1 - e2


def rma(series: pd.Series, length: int) -> pd.Series:
    """
    Wilder's RMA (a.k.a. smoothed moving average).
    Equivalent to EMA with alpha = 1/length (TradingView's ta.rma).
    """
    length = int(length)
    alpha = 1.0 / float(length)
    return series.ewm(alpha=alpha, adjust=False, min_periods=length).mean()


def rsi_wilder(close: pd.Series, length: int = 14) -> pd.Series:
    length = int(length)
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = rma(gain, length)
    avg_loss = rma(loss, length)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def hlc3(df: pd.DataFrame) -> pd.Series:
    return (df["High"] + df["Low"] + df["Close"]) / 3.0


def linreg(series: pd.Series, length: int) -> pd.Series:
    """
    Pine-like linreg(series, length, 0): value of regression line at the last bar in the window.
    """
    length = int(length)
    x = np.arange(length, dtype=float)
    x_mean = x.mean()
    denom = ((x - x_mean) ** 2).sum()

    def _apply(y: np.ndarray) -> float:
        y = y.astype(float)
        y_mean = y.mean()
        num = ((x - x_mean) * (y - y_mean)).sum()
        slope = num / denom if denom != 0 else 0.0
        intercept = y_mean - slope * x_mean
        return float(intercept + slope * x[-1])

    return series.rolling(window=length, min_periods=length).apply(lambda w: _apply(w.to_numpy()), raw=False)


def gaussian_weights(length: int, bandwidth: float) -> np.ndarray:
    x = np.arange(length, dtype=float)
    h = float(bandwidth)
    if h <= 0:
        w = np.zeros(length, dtype=float)
        w[0] = 1.0
        return w
    w = np.exp(-(x**2) / (h * h * 2.0))
    return w

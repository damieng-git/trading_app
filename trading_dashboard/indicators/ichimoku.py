"""
Ichimoku Kinkō Hyō — standard implementation.

Computes the five Ichimoku lines + Kumo state from OHLCV data.
Designed for weekly timeframe (per user's "Tendance (filtre)" dimension)
but runs on any timeframe.

Pine source reference: docs/pinescripts/Ishimoku Theories/
"""

from __future__ import annotations

import pandas as pd

from . import highest, lowest


def ichimoku(
    df: pd.DataFrame,
    tenkan_len: int = 9,
    kijun_len: int = 26,
    senkou_b_len: int = 52,
    offset: int = 26,
) -> pd.DataFrame:
    """
    Standard Ichimoku Kinkō Hyō.

    Returns a DataFrame with columns:
        Ichi_tenkan      - Tenkan-sen (conversion line)
        Ichi_kijun       - Kijun-sen (base line)
        Ichi_senkou_a    - Senkou Span A (leading span A), shifted +offset
        Ichi_senkou_b    - Senkou Span B (leading span B), shifted +offset
        Ichi_chikou      - Chikou Span (lagging span = Close shifted -offset)
        Ichi_kumo_bull   - True when Senkou A >= Senkou B (bullish cloud)
        Ichi_above_kumo  - True when Close is above both Senkou spans (current, not shifted)
        Ichi_below_kumo  - True when Close is below both Senkou spans
        Ichi_tk_cross    - True on bars where Tenkan crosses above Kijun
        Ichi_signal      - Composite: 1 (bullish), -1 (bearish), 0 (neutral/inside cloud)
    """
    hi = df["High"]
    lo = df["Low"]
    cl = df["Close"]

    def _midpoint(length: int) -> pd.Series:
        return (highest(hi, length) + lowest(lo, length)) / 2.0

    tenkan = _midpoint(tenkan_len)
    kijun = _midpoint(kijun_len)

    senkou_a_raw = (tenkan + kijun) / 2.0
    senkou_b_raw = _midpoint(senkou_b_len)

    senkou_a = senkou_a_raw.shift(offset)
    senkou_b = senkou_b_raw.shift(offset)
    chikou = cl.shift(-offset)

    kumo_bull = (senkou_a >= senkou_b).fillna(False)

    kumo_top = pd.concat([senkou_a, senkou_b], axis=1).max(axis=1)
    kumo_bot = pd.concat([senkou_a, senkou_b], axis=1).min(axis=1)
    above_kumo = (cl > kumo_top).fillna(False)
    below_kumo = (cl < kumo_bot).fillna(False)

    tk_cross_up = (tenkan > kijun) & (tenkan.shift(1) <= kijun.shift(1))

    signal = pd.Series(0, index=df.index, dtype=int)
    bull_mask = above_kumo & (tenkan > kijun)
    bear_mask = below_kumo & (tenkan < kijun)
    signal = signal.where(~bull_mask, 1)
    signal = signal.where(~bear_mask, -1)

    return pd.DataFrame({
        "Ichi_tenkan": tenkan,
        "Ichi_kijun": kijun,
        "Ichi_senkou_a": senkou_a,
        "Ichi_senkou_b": senkou_b,
        "Ichi_chikou": chikou,
        "Ichi_kumo_bull": kumo_bull,
        "Ichi_above_kumo": above_kumo,
        "Ichi_below_kumo": below_kumo,
        "Ichi_tk_cross": tk_cross_up.fillna(False),
        "Ichi_signal": signal,
    }, index=df.index)

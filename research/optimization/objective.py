"""
Objective functions for indicator parameter optimization.

Each objective takes a KPI state series and a price DataFrame, and
returns a score (higher = better).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def hit_rate(states: pd.Series, close: pd.Series, horizon: int = 5) -> float:
    """
    Fraction of bullish signals followed by a positive return over *horizon* bars.

    Score range: 0.0 – 1.0 (higher = better signal accuracy).
    """
    fwd_ret = close.pct_change(horizon).shift(-horizon)
    bull = states == 1
    if bull.sum() == 0:
        return 0.0
    correct = (fwd_ret[bull] > 0).sum()
    return float(correct / bull.sum())


def profit_factor(states: pd.Series, close: pd.Series, horizon: int = 5) -> float:
    """
    Gross profit / gross loss for signals held over *horizon* bars.

    Score > 1.0 means the indicator is profitable on average.
    """
    fwd_ret = close.pct_change(horizon).shift(-horizon)
    bull = states == 1
    bear = states == -1

    gross_profit = fwd_ret[bull].clip(lower=0).sum() + (-fwd_ret[bear]).clip(lower=0).sum()
    gross_loss = (-fwd_ret[bull]).clip(lower=0).sum() + fwd_ret[bear].clip(lower=0).sum()

    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return float(gross_profit / gross_loss)


def signal_sharpe(states: pd.Series, close: pd.Series, horizon: int = 5) -> float:
    """
    Sharpe-like ratio of signal returns (mean / std).

    Uses both bull and bear signals directionally.
    """
    fwd_ret = close.pct_change(horizon).shift(-horizon)
    signal_ret = pd.Series(0.0, index=states.index)
    signal_ret[states == 1] = fwd_ret[states == 1]
    signal_ret[states == -1] = -fwd_ret[states == -1]

    active = signal_ret[states.isin([1, -1])]
    if len(active) < 5:
        return 0.0
    std = active.std()
    if std == 0 or np.isnan(std):
        return 0.0
    return float(active.mean() / std)


def trend_alignment(states: pd.Series, close: pd.Series, lookback: int = 20) -> float:
    """
    Measures how well bullish/bearish states align with the actual price trend.

    Score range: -1.0 to 1.0 (1.0 = perfect alignment).
    """
    trend = np.sign(close - close.rolling(lookback).mean())
    aligned = (states == trend.astype(int))
    active = states.isin([1, -1])
    if active.sum() == 0:
        return 0.0
    return float(aligned[active].mean() * 2 - 1)


OBJECTIVES = {
    "hit_rate": hit_rate,
    "profit_factor": profit_factor,
    "sharpe": signal_sharpe,
    "trend_alignment": trend_alignment,
}

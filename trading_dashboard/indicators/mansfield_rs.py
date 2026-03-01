"""
Mansfield Relative Strength (Weekly).

Measures a stock's performance relative to a benchmark index.
Positive = outperforming, negative = underperforming.
Rising = gaining relative momentum, falling = losing relative momentum.

The classic Mansfield RS is:
  RS_raw  = (Close / Benchmark_Close) * 100
  RS_MA   = SMA(RS_raw, ma_len)
  MRS     = ((RS_raw / RS_MA) - 1) * 100

A positive MRS means the stock is outperforming the benchmark's own trend.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import sma


def mansfield_relative_strength(
    df: pd.DataFrame,
    benchmark_close: pd.Series,
    ma_len: int = 52,
) -> pd.DataFrame:
    """
    Compute Mansfield Relative Strength.

    Parameters
    ----------
    df : DataFrame with at least a 'Close' column.
    benchmark_close : Series of benchmark close prices, aligned to df's index.
    ma_len : SMA length for the RS ratio (default 52 = ~1 year weekly).

    Returns
    -------
    DataFrame with columns:
        MRS_raw       - raw relative strength ratio (stock / benchmark * 100)
        MRS_ma        - SMA of the raw RS
        MRS           - Mansfield RS = ((raw / ma) - 1) * 100
        MRS_positive  - True when MRS > 0 (outperforming)
        MRS_rising    - True when MRS is rising (MRS > MRS[1])
    """
    close = df["Close"]
    bench = benchmark_close.reindex(df.index)

    rs_raw = (close / bench.replace(0.0, np.nan)) * 100.0
    rs_ma = sma(rs_raw, ma_len)
    mrs = ((rs_raw / rs_ma.replace(0.0, np.nan)) - 1.0) * 100.0

    return pd.DataFrame({
        "MRS_raw": rs_raw,
        "MRS_ma": rs_ma,
        "MRS": mrs,
        "MRS_positive": (mrs > 0).fillna(False),
        "MRS_rising": (mrs > mrs.shift(1)).fillna(False),
    }, index=df.index)

"""
KPI "traffic light" state encoding rules.

Content migrated from legacy `trading_dashboard/kpi_rules.py`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

STATE_BULL = 1
STATE_NEUTRAL = 0
STATE_BEAR = -1
STATE_NA = -2


def state_from_regime(index: pd.Index, cond: pd.Series | None, avail: pd.Series | None = None) -> pd.Series:
    """
    Convert a boolean regime (True/False) into bullish/bearish states.
    Any bar where avail is False (or cond is missing) becomes NA.
    """
    if cond is None:
        return pd.Series(STATE_NA, index=index, dtype=int)
    c = cond.reindex(index)
    a = avail.reindex(index) if avail is not None else pd.Series(True, index=index)
    a = a.fillna(False)
    out = pd.Series(np.where(c.fillna(False).to_numpy(dtype=bool), STATE_BULL, STATE_BEAR), index=index, dtype=int)
    out.loc[~a.to_numpy(dtype=bool)] = STATE_NA
    return out


def state_from_signals(
    index: pd.Index,
    bull_sig: pd.Series | None,
    bear_sig: pd.Series | None,
    avail: pd.Series | None = None,
) -> pd.Series:
    """
    Convert event signals into bullish/bearish/neutral states.
    Default is Neutral; any bar where avail is False becomes NA.

    Precedence: if both bull and bear are True on the same bar, bear wins.
    """
    a = avail.reindex(index) if avail is not None else pd.Series(True, index=index)
    a = a.fillna(False)
    out = pd.Series(STATE_NEUTRAL, index=index, dtype=int)
    if bull_sig is not None:
        out.loc[bull_sig.reindex(index).fillna(False).to_numpy(dtype=bool)] = STATE_BULL
    if bear_sig is not None:
        out.loc[bear_sig.reindex(index).fillna(False).to_numpy(dtype=bool)] = STATE_BEAR
    out.loc[~a.to_numpy(dtype=bool)] = STATE_NA
    return out


def state_from_persistent_signals(
    index: pd.Index,
    bull_sig: pd.Series | None,
    bear_sig: pd.Series | None,
    avail: pd.Series | None = None,
) -> pd.Series:
    """
    Like state_from_signals, but persists the last non-neutral state forward.
    """
    a = avail.reindex(index) if avail is not None else pd.Series(True, index=index)
    a = a.fillna(False)

    out = pd.Series(STATE_NEUTRAL, index=index, dtype=int)
    if bull_sig is not None:
        out.loc[bull_sig.reindex(index).fillna(False).to_numpy(dtype=bool)] = STATE_BULL
    if bear_sig is not None:
        out.loc[bear_sig.reindex(index).fillna(False).to_numpy(dtype=bool)] = STATE_BEAR

    tmp = out.astype(float).where(out != STATE_NEUTRAL)
    out = tmp.ffill().fillna(STATE_NEUTRAL).astype(int)
    out.loc[~a.to_numpy(dtype=bool)] = STATE_NA
    return out


"""Python mirrors of the workbook's timing flags, driven by cfg thresholds.

These reproduce the live Excel formulas in build_workbook._formula_for so the
dashboard and the xlsx agree. A missing (None / NaN) input is always False.
"""

from __future__ import annotations

from typing import Optional

import numpy as np


def _num(v) -> Optional[float]:
    """Float or None. None, NaN, '' and non-numerics all become None."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(f) else f


def _th(cfg: dict) -> dict:
    return (cfg or {}).get("thresholds", {})


def is_oversold(row, cfg: dict) -> bool:
    rsi = _num(row.get("rsi_14"))
    return rsi is not None and rsi < float(_th(cfg).get("oversold_rsi", 30))


def is_breakout(row, cfg: dict) -> bool:
    pfh = _num(row.get("pct_from_high"))
    return pfh is not None and abs(pfh) <= float(_th(cfg).get("breakout_within_pct", 0.03))


def is_pullback(row, cfg: dict) -> bool:
    d50, d200 = _num(row.get("dist_ma50")), _num(row.get("dist_ma200"))
    if d50 is None or d200 is None:
        return False
    return d50 < d200 and abs(d50) <= float(_th(cfg).get("pullback_within_pct", 0.03))


def is_downtrend(row, cfg: Optional[dict] = None) -> bool:
    """The existing _flag_down column (1Y return at or below the cutoff)."""
    v = row.get("_flag_down")
    if v is None:
        return False
    try:
        if isinstance(v, float) and np.isnan(v):
            return False
        return bool(v)
    except (TypeError, ValueError):
        return False


def compute_flags(row, cfg: dict) -> dict:
    return {
        "oversold": is_oversold(row, cfg),
        "breakout": is_breakout(row, cfg),
        "pullback": is_pullback(row, cfg),
        "downtrend": is_downtrend(row, cfg),
    }


__all__ = ["is_oversold", "is_breakout", "is_pullback", "is_downtrend",
           "compute_flags"]

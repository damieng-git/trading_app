"""
Indicator template — copy this file to create a new indicator.

Checklist for adding a new indicator:
1. Copy this file to `trading_dashboard/indicators/<your_indicator>.py`
2. Implement `compute()` and `kpi_state()` (if applicable)
3. Fill in INDICATOR_META
4. Import and re-export in `__init__.py`
5. Register in `registry.py` (or use the meta below for auto-registration)
6. Add parameter defaults to `apps/dashboard/configs/indicator_config.json`
7. Add KPI state logic in `kpis/catalog.py` (if kpi_name is set)
8. Run: python -m apps.dashboard.build_dashboard --mode rebuild_ui
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from ._base import sma, ema, atr  # import whatever primitives you need


# ---------------------------------------------------------------------------
# Metadata — used by the registry for auto-discovery
# ---------------------------------------------------------------------------

INDICATOR_META: Dict[str, Any] = {
    "key": "MY_INDICATOR",                # unique identifier
    "title": "My Indicator (Display Name)",
    "dimension": "trend",                  # trend | momentum | relative_strength | breakout | risk_exit | other
    "overlay": True,                       # True = plotted on price chart, False = separate panel
    "kpi_name": "My Indicator",            # name shown in KPI panel (None = no KPI)
    "kpi_type": "trend",                   # "trend" | "breakout" | None
    "pine_source": "docs/pinescripts/My Indicator/",  # path to original Pine Script
    "config_key": "MY_INDICATOR",          # key in indicator_config.json
    "config_defaults": {                   # default parameters
        "length": 14,
        "multiplier": 2.0,
    },
}


# ---------------------------------------------------------------------------
# Compute — called by the enrichment pipeline
# ---------------------------------------------------------------------------

def compute(df: pd.DataFrame, config: Dict[str, Any] | None = None) -> pd.DataFrame:
    """
    Compute the indicator on OHLCV DataFrame *df*.

    Parameters
    ----------
    df : pd.DataFrame
        Must have columns: Open, High, Low, Close, Volume (Volume optional).
    config : dict, optional
        Parameter overrides from indicator_config.json.

    Returns
    -------
    pd.DataFrame with new indicator columns (same index as df).
    """
    cfg = {**INDICATOR_META["config_defaults"], **(config or {})}
    length = int(cfg["length"])
    mult = float(cfg["multiplier"])

    # --- Your indicator logic here ---
    result = sma(df["Close"], length)

    return pd.DataFrame({
        f"{INDICATOR_META['key']}_value": result,
    }, index=df.index)


# ---------------------------------------------------------------------------
# KPI state — optional, for traffic-light scoring
# ---------------------------------------------------------------------------

def kpi_state(df: pd.DataFrame, bar: int = -1) -> int:
    """
    Determine the KPI state at the given bar index.

    Returns
    -------
    1 = bullish, 0 = neutral, -1 = bearish, -2 = unavailable
    """
    key = INDICATOR_META["key"]
    col = f"{key}_value"
    if col not in df.columns or len(df) == 0:
        return -2

    val = df[col].iloc[bar]
    if pd.isna(val):
        return -2

    close = df["Close"].iloc[bar]
    if val > close:
        return 1
    elif val < close:
        return -1
    return 0

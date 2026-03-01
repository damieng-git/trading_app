"""
Indicator registry: central metadata for all indicators and their KPI/dimension assignments.

Each indicator is registered with:
- key: unique identifier (matches indicator_config.json)
- title: human-readable display name
- dimension: one of DIMENSIONS (for grouping in the dashboard UI)
- overlay: whether it's plotted on the price chart
- kpi_name: the KPI display name used in the screener (None = no KPI)
- kpi_type: "trend" | "breakout" | None
- columns: output DataFrame columns produced by this indicator
- config_key: key used in indicator_config.json (often same as key)
- config_defaults: default parameter dict

New indicators register themselves by calling ``register()``.
The pipeline iterates ``get_all()`` to compute indicators and build KPI state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── Dimension definitions ────────────────────────────────────────────────────

DIMENSIONS: Dict[str, str] = {
    "trend": "Trend",
    "momentum": "Momentum",
    "relative_strength": "Relative Strength",
    "breakout": "Breakout",
    "risk_exit": "Risk / Exit",
    "other": "Other",
}

DIMENSION_ORDER: List[str] = [
    "trend",
    "momentum",
    "relative_strength",
    "breakout",
    "risk_exit",
    "other",
]


# ── Registration dataclass ───────────────────────────────────────────────────

@dataclass
class IndicatorDef:
    key: str
    title: str
    dimension: str
    overlay: bool = True
    kpi_name: Optional[str] = None
    kpi_type: Optional[str] = None       # "trend" | "breakout" | None
    columns: List[str] = field(default_factory=list)
    config_key: Optional[str] = None     # defaults to key
    config_defaults: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.config_key is None:
            self.config_key = self.key
        if self.dimension not in DIMENSIONS:
            raise ValueError(
                f"Unknown dimension '{self.dimension}' for indicator '{self.key}'. "
                f"Must be one of: {list(DIMENSIONS.keys())}"
            )


# ── Global registry ──────────────────────────────────────────────────────────

_REGISTRY: Dict[str, IndicatorDef] = {}


def register(defn: IndicatorDef) -> IndicatorDef:
    _REGISTRY[defn.key] = defn
    return defn


def get(key: str) -> Optional[IndicatorDef]:
    return _REGISTRY.get(key)


def get_all() -> List[IndicatorDef]:
    return list(_REGISTRY.values())


def get_by_dimension(dimension: str) -> List[IndicatorDef]:
    return [d for d in _REGISTRY.values() if d.dimension == dimension]


def get_kpi_trend_order() -> List[str]:
    """KPI names for trend scoring, in registration order."""
    return [d.kpi_name for d in _REGISTRY.values() if d.kpi_type == "trend" and d.kpi_name]


def get_kpi_breakout_order() -> List[str]:
    """KPI names for breakout scoring, in registration order."""
    return [d.kpi_name for d in _REGISTRY.values() if d.kpi_type == "breakout" and d.kpi_name]


def get_kpi_order() -> List[str]:
    return get_kpi_trend_order() + get_kpi_breakout_order()


def get_dimension_for_kpi(kpi_name: str) -> Optional[str]:
    for d in _REGISTRY.values():
        if d.kpi_name == kpi_name:
            return d.dimension
    return None


def get_dimension_label(dimension_key: str) -> str:
    return DIMENSIONS.get(dimension_key, dimension_key)


def get_dimension_map() -> Dict[str, str]:
    """Returns {kpi_name: dimension_key} for all registered KPIs."""
    return {d.kpi_name: d.dimension for d in _REGISTRY.values() if d.kpi_name}


# ── Register all existing indicators ────────────────────────────────────────
# Ordered by dimension, then by original KPI_TREND_ORDER / KPI_BREAKOUT_ORDER.

# --- Trend ---
register(IndicatorDef(
    key="NW_LuxAlgo", title="Nadaraya-Watson Smoothers [LuxAlgo]",
    dimension="trend", overlay=True,
    kpi_name="Nadaraya-Watson Smoother", kpi_type="trend",
    config_key="NW_LuxAlgo",
    config_defaults={"bandwidth": 8.0, "window": 500},
))
register(IndicatorDef(
    key="TuTCI", title="Turtle Trade Channels",
    dimension="trend", overlay=True,
    kpi_name="TuTCI", kpi_type="trend",
    config_defaults={"length": 20, "exit_length": 10},
))
register(IndicatorDef(
    key="MA_Ribbon", title="MA Ribbon (4 MAs)",
    dimension="trend", overlay=True,
    kpi_name="MA Ribbon", kpi_type="trend",
))
register(IndicatorDef(
    key="MadridRibbon", title="Madrid MA Ribbon Bar v2",
    dimension="trend", overlay=False,
    kpi_name="Madrid Ribbon", kpi_type="trend",
    config_defaults={"exponential": True},
))
register(IndicatorDef(
    key="DonchianRibbon", title="Donchian Trend Ribbon",
    dimension="trend", overlay=False,
    kpi_name="Donchian Ribbon", kpi_type="trend",
    config_defaults={"dlen": 20, "depth": 10},
))
register(IndicatorDef(
    key="DEMA", title="Double EMA (DEMA, 9)",
    dimension="trend", overlay=True,
    kpi_name="DEMA", kpi_type="trend",
    config_defaults={"length": 9},
))
register(IndicatorDef(
    key="Ichimoku", title="Ichimoku Kinkō Hyō",
    dimension="trend", overlay=True,
    kpi_name="Ichimoku", kpi_type="trend",
    config_defaults={"tenkan": 9, "kijun": 26, "senkou_b": 52, "offset": 26},
))
register(IndicatorDef(
    key="GK_Trend", title="GK Trend Ribbon",
    dimension="trend", overlay=True,
    kpi_name="GK Trend Ribbon", kpi_type="trend",
    config_defaults={"length": 200, "band_mult": 2.0, "atr_length": 21, "confirm_bars": 2},
))
register(IndicatorDef(
    key="Impulse_Trend", title="Impulse Trend Levels",
    dimension="trend", overlay=True,
    kpi_name="Impulse Trend", kpi_type="trend",
    config_defaults={"trend_length": 19, "impulse_lookback": 5, "decay_rate": 0.99, "mad_length": 20, "band_min": 1.5, "band_max": 1.9},
))

# --- Momentum ---
register(IndicatorDef(
    key="WT_LB", title="WaveTrend [LazyBear]",
    dimension="momentum", overlay=False,
    kpi_name="WT_LB", kpi_type="trend",
    config_defaults={"n1": 10, "n2": 21},
))
register(IndicatorDef(
    key="SQZMOM_LB", title="Squeeze Momentum [LazyBear]",
    dimension="momentum", overlay=False,
    kpi_name="SQZMOM_LB", kpi_type="trend",
    config_defaults={"length": 20, "mult": 2.0, "length_kc": 20, "mult_kc": 1.5, "use_true_range": True},
))
register(IndicatorDef(
    key="SMI", title="Stochastic Momentum Index",
    dimension="momentum", overlay=False,
    kpi_name="Stoch_MTM", kpi_type="trend",
    config_defaults={"a": 10, "b": 3, "c": 10, "smooth_period": 5},
))
register(IndicatorDef(
    key="MACD", title="MACD (12, 26, 9)",
    dimension="momentum", overlay=False,
    kpi_name="CM_Ult_MacD_MFT", kpi_type="trend",
    config_defaults={"fast": 12, "slow": 26, "signal": 9},
))
register(IndicatorDef(
    key="cRSI", title="cRSI",
    dimension="momentum", overlay=False,
    kpi_name="cRSI", kpi_type="trend",
    config_defaults={"domcycle": 20, "vibration": 10, "leveling": 10.0},
))
register(IndicatorDef(
    key="ADX_DI", title="ADX & DI (14)",
    dimension="momentum", overlay=False,
    kpi_name="ADX & DI", kpi_type="trend",
    config_defaults={"length": 14},
))
register(IndicatorDef(
    key="GMMA", title="GMMA (EMAs)",
    dimension="momentum", overlay=True,
    kpi_name="GMMA", kpi_type="trend",
))
register(IndicatorDef(
    key="RSI_Zeiierman", title="RSI Strength & Consolidation Zones (Zeiierman)",
    dimension="momentum", overlay=False,
    kpi_name="RSI Strength & Consolidation Zones (Zeiierman)", kpi_type="trend",
    config_key="RSI Strength & Consolidation Zones (Zeiierman)",
    config_defaults={"rsi_length": 14, "dmi_length": 14, "adx_smoothing": 14, "filter_strength": 0.1},
))
register(IndicatorDef(
    key="OBVOSC", title="OBV Oscillator (20)",
    dimension="momentum", overlay=False,
    kpi_name="OBVOSC_LB", kpi_type="trend",
    config_defaults={"length": 20},
))

# --- Relative Strength ---
register(IndicatorDef(
    key="Mansfield_RS", title="Mansfield Relative Strength",
    dimension="relative_strength", overlay=False,
    kpi_name="Mansfield RS", kpi_type="trend",
    config_defaults={"ma_len": 52, "benchmark": "SPY"},
))
register(IndicatorDef(
    key="SR_Breaks", title="SR Breaks & Retests",
    dimension="relative_strength", overlay=True,
    kpi_name="SR Breaks", kpi_type="trend",
    config_defaults={"lookback": 20, "vol_len": 2, "box_width": 1.0, "atr_len": 200},
))

# --- Breakout ---
register(IndicatorDef(
    key="BB", title="Bollinger Bands (20, 2.0)",
    dimension="breakout", overlay=True,
    kpi_name="BB 30", kpi_type="breakout",
    config_defaults={"length": 20, "mult": 2.0, "ma_type": "SMA"},
))
register(IndicatorDef(
    key="NWE_Envelope_MAE", title="Nadaraya-Watson Envelope (MAE bands)",
    dimension="breakout", overlay=True,
    kpi_name="Nadaraya-Watson Envelop (MAE)", kpi_type="breakout",
    config_defaults={"bandwidth": 8.0, "window": 500, "mult": 3.0, "repaint": False},
))
register(IndicatorDef(
    key="NWE_Envelope_STD", title="Nadaraya-Watson Envelope (STD bands)",
    dimension="breakout", overlay=True,
    kpi_name="Nadaraya-Watson Envelop (STD)", kpi_type="breakout",
    config_defaults={"bandwidth": 8.0, "window": 500, "mult": 3.0, "repaint": False},
))
register(IndicatorDef(
    key="NWE_Envelope_RP", title="Nadaraya-Watson Envelope (repainting)",
    dimension="breakout", overlay=True,
    kpi_name="Nadaraya-Watson Envelop (Repainting)", kpi_type="breakout",
    config_defaults={"bandwidth": 8.0, "window": 500, "mult": 3.0, "repaint": True},
))

# RSI_Zeiierman_breakout and Breakout_Targets removed: no chart rendering exists,
# filter checkboxes had no effect.  KPI states are still computed in catalog.py
# but no longer appear in the UI filter panel.

# --- Risk / Exit ---
register(IndicatorDef(
    key="SuperTrend", title="SuperTrend (12, 3.0)",
    dimension="risk_exit", overlay=True,
    kpi_name="SuperTrend", kpi_type="trend",
    config_defaults={"periods": 12, "multiplier": 3.0, "change_atr_method": True},
))
register(IndicatorDef(
    key="UT_Bot", title="UT Bot Alerts",
    dimension="risk_exit", overlay=True,
    kpi_name="UT Bot Alert", kpi_type="trend",
    config_defaults={"a": 1.0, "c": 10},
))
register(IndicatorDef(
    key="PSAR", title="Parabolic SAR",
    dimension="risk_exit", overlay=True,
    kpi_name="CM_P-SAR", kpi_type="trend",
    config_defaults={"start": 0.02, "increment": 0.02, "maximum": 0.2},
))
# --- Other (non-scored: displayed on chart but excluded from scores/spider) ---
register(IndicatorDef(
    key="VOL_MA", title="Volume + MA20",
    dimension="momentum", overlay=False,
    kpi_name="Volume + MA20", kpi_type="trend",
    config_defaults={"length": 20},
))
register(IndicatorDef(
    key="ATR", title="ATR Stop Loss Finder",
    dimension="other", overlay=True,
    kpi_name=None, kpi_type=None,
    config_defaults={"length": 14, "smoothing": "RMA", "mult": 1.5},
))

"""
config_loader.py

Configuration constants, path definitions, and build config loading for the trading dashboard.
Extracted from build_dashboard.py for separation of concerns.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)


# =============================================================================
# USER-CONFIGURABLE SETTINGS
# =============================================================================

# Display symbols you want in the dashboard:
SYMBOLS: List[str] = [
    # US
    "DASH",
    # Step 2 sample (30 tickers) — extracted from README/stocks sample.pdf
    "BVI.PA",
    "ML.PA",
    "CAP.PA",
    "KER.PA",
    "ACA.PA",
    "EDEN.PA",
    "STLAP.PA",
    "RI.PA",
    "DSY.PA",
    "TEP.PA",
    "CON.DE",
    "ZAL.DE",
    "G24.DE",
    "SAP.DE",
    "ALV.DE",
    "MBG.DE",
    "VOW3.DE",
    "DTG.DE",
    "CBK.DE",
    "HEN3.DE",
    "VOD.L",
    "AV.L",
    "PSH.L",
    "BP.L",
    "PHNX.L",
    "CCEP.L",
    "TSCO.L",
    "HSX.L",
    "EDV.L",
    "BEZ.L",
]

# Optional mapping from display symbol -> yfinance ticker.
# If left empty, the script will auto-try a few common suffixes.
YFINANCE_TICKER_MAP: Dict[str, str] = {
    # Examples:
    # "KER": "KER.PA",
    # "EXSD": "EXSD.DE",
}

# Date range for download (daily), then resampled to weekly:
START_DATE = "2018-01-01"
END_DATE = None  # None = up to latest

# Mandatory weekly timeline:
WEEKLY_RULE = "W-FRI"  # recommended by your instructions

# Supported timeline toggles for the dashboard:
# - 4H is built from 1h data resampled to 4h
# - 1D uses yfinance 1d candles
# - 1W uses 1d candles resampled to W-FRI
# - 2W uses 1d candles resampled to 2W-FRI (bi-weekly)
# - 1M uses 1d candles resampled to month-end
DEFAULT_TIMEFRAMES: list[str] = ["4H", "1D", "1W", "2W", "1M"]
VALID_TIMEFRAMES: set[str] = {"4H", "1D", "1W", "2W", "1M"}
TIMEFRAMES: List[str] = list(DEFAULT_TIMEFRAMES)


@dataclass(frozen=True)
class Timeframe:
    """Centralizes all per-timeframe metadata to eliminate scattered magic numbers."""
    key: str
    max_plot_bars: int
    min_bars: int
    enrich_buffer_bars: int
    bars_per_year: int


TIMEFRAME_REGISTRY: Dict[str, Timeframe] = {
    "4H": Timeframe(key="4H", max_plot_bars=5000, min_bars=500, enrich_buffer_bars=600, bars_per_year=1512),
    "1D": Timeframe(key="1D", max_plot_bars=600, min_bars=200, enrich_buffer_bars=300, bars_per_year=252),
    "1W": Timeframe(key="1W", max_plot_bars=140, min_bars=80, enrich_buffer_bars=60, bars_per_year=52),
    "2W": Timeframe(key="2W", max_plot_bars=70, min_bars=40, enrich_buffer_bars=30, bars_per_year=26),
    "1M": Timeframe(key="1M", max_plot_bars=36, min_bars=18, enrich_buffer_bars=15, bars_per_year=12),
}


def get_timeframe(key: str) -> Timeframe:
    """Look up a Timeframe by key, raising KeyError if unknown."""
    return TIMEFRAME_REGISTRY[key]

# Paths (robust to current working directory).
# This file lives in: trading_app/apps/dashboard/config_loader.py
# Repo root (PROJECT_ROOT) is: trading_app/
SCRIPT_DIR = Path(__file__).resolve().parent
import os as _os
REPO_DIR = Path(_os.environ.get("TRADING_APP_ROOT", str(SCRIPT_DIR.parents[2])))
PROJECT_ROOT = REPO_DIR
PROJECT_DIR = REPO_DIR  # legacy name used throughout the file

# Optional legacy README folder (not required for dashboard runtime)
README_DIR = REPO_DIR / "README"

# Local docs/support (kept lightweight in this repo)
DOCS_DIR = REPO_DIR / "docs"
SUPPORT_DIR = REPO_DIR / "data" / "support_files"

# Optional PineScript sources (if present)
PINESCRIPTS_DIR = REPO_DIR / "docs" / "pinescripts"
LEGACY_PINESCRIPTS_DIR = REPO_DIR / "PineScripts"

# Data tiers (end-state layout)
DATA_DIR = REPO_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
FEATURE_STORE_DIR = DATA_DIR / "feature_store"
FEATURE_STORE_ENRICHED_DIR = FEATURE_STORE_DIR / "enriched"
OHLCV_CACHE_DIR = CACHE_DIR / "ohlcv_raw"
DASHBOARD_ARTIFACTS_DIR = DATA_DIR / "dashboard_artifacts"
DEFAULT_DATASET_NAME = "dashboard"

# Enriched dataset (feature store)
OUTPUT_DATA_DIR = FEATURE_STORE_ENRICHED_DIR / DEFAULT_DATASET_NAME
OUTPUT_STOCK_DATA_DIR = OUTPUT_DATA_DIR / "stock_data"
LEGACY_OUTPUT_STOCK_DATA_DIR = DATA_DIR / "Stock Data"

# Primary dashboard artifact (HTML)
OUTPUT_HTML = DASHBOARD_ARTIFACTS_DIR / "dashboard_weekly_plotly.html"

# Docs / metadata artifacts
OUTPUT_MAPPING = DOCS_DIR / "pine_to_python_mapping.md"
OUTPUT_README = REPO_DIR / "README.md"

TRADINGVIEW_DATA_DIR = DASHBOARD_ARTIFACTS_DIR / "tradingview_data"
CONFIG_JSON = SCRIPT_DIR / "configs" / "config.json"
LISTS_DIR = SCRIPT_DIR / "configs" / "lists"
INDICATOR_CONFIG_JSON_DEFAULT = SCRIPT_DIR / "configs" / "indicator_config.json"
SYMBOL_DISPLAY_OVERRIDES_JSON = SCRIPT_DIR / "configs" / "symbol_display_overrides.json"
RUN_METADATA_JSON = OUTPUT_DATA_DIR / "run_metadata.json"
DATA_HEALTH_JSON = OUTPUT_DATA_DIR / "data_health.json"

# Lazy-load assets (multi-file dashboard mode)
DASHBOARD_ASSETS_DIR = DASHBOARD_ARTIFACTS_DIR / "dashboard_assets"
DASHBOARD_SHELL_HTML = DASHBOARD_ARTIFACTS_DIR / "dashboard_shell.html"

# Screener + alerts outputs
SCREENER_SUMMARY_JSON = DASHBOARD_ARTIFACTS_DIR / "screener_summary.json"
ALERT_FILES_DIR = DASHBOARD_ARTIFACTS_DIR / "alert_files"
ALERTS_LOOKBACK_BARS_DEFAULT = 3
DASHBOARD_MODE_DEFAULT = "lazy_static"


# Reuse cached CSVs to avoid re-downloading (faster + lower RAM/network).
USE_CACHED_OUTPUT_DATA = True
# If cached CSVs are older than this, recompute/download.
# (Prevents the dashboard silently using stale data forever.)
CACHE_TTL_HOURS: float = 24.0

# HTML size control: derived from TIMEFRAME_REGISTRY for consistency.
MAX_PLOT_BARS_PER_TF: Dict[str, int] = {tf.key: tf.max_plot_bars for tf in TIMEFRAME_REGISTRY.values()}

# Plot window control (date-based). This aligns the historical span across timeframes.
PLOT_LOOKBACK_MONTHS_DEFAULT = 24


# =============================================================================
# Dataclasses
# =============================================================================


@dataclass(frozen=True)
class BuildPaths:
    output_data_dir: Path
    output_stock_data_dir: Path
    output_raw_ohlcv_dir: Path
    docs_dir: Path
    output_html: Path
    dashboard_assets_dir: Path
    dashboard_shell_html: Path
    run_metadata_json: Path
    data_health_json: Path
    screener_summary_json: Path
    alert_files_dir: Path
    output_mapping: Path
    output_readme: Path


@dataclass(frozen=True)
class BuildConfig:
    symbols: list[str]
    # Optional symbol groups (e.g. watchlist/portfolio). Used only by the dashboard shell UI.
    # Export uses the union of `symbols` and all grouped symbols.
    symbol_groups: dict[str, list[str]]
    timeframes: list[str]
    max_plot_bars_per_tf: dict[str, int]
    cache_ttl_hours: float
    kpi_weights: dict[str, float]
    combo_3_kpis: list[str]
    combo_4_kpis: list[str]
    combo_kpis_by_tf: dict[str, dict[str, list[str]]]
    alerts_lookback_bars: int
    plot_lookback_months: int
    dashboard_mode: str
    min_bars_by_tf: dict[str, int]
    max_missing_close_pct: float
    max_missing_volume_pct: float
    dataset_name: str
    stoch_mtm_thresholds: dict[str, float]
    output_data_dir_override: str | None = None


# =============================================================================
# Config loading functions
# =============================================================================


def _cfg_parse(cfg: dict, key: str, parser, default, label: str = ""):
    """Parse a single config key with isolated error handling."""
    if key not in cfg:
        return default
    try:
        return parser(cfg[key], default)
    except Exception as e:
        logger.warning("Config key '%s' (%s) invalid, using default: %s", key, label or key, e)
        return default


def load_build_config() -> BuildConfig:
    symbols = list(SYMBOLS)
    symbol_groups: dict[str, list[str]] = {}
    timeframes = list(TIMEFRAMES)
    max_plot_bars = dict(MAX_PLOT_BARS_PER_TF)
    cache_ttl_hours = float(CACHE_TTL_HOURS)
    kpi_weights: Dict[str, float] = {}
    combo_3_kpis: list[str] = ["Nadaraya-Watson Smoother", "Madrid Ribbon", "Volume + MA20"]
    combo_4_kpis: list[str] = ["Nadaraya-Watson Smoother", "Madrid Ribbon", "GK Trend Ribbon", "cRSI"]
    combo_kpis_by_tf: dict = {}
    alerts_lookback_bars: int = int(ALERTS_LOOKBACK_BARS_DEFAULT)
    plot_lookback_months: int = int(PLOT_LOOKBACK_MONTHS_DEFAULT)
    dashboard_mode: str = str(DASHBOARD_MODE_DEFAULT)
    min_bars_by_tf: dict[str, int] = {tf.key: tf.min_bars for tf in TIMEFRAME_REGISTRY.values()}
    max_missing_close_pct: float = 5.0
    max_missing_volume_pct: float = 20.0
    dataset_name: str = DEFAULT_DATASET_NAME
    stoch_mtm_thresholds: dict[str, float] = {"overbought": 40.0, "oversold": -40.0, "long_threshold": -35.0, "short_threshold": 35.0}
    output_data_dir_override: str | None = None

    try:
        from trading_dashboard.symbols.manager import SymbolManager
        _lists_dir = SCRIPT_DIR / "configs" / "lists"
        if _lists_dir.is_dir():
            _sm = SymbolManager.from_lists_dir(_lists_dir, config_path=CONFIG_JSON)
            symbols = _sm.symbols
            symbol_groups = _sm.groups
    except Exception as e:
        logger.warning("Could not load symbols from lists dir: %s", e)

    if CONFIG_JSON.exists() and CONFIG_JSON.stat().st_size > 0:
        try:
            cfg = json.loads(CONFIG_JSON.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to parse config.json")
            cfg = {}
        if isinstance(cfg, dict):
            symbols = _cfg_parse(cfg, "symbols", lambda v, d: [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else d, symbols, "symbols")

            def _parse_groups(v, d):
                if not isinstance(v, dict):
                    return d
                merged = dict(d or {})
                for k, vals in v.items():
                    key = str(k or "").strip()
                    if key and isinstance(vals, list):
                        merged[key] = [str(x).strip().upper() for x in vals if str(x).strip()]
                return merged
            symbol_groups = _cfg_parse(cfg, "symbol_groups", _parse_groups, symbol_groups, "symbol_groups")
            timeframes = _cfg_parse(cfg, "timeframes", lambda v, d: [str(x).strip().upper() for x in v if str(x).strip()] if isinstance(v, list) else d, timeframes, "timeframes")
            max_plot_bars = _cfg_parse(cfg, "max_plot_bars_per_tf", lambda v, d: {str(k).strip().upper(): int(vv) for k, vv in v.items()} if isinstance(v, dict) else d, max_plot_bars, "max_plot_bars_per_tf")
            cache_ttl_hours = _cfg_parse(cfg, "cache_ttl_hours", lambda v, d: float(v) if v else d, cache_ttl_hours, "cache_ttl_hours")
            kpi_weights = _cfg_parse(cfg, "kpi_weights", lambda v, d: {str(k): float(vv) for k, vv in v.items() if str(k).strip()} if isinstance(v, dict) else d, kpi_weights, "kpi_weights")
            combo_3_kpis = _cfg_parse(cfg, "combo_3_kpis", lambda v, d: [str(x) for x in v] if isinstance(v, list) else d, combo_3_kpis, "combo_3_kpis")
            combo_4_kpis = _cfg_parse(cfg, "combo_4_kpis", lambda v, d: [str(x) for x in v] if isinstance(v, list) else d, combo_4_kpis, "combo_4_kpis")

            def _parse_combo_nested(v, d):
                if not isinstance(v, dict):
                    return d
                return {str(k1): {str(k2): [str(x) for x in v2] for k2, v2 in v1.items()} for k1, v1 in v.items() if isinstance(v1, dict)}
            combo_kpis_by_tf = _cfg_parse(cfg, "combo_kpis_by_tf", _parse_combo_nested, combo_kpis_by_tf, "combo_kpis_by_tf")
            alerts_lookback_bars = _cfg_parse(cfg, "alerts_lookback_bars", lambda v, d: int(v) if v else d, alerts_lookback_bars, "alerts_lookback_bars")
            plot_lookback_months = _cfg_parse(cfg, "plot_lookback_months", lambda v, d: int(v) if v else d, plot_lookback_months, "plot_lookback_months")
            dashboard_mode = _cfg_parse(cfg, "dashboard_mode", lambda v, d: str(v).strip() if v else d, dashboard_mode, "dashboard_mode")

            def _parse_min_bars(v, d):
                if not isinstance(v, dict):
                    return d
                out = dict(d)
                for k, vv in v.items():
                    kk = str(k).strip().upper()
                    if kk:
                        out[kk] = int(vv)
                return out
            min_bars_by_tf = _cfg_parse(cfg, "min_bars_by_tf", _parse_min_bars, min_bars_by_tf, "min_bars_by_tf")
            max_missing_close_pct = _cfg_parse(cfg, "max_missing_close_pct", lambda v, d: float(v) if v else d, max_missing_close_pct, "max_missing_close_pct")
            max_missing_volume_pct = _cfg_parse(cfg, "max_missing_volume_pct", lambda v, d: float(v) if v else d, max_missing_volume_pct, "max_missing_volume_pct")
            output_data_dir_override = _cfg_parse(cfg, "output_data_dir", lambda v, d: str(v).strip() or None, output_data_dir_override, "output_data_dir")
            dataset_name = _cfg_parse(cfg, "dataset_name", lambda v, d: str(v).strip() or d, dataset_name, "dataset_name")
            stoch_mtm_thresholds = _cfg_parse(cfg, "stoch_mtm_thresholds", lambda v, d: {str(k): float(vv) for k, vv in v.items()} if isinstance(v, dict) else d, stoch_mtm_thresholds, "stoch_mtm_thresholds")

    dashboard_mode = (dashboard_mode or "").strip().lower()
    if dashboard_mode not in {"lazy_server", "lazy_static", "monolithic"}:
        dashboard_mode = DASHBOARD_MODE_DEFAULT

    # Ensure export covers the union of explicit symbols and all group members.
    try:
        union_syms = set([str(x).strip().upper() for x in symbols if str(x).strip()])
        for arr in (symbol_groups or {}).values():
            for s in (arr or []):
                ss = str(s).strip().upper()
                if ss:
                    union_syms.add(ss)
        symbols = sorted(union_syms)
    except Exception:
        symbols = [str(x).strip().upper() for x in symbols if str(x).strip()]

    return BuildConfig(
        symbols=list(symbols),
        symbol_groups=dict(symbol_groups or {}),
        timeframes=list(timeframes),
        max_plot_bars_per_tf=dict(max_plot_bars),
        cache_ttl_hours=float(cache_ttl_hours),
        kpi_weights=dict(kpi_weights),
        combo_3_kpis=list(combo_3_kpis),
        combo_4_kpis=list(combo_4_kpis),
        combo_kpis_by_tf=dict(combo_kpis_by_tf),
        alerts_lookback_bars=int(alerts_lookback_bars),
        plot_lookback_months=int(plot_lookback_months),
        dashboard_mode=str(dashboard_mode),
        min_bars_by_tf=dict(min_bars_by_tf),
        max_missing_close_pct=float(max_missing_close_pct),
        max_missing_volume_pct=float(max_missing_volume_pct),
        dataset_name=str(dataset_name),
        stoch_mtm_thresholds=dict(stoch_mtm_thresholds),
        output_data_dir_override=output_data_dir_override,
    )


def resolve_paths(cfg: BuildConfig) -> BuildPaths:
    if cfg.output_data_dir_override:
        output_data_dir = (PROJECT_DIR / cfg.output_data_dir_override)
        output_stock_data_dir = output_data_dir / "stock_data"
        output_raw_ohlcv_dir = output_data_dir / "ohlcv_raw"
        dashboard_artifacts_dir = output_data_dir
    else:
        dn = (cfg.dataset_name or DEFAULT_DATASET_NAME).strip() or DEFAULT_DATASET_NAME
        output_data_dir = FEATURE_STORE_ENRICHED_DIR / dn
        output_stock_data_dir = output_data_dir / "stock_data"
        output_raw_ohlcv_dir = OHLCV_CACHE_DIR / dn
        dashboard_artifacts_dir = DASHBOARD_ARTIFACTS_DIR

    docs_dir = DOCS_DIR
    return BuildPaths(
        output_data_dir=output_data_dir,
        output_stock_data_dir=output_stock_data_dir,
        output_raw_ohlcv_dir=output_raw_ohlcv_dir,
        docs_dir=docs_dir,
        output_html=dashboard_artifacts_dir / "dashboard_weekly_plotly.html",
        dashboard_assets_dir=dashboard_artifacts_dir / "dashboard_assets",
        dashboard_shell_html=dashboard_artifacts_dir / "dashboard_shell.html",
        run_metadata_json=output_data_dir / "run_metadata.json",
        data_health_json=output_data_dir / "data_health.json",
        screener_summary_json=dashboard_artifacts_dir / "screener_summary.json",
        alert_files_dir=dashboard_artifacts_dir / "alert_files",
        output_mapping=OUTPUT_MAPPING,
        output_readme=OUTPUT_README,
    )

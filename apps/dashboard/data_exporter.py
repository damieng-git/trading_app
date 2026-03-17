"""
Export enriched DataFrames as compact JSON for client-side chart rendering.

Replaces the Plotly figure generation step — the browser builds charts on demand.
"""

from __future__ import annotations

import gzip as _gzip
import json
import logging
import math
from pathlib import Path
from typing import Any


def _gzip_file(path: Path) -> None:
    """Write a gzip companion for a static asset."""
    gz_path = path.with_suffix(path.suffix + ".gz")
    with open(path, "rb") as f_in:
        with _gzip.open(gz_path, "wb", compresslevel=6) as f_out:
            f_out.write(f_in.read())

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _clean(v: Any) -> Any:
    """NaN / Inf → None for JSON serialisation."""
    if isinstance(v, (float, np.floating)):
        if math.isnan(v) or math.isinf(v):
            return None
        return round(float(v), 6)
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    return v


def _series_to_list(s: pd.Series) -> list:
    """Vectorized conversion: Series → JSON-safe list."""
    arr = s.to_numpy()
    if arr.dtype == bool or arr.dtype == np.bool_:
        return arr.tolist()
    if np.issubdtype(arr.dtype, np.integer):
        return arr.tolist()
    if np.issubdtype(arr.dtype, np.floating):
        finite = np.isfinite(arr)
        if finite.all():
            return np.round(arr, 6).tolist()
        rounded = np.round(arr, 6)
        return [float(v) if f else None for v, f in zip(rounded, finite)]
    return [_clean(v) for v in arr]


def export_symbol_data(
    symbol: str,
    timeframe: str,
    df: pd.DataFrame,
    *,
    display_name: str = "",
    precomputed_kpi_state: dict | None = None,
    combo_3_kpis: list[str] | None = None,
    combo_4_kpis: list[str] | None = None,
    kpi_weights: dict | None = None,
    sma200_ok: list[bool] | None = None,
    sma200_vals: list[float | None] | None = None,
    sma20_vals: list[float | None] | None = None,
    position_events: list[dict] | None = None,
    position_events_by_strategy: dict[str, list[dict]] | None = None,
    c3_states_by_strategy: dict[str, dict] | None = None,
) -> dict:
    """
    Convert an enriched DataFrame into a JSON-serialisable dict.

    The output is consumed by ``chart_builder.js`` in the browser.
    """
    if df is None or df.empty:
        return {}

    weekly_shifted = False
    if timeframe.upper() in ("1W", "2W") and not df.empty:
        idx = pd.to_datetime(df.index)
        if len(idx) > 0 and pd.Series(idx.dayofweek).mode().iloc[0] == 4:
            shift = pd.DateOffset(days=4)
            df = df.copy()
            df.index = idx - shift
            weekly_shifted = True
            if precomputed_kpi_state is not None:
                shifted = {}
                for k, s in precomputed_kpi_state.items():
                    sc = s.copy()
                    sc.index = pd.to_datetime(sc.index) - shift
                    shifted[k] = sc
                precomputed_kpi_state = shifted

    x = [str(t)[:23] for t in df.index]

    # All DataFrame columns
    columns: dict[str, list] = {}
    for col in df.columns:
        try:
            columns[col] = _series_to_list(df[col])
        except Exception as exc:
            logger.debug("Failed to serialize column %s for %s/%s: %s", col, symbol, timeframe, exc)
            pass

    # KPI timeline matrix
    kpi: dict = {}
    try:
        from apps.dashboard.figures import compute_kpi_timeline_matrix
        kpi = compute_kpi_timeline_matrix(df, precomputed_state=precomputed_kpi_state)
    except Exception:
        logger.warning("Could not compute KPI timeline matrix for %s/%s", symbol, timeframe)

    # Build strategy→KPI mapping for front-end filtering
    strategy_kpis: dict[str, list[str]] = {}
    try:
        from trading_dashboard.indicators.registry import get_kpi_trend_order, get_strategies
        for strat in get_strategies():
            strategy_kpis[strat] = get_kpi_trend_order(strat)
    except Exception as exc:
        logger.warning("Failed to load strategy KPI registry: %s", exc)

    out: dict = {
        "symbol": symbol,
        "timeframe": timeframe,
        "display_name": display_name or "",
        "weekly_shifted": weekly_shifted,
        "x": x,
        "c": columns,
        "kpi": kpi,
        "combo_3_kpis": combo_3_kpis or [],
        "combo_4_kpis": combo_4_kpis or [],
        "kpi_weights": kpi_weights or {},
        "strategy_kpis": strategy_kpis,
    }
    if sma200_ok is not None:
        out["sma200_ok"] = sma200_ok
        out["sma20_ok"] = sma200_ok   # v5: same array, now SMA20>=SMA200
    if sma200_vals is not None:
        out["sma200_vals"] = sma200_vals
    if sma20_vals is not None:
        out["sma20_vals"] = sma20_vals
    if position_events is not None:
        out["position_events"] = position_events
    if position_events_by_strategy:
        out["position_events_by_strategy"] = position_events_by_strategy
    if c3_states_by_strategy:
        out["c3_states_by_strategy"] = c3_states_by_strategy
    return out


def export_symbol_data_json(
    symbol: str,
    timeframe: str,
    df: pd.DataFrame,
    **kwargs,
) -> str:
    """Return compact JSON string for a single symbol/timeframe."""
    d = export_symbol_data(symbol, timeframe, df, **kwargs)
    return json.dumps(d, separators=(",", ":"), allow_nan=False)


def write_symbol_data_asset(
    out_dir: Path,
    sym_dir: str,
    timeframe: str,
    data_json: str,
    *,
    key: str = "",
) -> Path:
    """Write the data JSON as a .js asset file (same pattern as old figure assets)."""
    p = out_dir / sym_dir / f"{timeframe}.js"
    p.parent.mkdir(parents=True, exist_ok=True)
    js = (
        "window.TD_ASSET_PAYLOADS = window.TD_ASSET_PAYLOADS || {};\n"
        f"window.TD_ASSET_PAYLOADS[{json.dumps(key)}] = {data_json};\n"
    )
    p.write_text(js, encoding="utf-8")
    _gzip_file(p)
    return p

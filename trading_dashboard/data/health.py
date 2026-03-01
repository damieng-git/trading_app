"""Data quality checks for OHLCV DataFrames."""

from __future__ import annotations

import pandas as pd


def summarize_df_health(
    df: pd.DataFrame,
    *,
    tf: str | None = None,
    min_bars: int | None = None,
    max_missing_close_pct: float | None = None,
    max_missing_volume_pct: float | None = None,
) -> dict:
    """Return a dict summarising data quality for *df*."""
    if df is None or df.empty:
        return {
            "bars": 0,
            "start": None,
            "end": None,
            "missing_close_pct": None,
            "missing_volume_pct": None,
            "warnings": ["empty_dataframe"],
            "ok": False,
        }
    idx = df.index
    start = pd.to_datetime(idx.min()).isoformat() if len(idx) else None
    end = pd.to_datetime(idx.max()).isoformat() if len(idx) else None
    close = df["Close"] if "Close" in df.columns else None
    vol = df["Volume"] if "Volume" in df.columns else None
    missing_close_pct = float(close.isna().mean() * 100.0) if close is not None else None
    missing_volume_pct = float(vol.isna().mean() * 100.0) if vol is not None else None
    bars = int(len(df))

    warns: list[str] = []
    if min_bars is not None and bars < int(min_bars):
        warns.append(f"too_short({tf or 'tf'}): {bars} < {int(min_bars)} bars")
    if max_missing_close_pct is not None and missing_close_pct is not None and missing_close_pct > float(max_missing_close_pct):
        warns.append(f"missing_close_pct: {missing_close_pct:.1f}% > {float(max_missing_close_pct):.1f}%")
    if max_missing_volume_pct is not None and missing_volume_pct is not None and missing_volume_pct > float(max_missing_volume_pct):
        warns.append(f"missing_volume_pct: {missing_volume_pct:.1f}% > {float(max_missing_volume_pct):.1f}%")

    return {
        "bars": bars,
        "start": start,
        "end": end,
        "missing_close_pct": missing_close_pct,
        "missing_volume_pct": missing_volume_pct,
        "warnings": warns,
        "ok": (bars > 0 and len(warns) == 0),
    }

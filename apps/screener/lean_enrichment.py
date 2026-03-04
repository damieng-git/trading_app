"""Lean indicator enrichment for daily screener.

Computes *only* the 5 indicators required for 1D C3/C4 detection plus
SMA200 and SMA20 (for the v6 SMA20>SMA200 entry gate).

1D C3: Nadaraya-Watson Smoother, Madrid Ribbon, Volume > MA20  (unchanged in v6)
1D C4: Nadaraya-Watson Smoother, Madrid Ribbon, GK Trend Ribbon, cRSI  (unchanged in v6)
Entry gates: SMA20 > SMA200, Volume spike 1.5× (uses Vol_MA20 already computed)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_NW_DEFAULT_BANDWIDTH = 8.0
_NW_WINDOW = 500


def _load_indicator_params(key: str, config_path: Path | None) -> Dict[str, Any]:
    """Load per-indicator parameter overrides from indicator_config.json."""
    if config_path is None or not config_path.exists():
        return {}
    try:
        import json
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        node = raw.get(key, {})
        if not isinstance(node, dict):
            return {}
        params = node.get("params", node)
        return params if isinstance(params, dict) else {}
    except Exception:
        return {}


def compute_lean_indicators(
    df: pd.DataFrame,
    *,
    indicator_config_path: Path | None = None,
) -> pd.DataFrame:
    """Enrich an OHLCV DataFrame with only the indicators needed for 1D C3/C4.

    Returns a new DataFrame with the original OHLCV columns plus indicator columns.
    """
    from trading_dashboard.indicators import crsi, gk_trend_ribbon, madrid_ma_ribbon_state
    from trading_dashboard.indicators.nadaraya_watson import _nw_kernel, nwe_color_and_arrows

    cfg = indicator_config_path

    base_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    out = df[base_cols].copy()

    # ── 1. Nadaraya-Watson Smoother ──────────────────────────────────────
    p = _load_indicator_params("NW_LuxAlgo", cfg)
    nw_bw = float(p.get("bandwidth", _NW_DEFAULT_BANDWIDTH))
    nw_win = int(p.get("window", _NW_WINDOW))

    close_arr = out["Close"].to_numpy(dtype=float)
    nw_yhat_rp = _nw_kernel(close_arr, bandwidth=nw_bw, window=nw_win, repaint=True)
    nw_yhat_ep = _nw_kernel(close_arr, bandwidth=nw_bw, window=nw_win, repaint=False)

    nw_rp_series = pd.Series(nw_yhat_rp, index=out.index)
    nw_ep_series = pd.Series(nw_yhat_ep, index=out.index)
    nw_value = nw_rp_series.where(nw_rp_series.notna(), nw_ep_series)

    use_rp = nw_rp_series.notna()
    extras_rp = nwe_color_and_arrows(nw_value, forward_diff=True)
    extras_ep = nwe_color_and_arrows(nw_value, forward_diff=False)
    rp_color = extras_rp["NW_color"].copy()
    if len(rp_color) > 0:
        rp_color.iloc[-1] = extras_ep["NW_color"].iloc[-1]

    out["NW_LuxAlgo_color"] = pd.Series(
        np.where(use_rp, rp_color, extras_ep["NW_color"]), index=out.index
    )

    # ── 2. Madrid Ribbon ─────────────────────────────────────────────────
    p = _load_indicator_params("MadridRibbon", cfg)
    mm = madrid_ma_ribbon_state(out, exponential=bool(p.get("exponential", True)))
    out = out.join(mm)

    # ── 3. Volume > MA20 ────────────────────────────────────────────────
    if "Volume" in out.columns:
        p = _load_indicator_params("VOL_MA", cfg)
        vol_len = int(p.get("length", 20))
        out["Vol_MA20"] = out["Volume"].rolling(window=vol_len, min_periods=vol_len).mean()
        out["Vol_gt_MA20"] = (out["Volume"] > out["Vol_MA20"]).fillna(False)

    # ── 4. GK Trend Ribbon ───────────────────────────────────────────────
    p = _load_indicator_params("GK_Trend", cfg)
    gk_zl, gk_upper, gk_lower, gk_trend = gk_trend_ribbon(
        out,
        length=int(p.get("length", 200)),
        band_mult=float(p.get("band_mult", 2.0)),
        atr_length=int(p.get("atr_length", 21)),
        confirm_bars=int(p.get("confirm_bars", 2)),
    )
    out["GK_trend"] = gk_trend

    # ── 5. cRSI ──────────────────────────────────────────────────────────
    p = _load_indicator_params("cRSI", cfg)
    cr = crsi(
        out["Close"],
        domcycle=int(p.get("domcycle", 20)),
        vibration=int(p.get("vibration", 10)),
        leveling=float(p.get("leveling", 10.0)),
    )
    out = out.join(cr)

    # ── 6. SMA200 + SMA20 (v5 entry gate: SMA20 > SMA200) ──────────────
    out["SMA200"] = out["Close"].rolling(window=200, min_periods=200).mean()
    out["SMA20"] = out["Close"].rolling(window=20, min_periods=20).mean()

    return out

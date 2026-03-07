"""Lean indicator enrichment for daily screener.

Computes *only* the 5 indicators required for 1D C3/C4 detection plus
SMA200 and SMA20 (for the SMA20>SMA200 entry gate).

1D C3: Nadaraya-Watson Smoother, Madrid Ribbon, Volume > MA20
1D C4: Nadaraya-Watson Smoother, Madrid Ribbon, GK Trend Ribbon, cRSI
Entry gates: SMA20 > SMA200, Volume spike 1.5× (uses Vol_MA20 already computed)

Optional extra_kpis extend the default set for strategy-specific scanning:
  "DEMA"      → DEMA_9 column
  "ADX & DI"  → DI_plus, DI_minus columns
  "WT_LB"     → WT_LB_wt1, WT_LB_wt2 columns
  "SQZMOM_LB" → SQZ_val column
  "Stoch_MTM" → SMI, SMI_ema columns
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
    extra_kpis: list[str] | None = None,
) -> pd.DataFrame:
    """Enrich an OHLCV DataFrame with only the indicators needed for C3/C4 detection.

    ``extra_kpis`` lists additional KPI names (e.g. ``["DEMA", "ADX & DI"]``) whose
    indicator columns are appended beyond the default 5-indicator set.

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

    # ── 6. SMA200 + SMA20 (entry gate: SMA20 > SMA200) ──────────────────
    out["SMA200"] = out["Close"].rolling(window=200, min_periods=200).mean()
    out["SMA20"] = out["Close"].rolling(window=20, min_periods=20).mean()

    # ── 7. Optional extra KPIs for strategy-specific scanning ─────────────
    _extra = set(extra_kpis) if extra_kpis else set()

    if "DEMA" in _extra:
        from trading_dashboard.indicators import dema as _dema
        p = _load_indicator_params("DEMA", cfg)
        out["DEMA_9"] = _dema(out["Close"], int(p.get("length", 9)))

    if "ADX & DI" in _extra:
        from trading_dashboard.indicators import adx_di as _adx_di
        p = _load_indicator_params("ADX_DI", cfg)
        adx_val, di_p, di_m = _adx_di(out, length=int(p.get("length", 14)))
        out["ADX"] = adx_val
        out["DI_plus"] = di_p
        out["DI_minus"] = di_m

    if "WT_LB" in _extra:
        from trading_dashboard.indicators import wavetrend_lazybear as _wt
        p = _load_indicator_params("WT_LB", cfg)
        wt1, wt2, wt_hist = _wt(out, n1=int(p.get("n1", 10)), n2=int(p.get("n2", 21)))
        out["WT_LB_wt1"] = wt1
        out["WT_LB_wt2"] = wt2
        out["WT_LB_hist"] = wt_hist

    if "SQZMOM_LB" in _extra and "Volume" in out.columns:
        from trading_dashboard.indicators import squeeze_momentum_lazybear as _sqz
        p = _load_indicator_params("SQZMOM_LB", cfg)
        sqz = _sqz(
            out,
            length=int(p.get("length", 20)),
            mult=float(p.get("mult", 2.0)),
            length_kc=int(p.get("length_kc", 20)),
            mult_kc=float(p.get("mult_kc", 1.5)),
            use_true_range=bool(p.get("use_true_range", True)),
        )
        out["SQZ_val"] = sqz["SQZ_val"]

    if "Stoch_MTM" in _extra:
        from trading_dashboard.indicators import stoch_momentum_index as _smi
        p = _load_indicator_params("SMI", cfg)
        smi_val, smi_ema = _smi(
            out,
            a=int(p.get("a", 10)),
            b=int(p.get("b", 3)),
            c=int(p.get("c", 10)),
            smooth_period=int(p.get("smooth_period", 5)),
        )
        out["SMI"] = smi_val
        out["SMI_ema"] = smi_ema

    return out

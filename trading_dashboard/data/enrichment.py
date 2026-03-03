"""
Indicator enrichment pipeline.

Computes all registered indicators on an OHLCV DataFrame and returns
the enriched DataFrame + a list of IndicatorSpec metadata objects.
"""

from __future__ import annotations

import functools
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from trading_dashboard.indicators import (
    adx_di,
    atr_stop_loss_finder,
    bollinger_bands,
    breakout_targets,
    cci_chop_bb,
    crsi,
    donchian_trend_ribbon,
    dema,
    gk_trend_ribbon,
    ichimoku,
    impulse_trend_levels,
    luxalgo_normalized,
    madrid_ma_ribbon_state,
    gmma,
    macd,
    ma_ribbon,
    mansfield_relative_strength,
    nadaraya_watson_endpoint,
    nadaraya_watson_envelope_luxalgo,
    nadaraya_watson_envelope_luxalgo_std,
    nadaraya_watson_repainting,
    nwe_color_and_arrows,
    obv_oscillator,
    obv_oscillator_dual_ema,
    parabolic_sar,
    price_action_index,
    risk_indicator,
    squeeze_momentum_lazybear,
    stoch_momentum_index,
    supertrend,
    rsi_strength_consolidation_zeiierman,
    sr_breaks_retests,
    turtle_trade_channels,
    ut_bot_alert,
    wavetrend_lazybear,
    wt_mtf_signal,
)
from trading_dashboard.data.downloader import load_benchmark_close

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# IndicatorSpec — lightweight metadata returned alongside enriched DataFrames
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IndicatorSpec:
    """Lightweight metadata for an indicator: key, title, overlay flag, and output column names."""
    key: str
    title: str
    overlay: bool
    columns: List[str]


# ---------------------------------------------------------------------------
# Indicator config loading
# ---------------------------------------------------------------------------

NW_DEFAULT_BANDWIDTH = 8.0
NW_WINDOW = 500


def load_indicator_config_json(path: Path, *, fallback_research_dir: Path | None = None) -> Dict[str, Any]:
    """
    Load indicator parameter overrides from JSON.

    Tolerates both ``{"key": {"params": {...}}}`` and ``{"key": {...}}`` shapes.
    """
    try:
        if not path.exists():
            if fallback_research_dir is not None and str(path.name or "").startswith("indicator_config_optimised_"):
                alt = fallback_research_dir / "research" / "indicator_config_optimiser" / "configs" / path.name
                if alt.exists():
                    path = alt
                else:
                    return {}
            else:
                return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


@functools.lru_cache(maxsize=8)
def _indicator_config_cached(path_str: str) -> Dict[str, Any]:
    return load_indicator_config_json(Path(path_str))


def _indicator_params(key: str, config_path: Path) -> Dict[str, Any]:
    cfg = _indicator_config_cached(str(config_path))
    node = cfg.get(key, {})
    if not isinstance(node, dict):
        return {}
    params = node.get("params", node)
    return params if isinstance(params, dict) else {}


# ---------------------------------------------------------------------------
# Main enrichment function
# ---------------------------------------------------------------------------

def translate_and_compute_indicators(
    df: pd.DataFrame,
    *,
    indicator_config_path: Path | None = None,
    cache_dir: Path | None = None,
    feature_store_dir: Path | None = None,
    timeframe: str = "1D",
    symbol: str = "",
    sector_info: dict | None = None,
) -> Tuple[pd.DataFrame, List[IndicatorSpec]]:
    """
    Compute all translated indicators on an OHLCV DataFrame.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV DataFrame (must have Open, High, Low, Close; Volume optional).
    indicator_config_path : Path, optional
        Path to indicator_config.json with parameter overrides.
    cache_dir : Path, optional
        Directory to search for benchmark CSVs (for Mansfield RS).
    feature_store_dir : Path, optional
        Directory to search for enriched CSVs (fallback benchmark search).

    Returns
    -------
    (enriched_df, specs) — the enriched DataFrame and list of IndicatorSpec.
    """
    # Use a no-op config path if none provided
    _cfg_path = indicator_config_path or Path("/dev/null")

    def _p(key: str) -> Dict[str, Any]:
        return _indicator_params(key, _cfg_path)

    base_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    out = df[base_cols].copy()
    specs: List[IndicatorSpec] = []

    # --- WaveTrend [LazyBear] ---
    p = _p("WT_LB")
    wt1, wt2, wt_hist = wavetrend_lazybear(out, n1=int(p.get("n1", 10)), n2=int(p.get("n2", 21)))
    out["WT_LB_wt1"] = wt1
    out["WT_LB_wt2"] = wt2
    out["WT_LB_hist"] = wt_hist
    specs.append(IndicatorSpec(key="WT_LB", title="WaveTrend [LazyBear] (WT_LB)", overlay=False, columns=["WT_LB_wt1", "WT_LB_wt2", "WT_LB_hist"]))

    # --- DEMA ---
    p = _p("DEMA")
    out["DEMA_9"] = dema(out["Close"], int(p.get("length", 9)))
    specs.append(IndicatorSpec(key="DEMA", title="Double EMA (DEMA, 9)", overlay=True, columns=["DEMA_9"]))

    # --- Parabolic SAR ---
    p = _p("PSAR")
    out["PSAR"] = parabolic_sar(out, start=float(p.get("start", 0.02)), increment=float(p.get("increment", 0.02)), maximum=float(p.get("maximum", 0.2)))
    specs.append(IndicatorSpec(key="PSAR", title="Parabolic SAR (CM_P-SAR defaults)", overlay=True, columns=["PSAR"]))

    # --- ATR Stop Loss Finder ---
    p = _p("ATR")
    atr_sl = atr_stop_loss_finder(out, length=int(p.get("length", 14)), smoothing=str(p.get("smoothing", "RMA")), mult=float(p.get("mult", 1.5)))
    out = out.join(atr_sl)
    specs.append(IndicatorSpec(key="ATR", title="ATR Stop Loss Finder", overlay=True, columns=["ATR_mult", "ATR_short_stop", "ATR_long_stop"]))

    # --- Bollinger Bands ---
    p = _p("BB")
    vol = out["Volume"] if "Volume" in out.columns else None
    bb_basis, bb_upper, bb_lower = bollinger_bands(out["Close"], length=int(p.get("length", 20)), mult=float(p.get("mult", 2.0)), ma_type=str(p.get("ma_type", "SMA")), volume=vol)
    out["BB_basis"] = bb_basis
    out["BB_upper"] = bb_upper
    out["BB_lower"] = bb_lower
    specs.append(IndicatorSpec(key="BB", title="Bollinger Bands (20, 2.0)", overlay=True, columns=["BB_basis", "BB_upper", "BB_lower"]))

    # --- SuperTrend ---
    p = _p("SuperTrend")
    st_line, st_trend, _st_atr = supertrend(out, periods=int(p.get("periods", 12)), multiplier=float(p.get("multiplier", 3.0)), change_atr_method=bool(p.get("change_atr_method", True)))
    out["SuperTrend_line"] = st_line
    out["SuperTrend_trend"] = st_trend
    out["SuperTrend_buy"] = (st_trend == 1) & (st_trend.shift(1) == -1)
    out["SuperTrend_sell"] = (st_trend == -1) & (st_trend.shift(1) == 1)
    specs.append(IndicatorSpec(key="SuperTrend", title="SuperTrend (12, 3.0)", overlay=True, columns=["SuperTrend_line", "SuperTrend_trend", "SuperTrend_buy", "SuperTrend_sell"]))

    # --- UT Bot Alerts ---
    p = _p("UT_Bot")
    ut = ut_bot_alert(out, a=float(p.get("a", 1.0)), c=int(p.get("c", 10)))
    out = out.join(ut)
    specs.append(IndicatorSpec(key="UT_Bot", title="UT Bot Alerts", overlay=True, columns=["UT_trailing_stop", "UT_pos", "UT_buy", "UT_sell"]))

    # --- Turtle Trade Channels ---
    p = _p("TuTCI")
    tutci = turtle_trade_channels(out, length=int(p.get("length", 20)), exit_length=int(p.get("exit_length", 10)))
    out = out.join(tutci)
    specs.append(IndicatorSpec(key="TuTCI", title="Turtle Trade Channels", overlay=True, columns=["TuTCI_upper", "TuTCI_lower", "TuTCI_trend", "TuTCI_exit"]))

    # --- GMMA ---
    out = out.join(gmma(out))
    specs.append(IndicatorSpec(key="GMMA", title="GMMA (EMAs)", overlay=True, columns=[c for c in out.columns if c.startswith("GMMA_ema_")]))

    # --- MA Ribbon ---
    _ma_ribbon_lens = (4, 10, 20, 40) if timeframe.upper() == "1W" else (20, 50, 100, 200)
    out = out.join(ma_ribbon(out, lengths=_ma_ribbon_lens))
    specs.append(IndicatorSpec(key="MA_Ribbon", title="MA Ribbon (4 MAs)", overlay=True, columns=["MA_Ribbon_ma1", "MA_Ribbon_ma2", "MA_Ribbon_ma3", "MA_Ribbon_ma4"]))

    # --- Nadaraya-Watson Envelopes (MAE + STD + Repainting) ---
    # Compute the NW kernel ONCE and derive all three envelope variants from it.
    # Cap window at 250 for 4H — Gaussian weights beyond ~200 are negligible
    # (weight at idx 200 with bw=8: exp(-200²/128) ≈ 0), and 4H has 1,500–2,100
    # bars making window=500 disproportionately expensive for no signal benefit.
    p_mae = _p("NWE_Envelope_MAE") or _p("NWE_Envelope")
    nwe_bw = float(p_mae.get("bandwidth", NW_DEFAULT_BANDWIDTH))
    nwe_win = int(p_mae.get("window", NW_WINDOW))
    if timeframe.upper() == "4H":
        nwe_win = min(nwe_win, 250)
    nwe_mult = float(p_mae.get("mult", 3.0))

    from trading_dashboard.indicators.nadaraya_watson import _nw_kernel  # shared kernel
    _close_arr = out["Close"].to_numpy(dtype=float)
    _nw_yhat = _nw_kernel(_close_arr, bandwidth=nwe_bw, window=nwe_win, repaint=False)
    _nw_yhat_rp = _nw_kernel(_close_arr, bandwidth=nwe_bw, window=nwe_win, repaint=True)

    # MAE bands (non-repainting)
    _mae = np.abs(_close_arr - _nw_yhat)
    _mae_avg = pd.Series(_mae, index=out.index).rolling(window=nwe_win, min_periods=1).mean().to_numpy()
    out["NWE_MAE_env_mid"] = pd.Series(_nw_yhat, index=out.index)
    out["NWE_MAE_env_upper"] = pd.Series(_nw_yhat + nwe_mult * _mae_avg, index=out.index)
    out["NWE_MAE_env_lower"] = pd.Series(_nw_yhat - nwe_mult * _mae_avg, index=out.index)
    out["NWE_MAE_env_crossunder"] = (out["Close"] < out["NWE_MAE_env_lower"]) & (out["Close"].shift(1) >= out["NWE_MAE_env_lower"].shift(1))
    out["NWE_MAE_env_crossover"] = (out["Close"] > out["NWE_MAE_env_upper"]) & (out["Close"].shift(1) <= out["NWE_MAE_env_upper"].shift(1))
    specs.append(IndicatorSpec(key="NWE_Envelope_MAE", title="Nadaraya-Watson Envelope (MAE bands)", overlay=True, columns=["NWE_MAE_env_mid", "NWE_MAE_env_upper", "NWE_MAE_env_lower", "NWE_MAE_env_crossover", "NWE_MAE_env_crossunder"]))

    # STD bands (non-repainting, same kernel)
    _residuals = _close_arr - _nw_yhat
    _std = pd.Series(_residuals, index=out.index).rolling(window=nwe_win, min_periods=1).std(ddof=0).to_numpy()
    p_std = _p("NWE_Envelope_STD") or p_mae
    nwe_std_mult = float(p_std.get("mult", nwe_mult))
    out["NWE_STD_env_mid"] = pd.Series(_nw_yhat, index=out.index)
    out["NWE_STD_env_upper"] = pd.Series(_nw_yhat + nwe_std_mult * _std, index=out.index)
    out["NWE_STD_env_lower"] = pd.Series(_nw_yhat - nwe_std_mult * _std, index=out.index)
    out["NWE_STD_env_crossunder"] = (out["Close"] < out["NWE_STD_env_lower"]) & (out["Close"].shift(1) >= out["NWE_STD_env_lower"].shift(1))
    out["NWE_STD_env_crossover"] = (out["Close"] > out["NWE_STD_env_upper"]) & (out["Close"].shift(1) <= out["NWE_STD_env_upper"].shift(1))
    specs.append(IndicatorSpec(key="NWE_Envelope_STD", title="Nadaraya-Watson Envelope (STD bands)", overlay=True, columns=["NWE_STD_env_mid", "NWE_STD_env_upper", "NWE_STD_env_lower", "NWE_STD_env_crossover", "NWE_STD_env_crossunder"]))

    # Repainting bands (repainting kernel, MAE error)
    p_rp = _p("NWE_Envelope_RP") or p_mae
    nwe_rp_mult = float(p_rp.get("mult", nwe_mult))
    _mae_rp = np.abs(_close_arr - _nw_yhat_rp)
    _mae_rp_avg = pd.Series(_mae_rp, index=out.index).rolling(window=nwe_win, min_periods=1).mean().to_numpy()
    out["NWE_RP_env_mid"] = pd.Series(_nw_yhat_rp, index=out.index)
    out["NWE_RP_env_upper"] = pd.Series(_nw_yhat_rp + nwe_rp_mult * _mae_rp_avg, index=out.index)
    out["NWE_RP_env_lower"] = pd.Series(_nw_yhat_rp - nwe_rp_mult * _mae_rp_avg, index=out.index)
    out["NWE_RP_env_crossunder"] = (out["Close"] < out["NWE_RP_env_lower"]) & (out["Close"].shift(1) >= out["NWE_RP_env_lower"].shift(1))
    out["NWE_RP_env_crossover"] = (out["Close"] > out["NWE_RP_env_upper"]) & (out["Close"].shift(1) <= out["NWE_RP_env_upper"].shift(1))
    specs.append(IndicatorSpec(key="NWE_Envelope_RP", title="Nadaraya-Watson Envelope (repainting, TradingView-like)", overlay=True, columns=["NWE_RP_env_mid", "NWE_RP_env_upper", "NWE_RP_env_lower", "NWE_RP_env_crossover", "NWE_RP_env_crossunder"]))

    # --- Nadaraya-Watson Smoothers [LuxAlgo] ---
    # Reuse the kernels already computed above (_nw_yhat, _nw_yhat_rp) if
    # bandwidth/window match the envelope config.  Otherwise compute fresh.
    p = _p("NW_LuxAlgo")
    nw_bw = float(p.get("bandwidth", NW_DEFAULT_BANDWIDTH))
    nw_win = int(p.get("window", NW_WINDOW))
    if nw_bw == nwe_bw and nw_win == nwe_win:
        nwe_repainting = pd.Series(_nw_yhat_rp, index=out.index)
        nwe_endpoint = pd.Series(_nw_yhat, index=out.index)
    else:
        nwe_repainting = nadaraya_watson_repainting(out["Close"], bandwidth=nw_bw, window=nw_win)
        nwe_endpoint = nadaraya_watson_endpoint(out["Close"], bandwidth=nw_bw, window=nw_win)
    out["NW_LuxAlgo_repainting"] = nwe_repainting
    out["NW_LuxAlgo_endpoint"] = nwe_endpoint
    out["NW_LuxAlgo_value"] = out["NW_LuxAlgo_repainting"].where(out["NW_LuxAlgo_repainting"].notna(), out["NW_LuxAlgo_endpoint"])

    use_repainting = out["NW_LuxAlgo_repainting"].notna()
    extras_repaint = nwe_color_and_arrows(out["NW_LuxAlgo_value"], forward_diff=True)
    extras_endpoint = nwe_color_and_arrows(out["NW_LuxAlgo_value"], forward_diff=False)
    repaint_color = extras_repaint["NW_color"].copy()
    if len(repaint_color) > 0:
        repaint_color.iloc[-1] = extras_endpoint["NW_color"].iloc[-1]
    out["NW_LuxAlgo_color"] = pd.Series(np.where(use_repainting, repaint_color, extras_endpoint["NW_color"]), index=out.index)
    out["NW_LuxAlgo_arrow_up"] = pd.Series(np.where(use_repainting, extras_repaint["NW_arrow_up"], extras_endpoint["NW_arrow_up"]), index=out.index).astype(bool)
    out["NW_LuxAlgo_arrow_down"] = pd.Series(np.where(use_repainting, extras_repaint["NW_arrow_down"], extras_endpoint["NW_arrow_down"]), index=out.index).astype(bool)
    specs.append(IndicatorSpec(key="NW_LuxAlgo", title="Nadaraya-Watson Smoothers [LuxAlgo] (TradingView default = repainting)", overlay=True, columns=["NW_LuxAlgo_value", "NW_LuxAlgo_repainting", "NW_LuxAlgo_endpoint", "NW_LuxAlgo_color", "NW_LuxAlgo_arrow_up", "NW_LuxAlgo_arrow_down"]))

    # --- ADX & DI ---
    p = _p("ADX_DI")
    adx, di_p, di_m = adx_di(out, length=int(p.get("length", 14)))
    out["ADX"] = adx
    out["DI_plus"] = di_p
    out["DI_minus"] = di_m
    specs.append(IndicatorSpec(key="ADX_DI", title="ADX & DI (14)", overlay=False, columns=["ADX", "DI_plus", "DI_minus"]))

    # --- Donchian Ribbon ---
    p = _p("DonchianRibbon")
    don = donchian_trend_ribbon(out, dlen=int(p.get("dlen", 20)), depth=int(p.get("depth", 10)))
    out = out.join(don)
    specs.append(IndicatorSpec(key="DonchianRibbon", title="Donchian Trend Ribbon (trend state)", overlay=False, columns=list(don.columns)))

    # --- Madrid Ribbon ---
    p = _p("MadridRibbon")
    mm = madrid_ma_ribbon_state(out, exponential=bool(p.get("exponential", True)))
    out = out.join(mm)
    mmarb_cols = [c for c in mm.columns if c.startswith("MMARB_state_")]
    specs.append(IndicatorSpec(key="MadridRibbon", title="Madrid MA Ribbon Bar v2 (MMARB)", overlay=False, columns=["MMARB_ma05", "MMARB_ma100"] + mmarb_cols))

    # --- OBV Oscillator ---
    if "Volume" in out.columns:
        p = _p("OBVOSC")
        obv, obv_osc = obv_oscillator(out, length=int(p.get("length", 20)))
        out["OBV"] = obv
        out["OBV_osc"] = obv_osc
        specs.append(IndicatorSpec(key="OBVOSC", title="OBV Oscillator (20)", overlay=False, columns=["OBV_osc"]))

    # --- Squeeze Momentum ---
    if "Volume" in out.columns:
        p = _p("SQZMOM_LB")
        sqz = squeeze_momentum_lazybear(out, length=int(p.get("length", 20)), mult=float(p.get("mult", 2.0)), length_kc=int(p.get("length_kc", 20)), mult_kc=float(p.get("mult_kc", 1.5)), use_true_range=bool(p.get("use_true_range", True)))
        out["SQZ_val"] = sqz["SQZ_val"]
        out["SQZ_on"] = sqz["SQZ_on"]
        out["SQZ_off"] = sqz["SQZ_off"]
        out["SQZ_no"] = sqz["SQZ_no"]
        out["SQZ_bcolor"] = sqz["SQZ_bcolor"]
        out["SQZ_scolor"] = sqz["SQZ_scolor"]
        specs.append(IndicatorSpec(key="SQZMOM_LB", title="SQZMOM_LB", overlay=False, columns=["SQZ_val", "SQZ_on", "SQZ_off", "SQZ_no", "SQZ_bcolor", "SQZ_scolor"]))

    # --- Stochastic Momentum Index ---
    p = _p("SMI")
    smi, smi_ema = stoch_momentum_index(out, a=int(p.get("a", 10)), b=int(p.get("b", 3)), c=int(p.get("c", 10)), smooth_period=int(p.get("smooth_period", 5)))
    out["SMI"] = smi
    out["SMI_ema"] = smi_ema
    specs.append(IndicatorSpec(key="SMI", title="Stochastic Momentum Index", overlay=False, columns=["SMI", "SMI_ema"]))

    # --- MACD ---
    p = _p("MACD")
    macd_line, macd_sig, macd_hist = macd(out["Close"], fast=int(p.get("fast", 12)), slow=int(p.get("slow", 26)), signal=int(p.get("signal", 9)))
    out["MACD"] = macd_line
    out["MACD_signal"] = macd_sig
    out["MACD_hist"] = macd_hist
    specs.append(IndicatorSpec(key="MACD", title="MACD (12, 26, 9)", overlay=False, columns=["MACD", "MACD_signal", "MACD_hist"]))

    # --- Volume + MA20 ---
    if "Volume" in out.columns:
        p = _p("VOL_MA")
        vol_len = int(p.get("length", 20))
        out["Vol_MA20"] = out["Volume"].rolling(window=vol_len, min_periods=vol_len).mean()
        out["Vol_gt_MA20"] = (out["Volume"] > out["Vol_MA20"]).fillna(False)
        specs.append(IndicatorSpec(key="VOL_MA", title="Volume + MA20", overlay=False, columns=["Volume", "Vol_MA20", "Vol_gt_MA20"]))

    # --- cRSI ---
    p = _p("cRSI")
    cr = crsi(out["Close"], domcycle=int(p.get("domcycle", 20)), vibration=int(p.get("vibration", 10)), leveling=float(p.get("leveling", 10.0)))
    out = out.join(cr)
    specs.append(IndicatorSpec(key="cRSI", title="cRSI (domcycle=20)", overlay=False, columns=["cRSI", "cRSI_lb", "cRSI_ub"]))

    # --- RSI Strength & Consolidation Zones (Zeiierman) ---
    p = _p("RSI Strength & Consolidation Zones (Zeiierman)")
    zei = rsi_strength_consolidation_zeiierman(out, rsi_length=int(p.get("rsi_length", 14)), dmi_length=int(p.get("dmi_length", 14)), adx_smoothing=int(p.get("adx_smoothing", 14)), filter_strength=float(p.get("filter_strength", 0.1)))
    out = out.join(zei)
    specs.append(IndicatorSpec(key="RSI Strength & Consolidation Zones (Zeiierman)", title="RSI Strength & Consolidation Zones (Zeiierman)", overlay=False, columns=list(zei.columns)))

    # --- Ichimoku ---
    p = _p("Ichimoku")
    ichi = ichimoku(out, tenkan_len=int(p.get("tenkan", 9)), kijun_len=int(p.get("kijun", 26)), senkou_b_len=int(p.get("senkou_b", 52)), offset=int(p.get("offset", 26)))
    out = out.join(ichi)
    specs.append(IndicatorSpec(key="Ichimoku", title="Ichimoku Kinkō Hyō", overlay=True, columns=list(ichi.columns)))

    # --- SR Breaks & Retests ---
    p = _p("SR_Breaks")
    _sr_lb_default = 10 if timeframe.upper() == "1W" else 20
    sr = sr_breaks_retests(out, lookback=int(p.get("lookback", _sr_lb_default)), vol_len=int(p.get("vol_len", 2)), box_width=float(p.get("box_width", 1.0)), atr_len=int(p.get("atr_len", 200)))
    out = out.join(sr)
    specs.append(IndicatorSpec(key="SR_Breaks", title="SR Breaks & Retests", overlay=True, columns=list(sr.columns)))

    # --- GK Trend Ribbon ---
    p = _p("GK_Trend")
    _gk_default_len = 40 if timeframe == "1W" else 200
    _gk_default_atr = 10 if timeframe == "1W" else 21
    gk_zl, gk_upper, gk_lower, gk_trend = gk_trend_ribbon(
        out, length=int(p.get("length", _gk_default_len)), band_mult=float(p.get("band_mult", 2.0)),
        atr_length=int(p.get("atr_length", _gk_default_atr)), confirm_bars=int(p.get("confirm_bars", 2)),
    )
    out["GK_zl"] = gk_zl
    out["GK_upper"] = gk_upper
    out["GK_lower"] = gk_lower
    out["GK_trend"] = gk_trend
    specs.append(IndicatorSpec(key="GK_Trend", title="GK Trend Ribbon", overlay=True,
                               columns=["GK_zl", "GK_upper", "GK_lower", "GK_trend"]))

    # --- Impulse Trend Levels ---
    p = _p("Impulse_Trend")
    itl_basis, itl_upper, itl_lower, itl_trend = impulse_trend_levels(
        out, trend_length=int(p.get("trend_length", 19)),
        impulse_lookback=int(p.get("impulse_lookback", 5)),
        decay_rate=float(p.get("decay_rate", 0.99)),
        mad_length=int(p.get("mad_length", 20)),
        band_min=float(p.get("band_min", 1.5)),
        band_max=float(p.get("band_max", 1.9)),
    )
    out["ITL_basis"] = itl_basis
    out["ITL_upper"] = itl_upper
    out["ITL_lower"] = itl_lower
    out["ITL_trend"] = itl_trend
    specs.append(IndicatorSpec(key="Impulse_Trend", title="Impulse Trend Levels", overlay=True,
                               columns=["ITL_basis", "ITL_upper", "ITL_lower", "ITL_trend"]))

    # --- Breakout Targets ---
    p = _p("Breakout_Targets")
    bt_signal, bt_rng_hi, bt_rng_lo = breakout_targets(
        out, range_period=int(p.get("range_period", 99)),
        atr_period=int(p.get("atr_period", 14)),
        sl_mult=float(p.get("sl_mult", 5.0)),
        tp1_mult=float(p.get("tp1_mult", 0.5)),
        tp2_mult=float(p.get("tp2_mult", 1.0)),
        tp3_mult=float(p.get("tp3_mult", 1.5)),
    )
    out["BT_signal"] = bt_signal
    out["BT_range_high"] = bt_rng_hi
    out["BT_range_low"] = bt_rng_lo
    specs.append(IndicatorSpec(key="Breakout_Targets", title="Breakout Targets", overlay=True,
                               columns=["BT_signal", "BT_range_high", "BT_range_low"]))

    # --- Mansfield Relative Strength (national-index benchmark) ---
    # Priority: national index (by ticker suffix) > sector ETF > config default (SPY)
    p = _p("Mansfield_RS")
    _mrs_fallback = str(p.get("benchmark", "^GSPC")).strip().upper() or "^GSPC"
    _mrs_ma_len = int(p.get("ma_len", 52))

    _mrs_candidates: list[str] = []
    if symbol:
        try:
            from apps.dashboard.sector_map import get_national_index
            _mrs_candidates.append(get_national_index(symbol))
        except Exception as exc:
            logger.debug("Failed to get national index for Mansfield RS (symbol=%s): %s", symbol, exc)
            pass
    if sector_info:
        try:
            from apps.dashboard.sector_map import get_benchmark_etf
            _sector_etf = get_benchmark_etf(
                sector_info.get("sector", ""),
                sector_info.get("industry", ""),
                sector_info.get("geo", "US"),
            )
            if _sector_etf:
                _mrs_candidates.append(_sector_etf)
        except Exception as exc:
            logger.debug("Failed to get benchmark ETF for Mansfield RS: %s", exc)
            pass
    if _mrs_fallback not in _mrs_candidates:
        _mrs_candidates.append(_mrs_fallback)

    _mrs_bench_close = None
    _mrs_benchmark_sym = _mrs_fallback
    for _cand in _mrs_candidates:
        try:
            _bc = load_benchmark_close(
                _cand, out.index,
                cache_dir=cache_dir,
                feature_store_dir=feature_store_dir,
            )
            if _bc is not None and not _bc.dropna().empty:
                _mrs_bench_close = _bc
                _mrs_benchmark_sym = _cand
                break
        except Exception as exc:
            logger.debug("Skipping benchmark candidate %s for Mansfield RS: %s", _cand, exc)
            continue

    if _mrs_bench_close is not None:
        mrs = mansfield_relative_strength(out, _mrs_bench_close, ma_len=_mrs_ma_len)
        out = out.join(mrs)
        specs.append(IndicatorSpec(key="Mansfield_RS", title=f"Mansfield RS (vs {_mrs_benchmark_sym})", overlay=False, columns=list(mrs.columns)))
        try:
            out[f"_bench_{_mrs_benchmark_sym}"] = _mrs_bench_close
        except Exception as exc:
            logger.debug("Failed to add benchmark column to Mansfield RS output: %s", exc)
            pass

    # ══════════════════════════════════════════════════════════════════════════
    # Stoof (Band Light) indicators
    # ══════════════════════════════════════════════════════════════════════════

    # BL1: MACD (15, 23, 5) — Pine ta.macd uses EMA for signal line
    p = _p("MACD_BL")
    bl_macd, bl_macd_sig, bl_macd_hist = macd(out["Close"], fast=int(p.get("fast", 15)), slow=int(p.get("slow", 23)), signal=int(p.get("signal", 5)), signal_ma="EMA")
    out["MACD_BL"] = bl_macd
    out["MACD_BL_signal"] = bl_macd_sig
    out["MACD_BL_hist"] = bl_macd_hist
    specs.append(IndicatorSpec(key="MACD_BL", title="MACD (15,23,5) [BL]", overlay=False, columns=["MACD_BL", "MACD_BL_signal", "MACD_BL_hist"]))

    # BL2: WaveTrend (27, 21) — Pine BL uses close, not hlc3
    p = _p("WT_LB_BL")
    bl_wt1, bl_wt2, bl_wt_hist = wavetrend_lazybear(out, n1=int(p.get("n1", 27)), n2=int(p.get("n2", 21)), source="close")
    out["WT_LB_BL_wt1"] = bl_wt1
    out["WT_LB_BL_wt2"] = bl_wt2
    out["WT_LB_BL_hist"] = bl_wt_hist
    specs.append(IndicatorSpec(key="WT_LB_BL", title="WaveTrend (27,21) [BL]", overlay=False, columns=["WT_LB_BL_wt1", "WT_LB_BL_wt2", "WT_LB_BL_hist"]))

    # BL3: OBV Oscillator dual-EMA
    if "Volume" in out.columns:
        p = _p("OBVOSC_BL")
        _obv_bl, _obv_bl_osc = obv_oscillator_dual_ema(out, short_length=int(p.get("short_length", 1)), long_length=int(p.get("long_length", 20)))
        out["OBVOSC_BL_osc"] = _obv_bl_osc
        specs.append(IndicatorSpec(key="OBVOSC_BL", title="OBV Osc Dual-EMA [BL]", overlay=False, columns=["OBVOSC_BL_osc"]))

    # BL4: CCI+Chop+BB v1
    p = _p("CCI_Chop_BB_v1")
    _ccb1_raw, _ccb1_smooth = cci_chop_bb(out, cci_length=int(p.get("cci_length", 18)), chop_length=int(p.get("chop_length", 14)), bb_length=int(p.get("bb_length", 20)), bb_mult=float(p.get("bb_mult", 2.0)), smooth=int(p.get("smooth", 10)))
    out["CCI_Chop_BB_v1_raw"] = _ccb1_raw
    out["CCI_Chop_BB_v1_smooth"] = _ccb1_smooth
    specs.append(IndicatorSpec(key="CCI_Chop_BB_v1", title="CCI+Chop+BB v1 [BL]", overlay=False, columns=["CCI_Chop_BB_v1_raw", "CCI_Chop_BB_v1_smooth"]))

    # BL5: ADX & DI (14) — Pine BL uses ta.rma for ADX smoothing
    p = _p("ADX_DI_BL")
    bl_adx, bl_dip, bl_dim = adx_di(out, length=int(p.get("length", 14)), adx_smoothing="RMA")
    out["ADX_BL"] = bl_adx
    out["DI_plus_BL"] = bl_dip
    out["DI_minus_BL"] = bl_dim
    specs.append(IndicatorSpec(key="ADX_DI_BL", title="ADX & DI (14) [BL]", overlay=False, columns=["ADX_BL", "DI_plus_BL", "DI_minus_BL"]))

    # BL6: LuxAlgo Normalized v1
    p = _p("LuxAlgo_Norm_v1")
    out["LuxAlgo_Norm_v1"] = luxalgo_normalized(out["Close"], length=int(p.get("length", 14)), presmooth=int(p.get("presmooth", 10)), postsmooth=int(p.get("postsmooth", 10)))
    specs.append(IndicatorSpec(key="LuxAlgo_Norm_v1", title="LuxAlgo Normalized v1 [BL]", overlay=False, columns=["LuxAlgo_Norm_v1"]))

    # BL7: Risk Indicator
    p = _p("Risk_Indicator")
    out["Risk_Indicator"] = risk_indicator(out["Close"], sma_period=int(p.get("sma_period", 50)), power_factor=float(p.get("power_factor", 0.395)), initial_atl=float(p.get("initial_atl", 2.5)))
    specs.append(IndicatorSpec(key="Risk_Indicator", title="Risk Indicator [BL]", overlay=False, columns=["Risk_Indicator"]))

    # BL8: LuxAlgo Normalized v2
    p = _p("LuxAlgo_Norm_v2")
    out["LuxAlgo_Norm_v2"] = luxalgo_normalized(out["Close"], length=int(p.get("length", 14)), presmooth=int(p.get("presmooth", 10)), postsmooth=int(p.get("postsmooth", 10)))
    specs.append(IndicatorSpec(key="LuxAlgo_Norm_v2", title="LuxAlgo Normalized v2 [BL]", overlay=False, columns=["LuxAlgo_Norm_v2"]))

    # BL9: CCI+Chop+BB v2
    p = _p("CCI_Chop_BB_v2")
    _ccb2_raw, _ccb2_smooth = cci_chop_bb(out, cci_length=int(p.get("cci_length", 90)), chop_length=int(p.get("chop_length", 24)), bb_length=int(p.get("bb_length", 10)), bb_mult=float(p.get("bb_mult", 2.0)), smooth=int(p.get("smooth", 10)))
    out["CCI_Chop_BB_v2_raw"] = _ccb2_raw
    out["CCI_Chop_BB_v2_smooth"] = _ccb2_smooth
    specs.append(IndicatorSpec(key="CCI_Chop_BB_v2", title="CCI+Chop+BB v2 [BL]", overlay=False, columns=["CCI_Chop_BB_v2_raw", "CCI_Chop_BB_v2_smooth"]))

    # BL10: Price Action Index
    p = _p("PAI")
    out["PAI"] = price_action_index(out, stoch_length=int(p.get("stoch_length", 20)), smooth=int(p.get("smooth", 3)), dispersion_length=int(p.get("dispersion_length", 20)))
    specs.append(IndicatorSpec(key="PAI", title="Price Action Index [BL]", overlay=False, columns=["PAI"]))

    # BL11: WT MTF Signal [PlungerMen] — WT extreme zone + MACD + RSI composite
    # Same-TF MACD fallback; cross-TF MACD applied via apply_mtf_overlay() post-enrichment
    p = _p("WT_MTF")
    _wtm1, _wtm2, _wtm_sig, _wtm_rsi = wt_mtf_signal(
        out,
        mtf_close=None,
        wt_channel_len=int(p.get("wt_channel_len", 27)),
        wt_average_len=int(p.get("wt_average_len", 21)),
        macd_fast=int(p.get("macd_fast", 15)),
        macd_slow=int(p.get("macd_slow", 26)),
        macd_signal_len=int(p.get("macd_signal_len", 12)),
        rsi_len=int(p.get("rsi_len", 16)),
        ob_level1=float(p.get("ob_level1", 60.0)),
        os_level1=float(p.get("os_level1", -60.0)),
        min_bars_in_extreme=int(p.get("min_bars_in_extreme", 2)),
        confirm_window=int(p.get("confirm_window", 1)),
        min_spread=float(p.get("min_spread", 1.5)),
        cooldown_bars=int(p.get("cooldown_bars", 8)),
    )
    out["WT_MTF_wt1"] = _wtm1
    out["WT_MTF_wt2"] = _wtm2
    out["WT_MTF_signal"] = _wtm_sig
    out["WT_MTF_rsi"] = _wtm_rsi
    specs.append(IndicatorSpec(key="WT_MTF", title="WT MTF Signal [PlungerMen]", overlay=False,
                               columns=["WT_MTF_wt1", "WT_MTF_wt2", "WT_MTF_signal", "WT_MTF_rsi"]))

    return out, specs


# ---------------------------------------------------------------------------
# MTF overlay — recompute WT_MTF signal using cross-timeframe MACD
# ---------------------------------------------------------------------------

_MTF_PAIRS: Dict[str, str] = {
    "1M": "4H",
    "2W": "4H",
    "1W": "4H",
    "1D": "4H",
}


def apply_mtf_overlay(
    tf_map: Dict[str, pd.DataFrame],
    *,
    indicator_config_path: Path | None = None,
) -> Dict[str, pd.DataFrame]:
    """
    Post-enrichment pass: re-compute WT_MTF_signal columns using
    MACD from a faster timeframe.

    Modifies DataFrames in *tf_map* in-place and returns the same dict.
    """
    _cfg_path = indicator_config_path or Path("/dev/null")

    def _p(key: str) -> Dict[str, Any]:
        return _indicator_params(key, _cfg_path)

    for slow_tf, fast_tf in _MTF_PAIRS.items():
        slow_df = tf_map.get(slow_tf)
        fast_df = tf_map.get(fast_tf)
        if slow_df is None or fast_df is None:
            continue
        if "Close" not in fast_df.columns or "Close" not in slow_df.columns:
            continue

        p = _p("WT_MTF")
        _wtm1, _wtm2, _wtm_sig, _wtm_rsi = wt_mtf_signal(
            slow_df,
            mtf_close=fast_df["Close"],
            wt_channel_len=int(p.get("wt_channel_len", 27)),
            wt_average_len=int(p.get("wt_average_len", 21)),
            macd_fast=int(p.get("macd_fast", 15)),
            macd_slow=int(p.get("macd_slow", 26)),
            macd_signal_len=int(p.get("macd_signal_len", 12)),
            rsi_len=int(p.get("rsi_len", 16)),
            ob_level1=float(p.get("ob_level1", 60.0)),
            os_level1=float(p.get("os_level1", -60.0)),
            min_bars_in_extreme=int(p.get("min_bars_in_extreme", 2)),
            confirm_window=int(p.get("confirm_window", 1)),
            min_spread=float(p.get("min_spread", 1.5)),
            cooldown_bars=int(p.get("cooldown_bars", 8)),
        )
        slow_df["WT_MTF_wt1"] = _wtm1
        slow_df["WT_MTF_wt2"] = _wtm2
        slow_df["WT_MTF_signal"] = _wtm_sig
        slow_df["WT_MTF_rsi"] = _wtm_rsi

    return tf_map

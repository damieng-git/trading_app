"""
KPI catalog + KPI state computation.

Content migrated from legacy `trading_dashboard/kpi_catalog.py`.
"""

from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from .rules import (
    STATE_BEAR,
    STATE_BULL,
    STATE_NA,
    STATE_NEUTRAL,
    state_from_regime,
    state_from_signals,
)


try:
    from trading_dashboard.indicators.registry import (
        get_kpi_trend_order as _reg_trend,
        get_kpi_breakout_order as _reg_breakout,
    )
    KPI_TREND_ORDER: List[str] = _reg_trend()
    KPI_BREAKOUT_ORDER: List[str] = _reg_breakout()
except Exception:
    KPI_TREND_ORDER: List[str] = [
        # Trend
        "Nadaraya-Watson Smoother",
        "TuTCI",
        "MA Ribbon",
        "Madrid Ribbon",
        "Donchian Ribbon",
        "DEMA",
        "Ichimoku",
        # Momentum
        "WT_LB",
        "SQZMOM_LB",
        "Stoch_MTM",
        "CM_Ult_MacD_MFT",
        "cRSI",
        "ADX & DI",
        "GMMA",
        "RSI Strength & Consolidation Zones (Zeiierman)",
        "OBVOSC_LB",
        # Relative Strength
        "Mansfield RS",
        "SR Breaks",
        # Risk / Exit
        "SuperTrend",
        "UT Bot Alert",
        "CM_P-SAR",
    ]
    KPI_BREAKOUT_ORDER: List[str] = [
        "BB 30",
        "Nadaraya-Watson Envelop (MAE)",
        "Nadaraya-Watson Envelop (STD)",
        "Nadaraya-Watson Envelop (Repainting)",
    ]

KPI_ORDER: List[str] = KPI_TREND_ORDER + KPI_BREAKOUT_ORDER


def compute_kpi_state_map(df: pd.DataFrame, *, stoch_mtm_thresholds: dict | None = None) -> Dict[str, pd.Series]:
    if df is None or df.empty:
        return {}

    idx = df.index
    close = df["Close"] if "Close" in df.columns else None

    state: Dict[str, pd.Series] = {}

    if "NW_LuxAlgo_color" in df.columns:
        cond = df["NW_LuxAlgo_color"].astype(str).str.strip().str.lower().eq("green")
        avail = df["NW_LuxAlgo_color"].notna()
        state["Nadaraya-Watson Smoother"] = state_from_regime(idx, cond, avail)
    else:
        nw = (
            df["NW_LuxAlgo_value"]
            if "NW_LuxAlgo_value" in df.columns
            else (df["NW_LuxAlgo_endpoint"] if "NW_LuxAlgo_endpoint" in df.columns else None)
        )
        cond = (nw >= nw.shift(1)) if nw is not None else None
        avail = (nw.notna() & nw.shift(1).notna()) if nw is not None else None
        state["Nadaraya-Watson Smoother"] = state_from_regime(idx, cond, avail)

    def _nwe_state(prefix: str, name: str) -> None:
        if close is None:
            state[name] = pd.Series(STATE_NA, index=idx, dtype=int)
            return
        upper_col = f"{prefix}_env_upper"
        lower_col = f"{prefix}_env_lower"
        if not all(c in df.columns for c in [upper_col, lower_col]):
            state[name] = pd.Series(STATE_NA, index=idx, dtype=int)
            return
        upper = pd.to_numeric(df[upper_col], errors="coerce")
        lower = pd.to_numeric(df[lower_col], errors="coerce")
        avail = close.notna() & upper.notna() & lower.notna()
        crossunder_col = f"{prefix}_env_crossunder"
        crossover_col = f"{prefix}_env_crossover"
        bull_sig = (
            df[crossunder_col].fillna(False)
            if crossunder_col in df.columns
            else ((close < lower) & (close.shift(1) >= lower.shift(1)))
        )
        bear_sig = (
            df[crossover_col].fillna(False)
            if crossover_col in df.columns
            else ((close > upper) & (close.shift(1) <= upper.shift(1)))
        )
        state[name] = state_from_signals(idx, bull_sig, bear_sig, avail)

    _nwe_state("NWE_MAE", "Nadaraya-Watson Envelop (MAE)")
    _nwe_state("NWE_STD", "Nadaraya-Watson Envelop (STD)")
    _nwe_state("NWE_RP", "Nadaraya-Watson Envelop (Repainting)")

    if close is not None and all(c in df.columns for c in ["BB_upper", "BB_lower"]):
        upper = pd.to_numeric(df["BB_upper"], errors="coerce")
        lower = pd.to_numeric(df["BB_lower"], errors="coerce")
        avail = close.notna() & upper.notna() & lower.notna()
        out = pd.Series(STATE_NEUTRAL, index=idx, dtype=int)
        out.loc[((close < lower) & avail).to_numpy(dtype=bool)] = STATE_BULL
        out.loc[((close > upper) & avail).to_numpy(dtype=bool)] = STATE_BEAR
        out.loc[(~avail).to_numpy(dtype=bool)] = STATE_NA
        state["BB 30"] = out
    else:
        state["BB 30"] = pd.Series(STATE_NA, index=idx, dtype=int)

    if close is not None and "ATR_long_stop" in df.columns:
        stop = pd.to_numeric(df["ATR_long_stop"], errors="coerce")
        avail = close.notna() & stop.notna()
        state["ATR"] = state_from_regime(idx, close >= stop, avail)
    else:
        state["ATR"] = pd.Series(STATE_NA, index=idx, dtype=int)

    if "SuperTrend_trend" in df.columns:
        t = pd.to_numeric(df["SuperTrend_trend"], errors="coerce")
        avail = t.notna()
        state["SuperTrend"] = state_from_regime(idx, t == 1, avail)
    else:
        state["SuperTrend"] = pd.Series(STATE_NA, index=idx, dtype=int)

    if "UT_pos" in df.columns:
        p = pd.to_numeric(df["UT_pos"], errors="coerce")
        avail = p.notna()
        state["UT Bot Alert"] = state_from_regime(idx, p == 1, avail)
    else:
        state["UT Bot Alert"] = pd.Series(STATE_NA, index=idx, dtype=int)

    if close is not None and "TuTCI_trend" in df.columns:
        t = pd.to_numeric(df["TuTCI_trend"], errors="coerce")
        avail = close.notna() & t.notna()
        state["TuTCI"] = state_from_regime(idx, close >= t, avail)
    else:
        state["TuTCI"] = pd.Series(STATE_NA, index=idx, dtype=int)

    short_cols = [
        c
        for c in df.columns
        if c.startswith("GMMA_ema_") and c.split("_")[-1].isdigit() and int(c.split("_")[-1]) in {3, 5, 8, 10, 12, 15}
    ]
    long_cols = [
        c
        for c in df.columns
        if c.startswith("GMMA_ema_") and c.split("_")[-1].isdigit() and int(c.split("_")[-1]) in {30, 35, 40, 45, 50, 60}
    ]
    if short_cols and long_cols:
        smin = pd.to_numeric(df[short_cols].min(axis=1), errors="coerce")
        lmax = pd.to_numeric(df[long_cols].max(axis=1), errors="coerce")
        avail = smin.notna() & lmax.notna()
        state["GMMA"] = state_from_regime(idx, smin > lmax, avail)
    else:
        state["GMMA"] = pd.Series(STATE_NA, index=idx, dtype=int)

    if all(c in df.columns for c in ["MA_Ribbon_ma1", "MA_Ribbon_ma2", "MA_Ribbon_ma3", "MA_Ribbon_ma4"]):
        ma1 = pd.to_numeric(df["MA_Ribbon_ma1"], errors="coerce")
        ma2 = pd.to_numeric(df["MA_Ribbon_ma2"], errors="coerce")
        ma3 = pd.to_numeric(df["MA_Ribbon_ma3"], errors="coerce")
        ma4 = pd.to_numeric(df["MA_Ribbon_ma4"], errors="coerce")
        avail = ma1.notna() & ma2.notna() & ma3.notna() & ma4.notna()
        cond = (ma1 > ma2) & (ma2 > ma3) & (ma3 > ma4)
        state["MA Ribbon"] = state_from_regime(idx, cond, avail)
    else:
        state["MA Ribbon"] = pd.Series(STATE_NA, index=idx, dtype=int)

    if all(c in df.columns for c in ["MMARB_ma05", "MMARB_ma100"]):
        ma05 = pd.to_numeric(df["MMARB_ma05"], errors="coerce")
        ma100 = pd.to_numeric(df["MMARB_ma100"], errors="coerce")
        avail = ma05.notna() & ma100.notna()
        state["Madrid Ribbon"] = state_from_regime(idx, ma05 > ma100, avail)
    else:
        if "MMARB_state_005" in df.columns:
            s = pd.to_numeric(df["MMARB_state_005"], errors="coerce")
            avail = s.notna()
            state["Madrid Ribbon"] = state_from_regime(idx, s > 0, avail)
        else:
            state["Madrid Ribbon"] = pd.Series(STATE_NA, index=idx, dtype=int)

    if "Donchian_maintrend" in df.columns:
        mt = pd.to_numeric(df["Donchian_maintrend"], errors="coerce")
        avail = mt.notna() & (mt != 0)
        state["Donchian Ribbon"] = state_from_regime(idx, mt == 1, avail)
    else:
        state["Donchian Ribbon"] = pd.Series(STATE_NA, index=idx, dtype=int)

    if all(c in df.columns for c in ["WT_LB_wt1", "WT_LB_wt2"]):
        w1 = pd.to_numeric(df["WT_LB_wt1"], errors="coerce")
        w2 = pd.to_numeric(df["WT_LB_wt2"], errors="coerce")
        avail = w1.notna() & w2.notna()
        state["WT_LB"] = state_from_regime(idx, w1 > w2, avail)
    else:
        state["WT_LB"] = pd.Series(STATE_NA, index=idx, dtype=int)

    if all(c in df.columns for c in ["DI_plus", "DI_minus"]):
        dip = pd.to_numeric(df["DI_plus"], errors="coerce")
        dim = pd.to_numeric(df["DI_minus"], errors="coerce")
        avail = dip.notna() & dim.notna()
        state["ADX & DI"] = state_from_regime(idx, dip > dim, avail)
    else:
        state["ADX & DI"] = pd.Series(STATE_NA, index=idx, dtype=int)

    if "OBV_osc" in df.columns:
        o = pd.to_numeric(df["OBV_osc"], errors="coerce")
        avail = o.notna()
        state["OBVOSC_LB"] = state_from_regime(idx, o >= 0, avail)
    else:
        state["OBVOSC_LB"] = pd.Series(STATE_NA, index=idx, dtype=int)

    if "SQZ_val" in df.columns:
        v = pd.to_numeric(df["SQZ_val"], errors="coerce")
        avail = v.notna()
        state["SQZMOM_LB"] = state_from_regime(idx, v > 0, avail)
    else:
        state["SQZMOM_LB"] = pd.Series(STATE_NA, index=idx, dtype=int)

    # Stoch_MTM — zone-based: green when SMI < -40 (oversold), red when > 40 (overbought)
    if all(c in df.columns for c in ["SMI", "SMI_ema"]):
        smi = pd.to_numeric(df["SMI"], errors="coerce")
        _smi_t = stoch_mtm_thresholds or {}
        ob = float(_smi_t.get("overbought", 40.0))
        os_ = float(_smi_t.get("oversold", -40.0))
        avail = smi.notna()
        out = pd.Series(STATE_NEUTRAL, index=idx, dtype=int)
        out.loc[(smi < os_) & avail] = STATE_BULL
        out.loc[(smi > ob) & avail] = STATE_BEAR
        out.loc[~avail] = STATE_NA
        state["Stoch_MTM"] = out
    else:
        state["Stoch_MTM"] = pd.Series(STATE_NA, index=idx, dtype=int)

    if all(c in df.columns for c in ["MACD", "MACD_signal"]):
        m = pd.to_numeric(df["MACD"], errors="coerce")
        s = pd.to_numeric(df["MACD_signal"], errors="coerce")
        avail = m.notna() & s.notna()
        state["CM_Ult_MacD_MFT"] = state_from_regime(idx, m >= s, avail)
    else:
        state["CM_Ult_MacD_MFT"] = pd.Series(STATE_NA, index=idx, dtype=int)

    if "Vol_gt_MA20" in df.columns:
        v = df["Vol_gt_MA20"]
        avail = v.notna()
        state["Volume + MA20"] = state_from_regime(idx, v.astype(bool), avail)
    else:
        state["Volume + MA20"] = pd.Series(STATE_NA, index=idx, dtype=int)

    # cRSI — zone-based: green when cRSI < lower band (oversold), red when > upper band (overbought)
    if all(c in df.columns for c in ["cRSI", "cRSI_lb", "cRSI_ub"]):
        crsi = pd.to_numeric(df["cRSI"], errors="coerce")
        lb = pd.to_numeric(df["cRSI_lb"], errors="coerce")
        ub = pd.to_numeric(df["cRSI_ub"], errors="coerce")
        avail_val = crsi.notna() & lb.notna() & ub.notna()
        out = pd.Series(STATE_NEUTRAL, index=idx, dtype=int)
        out.loc[(crsi < lb) & avail_val] = STATE_BULL
        out.loc[(crsi > ub) & avail_val] = STATE_BEAR
        out.loc[~avail_val] = STATE_NA
        state["cRSI"] = out

        avail_sig = avail_val & crsi.shift(1).notna() & lb.shift(1).notna() & ub.shift(1).notna()
        bull_sig = avail_sig & (crsi > lb) & (crsi.shift(1) <= lb.shift(1))
        bear_sig = avail_sig & (crsi < ub) & (crsi.shift(1) >= ub.shift(1))
        state["cRSI (breakout)"] = state_from_signals(idx, bull_sig, bear_sig, avail_sig)
    else:
        state["cRSI"] = pd.Series(STATE_NA, index=idx, dtype=int)
        state["cRSI (breakout)"] = pd.Series(STATE_NA, index=idx, dtype=int)

    if "Zei_bullish" in df.columns:
        zb = df["Zei_bullish"]
        avail_val = zb.notna()
        avail_sig = avail_val & zb.shift(1).notna()
        state["RSI Strength & Consolidation Zones (Zeiierman)"] = state_from_regime(idx, zb.astype(bool), avail_val)

        bull_sig = avail_sig & zb.astype(bool) & (~zb.shift(1).astype(bool))
        bear_sig = avail_sig & (~zb.astype(bool)) & (zb.shift(1).astype(bool))
        state["RSI Strength & Consolidation Zones (Zeiierman) (breakout)"] = state_from_signals(idx, bull_sig, bear_sig, avail_sig)
    else:
        state["RSI Strength & Consolidation Zones (Zeiierman)"] = pd.Series(STATE_NA, index=idx, dtype=int)
        state["RSI Strength & Consolidation Zones (Zeiierman) (breakout)"] = pd.Series(STATE_NA, index=idx, dtype=int)

    if close is not None and "DEMA_9" in df.columns:
        d = pd.to_numeric(df["DEMA_9"], errors="coerce")
        avail = close.notna() & d.notna()
        state["DEMA"] = state_from_regime(idx, close > d, avail)
    else:
        state["DEMA"] = pd.Series(STATE_NA, index=idx, dtype=int)

    if close is not None and "PSAR" in df.columns:
        p = pd.to_numeric(df["PSAR"], errors="coerce")
        avail = close.notna() & p.notna()
        state["CM_P-SAR"] = state_from_regime(idx, p < close, avail)
    else:
        state["CM_P-SAR"] = pd.Series(STATE_NA, index=idx, dtype=int)

    # --- Ichimoku: bullish when close is above the kumo ---
    if "Ichi_above_kumo" in df.columns:
        above = df["Ichi_above_kumo"].fillna(False).astype(bool)
        below = df["Ichi_below_kumo"].fillna(False).astype(bool) if "Ichi_below_kumo" in df.columns else ~above
        avail = above | below
        out = pd.Series(STATE_NEUTRAL, index=idx, dtype=int)
        out.loc[above.to_numpy(dtype=bool)] = STATE_BULL
        out.loc[below.to_numpy(dtype=bool)] = STATE_BEAR
        out.loc[(~avail).to_numpy(dtype=bool)] = STATE_NA
        state["Ichimoku"] = out
    else:
        state["Ichimoku"] = pd.Series(STATE_NA, index=idx, dtype=int)

    # --- SR Breaks: bullish when SR_state == 1 (support holds / resistance broken) ---
    if "SR_state" in df.columns:
        sr = pd.to_numeric(df["SR_state"], errors="coerce")
        avail = sr.notna() & (sr != 0)
        state["SR Breaks"] = state_from_regime(idx, sr == 1, avail)
    else:
        state["SR Breaks"] = pd.Series(STATE_NA, index=idx, dtype=int)

    # --- GK Trend Ribbon: bullish when GK_trend == 1 ---
    if "GK_trend" in df.columns:
        gk = pd.to_numeric(df["GK_trend"], errors="coerce")
        avail = gk.notna()
        state["GK Trend Ribbon"] = state_from_regime(idx, gk == 1, avail)
    else:
        state["GK Trend Ribbon"] = pd.Series(STATE_NA, index=idx, dtype=int)

    # --- Impulse Trend: bullish when ITL_trend == 1 ---
    if "ITL_trend" in df.columns:
        itl = pd.to_numeric(df["ITL_trend"], errors="coerce")
        avail = itl.notna()
        state["Impulse Trend"] = state_from_regime(idx, itl == 1, avail)
    else:
        state["Impulse Trend"] = pd.Series(STATE_NA, index=idx, dtype=int)

    # --- Breakout Targets: bullish breakout signal ---
    if "BT_signal" in df.columns:
        bt = pd.to_numeric(df["BT_signal"], errors="coerce")
        avail = bt.notna()
        bull_sig = bt == 1
        bear_sig = bt == -1
        state["Breakout Targets"] = state_from_signals(idx, bull_sig, bear_sig, avail)
    else:
        state["Breakout Targets"] = pd.Series(STATE_NA, index=idx, dtype=int)

    # --- Mansfield RS: bullish when MRS > 0 (outperforming benchmark) ---
    if "MRS" in df.columns:
        mrs = pd.to_numeric(df["MRS"], errors="coerce")
        avail = mrs.notna()
        state["Mansfield RS"] = state_from_regime(idx, mrs > 0, avail)
    else:
        state["Mansfield RS"] = pd.Series(STATE_NA, index=idx, dtype=int)

    for name in KPI_ORDER:
        if name not in state:
            state[name] = pd.Series(STATE_NA, index=idx, dtype=int)

    # ══════════════════════════════════════════════════════════════════════════
    # Stoof (Band Light) KPI states
    # ══════════════════════════════════════════════════════════════════════════

    # BL1: MACD — green when hist >= 0 and MACD not in dead zone, red when hist < 0 and not in dead zone
    if all(c in df.columns for c in ["MACD_BL", "MACD_BL_hist"]):
        macd_bl = pd.to_numeric(df["MACD_BL"], errors="coerce")
        hist_bl = pd.to_numeric(df["MACD_BL_hist"], errors="coerce")
        macd_max = macd_bl.rolling(50, min_periods=1).max()
        macd_min = macd_bl.rolling(50, min_periods=1).min()
        dead_zone = (macd_max - macd_min) * 0.05
        avail = macd_bl.notna() & hist_bl.notna()
        near_zero = macd_bl.abs() <= dead_zone
        out = pd.Series(STATE_NEUTRAL, index=idx, dtype=int)
        out.loc[(hist_bl >= 0) & ~near_zero & avail] = STATE_BULL
        out.loc[(hist_bl < 0) & ~near_zero & avail] = STATE_BEAR
        out.loc[~avail] = STATE_NA
        state["MACD_BL"] = out
    else:
        state["MACD_BL"] = pd.Series(STATE_NA, index=idx, dtype=int)

    # BL2: WaveTrend — contrarian: green when oversold (WT <= -60), red when overbought (WT >= 60)
    if "WT_LB_BL_wt1" in df.columns:
        wt_bl = pd.to_numeric(df["WT_LB_BL_wt1"], errors="coerce")
        avail = wt_bl.notna()
        out = pd.Series(STATE_NEUTRAL, index=idx, dtype=int)
        out.loc[(wt_bl <= -60.0) & avail] = STATE_BULL
        out.loc[(wt_bl >= 60.0) & avail] = STATE_BEAR
        out.loc[~avail] = STATE_NA
        state["WT_LB_BL"] = out
    else:
        state["WT_LB_BL"] = pd.Series(STATE_NA, index=idx, dtype=int)

    # BL3: OBV — contrarian: green when OBV osc < 0 (selling exhaustion), red when > 0
    if "OBVOSC_BL_osc" in df.columns:
        obv_bl = pd.to_numeric(df["OBVOSC_BL_osc"], errors="coerce")
        avail = obv_bl.notna()
        state["OBVOSC_BL"] = state_from_regime(idx, obv_bl < 0, avail)
    else:
        state["OBVOSC_BL"] = pd.Series(STATE_NA, index=idx, dtype=int)

    # BL4: CCI+Chop+BB v1 — green when smoothed < 25, red when > 65
    if "CCI_Chop_BB_v1_smooth" in df.columns:
        ccb1 = pd.to_numeric(df["CCI_Chop_BB_v1_smooth"], errors="coerce")
        avail = ccb1.notna()
        out = pd.Series(STATE_NEUTRAL, index=idx, dtype=int)
        out.loc[(ccb1 < 25.0) & avail] = STATE_BULL
        out.loc[(ccb1 > 65.0) & avail] = STATE_BEAR
        out.loc[~avail] = STATE_NA
        state["CCI_Chop_BB_v1"] = out
    else:
        state["CCI_Chop_BB_v1"] = pd.Series(STATE_NA, index=idx, dtype=int)

    # BL5: ADX+DI — green when ADX > 20 and DI+ > DI-, red when ADX > 20 and DI- > DI+
    if all(c in df.columns for c in ["ADX_BL", "DI_plus_BL", "DI_minus_BL"]):
        adx_bl = pd.to_numeric(df["ADX_BL"], errors="coerce")
        dip_bl = pd.to_numeric(df["DI_plus_BL"], errors="coerce")
        dim_bl = pd.to_numeric(df["DI_minus_BL"], errors="coerce")
        avail = adx_bl.notna() & dip_bl.notna() & dim_bl.notna()
        out = pd.Series(STATE_NEUTRAL, index=idx, dtype=int)
        trending = adx_bl > 20.0
        out.loc[trending & (dip_bl > dim_bl) & avail] = STATE_BULL
        out.loc[trending & (dim_bl > dip_bl) & avail] = STATE_BEAR
        out.loc[~avail] = STATE_NA
        state["ADX_DI_BL"] = out
    else:
        state["ADX_DI_BL"] = pd.Series(STATE_NA, index=idx, dtype=int)

    # BL6: LuxAlgo Normalized v1 — green when < 20, red when > 80
    if "LuxAlgo_Norm_v1" in df.columns:
        lux1 = pd.to_numeric(df["LuxAlgo_Norm_v1"], errors="coerce")
        avail = lux1.notna()
        out = pd.Series(STATE_NEUTRAL, index=idx, dtype=int)
        out.loc[(lux1 < 20.0) & avail] = STATE_BULL
        out.loc[(lux1 > 80.0) & avail] = STATE_BEAR
        out.loc[~avail] = STATE_NA
        state["LuxAlgo_Norm_v1"] = out
    else:
        state["LuxAlgo_Norm_v1"] = pd.Series(STATE_NA, index=idx, dtype=int)

    # BL7: Risk Indicator — green when < 0.2 (low risk), red when > 0.8 (high risk)
    if "Risk_Indicator" in df.columns:
        risk = pd.to_numeric(df["Risk_Indicator"], errors="coerce")
        avail = risk.notna()
        out = pd.Series(STATE_NEUTRAL, index=idx, dtype=int)
        out.loc[(risk < 0.2) & avail] = STATE_BULL
        out.loc[(risk > 0.8) & avail] = STATE_BEAR
        out.loc[~avail] = STATE_NA
        state["Risk_Indicator"] = out
    else:
        state["Risk_Indicator"] = pd.Series(STATE_NA, index=idx, dtype=int)

    # BL8: LuxAlgo Normalized v2 — same rules as v1
    if "LuxAlgo_Norm_v2" in df.columns:
        lux2 = pd.to_numeric(df["LuxAlgo_Norm_v2"], errors="coerce")
        avail = lux2.notna()
        out = pd.Series(STATE_NEUTRAL, index=idx, dtype=int)
        out.loc[(lux2 < 20.0) & avail] = STATE_BULL
        out.loc[(lux2 > 80.0) & avail] = STATE_BEAR
        out.loc[~avail] = STATE_NA
        state["LuxAlgo_Norm_v2"] = out
    else:
        state["LuxAlgo_Norm_v2"] = pd.Series(STATE_NA, index=idx, dtype=int)

    # BL9: CCI+Chop+BB v2 — same rules as v1
    if "CCI_Chop_BB_v2_smooth" in df.columns:
        ccb2 = pd.to_numeric(df["CCI_Chop_BB_v2_smooth"], errors="coerce")
        avail = ccb2.notna()
        out = pd.Series(STATE_NEUTRAL, index=idx, dtype=int)
        out.loc[(ccb2 < 25.0) & avail] = STATE_BULL
        out.loc[(ccb2 > 65.0) & avail] = STATE_BEAR
        out.loc[~avail] = STATE_NA
        state["CCI_Chop_BB_v2"] = out
    else:
        state["CCI_Chop_BB_v2"] = pd.Series(STATE_NA, index=idx, dtype=int)

    # BL10: PAI — binary: green when Z >= 0, red when Z < 0
    if "PAI" in df.columns:
        pai = pd.to_numeric(df["PAI"], errors="coerce")
        avail = pai.notna()
        state["PAI"] = state_from_regime(idx, pai >= 0, avail)
    else:
        state["PAI"] = pd.Series(STATE_NA, index=idx, dtype=int)

    # BL11: WT_MTF — zone + cross: green when bullish cross (wt1 > wt2) in oversold zone, red when bearish cross in overbought
    if all(c in df.columns for c in ["WT_MTF_wt1", "WT_MTF_wt2"]):
        wt1 = pd.to_numeric(df["WT_MTF_wt1"], errors="coerce")
        wt2 = pd.to_numeric(df["WT_MTF_wt2"], errors="coerce")
        avail = wt1.notna() & wt2.notna()
        out = pd.Series(STATE_NEUTRAL, index=idx, dtype=int)
        out.loc[(wt1 < -60.0) & (wt2 < -60.0) & (wt1 > wt2) & avail] = STATE_BULL
        out.loc[(wt1 > 60.0) & (wt2 > 60.0) & (wt1 < wt2) & avail] = STATE_BEAR
        out.loc[~avail] = STATE_NA
        state["WT_MTF"] = out
    else:
        state["WT_MTF"] = pd.Series(STATE_NA, index=idx, dtype=int)

    return state


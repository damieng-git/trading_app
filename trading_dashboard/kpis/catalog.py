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
    state_from_persistent_signals,
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

    if all(c in df.columns for c in ["SMI", "SMI_ema"]):
        smi = pd.to_numeric(df["SMI"], errors="coerce")
        se = pd.to_numeric(df["SMI_ema"], errors="coerce")
        avail_val = smi.notna() & se.notna()
        avail_sig = avail_val & smi.shift(1).notna() & se.shift(1).notna()

        _smi_t = stoch_mtm_thresholds or {}
        ob = float(_smi_t.get("overbought", 40.0))
        os_ = float(_smi_t.get("oversold", -40.0))
        long_thr = float(_smi_t.get("long_threshold", -35.0))
        short_thr = float(_smi_t.get("short_threshold", 35.0))

        os_end = avail_sig & (smi >= os_) & (smi.shift(1) < os_)
        ob_end = avail_sig & (smi <= ob) & (smi.shift(1) > ob)

        cross_up = avail_sig & (smi > se) & (smi.shift(1) <= se.shift(1))
        cross_dn = avail_sig & (smi < se) & (smi.shift(1) >= se.shift(1))

        long_entry = os_end & cross_up & (smi <= long_thr)
        short_entry = ob_end & cross_dn & (smi >= short_thr)

        state["Stoch_MTM"] = state_from_persistent_signals(idx, long_entry, short_entry, avail_val)
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

    if all(c in df.columns for c in ["cRSI", "cRSI_lb", "cRSI_ub"]):
        crsi = pd.to_numeric(df["cRSI"], errors="coerce")
        lb = pd.to_numeric(df["cRSI_lb"], errors="coerce")
        ub = pd.to_numeric(df["cRSI_ub"], errors="coerce")
        avail_val = crsi.notna() & lb.notna() & ub.notna()
        avail_sig = avail_val & crsi.shift(1).notna() & lb.shift(1).notna() & ub.shift(1).notna()
        bull_sig = avail_sig & (crsi > lb) & (crsi.shift(1) <= lb.shift(1))
        bear_sig = avail_sig & (crsi < ub) & (crsi.shift(1) >= ub.shift(1))
        state["cRSI"] = state_from_persistent_signals(idx, bull_sig, bear_sig, avail_val)
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

    return state


"""Plotly figure builder for the trading dashboard."""

from __future__ import annotations

import logging
from typing import List

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from trading_dashboard.data.enrichment import IndicatorSpec

from .figures_indicators import (
    _add_combo_overlay,
    _add_exit_flow_overlay,
    compute_kpi_timeline_matrix,
)

pd.set_option("future.no_silent_downcasting", True)
logger = logging.getLogger(__name__)


def build_figure_for_symbol_timeframe(symbol: str, timeframe: str, df: pd.DataFrame, specs: List[IndicatorSpec], *, display_name: str = "", kpi_weights: dict | None = None, precomputed_kpi_state: dict | None = None, combo_3_kpis: list[str] | None = None, combo_4_kpis: list[str] | None = None) -> go.Figure:
    """
    Build a single figure for a single symbol and selected timeframe.
    Legends are disabled by design.
    """
    if kpi_weights is None:
        kpi_weights = {}
    # For weekly data, ensure labels show Monday (week start) not Friday (week end).
    _weekly_shifted = False
    if timeframe.upper() == "1W" and not df.empty:
        idx = pd.to_datetime(df.index)
        if len(idx) > 0 and pd.Series(idx.dayofweek).mode().iloc[0] == 4:
            _shift = pd.DateOffset(days=4)
            df = df.copy()
            df.index = idx - _shift
            _weekly_shifted = True
            # Shift precomputed KPI state indices to match the display index.
            if precomputed_kpi_state is not None:
                shifted_state = {}
                for k, s in precomputed_kpi_state.items():
                    sc = s.copy()
                    sc.index = pd.to_datetime(sc.index) - _shift
                    shifted_state[k] = sc
                precomputed_kpi_state = shifted_state

    x = df.index

    def _short_label(s: str, *, max_len: int = 20) -> str:
        s0 = (s or "").strip()
        if not s0:
            return ""
        # NOTE: We keep BO_ for PDFs/docs, but in the dashboard breakout panel
        # the context already indicates "breakout", so we do NOT show the BO_ prefix.
        if s0.startswith("BO_"):
            s0 = s0[3:]
        # Prefer curated abbreviations for the most common long labels.
        m = {
            "Nadaraya-Watson Smoother": "NW Smooth",
            "Nadaraya-Watson Envelop (MAE)": "NWE MAE",
            "Nadaraya-Watson Envelop (STD)": "NWE STD",
            "RSI Strength & Consolidation Zones (Zeiierman)": "RSI Zones",
            "RSI Strength & Consolidation Zones (Zeiierman) (breakout)": "RSI Zones (BO)",
            "Stoch_MTM": "SMI",
            "CM_Ult_MacD_MFT": "MACD",
            "CM_P-SAR": "P-SAR",
            "SQZMOM_LB": "Squeeze Mom",
            "OBVOSC_LB": "OBV Osc",
            "WT_LB": "WaveTrend",
            "Volume + MA20": "Volume/MA20",
            "Donchian Ribbon": "Donchian",
            "Madrid Ribbon": "Madrid",
            "UT Bot Alert": "UT Bot",
            "ADX & DI": "ADX/DI",
        }
        s1 = m.get(s0, s0)
        # Hard limit as requested.
        if len(s1) <= max_len:
            return s1
        # Intelligent truncation: keep the start meaningful.
        return (s1[: max_len - 1].rstrip() + "…") if max_len >= 2 else s1[:max_len]

    def _has(cols: list[str]) -> bool:
        return all(c in df.columns for c in cols)

    fig = make_subplots(
        rows=6,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.02,
        row_heights=[0.32, 0.14, 0.06, 0.06, 0.21, 0.21],
        subplot_titles=[f"Price ({timeframe})", "Oscillators", "TrendScore", "Breakout Count", "KPI — Breakout (signals)", "KPI — Trend (regime)"],
    )

    # Indicator labels (match PineScripts file stems)
    LABEL = {
        "Price": "Price",
        "NW_Smoother": "Nadaraya-Watson Smoother",
        "NW_Envelope_MAE": "Nadaraya-Watson Envelop (MAE)",
        "NW_Envelope_STD": "Nadaraya-Watson Envelop (STD)",
        "NW_Envelope_RP": "Nadaraya-Watson Envelop (Repainting)",
        "BB": "BB 30",
        "ATR": "ATR",
        "SuperTrend": "SuperTrend",
        "UT_Bot": "UT Bot Alert",
        "TuTCI": "TuTCI",
        "GMMA": "GMMA",
        "MA_Ribbon": "MA Ribbon",
        "MadridRibbon": "Madrid Ribbon",
        "DonchianRibbon": "Donchian Ribbon",
        "PSAR": "CM_P-SAR",
        "DEMA": "DEMA",
        "WT_LB": "WT_LB",
        "ADX_DI": "ADX & DI",
        "OBVOSC": "OBVOSC_LB",
        "SQZMOM": "SQZMOM_LB",
        "SMI": "Stoch_MTM",
        "MACD": "CM_Ult_MacD_MFT",
        "VOL_MA": "Volume + MA20",
        "cRSI": "cRSI",
        "RSI_Zei": "RSI Strength & Consolidation Zones (Zeiierman)",
        "Ichimoku": "Ichimoku",
        "Mansfield_RS": "Mansfield RS",
        "SR_Breaks": "SR Breaks",
        "GK_Trend": "GK Trend Ribbon",
        "Impulse_Trend": "Impulse Trend",
    }

    # Default: no user indicators selected (Price + KPI panels stay visible).
    default_visible: set[str] = set()

    def _add(trace: go.BaseTraceType, *, row: int, indicator_label: str, visible: bool | None = None) -> None:
        trace.showlegend = False
        trace.meta = {"indicator": indicator_label}
        trace.visible = visible if visible is not None else (indicator_label in default_visible or indicator_label == LABEL["Price"])
        fig.add_trace(trace, row=row, col=1)

    _add(
        go.Candlestick(
            x=x,
            open=df["Open"],
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            name="Price",
            showlegend=False,
            hoverinfo="skip",
        ),
        row=1,
        indicator_label=LABEL["Price"],
        visible=True,
    )
    # Hover-catcher: invisible trace that fires hover events and shows OHLCV tooltip.
    _hover_vol = df["Volume"].values if "Volume" in df.columns else np.zeros(len(df))
    _hover_cd = np.column_stack([
        df["Open"].values, df["High"].values,
        df["Low"].values, df["Close"].values,
        _hover_vol,
    ])
    _add(
        go.Scatter(
            x=x,
            y=df["Close"],
            mode="markers",
            marker=dict(size=18, opacity=0),
            name="",
            customdata=_hover_cd,
            hovertemplate=(
                "<b>O</b> %{customdata[0]:.2f}  "
                "<b>H</b> %{customdata[1]:.2f}<br>"
                "<b>L</b> %{customdata[2]:.2f}  "
                "<b>C</b> %{customdata[3]:.2f}<br>"
                "<b>Vol</b> %{customdata[4]:,.0f}"
                "<extra></extra>"
            ),
            showlegend=False,
        ),
        row=1,
        indicator_label=LABEL["Price"],
        visible=True,
    )

    # Overlays (row 1)
    if "NW_LuxAlgo_value" in df.columns or "NW_LuxAlgo_endpoint" in df.columns:
        nwe = df["NW_LuxAlgo_value"] if "NW_LuxAlgo_value" in df.columns else df["NW_LuxAlgo_endpoint"]
        slope_up = df["NW_LuxAlgo_color"].astype(str).str.lower().eq("green") if "NW_LuxAlgo_color" in df.columns else (nwe > nwe.shift(1))
        _add(go.Scatter(x=x, y=nwe.where(slope_up), mode="lines", line=dict(width=2, color="#22c55e"), connectgaps=False), row=1, indicator_label=LABEL["NW_Smoother"])
        _add(go.Scatter(x=x, y=nwe.where(~slope_up), mode="lines", line=dict(width=2, color="#ef4444"), connectgaps=False), row=1, indicator_label=LABEL["NW_Smoother"])
        if "NW_LuxAlgo_arrow_up" in df.columns and "NW_LuxAlgo_arrow_down" in df.columns:
            up_mask = df["NW_LuxAlgo_arrow_up"].fillna(False)
            dn_mask = df["NW_LuxAlgo_arrow_down"].fillna(False)
            _add(go.Scatter(x=x[up_mask], y=df.loc[up_mask, "Close"], mode="markers", marker=dict(symbol="triangle-up", size=10, color="#22c55e")), row=1, indicator_label=LABEL["NW_Smoother"])
            _add(go.Scatter(x=x[dn_mask], y=df.loc[dn_mask, "Close"], mode="markers", marker=dict(symbol="triangle-down", size=10, color="#ef4444")), row=1, indicator_label=LABEL["NW_Smoother"])

    if _has(["NWE_MAE_env_upper", "NWE_MAE_env_lower"]):
        _add(
            go.Scatter(x=x, y=df["NWE_MAE_env_upper"], mode="lines", line=dict(width=1.6, color="#22c55e")),
            row=1,
            indicator_label=LABEL["NW_Envelope_MAE"],
        )
        _add(
            go.Scatter(x=x, y=df["NWE_MAE_env_lower"], mode="lines", line=dict(width=1.6, color="#ef4444")),
            row=1,
            indicator_label=LABEL["NW_Envelope_MAE"],
        )
        if "NWE_MAE_env_crossover" in df.columns:
            mask = df["NWE_MAE_env_crossover"].fillna(False)
            _add(
                go.Scatter(x=x[mask], y=df.loc[mask, "High"], mode="markers", marker=dict(symbol="triangle-down", size=10, color="#ef4444")),
                row=1,
                indicator_label=LABEL["NW_Envelope_MAE"],
            )
        if "NWE_MAE_env_crossunder" in df.columns:
            mask = df["NWE_MAE_env_crossunder"].fillna(False)
            _add(
                go.Scatter(x=x[mask], y=df.loc[mask, "Low"], mode="markers", marker=dict(symbol="triangle-up", size=10, color="#22c55e")),
                row=1,
                indicator_label=LABEL["NW_Envelope_MAE"],
            )

    if _has(["NWE_STD_env_upper", "NWE_STD_env_lower"]):
        _add(
            go.Scatter(x=x, y=df["NWE_STD_env_upper"], mode="lines", line=dict(width=1.6, color="#60a5fa")),
            row=1,
            indicator_label=LABEL["NW_Envelope_STD"],
        )
        _add(
            go.Scatter(x=x, y=df["NWE_STD_env_lower"], mode="lines", line=dict(width=1.6, color="#f59e0b")),
            row=1,
            indicator_label=LABEL["NW_Envelope_STD"],
        )
        if "NWE_STD_env_crossover" in df.columns:
            mask = df["NWE_STD_env_crossover"].fillna(False)
            _add(
                go.Scatter(x=x[mask], y=df.loc[mask, "High"], mode="markers", marker=dict(symbol="triangle-down", size=10, color="#f59e0b")),
                row=1,
                indicator_label=LABEL["NW_Envelope_STD"],
            )
        if "NWE_STD_env_crossunder" in df.columns:
            mask = df["NWE_STD_env_crossunder"].fillna(False)
            _add(
                go.Scatter(x=x[mask], y=df.loc[mask, "Low"], mode="markers", marker=dict(symbol="triangle-up", size=10, color="#60a5fa")),
                row=1,
                indicator_label=LABEL["NW_Envelope_STD"],
            )

    if _has(["NWE_RP_env_upper", "NWE_RP_env_lower"]):
        _add(
            go.Scatter(x=x, y=df["NWE_RP_env_upper"], mode="lines", line=dict(width=1.2, color="rgba(34,197,94,0.70)", dash="dot")),
            row=1,
            indicator_label=LABEL["NW_Envelope_RP"],
        )
        _add(
            go.Scatter(x=x, y=df["NWE_RP_env_lower"], mode="lines", line=dict(width=1.2, color="rgba(239,68,68,0.70)", dash="dot")),
            row=1,
            indicator_label=LABEL["NW_Envelope_RP"],
        )
        if "NWE_RP_env_crossover" in df.columns:
            mask = df["NWE_RP_env_crossover"].fillna(False)
            _add(
                go.Scatter(x=x[mask], y=df.loc[mask, "High"], mode="markers", marker=dict(symbol="triangle-down", size=9, color="rgba(239,68,68,0.75)")),
                row=1,
                indicator_label=LABEL["NW_Envelope_RP"],
            )
        if "NWE_RP_env_crossunder" in df.columns:
            mask = df["NWE_RP_env_crossunder"].fillna(False)
            _add(
                go.Scatter(x=x[mask], y=df.loc[mask, "Low"], mode="markers", marker=dict(symbol="triangle-up", size=9, color="rgba(34,197,94,0.75)")),
                row=1,
                indicator_label=LABEL["NW_Envelope_RP"],
            )

    if _has(["BB_basis", "BB_upper", "BB_lower"]):
        _add(go.Scatter(x=x, y=df["BB_basis"], mode="lines", line=dict(width=1.6, color="#2962FF")), row=1, indicator_label=LABEL["BB"])
        _add(go.Scatter(x=x, y=df["BB_upper"], mode="lines", line=dict(width=1.2, color="#F23645")), row=1, indicator_label=LABEL["BB"])
        _add(go.Scatter(x=x, y=df["BB_lower"], mode="lines", line=dict(width=1.2, color="#089981")), row=1, indicator_label=LABEL["BB"])

    if _has(["ATR_short_stop", "ATR_long_stop"]):
        _add(go.Scatter(x=x, y=df["ATR_short_stop"], mode="lines", line=dict(width=1.2, color="#ef4444", dash="dot")), row=1, indicator_label=LABEL["ATR"])
        _add(go.Scatter(x=x, y=df["ATR_long_stop"], mode="lines", line=dict(width=1.2, color="#22c55e", dash="dot")), row=1, indicator_label=LABEL["ATR"])

    if _has(["SuperTrend_line", "SuperTrend_trend"]):
        # Match TradingView Supertrend visuals: green/red line with breaks + highlighter fill + Buy/Sell labels.
        st = df["SuperTrend_line"]
        tr = df["SuperTrend_trend"]

        # Pine uses ohlc4 as the "middle" plot for highlighting fill.
        ohlc4 = (df["Open"] + df["High"] + df["Low"] + df["Close"]) / 4.0

        # Uptrend: fill between ohlc4 and up-line
        _add(
            go.Scatter(
                x=x,
                y=ohlc4,
                mode="lines",
                line=dict(width=0, color="rgba(0,0,0,0)"),
                hoverinfo="skip",
            ),
            row=1,
            indicator_label=LABEL["SuperTrend"],
        )
        _add(
            go.Scatter(
                x=x,
                y=st.where(tr == 1),
                mode="lines",
                line=dict(width=2, color="#22c55e"),
                fill="tonexty",
                fillcolor="rgba(34,197,94,0.12)",
                connectgaps=False,
            ),
            row=1,
            indicator_label=LABEL["SuperTrend"],
        )

        # Downtrend: fill between ohlc4 and down-line
        _add(
            go.Scatter(
                x=x,
                y=ohlc4,
                mode="lines",
                line=dict(width=0, color="rgba(0,0,0,0)"),
                hoverinfo="skip",
            ),
            row=1,
            indicator_label=LABEL["SuperTrend"],
        )
        _add(
            go.Scatter(
                x=x,
                y=st.where(tr == -1),
                mode="lines",
                line=dict(width=2, color="#ff0000"),
                fill="tonexty",
                fillcolor="rgba(255,0,0,0.10)",
                connectgaps=False,
            ),
            row=1,
            indicator_label=LABEL["SuperTrend"],
        )
        if "SuperTrend_buy" in df.columns:
            mask = df["SuperTrend_buy"].fillna(False)
            _add(
                go.Scatter(
                    x=x[mask],
                    y=st.loc[mask],
                    mode="markers+text",
                    marker=dict(symbol="circle", size=8, color="#22c55e"),
                    text=["Buy"] * int(mask.sum()),
                    textposition="top center",
                    textfont=dict(color="#ffffff", size=9),
                ),
                row=1,
                indicator_label=LABEL["SuperTrend"],
            )
        if "SuperTrend_sell" in df.columns:
            mask = df["SuperTrend_sell"].fillna(False)
            _add(
                go.Scatter(
                    x=x[mask],
                    y=st.loc[mask],
                    mode="markers+text",
                    marker=dict(symbol="circle", size=8, color="#ff0000"),
                    text=["Sell"] * int(mask.sum()),
                    textposition="bottom center",
                    textfont=dict(color="#ffffff", size=9),
                ),
                row=1,
                indicator_label=LABEL["SuperTrend"],
            )

    if "UT_trailing_stop" in df.columns:
        ts = df["UT_trailing_stop"]
        pos = df["UT_pos"] if "UT_pos" in df.columns else None

        # Pine xcolor: pos==-1 red, pos==1 green, else blue.
        if pos is not None:
            _add(
                go.Scatter(
                    x=x,
                    y=ts.where(pos == 1),
                    mode="lines",
                    line=dict(width=2, color="#22c55e"),
                    connectgaps=False,
                ),
                row=1,
                indicator_label=LABEL["UT_Bot"],
            )
            _add(
                go.Scatter(
                    x=x,
                    y=ts.where(pos == -1),
                    mode="lines",
                    line=dict(width=2, color="#ff0000"),
                    connectgaps=False,
                ),
                row=1,
                indicator_label=LABEL["UT_Bot"],
            )
            _add(
                go.Scatter(
                    x=x,
                    y=ts.where(pos == 0),
                    mode="lines",
                    line=dict(width=2, color="#2962FF"),
                    connectgaps=False,
                ),
                row=1,
                indicator_label=LABEL["UT_Bot"],
            )
        else:
            _add(
                go.Scatter(
                    x=x,
                    y=ts,
                    mode="lines",
                    line=dict(width=2, color="#334155"),
                ),
                row=1,
                indicator_label=LABEL["UT_Bot"],
            )

        # Buy/Sell labels (TradingView: belowbar/abovebar)
        if "UT_buy" in df.columns:
            mask = df["UT_buy"].fillna(False)
            _add(
                go.Scatter(
                    x=x[mask],
                    y=df.loc[mask, "Low"] if "Low" in df.columns else df.loc[mask, "Close"],
                    mode="markers+text",
                    marker=dict(symbol="square", size=10, color="#22c55e"),
                    text=["Buy"] * int(mask.sum()),
                    textposition="bottom center",
                    textfont=dict(color="#ffffff", size=9),
                ),
                row=1,
                indicator_label=LABEL["UT_Bot"],
            )
        if "UT_sell" in df.columns:
            mask = df["UT_sell"].fillna(False)
            _add(
                go.Scatter(
                    x=x[mask],
                    y=df.loc[mask, "High"] if "High" in df.columns else df.loc[mask, "Close"],
                    mode="markers+text",
                    marker=dict(symbol="square", size=10, color="#ff0000"),
                    text=["Sell"] * int(mask.sum()),
                    textposition="top center",
                    textfont=dict(color="#ffffff", size=9),
                ),
                row=1,
                indicator_label=LABEL["UT_Bot"],
            )

    if _has(["TuTCI_upper", "TuTCI_lower", "TuTCI_trend", "TuTCI_exit"]):
        _add(go.Scatter(x=x, y=df["TuTCI_upper"], mode="lines", line=dict(width=1.2, color="#0094FF")), row=1, indicator_label=LABEL["TuTCI"])
        _add(go.Scatter(x=x, y=df["TuTCI_lower"], mode="lines", line=dict(width=1.2, color="#0094FF")), row=1, indicator_label=LABEL["TuTCI"])
        _add(go.Scatter(x=x, y=df["TuTCI_trend"], mode="lines", line=dict(width=1.8, color="#ef4444")), row=1, indicator_label=LABEL["TuTCI"])
        _add(go.Scatter(x=x, y=df["TuTCI_exit"], mode="lines", line=dict(width=1.2, color="#3b82f6")), row=1, indicator_label=LABEL["TuTCI"])

    if any(c.startswith("GMMA_ema_") for c in df.columns):
        gmma_cols = [c for c in df.columns if c.startswith("GMMA_ema_")]
        # Prefer increasing period order if possible.
        def _gmma_len(col: str) -> int:
            try:
                return int(col.split("_")[-1])
            except Exception:
                return 10**9

        gmma_cols = sorted(gmma_cols, key=_gmma_len)
        short_lens = {3, 5, 8, 10, 12, 15}
        long_lens = {30, 35, 40, 45, 50, 60}
        short_palette = [
            "rgba(34,197,94,0.90)",
            "rgba(34,197,94,0.78)",
            "rgba(34,197,94,0.66)",
            "rgba(34,197,94,0.54)",
            "rgba(34,197,94,0.42)",
            "rgba(34,197,94,0.30)",
        ]
        long_palette = [
            "rgba(239,68,68,0.90)",
            "rgba(239,68,68,0.78)",
            "rgba(239,68,68,0.66)",
            "rgba(239,68,68,0.54)",
            "rgba(239,68,68,0.42)",
            "rgba(239,68,68,0.30)",
        ]
        si = 0
        li = 0
        for c in gmma_cols:
            L = _gmma_len(c)
            if L in short_lens:
                col = short_palette[min(si, len(short_palette) - 1)]
                si += 1
            elif L in long_lens:
                col = long_palette[min(li, len(long_palette) - 1)]
                li += 1
            else:
                col = "rgba(148,163,184,0.55)"
            _add(go.Scatter(x=x, y=df[c], mode="lines", line=dict(width=1.2, color=col)), row=1, indicator_label=LABEL["GMMA"])

    if _has(["MA_Ribbon_ma1", "MA_Ribbon_ma2", "MA_Ribbon_ma3", "MA_Ribbon_ma4"]):
        for c, col in zip(["MA_Ribbon_ma1", "MA_Ribbon_ma2", "MA_Ribbon_ma3", "MA_Ribbon_ma4"], ["#f6c309", "#fb9800", "#fb6500", "#f60c0c"]):
            _add(go.Scatter(x=x, y=df[c], mode="lines", line=dict(width=1.2, color=col)), row=1, indicator_label=LABEL["MA_Ribbon"])

    # Madrid Ribbon (MMARB): render as a ribbon of colored blocks (like TradingView's bar ribbon)
    mmarb_state_cols = [c for c in df.columns if c.startswith("MMARB_state_")]
    if mmarb_state_cols:
        mmarb_state_cols = sorted(mmarb_state_cols)
        mmarb_lens = [int(c.split("_")[-1]) for c in mmarb_state_cols]
        z = np.vstack([df[c].to_numpy(dtype=float) for c in mmarb_state_cols])
        mmarb_colorscale = [
            [0.00, "#7f1d1d"],  # -2 maroon
            [0.25, "#ef4444"],  # -1 red
            [0.50, "#9ca3af"],  #  0 gray
            [0.75, "#22c55e"],  # +1 green
            [1.00, "#00e676"],  # +2 lime
        ]
        _add(
            go.Heatmap(
                x=x,
                y=mmarb_lens,
                z=z,
                zmin=-2,
                zmax=2,
                colorscale=mmarb_colorscale,
                showscale=False,
                hovertemplate="MMARB %{y}<br>%{x}<extra></extra>",
            ),
            row=2,
            indicator_label=LABEL["MadridRibbon"],
            visible=False,
        )

    # Donchian Trend Ribbon: render as stacked blocks with strong/weak tint relative to maintrend
    don_cols = [c for c in df.columns if c.startswith("Donchian_trend_")]
    if ("Donchian_maintrend" in df.columns) and don_cols:
        def _don_len(col: str) -> int:
            try:
                return int(col.split("_")[-1])
            except Exception:
                return 10**9

        don_cols = sorted(don_cols, key=_don_len, reverse=True)  # dlen, dlen-1, ...
        main = df["Donchian_maintrend"].fillna(0).to_numpy(dtype=int)
        z_rows = []
        for col in don_cols:
            tr = df[col].fillna(0).to_numpy(dtype=int)
            # Encode: +2 strong green, +1 weak green, -1 weak red, -2 strong red, 0 neutral
            zz = np.where(
                main == 1,
                np.where(tr == 1, 2, 1),
                np.where(main == -1, np.where(tr == -1, -2, -1), 0),
            )
            z_rows.append(zz.astype(float))
        z = np.vstack(z_rows)
        # y levels (TradingView uses 5,10,15,... with histbase 0,5,10,...)
        y_levels = list(range(5, 5 * (len(don_cols) + 1), 5))
        don_colorscale = [
            [0.00, "#ff0000"],            # -2 strong red
            [0.25, "rgba(255,0,0,0.62)"], # -1 weak red
            [0.50, "rgba(0,0,0,0.0)"],    #  0 transparent
            [0.75, "rgba(0,255,0,0.62)"], # +1 weak green
            [1.00, "#00ff00"],            # +2 strong green
        ]
        _add(
            go.Heatmap(
                x=x,
                y=y_levels,
                z=z,
                zmin=-2,
                zmax=2,
                colorscale=don_colorscale,
                showscale=False,
                hovertemplate="Donchian ribbon<br>%{x}<extra></extra>",
            ),
            row=2,
            indicator_label=LABEL["DonchianRibbon"],
            visible=False,
        )

    if "PSAR" in df.columns:
        _add(go.Scatter(x=x, y=df["PSAR"], mode="markers", marker=dict(size=6, color="rgba(59,130,246,0.75)")), row=1, indicator_label=LABEL["PSAR"])

    if "DEMA_9" in df.columns:
        _add(go.Scatter(x=x, y=df["DEMA_9"], mode="lines", line=dict(width=1.6, color="#43A047")), row=1, indicator_label=LABEL["DEMA"])

    # Oscillators (row 2)
    if _has(["WT_LB_wt1", "WT_LB_wt2", "WT_LB_hist"]):
        _add(go.Scatter(x=x, y=df["WT_LB_wt1"], mode="lines", line=dict(color="#22c55e", width=1.8)), row=2, indicator_label=LABEL["WT_LB"], visible=True)
        _add(go.Scatter(x=x, y=df["WT_LB_wt2"], mode="lines", line=dict(color="#ef4444", width=1.8, dash="dot")), row=2, indicator_label=LABEL["WT_LB"], visible=True)
        _add(go.Bar(x=x, y=df["WT_LB_hist"], marker_color="rgba(59,130,246,0.35)"), row=2, indicator_label=LABEL["WT_LB"], visible=True)

    if _has(["ADX", "DI_plus", "DI_minus"]):
        _add(go.Scatter(x=x, y=df["DI_plus"], mode="lines", line=dict(color="#22c55e", width=1.2)), row=2, indicator_label=LABEL["ADX_DI"])
        _add(go.Scatter(x=x, y=df["DI_minus"], mode="lines", line=dict(color="#ef4444", width=1.2)), row=2, indicator_label=LABEL["ADX_DI"])
        _add(go.Scatter(x=x, y=df["ADX"], mode="lines", line=dict(color="#1e3a8a", width=1.6)), row=2, indicator_label=LABEL["ADX_DI"])

    if "OBV_osc" in df.columns:
        obv = df["OBV_osc"]
        obv_pos = obv.where(obv >= 0)
        obv_neg = obv.where(obv < 0)
        fill_gray = "rgba(148,163,184,0.28)"

        _add(
            go.Scatter(
                x=x,
                y=obv_pos,
                mode="lines",
                line=dict(color="#22c55e", width=2),
                fill="tozeroy",
                fillcolor=fill_gray,
                connectgaps=False,
            ),
            row=2,
            indicator_label=LABEL["OBVOSC"],
        )
        _add(
            go.Scatter(
                x=x,
                y=obv_neg,
                mode="lines",
                line=dict(color="#ef4444", width=2),
                fill="tozeroy",
                fillcolor=fill_gray,
                connectgaps=False,
            ),
            row=2,
            indicator_label=LABEL["OBVOSC"],
        )
        _add(
            go.Scatter(
                x=x,
                y=pd.Series(0.0, index=x),
                mode="lines",
                line=dict(color="rgba(148,163,184,0.45)", dash="dot", width=1),
            ),
            row=2,
            indicator_label=LABEL["OBVOSC"],
        )

    if "SQZ_val" in df.columns:
        val = df["SQZ_val"].astype(float)

        # Pine-derived histogram colors (preferred) or compute locally (fallback).
        if "SQZ_bcolor" in df.columns:
            colors = df["SQZ_bcolor"].astype(object).to_numpy()
        else:
            prev = val.shift(1).fillna(0.0)
            colors = np.where(
                (val > 0) & (val > prev),
                "#00e676",
                np.where((val > 0), "#22c55e", np.where((val < prev), "#ef4444", "#7f1d1d")),
            )

        _add(go.Bar(x=x, y=val, marker_color=colors, opacity=0.95), row=2, indicator_label=LABEL["SQZMOM"])

        # Pine squeeze markers at 0 (style=cross) using scolor.
        if "SQZ_scolor" in df.columns:
            scolor = df["SQZ_scolor"].astype(object).to_numpy()
            _add(
                go.Scatter(
                    x=x,
                    y=pd.Series(0.0, index=x),
                    mode="markers",
                    marker=dict(symbol="x", size=7, color=scolor),
                ),
                row=2,
                indicator_label=LABEL["SQZMOM"],
            )
        _add(
            go.Scatter(
                x=x,
                y=pd.Series(0.0, index=x),
                mode="lines",
                line=dict(color="rgba(148,163,184,0.45)", dash="dot", width=1),
            ),
            row=2,
            indicator_label=LABEL["SQZMOM"],
        )

    if _has(["SMI", "SMI_ema"]):
        # Stoch_MTM (Pine): fill only when SMI is beyond OB/OS thresholds.
        smi = df["SMI"].astype(float)
        ema_sig = df["SMI_ema"].astype(float)
        ob = 40.0
        os_ = -40.0
        ob_line = pd.Series(ob, index=x, dtype=float)
        os_line = pd.Series(os_, index=x, dtype=float)

        # Overbought fill (red): between OB line and SMI (only when SMI > OB)
        smi_ob = smi.where(smi > ob)
        _add(
            go.Scatter(
                x=x,
                y=ob_line,
                mode="lines",
                line=dict(color="rgba(0,0,0,0)", width=0),
                hoverinfo="skip",
                showlegend=False,
            ),
            row=2,
            indicator_label=LABEL["SMI"],
        )
        _add(
            go.Scatter(
                x=x,
                y=smi_ob,
                mode="lines",
                line=dict(color="rgba(0,0,0,0)", width=0),
                fill="tonexty",
                fillcolor="rgba(239,68,68,0.40)",  # TradingView: color.new(red, 60)
                connectgaps=False,
                hoverinfo="skip",
                showlegend=False,
            ),
            row=2,
            indicator_label=LABEL["SMI"],
        )

        # Oversold fill (green): between OS line and SMI (only when SMI < OS)
        smi_os = smi.where(smi < os_)
        _add(
            go.Scatter(
                x=x,
                y=os_line,
                mode="lines",
                line=dict(color="rgba(0,0,0,0)", width=0),
                hoverinfo="skip",
                showlegend=False,
            ),
            row=2,
            indicator_label=LABEL["SMI"],
        )
        _add(
            go.Scatter(
                x=x,
                y=smi_os,
                mode="lines",
                line=dict(color="rgba(0,0,0,0)", width=0),
                fill="tonexty",
                fillcolor="rgba(34,197,94,0.40)",  # TradingView: color.new(green, 60)
                connectgaps=False,
                hoverinfo="skip",
                showlegend=False,
            ),
            row=2,
            indicator_label=LABEL["SMI"],
        )

        # Threshold lines
        _add(go.Scatter(x=x, y=ob_line, mode="lines", line=dict(color="rgba(148,163,184,0.55)", width=1.0, dash="dot")), row=2, indicator_label=LABEL["SMI"])
        _add(go.Scatter(x=x, y=os_line, mode="lines", line=dict(color="rgba(148,163,184,0.55)", width=1.0, dash="dot")), row=2, indicator_label=LABEL["SMI"])

        # Main lines (make SMI visible on dark theme)
        _add(go.Scatter(x=x, y=smi, mode="lines", line=dict(color="rgba(226,232,240,0.95)", width=1.4)), row=2, indicator_label=LABEL["SMI"])
        _add(go.Scatter(x=x, y=ema_sig, mode="lines", line=dict(color="#ef4444", width=1.2)), row=2, indicator_label=LABEL["SMI"])

    if _has(["MACD", "MACD_signal", "MACD_hist"]):
        macd_line = df["MACD"]
        sig_line = df["MACD_signal"]
        hist = df["MACD_hist"]
        macd_is_above = (macd_line >= sig_line).fillna(False)
        cross = ((macd_line - sig_line) * (macd_line.shift(1) - sig_line.shift(1)) < 0).fillna(False)
        histA_is_up = (hist > hist.shift(1)) & (hist > 0)
        histA_is_down = (hist < hist.shift(1)) & (hist > 0)
        histB_is_down = (hist < hist.shift(1)) & (hist <= 0)
        histB_is_up = (hist > hist.shift(1)) & (hist <= 0)
        hist_colors = np.where(
            histA_is_up,
            "#22d3ee",
            np.where(histA_is_down, "#2563eb", np.where(histB_is_down, "#ef4444", np.where(histB_is_up, "#7f1d1d", "#facc15"))),
        )
        _add(go.Bar(x=x, y=hist, marker_color=hist_colors), row=2, indicator_label=LABEL["MACD"])
        _add(go.Scatter(x=x, y=pd.Series(0.0, index=x), mode="lines", line=dict(color="rgba(148,163,184,0.55)", width=2)), row=2, indicator_label=LABEL["MACD"])
        _add(go.Scatter(x=x, y=macd_line.where(macd_is_above), mode="lines", line=dict(color="#84cc16", width=4), connectgaps=False), row=2, indicator_label=LABEL["MACD"])
        _add(go.Scatter(x=x, y=macd_line.where(~macd_is_above), mode="lines", line=dict(color="#ef4444", width=4), connectgaps=False), row=2, indicator_label=LABEL["MACD"])
        _add(go.Scatter(x=x, y=sig_line, mode="lines", line=dict(color="#facc15", width=2)), row=2, indicator_label=LABEL["MACD"])
        if cross.any():
            cross_color = np.where(macd_is_above, "#84cc16", "#ef4444")
            _add(go.Scatter(x=x[cross], y=sig_line[cross], mode="markers", marker=dict(symbol="circle", size=10, color=cross_color[cross.to_numpy()])), row=2, indicator_label=LABEL["MACD"])

    if _has(["Volume", "Vol_MA20"]):
        vol = df["Volume"]
        gt = df["Vol_gt_MA20"] if "Vol_gt_MA20" in df.columns else (vol > df["Vol_MA20"])
        vol_colors = np.where(gt.fillna(False), "#22c55e", "rgba(148,163,184,0.55)")
        _add(go.Bar(x=x, y=vol, marker_color=vol_colors, opacity=0.9), row=2, indicator_label=LABEL["VOL_MA"])
        _add(go.Scatter(x=x, y=df["Vol_MA20"], mode="lines", line=dict(color="#facc15", width=2.0)), row=2, indicator_label=LABEL["VOL_MA"])

    if _has(["cRSI", "cRSI_lb", "cRSI_ub"]):
        _add(go.Scatter(x=x, y=df["cRSI"], mode="lines", line=dict(color="#a21caf", width=1.4)), row=2, indicator_label=LABEL["cRSI"])
        _add(go.Scatter(x=x, y=df["cRSI_lb"], mode="lines", line=dict(color="#06b6d4", width=1.0, dash="dash")), row=2, indicator_label=LABEL["cRSI"])
        _add(go.Scatter(x=x, y=df["cRSI_ub"], mode="lines", line=dict(color="#06b6d4", width=1.0, dash="dash")), row=2, indicator_label=LABEL["cRSI"])

    if _has(["Zei_rsi", "Zei_rsi_strength", "Zei_bullish"]):
        bullish = df["Zei_bullish"].fillna(False)
        rsi = df["Zei_rsi"]
        rsi_s = df["Zei_rsi_strength"]
        _add(go.Scatter(x=x, y=pd.Series(70.0, index=x), mode="lines", line=dict(color="rgba(50,211,255,0.0)", width=1)), row=2, indicator_label=LABEL["RSI_Zei"])
        _add(go.Scatter(x=x, y=pd.Series(30.0, index=x), mode="lines", line=dict(color="rgba(50,211,255,0.0)", width=1), fill="tonexty", fillcolor="rgba(50,211,255,0.10)"), row=2, indicator_label=LABEL["RSI_Zei"])
        _add(go.Scatter(x=x, y=pd.Series(50.0, index=x), mode="lines", line=dict(color="rgba(50,211,255,0.35)", width=1, dash="dash")), row=2, indicator_label=LABEL["RSI_Zei"])
        _add(go.Scatter(x=x, y=rsi.where(bullish), mode="lines", line=dict(color="rgba(0,0,0,0)", width=0), connectgaps=False), row=2, indicator_label=LABEL["RSI_Zei"])
        _add(go.Scatter(x=x, y=rsi_s.where(bullish), mode="lines", line=dict(color="#00e676", width=8), fill="tonexty", fillcolor="rgba(0,230,118,0.35)", connectgaps=False), row=2, indicator_label=LABEL["RSI_Zei"])
        _add(go.Scatter(x=x, y=rsi.where(~bullish), mode="lines", line=dict(color="rgba(0,0,0,0)", width=0), connectgaps=False), row=2, indicator_label=LABEL["RSI_Zei"])
        _add(go.Scatter(x=x, y=rsi_s.where(~bullish), mode="lines", line=dict(color="#ef4444", width=8), fill="tonexty", fillcolor="rgba(239,68,68,0.30)", connectgaps=False), row=2, indicator_label=LABEL["RSI_Zei"])
        _add(go.Scatter(x=x, y=rsi, mode="lines", line=dict(color="#32d3ff", width=2.5)), row=2, indicator_label=LABEL["RSI_Zei"])

        # Arrows on the PRICE chart (row 1) when the regime flips.
        # These must toggle together with the RSI Zei indicator in the tickbox menu.
        bull_flip = bullish & (~bullish.shift(1).fillna(False))
        bear_flip = (~bullish) & (bullish.shift(1).fillna(False))
        if bull_flip.any():
            _add(
                go.Scatter(
                    x=x[bull_flip],
                    y=df.loc[bull_flip, "Low"],
                    mode="markers",
                    marker=dict(symbol="triangle-up", size=10, color="#22c55e"),
                ),
                row=1,
                indicator_label=LABEL["RSI_Zei"],
            )
        if bear_flip.any():
            _add(
                go.Scatter(
                    x=x[bear_flip],
                    y=df.loc[bear_flip, "High"],
                    mode="markers",
                    marker=dict(symbol="triangle-down", size=10, color="#ff0000"),
                ),
                row=1,
                indicator_label=LABEL["RSI_Zei"],
            )

    # --- Ichimoku Kinkō Hyō ---
    if _has(["Ichi_tenkan", "Ichi_kijun"]):
        _add(go.Scatter(x=x, y=df["Ichi_tenkan"], mode="lines", line=dict(width=1, color="#ef4444"), name="Tenkan"), row=1, indicator_label=LABEL["Ichimoku"])
        _add(go.Scatter(x=x, y=df["Ichi_kijun"], mode="lines", line=dict(width=1, color="#2962FF"), name="Kijun"), row=1, indicator_label=LABEL["Ichimoku"])
    if _has(["Ichi_senkou_a", "Ichi_senkou_b"]):
        sa = df["Ichi_senkou_a"]
        sb = df["Ichi_senkou_b"]
        bull_cloud = (sa >= sb).fillna(False)
        _add(go.Scatter(x=x, y=sa, mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip"), row=1, indicator_label=LABEL["Ichimoku"])
        _add(go.Scatter(x=x, y=sb.where(bull_cloud), mode="lines", line=dict(width=0), fill="tonexty", fillcolor="rgba(34,197,94,0.12)", showlegend=False, hoverinfo="skip"), row=1, indicator_label=LABEL["Ichimoku"])
        _add(go.Scatter(x=x, y=sa, mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip"), row=1, indicator_label=LABEL["Ichimoku"])
        _add(go.Scatter(x=x, y=sb.where(~bull_cloud), mode="lines", line=dict(width=0), fill="tonexty", fillcolor="rgba(239,68,68,0.12)", showlegend=False, hoverinfo="skip"), row=1, indicator_label=LABEL["Ichimoku"])
    if _has(["Ichi_chikou"]):
        _add(go.Scatter(x=x, y=df["Ichi_chikou"], mode="lines", line=dict(width=1, color="rgba(120,123,134,0.5)"), name="Chikou"), row=1, indicator_label=LABEL["Ichimoku"])

    # --- GK Trend Ribbon ---
    if _has(["GK_zl", "GK_upper", "GK_lower", "GK_trend"]):
        gk_trend = df["GK_trend"]
        _add(go.Scatter(
            x=x, y=df["GK_zl"].where(gk_trend == 1),
            mode="lines", line=dict(width=2, color="#22c55e"), connectgaps=False, name="GK Zero-Lag",
        ), row=1, indicator_label=LABEL["GK_Trend"])
        _add(go.Scatter(
            x=x, y=df["GK_zl"].where(gk_trend == -1),
            mode="lines", line=dict(width=2, color="#ef4444"), connectgaps=False,
        ), row=1, indicator_label=LABEL["GK_Trend"])
        _add(go.Scatter(
            x=x, y=df["GK_zl"].where(gk_trend == 0),
            mode="lines", line=dict(width=2, color="#9ca3af"), connectgaps=False,
        ), row=1, indicator_label=LABEL["GK_Trend"])
        _add(go.Scatter(
            x=x, y=df["GK_upper"], mode="lines",
            line=dict(width=1, color="rgba(148,163,184,0.4)", dash="dot"),
        ), row=1, indicator_label=LABEL["GK_Trend"])
        _add(go.Scatter(
            x=x, y=df["GK_lower"], mode="lines",
            line=dict(width=1, color="rgba(148,163,184,0.4)", dash="dot"),
        ), row=1, indicator_label=LABEL["GK_Trend"])

    # --- Impulse Trend Levels ---
    if _has(["ITL_basis", "ITL_upper", "ITL_lower", "ITL_trend"]):
        itl_trend = df["ITL_trend"]
        _add(go.Scatter(
            x=x, y=df["ITL_basis"].where(itl_trend == 1),
            mode="lines", line=dict(width=2, color="#22c55e"), connectgaps=False, name="Impulse Basis",
        ), row=1, indicator_label=LABEL["Impulse_Trend"])
        _add(go.Scatter(
            x=x, y=df["ITL_basis"].where(itl_trend == -1),
            mode="lines", line=dict(width=2, color="#ef4444"), connectgaps=False,
        ), row=1, indicator_label=LABEL["Impulse_Trend"])
        _add(go.Scatter(
            x=x, y=df["ITL_upper"], mode="lines",
            line=dict(width=1, color="rgba(34,197,94,0.35)", dash="dot"),
        ), row=1, indicator_label=LABEL["Impulse_Trend"])
        _add(go.Scatter(
            x=x, y=df["ITL_lower"], mode="lines",
            line=dict(width=1, color="rgba(239,68,68,0.35)", dash="dot"),
        ), row=1, indicator_label=LABEL["Impulse_Trend"])

    # --- SR Breaks & Retests ---
    if _has(["SR_support", "SR_resistance"]):
        _add(go.Scatter(x=x, y=df["SR_support"], mode="lines", line=dict(width=1, color="#22c55e", dash="dot"), name="Support"), row=1, indicator_label=LABEL["SR_Breaks"])
        _add(go.Scatter(x=x, y=df["SR_resistance"], mode="lines", line=dict(width=1, color="#ef4444", dash="dot"), name="Resistance"), row=1, indicator_label=LABEL["SR_Breaks"])

        def _add_sr_zone_rects(level: pd.Series, edge: pd.Series, fillcolor: str) -> None:
            lv = level.to_numpy(dtype=float)
            ev = edge.to_numpy(dtype=float)
            n = len(lv)
            if n == 0:
                return
            _tf_u = timeframe.upper()
            _hb = pd.Timedelta(days=0.5 if _tf_u == "1D" else (3.5 if _tf_u == "1W" else 0.085))
            i = 0
            while i < n:
                if np.isnan(lv[i]) or np.isnan(ev[i]):
                    i += 1
                    continue
                cur_lv, cur_ev = lv[i], ev[i]
                j = i + 1
                while j < n and not np.isnan(lv[j]) and lv[j] == cur_lv and not np.isnan(ev[j]) and ev[j] == cur_ev:
                    j += 1
                y0, y1 = (min(cur_lv, cur_ev), max(cur_lv, cur_ev))
                fig.add_shape(
                    type="rect",
                    x0=x[i] - _hb, x1=x[j - 1] + _hb,
                    y0=y0, y1=y1,
                    fillcolor=fillcolor, line_width=0,
                    layer="below", row=1, col=1,
                )
                i = j

        _add_sr_zone_rects(df["SR_support"], df["SR_support_lo"], "rgba(34,197,94,0.15)")
        _add_sr_zone_rects(df["SR_resistance"], df["SR_resistance_hi"], "rgba(239,68,68,0.15)")
    if _has(["SR_break_res"]):
        br_mask = df["SR_break_res"].fillna(False).astype(bool)
        if br_mask.any():
            _add(go.Scatter(x=x[br_mask], y=df.loc[br_mask, "SR_resistance"], mode="markers", marker=dict(symbol="triangle-up", size=12, color="#22c55e"), name="Break Res"), row=1, indicator_label=LABEL["SR_Breaks"])
    if _has(["SR_break_sup"]):
        bs_mask = df["SR_break_sup"].fillna(False).astype(bool)
        if bs_mask.any():
            _add(go.Scatter(x=x[bs_mask], y=df.loc[bs_mask, "SR_support"], mode="markers", marker=dict(symbol="triangle-down", size=12, color="#ef4444"), name="Break Sup"), row=1, indicator_label=LABEL["SR_Breaks"])
    if _has(["SR_sup_holds"]):
        sh_mask = df["SR_sup_holds"].fillna(False).astype(bool)
        if sh_mask.any():
            _add(go.Scatter(x=x[sh_mask], y=df.loc[sh_mask, "SR_support"], mode="markers", marker=dict(symbol="diamond", size=8, color="#22c55e"), name="Sup Holds"), row=1, indicator_label=LABEL["SR_Breaks"])
    if _has(["SR_res_holds"]):
        rh_mask = df["SR_res_holds"].fillna(False).astype(bool)
        if rh_mask.any():
            _add(go.Scatter(x=x[rh_mask], y=df.loc[rh_mask, "SR_resistance"], mode="markers", marker=dict(symbol="diamond", size=8, color="#ef4444"), name="Res Holds"), row=1, indicator_label=LABEL["SR_Breaks"])

    # --- Mansfield Relative Strength ---
    if _has(["MRS"]):
        _mrs_bench_cols = [c for c in df.columns if c.startswith("_bench_")]
        _mrs_bench_sym = _mrs_bench_cols[0].replace("_bench_", "") if _mrs_bench_cols else ""
        _mrs_label = f"Mansfield RS (vs {_mrs_bench_sym})" if _mrs_bench_sym else LABEL["Mansfield_RS"]
        mrs = df["MRS"]
        mrs_pos = mrs.where(mrs >= 0)
        mrs_neg = mrs.where(mrs < 0)
        _add(go.Bar(x=x, y=mrs_pos, marker_color="rgba(34,197,94,0.6)", name="MRS+"), row=2, indicator_label=_mrs_label)
        _add(go.Bar(x=x, y=mrs_neg, marker_color="rgba(239,68,68,0.6)", name="MRS-"), row=2, indicator_label=_mrs_label)
        if _has(["MRS_ma"]):
            pass  # zero line is implicit

    # --- Benchmark overlay (18): normalized benchmark close on yaxis2 of row 1 ---
    bench_cols = [c for c in df.columns if c.startswith("_bench_")]
    if bench_cols:
        bc = bench_cols[0]
        bench_name = bc.replace("_bench_", "")
        bench_vals = df[bc].dropna()
        if len(bench_vals) >= 2:
            # Normalize benchmark to same % scale as stock close for visual comparison
            close_first = df["Close"].dropna().iloc[0] if len(df["Close"].dropna()) else 1.0
            bench_first = bench_vals.iloc[0] if bench_vals.iloc[0] != 0 else 1.0
            bench_norm = (df[bc] / bench_first) * close_first
            _add(go.Scatter(
                x=x, y=bench_norm,
                mode="lines", line=dict(width=1.5, color="rgba(168,85,247,0.55)", dash="dash"),
                name=f"{bench_name} (overlay)", visible=False,
            ), row=1, indicator_label=f"Benchmark ({bench_name})")

    # Combo arrays — set to None here; populated below if trend KPIs are available.
    _c3_active: np.ndarray | None = None
    _c4_active: np.ndarray | None = None
    _all_kpi_z: dict[str, list] | None = None
    _eff_c3: list[str] = []
    _eff_c4: list[str] = []
    br_tick_vals: list[str] = []
    br_tick_text: list[str] = []
    tr_tick_vals: list[str] = []
    tr_tick_text: list[str] = []
    trend_kpis: list[str] = []
    br_kpis: list[str] = []

    # KPI timelines (row 3: trend/regime, row 4: breakout/signals)
    kpi_tl = compute_kpi_timeline_matrix(df, precomputed_state=precomputed_kpi_state)
    if kpi_tl.get("kpis"):
        # Build lookup for series by KPI name (so we can split into 2 heatmaps)
        idx_by_name = {name: i for i, name in enumerate(kpi_tl["kpis"])}

        def _slice(names: list[str]) -> tuple[list[str], list[list[int]], list[list[str]]]:
            kk: list[str] = []
            zz: list[list[int]] = []
            cc: list[list[str]] = []
            for n in names:
                i = idx_by_name.get(n)
                if i is None:
                    continue
                row = kpi_tl["z"][i]
                if all(v == -2 for v in row):
                    continue
                kk.append(n)
                zz.append(row)
                cc.append(kpi_tl["custom"][i])
            return kk, zz, cc

        trend_kpis, trend_z, trend_custom = _slice(kpi_tl.get("kpis_trend", []))
        br_kpis, br_z, br_custom = _slice(kpi_tl.get("kpis_breakout", []))

        # Discrete colorscale with zmin=-3, zmax=1 (5 discrete levels).
        # Normalized: -3->0.0, -2->0.25, -1->0.5, 0->0.75, 1->1.0
        eps = 1e-6
        p1 = 0.25   # boundary -3/-2
        p2 = 0.50   # boundary -2/-1
        p3 = 0.75   # boundary -1/0
        colorscale = [
            [0.0, "#ffffff"],         # -3 => separator (white)
            [p1 - eps, "#ffffff"],
            [p1, "#e5e7eb"],          # -2 => NA (light gray)
            [p2 - eps, "#e5e7eb"],
            [p2, "#ff0000"],          # -1 => bearish (red)
            [p3 - eps, "#ff0000"],
            [p3, "#9ca3af"],          #  0 => neutral (gray)
            [1.0 - eps, "#9ca3af"],
            [1.0, "#22c55e"],         #  1 => bullish (green)
        ]
        if br_kpis:
            n_cols = len(x)
            br_labels: list[str] = []
            for i, k in enumerate(br_kpis):
                label = _short_label(k)
                kk = str(k)
                if kk.startswith("BO_"):
                    kk = kk[3:]
                br_labels.append(label)
                row_z = br_z[i]
                row_c = br_custom[i]
                bull_x, bull_text = [], []
                bear_x, bear_text = [], []
                for ci in range(n_cols):
                    if row_z[ci] == 1:
                        bull_x.append(x[ci])
                        bull_text.append(f"{kk}: {row_c[ci]}")
                    elif row_z[ci] == -1:
                        bear_x.append(x[ci])
                        bear_text.append(f"{kk}: {row_c[ci]}")
                if bull_x:
                    _add(
                        go.Scatter(
                            x=bull_x, y=[label] * len(bull_x), mode="markers",
                            marker=dict(symbol="diamond", size=6, color="#22c55e"),
                            text=[kk] * len(bull_x),
                            hoverinfo="text+x",
                            hovertemplate=None,
                        ),
                        row=5, indicator_label="KPI Breakout", visible=True,
                    )
                if bear_x:
                    _add(
                        go.Scatter(
                            x=bear_x, y=[label] * len(bear_x), mode="markers",
                            marker=dict(symbol="diamond", size=6, color="#ef4444"),
                            text=[kk] * len(bear_x),
                            hoverinfo="text+x",
                            hovertemplate=None,
                        ),
                        row=5, indicator_label="KPI Breakout", visible=True,
                    )
            br_tick_vals = br_labels
            br_tick_text = br_labels[:]
            fig.update_yaxes(
                tickmode="array",
                tickvals=br_tick_vals,
                ticktext=br_tick_text,
                tickfont=dict(size=8),
                ticklabelstandoff=8,
                automargin=True,
                categoryorder="array",
                categoryarray=list(reversed(br_labels)),
                row=5, col=1,
            )

        # TrendScore over time (row 3): weighted sum of trend KPI states per bar
        if trend_kpis and trend_z:
            n_cols = len(x)
            _z_arr = np.array(trend_z, dtype=float)
            _weights = np.array([float(kpi_weights.get(k, 1.0)) for k in trend_kpis], dtype=float)
            _mask = np.isin(_z_arr, [1, -1])
            _weighted = np.where(_mask, _z_arr * _weights[:, np.newaxis], 0.0)
            ts_values = _weighted.sum(axis=0)
            ts_colors = ["rgba(34,197,94,0.8)" if v > 0 else "rgba(239,68,68,0.8)" if v < 0 else "rgba(148,163,184,0.5)" for v in ts_values]
            _add(
                go.Bar(
                    x=x, y=ts_values, marker_color=ts_colors,
                    hovertemplate="<b>TrendScore</b>: %{y:.1f}<br>%{x}<extra></extra>",
                ),
                row=3, indicator_label="TrendScore", visible=True,
            )
            ts_max = max(abs(float(v)) for v in ts_values) if len(ts_values) else 1
            ts_pad = max(ts_max * 1.1, 1)
            fig.update_yaxes(
                tickfont=dict(size=8),
                showgrid=False,
                zeroline=True,
                zerolinecolor="rgba(148,163,184,0.5)",
                range=[-ts_pad, ts_pad],
                autorange=False,
                fixedrange=True,
                row=3, col=1,
            )

        # Breakout count over time (row 4): number of bull/bear breakout KPIs per bar
        if br_kpis:
            n_cols = len(x)
            _br_arr = np.array(br_z, dtype=int)
            bo_bull = (_br_arr == 1).sum(axis=0).tolist()
            bo_bear = (-(_br_arr == -1).sum(axis=0)).tolist()
            bo_max = max(max(bo_bull) if bo_bull else 0, max(abs(v) for v in bo_bear) if bo_bear else 0, 1)
            bo_pad = bo_max + 1
            _add(
                go.Bar(
                    x=x, y=bo_bull, marker_color="rgba(34,197,94,0.7)",
                    hovertemplate="<b>Bull Breakouts</b>: %{y}<br>%{x}<extra></extra>",
                ),
                row=4, indicator_label="Breakout Count", visible=True,
            )
            _add(
                go.Bar(
                    x=x, y=bo_bear, marker_color="rgba(239,68,68,0.7)",
                    hovertemplate="<b>Bear Breakouts</b>: %{y}<br>%{x}<extra></extra>",
                ),
                row=4, indicator_label="Breakout Count", visible=True,
            )
            fig.update_yaxes(
                tickfont=dict(size=8),
                showgrid=False,
                zeroline=True,
                zerolinecolor="rgba(148,163,184,0.5)",
                range=[-bo_pad, bo_pad],
                autorange=False,
                fixedrange=True,
                row=4, col=1,
            )

        if trend_kpis:
            n_cols = len(x)

            # Combo-3 / Combo-4 are rendered on the PRICE chart (row 1), not here.
            # Use the full (unfiltered) kpi_tl data. KPIs that are entirely NA
            # in the displayed window are excluded so they don't block combos.
            _all_kpi_z = dict(zip(kpi_tl["kpis"], kpi_tl["z"]))
            _eff_c3 = combo_3_kpis or ["Nadaraya-Watson Smoother", "Madrid Ribbon", "Volume + MA20"]
            _eff_c4 = combo_4_kpis or ["Nadaraya-Watson Smoother", "Madrid Ribbon", "GK Trend Ribbon", "cRSI"]

            def _combo_bool(kpi_list: list) -> np.ndarray:
                rows = []
                for k in kpi_list:
                    row = _all_kpi_z.get(k)
                    if row is None:
                        return np.zeros(n_cols, dtype=bool)
                    rows.append(np.array(row, dtype=int))
                if not rows:
                    return np.zeros(n_cols, dtype=bool)
                return np.all(np.array(rows) == 1, axis=0)

            _c3_active = _combo_bool(_eff_c3)
            _c4_active = _combo_bool(_eff_c4)

            # Separate combo KPIs (in C3 or C4) from regular trend KPIs.
            # Combo KPIs appear at the TOP of the heatmap (last in y-array = top in Plotly).
            # A separator row (z=-3, white) creates a visual gap between groups.
            _combo_kpi_set = set(_eff_c3) | set(_eff_c4)
            regular_entries: list[tuple] = []  # (label, z_row, custom_row, name_row)
            combo_entries: list[tuple] = []
            for i, k in enumerate(trend_kpis):
                entry = (_short_label(k), trend_z[i], trend_custom[i], [k] * n_cols)
                if k in _combo_kpi_set:
                    combo_entries.append(entry)
                else:
                    regular_entries.append(entry)

            # Build ordered lists: regular first (bottom), separator, combo last (top)
            grouped_y: list[str] = []
            grouped_z: list[list[int]] = []
            grouped_custom: list[list[str]] = []
            grouped_kpi_names: list[list[str]] = []
            for label, z_row, custom_row, name_row in regular_entries:
                grouped_y.append(label)
                grouped_z.append(z_row)
                grouped_custom.append(custom_row)
                grouped_kpi_names.append(name_row)
            if combo_entries and regular_entries:
                # White separator row between regular and combo groups
                _sep_label = "__sep_combo"
                grouped_y.append(_sep_label)
                grouped_z.append([-3] * n_cols)
                grouped_custom.append([""] * n_cols)
                grouped_kpi_names.append([""] * n_cols)
            for label, z_row, custom_row, name_row in combo_entries:
                grouped_y.append(label)
                grouped_z.append(z_row)
                grouped_custom.append(custom_row)
                grouped_kpi_names.append(name_row)

            tr_tick_vals = [y for y in grouped_y if not y.startswith("__sep_")]
            tr_tick_text = tr_tick_vals[:]

            _add(
                go.Heatmap(
                    x=x,
                    y=grouped_y,
                    z=grouped_z,
                    zmin=-3,
                    zmax=1,
                    colorscale=colorscale,
                    zsmooth=False,
                    showscale=False,
                    xgap=0,
                    ygap=2,
                    customdata=grouped_kpi_names,
                    text=grouped_custom,
                    hovertemplate="<b>%{customdata}</b><br>%{x}<br>%{text}<extra></extra>",
                ),
                row=6,
                indicator_label="KPI Trend",
                visible=True,
            )
            fig.update_yaxes(
                tickmode="array",
                tickvals=tr_tick_vals,
                ticktext=tr_tick_text,
                tickfont=dict(size=8),
                ticklabelstandoff=8,
                automargin=True,
                categoryorder="array",
                categoryarray=grouped_y,
                row=6,
                col=1,
            )

    _add_combo_overlay(fig, df, x, _c3_active, _c4_active, timeframe, _add, LABEL)

    _add_exit_flow_overlay(
        fig, df, x,
        _c3_active, _c4_active,
        _eff_c3, _eff_c4,
        timeframe, _add, LABEL, _all_kpi_z,
    )

    _title = f"{display_name} ({symbol}) — {timeframe}" if display_name else f"{symbol} — {timeframe}"
    _n_br = len(br_tick_vals) if br_tick_vals else 0
    _n_tr = len(tr_tick_vals) if tr_tick_vals else 0
    _n_kpi = max(_n_br + _n_tr, 1)
    _kpi_share = 0.34
    _br_share = (_kpi_share * _n_br / _n_kpi) if _n_kpi else 0.17
    _tr_share = (_kpi_share * _n_tr / _n_kpi) if _n_kpi else 0.17
    _ts_share = 0.05
    _bo_share = 0.05
    _price_share = 0.32
    _osc_share = 1.0 - _price_share - _br_share - _tr_share - _ts_share - _bo_share
    fig.update_layout(
        title=_title,
        template="plotly_white",
        autosize=False,
        showlegend=False,
        xaxis_rangeslider_visible=False,
        margin=dict(l=160, r=28, t=70, b=40),
        height=2600,
        hovermode="x unified",
        spikedistance=-1,
        hoverdistance=-1,
    )
    _gap = 0.02
    # Bottom-up: row6=trend, row5=breakout dots, row4=breakout count, row3=TrendScore, row2=osc, row1=price
    _row6_bot = 0.0
    _row6_top = _row6_bot + _tr_share
    _row5_bot = _row6_top + _gap
    _row5_top = _row5_bot + _br_share
    _row4_bot = _row5_top + _gap
    _row4_top = _row4_bot + _bo_share
    _row3_bot = _row4_top + _gap
    _row3_top = _row3_bot + _ts_share
    _row2_bot = _row3_top + _gap
    _row2_top = _row2_bot + _osc_share
    _row1_bot = _row2_top + _gap
    _row1_top = 1.0
    fig.update_layout(
        yaxis=dict(domain=[_row1_bot, _row1_top]),
        yaxis2=dict(domain=[_row2_bot, _row2_top]),
        yaxis3=dict(domain=[_row3_bot, _row3_top]),
        yaxis4=dict(domain=[_row4_bot, _row4_top]),
        yaxis5=dict(domain=[_row5_bot, _row5_top]),
        yaxis6=dict(domain=[_row6_bot, _row6_top]),
    )
    fig.update_xaxes(showgrid=True, gridcolor="rgba(148,163,184,0.25)")
    # Set an explicit x-range on ALL axes so every subplot aligns identically.
    # A small half-bar padding keeps the first/last bars from being clipped.
    try:
        if len(x) >= 2:
            _tf_u = timeframe.upper()
            _pad = pd.Timedelta(days=1 if _tf_u == "1D" else (4 if _tf_u == "1W" else 0.25))
            _x_min = pd.to_datetime(x.min()) - _pad
            _x_max = pd.to_datetime(x.max()) + _pad
            fig.update_xaxes(range=[str(_x_min), str(_x_max)])
    except Exception as exc:
        logger.debug("Failed to set x-axis range on figure: %s", exc)
        pass
    for r in (1, 2, 3, 4, 5, 6):
        fig.update_xaxes(showticklabels=True, row=r, col=1)
    fig.update_xaxes(
        showspikes=True,
        # Ensure the vertical hover line spans all subplots.
        spikemode="across+toaxis",
        spikesnap="cursor",
        spikecolor="rgba(0,0,0,0.85)",
        spikethickness=1.5,
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor="rgba(148,163,184,0.25)",
        automargin=True,
        tickfont=dict(size=10),
        ticklabeloverflow="hide past domain",
    )

    # Re-apply KPI tick settings after global y-axis styling so labels are never dropped/clamped.
    try:
        if br_kpis and br_tick_vals:
            fig.update_yaxes(
                tickmode="array",
                tickvals=br_tick_vals,
                ticktext=br_tick_text,
                tickfont=dict(size=8),
                ticklabelstandoff=10,
                automargin=True,
                row=5,
                col=1,
            )
        if trend_kpis and tr_tick_vals:
            fig.update_yaxes(
                tickmode="array",
                tickvals=tr_tick_vals,
                ticktext=tr_tick_text,
                tickfont=dict(size=8),
                ticklabelstandoff=10,
                automargin=True,
                row=6,
                col=1,
            )
    except Exception as exc:
        logger.debug("Failed to re-apply KPI tick settings on figure: %s", exc)
        pass
    return fig


if __name__ == "__main__":
    # Verification: ensure module and public API import correctly
    import sys
    try:
        from apps.dashboard.figures import (
            compute_kpi_timeline_matrix,
        )
        print("figures module verification OK: build_figure_for_symbol_timeframe, compute_kpi_timeline_matrix", file=sys.stderr)
    except Exception as e:
        print(f"figures import failed (may be due to dependencies): {e}", file=sys.stderr)
        sys.exit(1)

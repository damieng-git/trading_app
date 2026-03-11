"""Indicator-specific trace building and combo overlays for trading dashboard figures."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from apps.dashboard.strategy import EXIT_PARAMS as _EXIT_FLOW_V4_PARAMS


def compute_kpi_timeline_matrix(df: pd.DataFrame, *, precomputed_state: dict | None = None) -> dict:
    """
    Compute a timeline matrix for KPI states.

    Encodes each KPI per bar as:
      1  = bullish (green)
      0  = neutral (gray)
     -1  = bearish (red)
     -2  = unavailable (light gray)

    Returns:
      {
        "kpis": [kpi_name...],
        "z": [[... per x] ... per kpi],
        "custom": [[... text per x] ... per kpi],
      }
    """
    if df is None or df.empty:
        return {"kpis": [], "z": [], "custom": []}

    # Single-source KPI rules + ordering
    from trading_dashboard.kpis.catalog import (  # local import
        KPI_BREAKOUT_ORDER,
        KPI_ORDER,
        KPI_TREND_ORDER,
        compute_kpi_state_map,
    )
    from trading_dashboard.kpis.rules import (  # local import
        STATE_BEAR,
        STATE_BULL,
        STATE_NA,
        STATE_NEUTRAL,
        state_from_signals,
    )

    kpi_trend_order = KPI_TREND_ORDER
    kpi_breakout_order = KPI_BREAKOUT_ORDER
    kpi_order = KPI_ORDER

    state = precomputed_state if precomputed_state is not None else compute_kpi_state_map(df)

    # -------------------------------------------------------------------------
    # Derived breakout-style signals from trend regimes (EVENT mode)
    #
    # For each TREND KPI, create an event series that only fires on state transitions:
    # - bullish event: state becomes bullish at t (from non-bull)
    # - bearish event: state becomes bearish at t (from non-bear)
    #
    # These are exposed in the dashboard breakout panel as BO_<KPI>.
    # -------------------------------------------------------------------------
    derived_breakout_names: list[str] = []
    derived_breakout_state: dict[str, pd.Series] = {}
    for name in kpi_trend_order:
        s = state.get(name)
        if s is None:
            continue
        s = s.reindex(df.index).fillna(STATE_NA).astype(int)
        avail = s.ne(STATE_NA)
        prev = s.shift(1)
        bull_sig = s.eq(STATE_BULL) & prev.ne(STATE_BULL)
        bear_sig = s.eq(STATE_BEAR) & prev.ne(STATE_BEAR)
        bo = state_from_signals(df.index, bull_sig.fillna(False), bear_sig.fillna(False), avail.fillna(False))
        bo_name = f"BO_{name}"
        derived_breakout_names.append(bo_name)
        derived_breakout_state[bo_name] = bo

    # Encode to z/custom matrices in the desired order
    z_rows: list[list[int]] = []
    c_rows: list[list[str]] = []
    full_kpi_order = list(kpi_order) + derived_breakout_names
    for name in full_kpi_order:
        if name in derived_breakout_state:
            s = derived_breakout_state[name]
        else:
            s = state.get(name, pd.Series(STATE_NA, index=df.index, dtype=int)).reindex(df.index).fillna(STATE_NA).astype(int)
        z = s.to_numpy(dtype=int)
        txt = np.where(
            z == STATE_BULL,
            "Bullish",
            np.where(z == STATE_BEAR, "Bearish", np.where(z == STATE_NEUTRAL, "Neutral", "NA")),
        ).astype(str)
        z_rows.append(z.tolist())
        c_rows.append(txt.tolist())

    return {
        "kpis": full_kpi_order,
        "z": z_rows,
        "custom": c_rows,
        "kpis_trend": kpi_trend_order,
        # Breakout panel = native breakout KPIs + derived BO_ trend events
        "kpis_breakout": list(kpi_breakout_order) + derived_breakout_names,
    }


def _add_exit_flow_overlay(
    fig: go.Figure,
    df: pd.DataFrame,
    x: pd.Index,
    c3_active: np.ndarray | None,
    c4_active: np.ndarray | None,
    combo_3_kpis: list[str],
    combo_4_kpis: list[str],
    timeframe: str,
    _add,
    LABEL: dict,
    kpi_z_map: dict[str, list] | None = None,
    *,
    position_events: list[dict] | None = None,
) -> None:
    """Pure renderer for pre-computed position events from ``strategy.py``.

    When *position_events* is provided the overlay renders directly from
    that list (single source of truth).  When absent, events are computed
    on the fly from the plot-window data as a fallback for backward
    compatibility.
    """
    tf_key = timeframe.upper()
    params = _EXIT_FLOW_V4_PARAMS.get(tf_key, _EXIT_FLOW_V4_PARAMS["1D"])
    _T, _M, _K = int(params["T"]), int(params["M"]), float(params["K"])
    n_bars = len(df)
    if n_bars < 20:
        return

    close = df["Close"].to_numpy(dtype=float)

    tf_u = timeframe.upper()
    half_bar = pd.Timedelta(days=0.5 if tf_u == "1D" else (3.5 if tf_u == "1W" else 0.085))

    # Build trades list from pre-computed events (or fallback)
    trades: list[dict] = []

    if position_events is not None:
        for ev in position_events:
            ei = ev["entry_idx"]
            xi = min(ev["exit_idx"], n_bars - 1)
            si = ev.get("scale_idx")
            if si is not None:
                si = min(si, n_bars - 1)

            ep = ev["entry_price"]
            xp = ev.get("exit_price")
            if xp is None:
                xp = float(close[xi])
            ret = (xp - ep) / ep * 100 if ep > 0 else 0.0

            trail = ev.get("stop_trail", [])
            if not trail:
                trail = [ep * 0.95]

            trades.append({
                "entry_idx": ei, "exit_idx": xi,
                "ep": ep, "xp": xp, "ret": ret, "hold": xi - ei,
                "scaled": ev["scaled"], "scale_idx": si,
                "exit_reason": ev["exit_reason"],
                "stop_trail": trail,
            })
    else:
        # Fallback: compute from plot-window data (backward compat)
        if c3_active is None or kpi_z_map is None:
            return
        if c4_active is None:
            c4_active = np.zeros_like(c3_active, dtype=bool)

        from apps.dashboard.strategy import compute_position_events as _cpe
        _st_from_z: dict = {}
        for kpi_name, z_row in kpi_z_map.items():
            _st_from_z[kpi_name] = pd.Series(
                np.array(z_row, dtype=float), index=df.index)

        raw = _cpe(df, _st_from_z, list(combo_3_kpis), list(combo_4_kpis), tf_u)
        for ev in raw:
            ei = ev["entry_idx"]
            xi = min(ev["exit_idx"], n_bars - 1)
            si = ev.get("scale_idx")
            ep = ev["entry_price"]
            xp = ev.get("exit_price")
            if xp is None:
                xp = float(close[xi])
            ret = (xp - ep) / ep * 100 if ep > 0 else 0.0
            trail = ev.get("stop_trail", [ep * 0.95])
            trades.append({
                "entry_idx": ei, "exit_idx": xi,
                "ep": ep, "xp": xp, "ret": ret, "hold": xi - ei,
                "scaled": ev["scaled"], "scale_idx": si,
                "exit_reason": ev["exit_reason"],
                "stop_trail": trail,
            })

    if not trades:
        return

    # --- Render position shading ---
    for t in trades:
        ei, xi = t["entry_idx"], t["exit_idx"]
        if t["scaled"] and t["scale_idx"] is not None and t["scale_idx"] > ei:
            si = t["scale_idx"]
            fig.add_shape(
                type="rect", x0=x[ei] - half_bar, x1=x[min(si, xi)] + half_bar,
                y0=0, y1=1, yref="y domain",
                fillcolor="rgba(34,197,94,0.07)", line_width=0, layer="below",
                row=1, col=1,
            )
            fig.add_shape(
                type="rect", x0=x[si] - half_bar, x1=x[xi] + half_bar,
                y0=0, y1=1, yref="y domain",
                fillcolor="rgba(255,152,0,0.10)", line_width=0, layer="below",
                row=1, col=1,
            )
        else:
            color = "rgba(255,152,0,0.10)" if t["scaled"] else "rgba(34,197,94,0.07)"
            fig.add_shape(
                type="rect", x0=x[ei] - half_bar, x1=x[xi] + half_bar,
                y0=0, y1=1, yref="y domain",
                fillcolor=color, line_width=0, layer="below",
                row=1, col=1,
            )

    # --- ATR stop-loss line ---
    stop_x_all, stop_y_all = [], []
    for t in trades:
        ei, xi = t["entry_idx"], t["exit_idx"]
        trail = t["stop_trail"]
        seg_x = [x[j] for j in range(ei, min(xi + 1, n_bars))]
        seg_y = [float(trail[j - ei]) if (j - ei) < len(trail) else float(trail[-1]) for j in range(ei, min(xi + 1, n_bars))]
        stop_x_all.extend(seg_x + [None])
        stop_y_all.extend(seg_y + [None])

    if stop_x_all:
        _add(
            go.Scatter(
                x=stop_x_all, y=stop_y_all,
                mode="lines",
                line=dict(color="rgba(239,68,68,0.55)", width=1.5, dash="dot"),
                hoverinfo="skip",
            ),
            row=1, indicator_label=LABEL["Price"], visible=True,
        )

    # --- Zone hover traces (invisible — fire on hover over trading zone) ---
    for t in trades:
        ei, xi = t["entry_idx"], t["exit_idx"]
        ep = t["ep"]
        xp = t["xp"]
        ret = t["ret"]
        atr_pct = abs(ep - t["stop_trail"][0]) / ep * 100 if ep > 0 else 0.0
        exit_date = str(x[xi])[:10] if t["exit_reason"] != "Open" else "Open"
        bars = xi - ei
        if tf_u == "4H":
            length_str = f"{bars * 4}h"
        elif tf_u == "1D":
            length_str = f"{bars}d"
        elif tf_u == "1W":
            length_str = f"{bars}w"
        elif tf_u == "2W":
            length_str = f"{bars * 2}w"
        elif tf_u == "1M":
            length_str = f"{bars}mo"
        else:
            length_str = f"{bars} bars"
        sizing = "1.5\u00d7" if t["scaled"] else "1\u00d7"
        sign = "\u25b2" if ret >= 0 else "\u25bc"
        hover_html = (
            f"<b>{sign} {ret:+.1f}% ({sizing})</b><br>"
            f"Entry:  {ep:.2f}<br>"
            f"Exit:   {xp:.2f}  ({exit_date})<br>"
            f"ATR:    {atr_pct:.1f}% of price<br>"
            f"Length: {length_str}"
        )
        zone_x = [x[j] for j in range(ei, min(xi + 1, n_bars))]
        zone_y = [float(close[j]) for j in range(ei, min(xi + 1, n_bars))]
        _add(
            go.Scatter(
                x=zone_x, y=zone_y,
                mode="markers",
                marker=dict(size=18, opacity=0),
                hovertemplate=hover_html + "<extra></extra>",
                showlegend=False,
                name="",
            ),
            row=1, indicator_label=LABEL["Price"], visible=True,
        )


def _add_combo_overlay(
    fig: go.Figure,
    df: pd.DataFrame,
    x: pd.Index,
    c3_active: np.ndarray | None,
    c4_active: np.ndarray | None,
    timeframe: str,
    _add,
    LABEL: dict,
) -> None:
    """Export combo zone metadata for JS hover overlays.

    Position shading and entry/exit markers are handled by _add_exit_flow_overlay
    (unified position model). This function only emits the zone metadata used by
    the client-side combo hover tooltip.
    """
    if c3_active is None:
        return
    if c4_active is None:
        c4_active = np.zeros_like(c3_active, dtype=bool)

    def _zone_ranges(mask: np.ndarray):
        diff = np.diff(np.concatenate(([0], mask.astype(int), [0])))
        starts = np.where(diff == 1)[0]
        ends = np.where(diff == -1)[0] - 1
        return zip(starts, ends)

    _combo_zone_meta: list[dict] = []
    for mask, combo_name in [(c3_active, "C3"), (c4_active, "C4")]:
        if not mask.any():
            continue
        for s, e in _zone_ranges(mask):
            _combo_zone_meta.append({
                "name": combo_name,
                "start": str(x[s])[:10],
                "end": str(x[e])[:10],
                "bars": int(e - s + 1),
                "x0": str(x[s]),
                "x1": str(x[e]),
            })
    fig.update_layout(meta={"combo_zones": _combo_zone_meta})

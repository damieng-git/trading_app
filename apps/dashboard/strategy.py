"""Entry Strategy v5 + Exit Flow v4 engine — entry detection, position tracking, ATR stop.

Entry v5 gates (Phase 13+14 backtested):
  1. C3 onset-only (transition, not continuation)
  2. SMA20 > SMA200 (1D/1W) — structural uptrend gate
  3. Volume spike 1.5× within last 5 bars — momentum confirmation
  4. Overextension ≤15% in 5 bars (1W only)
Exit Flow v4: two-stage KPI invalidation + ATR stop with M-bar checkpoint.

See research/kpi_optimization/STRATEGY.md for full specification.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

_CONFIG_PATH = Path(__file__).resolve().parent / "configs" / "config.json"

def _load_exit_params() -> dict[str, dict[str, int | float]]:
    """Load EXIT_PARAMS from config.json with hardcoded fallback."""
    defaults = {
        "4H": {"T": 4, "M": 48, "K": 4.0},
        "1D": {"T": 4, "M": 40, "K": 4.0},
        "1W": {"T": 2, "M": 20, "K": 4.0},
        "2W": {"T": 2, "M": 10, "K": 4.0},
        "1M": {"T": 1, "M": 6, "K": 4.0},
    }
    try:
        cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        return cfg.get("exit_params", defaults)
    except Exception:
        return defaults

EXIT_PARAMS: dict[str, dict[str, int | float]] = _load_exit_params()
ATR_PERIOD = 14

# 1W overextension filter: block C3 entry if Close > 15% above Close[5 bars ago]
_OVEREXT_LOOKBACK = 5
_OVEREXT_PCT = 15.0

# v5: Volume spike confirmation — entry blocked unless vol >= 1.5× vol_ma20
# within the last 5 bars (inclusive of entry bar)
_VOL_SPIKE_MULT = 1.5
_VOL_SPIKE_LOOKBACK = 5


def compute_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    """14-period Average True Range."""
    h, lo, pc = df["High"], df["Low"], df["Close"].shift(1)
    tr = pd.concat([h - lo, (h - pc).abs(), (lo - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()


# ---------------------------------------------------------------------------
# Single source of truth: position event list
# ---------------------------------------------------------------------------

def compute_position_events(
    df: pd.DataFrame,
    st: dict,
    c3_kpis: list,
    c4_kpis: list,
    tf: str,
    *,
    scan_start: int | None = None,
) -> list[dict]:
    """Forward-walk Entry v5 + Exit Flow v4 and return every position event.

    This is the **single source of truth** for entries, scale-ups, and exits.
    All downstream consumers (screener status, chart overlays, JS renderer)
    must derive their views from this event list rather than recomputing the
    position model independently.

    Each trade dict contains:
      signal_idx   – bar index where C3 onset fired (signal bar)
      entry_idx    – bar index where position is filled (signal_idx + 1)
      entry_price  – next-bar open fill price
      exit_idx     – bar index where exit is detected (or last bar if open)
      exit_price   – next-bar open fill price (or close on last bar)
      exit_reason  – "ATR stop" | "Full invalidation" | "N/M KPIs bearish"
                     | "Checkpoint exit" | "Open"
      scaled       – True if C4 fired (at entry or mid-position)
      scale_idx    – bar index of C4 scale-up (None if never scaled)
      stop_trail   – list[float] per bar from entry_idx to exit_idx inclusive
      hold         – number of bars held (exit_idx - entry_idx)
      ret_pct      – return % after commission + slippage, weighted by 1.5x
                     if scaled (None for open positions)

    Parameters
    ----------
    scan_start : int or None
        First bar index to scan.  ``None`` → scan from bar 0.
    """
    params = EXIT_PARAMS.get(tf)
    if not params or df is None or df.empty or len(df) < 20 or not c3_kpis:
        return []
    required_cols = {"High", "Low", "Close"}
    if not required_cols.issubset(df.columns):
        return []

    T, M, K = params["T"], params["M"], params["K"]
    cl = df["Close"].to_numpy(float)
    df["High"].to_numpy(float)
    df["Low"].to_numpy(float)
    op = df["Open"].to_numpy(float) if "Open" in df.columns else cl.copy()
    atr = compute_atr(df, ATR_PERIOD).to_numpy(float)
    n = len(df)

    if any(st.get(k) is None for k in c3_kpis):
        return []

    def _kpi_bull(kpi_name, idx):
        s = st.get(kpi_name)
        if s is None or idx >= len(s):
            return False
        v = s.iloc[idx]
        return not pd.isna(v) and int(v) == 1

    def _all_bull(kpi_list, idx):
        return all(_kpi_bull(k, idx) for k in kpi_list)

    c4_avail = all(st.get(k) is not None for k in c4_kpis)

    # v5 gate: SMA20 > SMA200 (1D/1W only)
    sma_gate = None
    if tf in ("1D", "1W") and n >= 200:
        sma200 = pd.Series(cl).rolling(200, min_periods=200).mean().to_numpy()
        sma20 = pd.Series(cl).rolling(20, min_periods=20).mean().to_numpy()
        sma_gate = sma20 >= sma200

    # 1W overextension filter
    overext_filter = None
    if tf == "1W" and n > _OVEREXT_LOOKBACK:
        ref = np.empty(n, dtype=float)
        ref[:_OVEREXT_LOOKBACK] = np.nan
        ref[_OVEREXT_LOOKBACK:] = cl[:-_OVEREXT_LOOKBACK]
        with np.errstate(divide="ignore", invalid="ignore"):
            pct_chg = (cl - ref) / ref * 100
        overext_filter = ~(pct_chg > _OVEREXT_PCT)

    # v5 gate: volume spike 1.5× within last 5 bars
    vol_spike_ok = None
    if "Volume" in df.columns:
        vol = df["Volume"].to_numpy(float)
        vol_ma20 = pd.Series(vol).rolling(20, min_periods=20).mean().to_numpy()
        with np.errstate(invalid="ignore"):
            spike_raw = (vol >= _VOL_SPIKE_MULT * vol_ma20).astype(float)
        spike_raw = np.nan_to_num(spike_raw, nan=0.0)
        vol_spike_ok = pd.Series(spike_raw).rolling(
            _VOL_SPIKE_LOOKBACK, min_periods=1
        ).max().to_numpy().astype(bool)

    start = scan_start if scan_start is not None else 0
    events: list[dict] = []
    i = start
    while i < n:
        c3_on = _all_bull(c3_kpis, i)
        c3_onset = c3_on and (i == 0 or not _all_bull(c3_kpis, i - 1))
        if not c3_onset:
            i += 1
            continue
        if sma_gate is not None and not sma_gate[i]:
            i += 1
            continue
        if overext_filter is not None and not overext_filter[i]:
            i += 1
            continue
        if vol_spike_ok is not None and not vol_spike_ok[i]:
            i += 1
            continue

        signal_idx = i
        fill_bar = i + 1
        if fill_bar >= n:
            break
        entry_price = float(op[fill_bar])
        if entry_price <= 0 or np.isnan(entry_price):
            i += 1
            continue

        scaled = c4_avail and _all_bull(c4_kpis, i)
        scale_idx = i if scaled else None
        active_kpis = c4_kpis if scaled else c3_kpis
        stop_price = entry_price
        atr_val = atr[i]
        stop = (stop_price - K * atr_val
                if not np.isnan(atr_val) and atr_val > 0
                else stop_price * 0.95)
        bars_since_reset = 0
        entry_idx = fill_bar
        stop_trail: list[float] = [stop]

        exit_idx = None
        exit_reason = None

        j = fill_bar + 1
        while j < n:
            bars_since_reset += 1
            c = float(cl[j])
            if np.isnan(c):
                stop_trail.append(stop_trail[-1] if stop_trail else stop)
                j += 1
                continue

            if c < stop:
                exit_idx = j
                exit_reason = "ATR stop"
                break

            if not scaled and c4_avail and _all_bull(c4_kpis, j):
                scaled = True
                scale_idx = j
                active_kpis = c4_kpis

            nk = len(active_kpis)
            nb = sum(1 for kk in active_kpis if not _kpi_bull(kk, j))
            bars_held = j - entry_idx

            if bars_held <= T:
                if nb >= nk:
                    exit_idx = j
                    exit_reason = "Full invalidation"
                    break
            else:
                if nb >= 2:
                    exit_idx = j
                    exit_reason = f"{nb}/{nk} KPIs bearish"
                    break

            if bars_since_reset >= M:
                if nb == 0:
                    stop_price = c
                    a_val = atr[j] if j < len(atr) else np.nan
                    stop = (stop_price - K * a_val
                            if not np.isnan(a_val) and a_val > 0
                            else stop)
                    bars_since_reset = 0
                else:
                    exit_idx = j
                    exit_reason = "Checkpoint exit"
                    break

            stop_trail.append(stop)
            j += 1

        if exit_idx is None:
            exit_idx = n - 1
            exit_reason = "Open"

        while len(stop_trail) < (exit_idx - entry_idx + 1):
            stop_trail.append(stop_trail[-1] if stop_trail else stop)

        # Fill prices
        is_open = exit_reason == "Open"
        exit_fill = min(exit_idx + 1, n - 1) if not is_open and exit_idx < n - 1 else exit_idx
        xp = float(op[exit_fill]) if exit_fill != exit_idx else float(cl[exit_idx])
        hold = exit_idx - entry_idx
        cost = COMMISSION + SLIPPAGE
        weight = 1.5 if scaled else 1.0
        ret_pct = (((xp - entry_price) / entry_price - cost) * 100 * weight
                   if entry_price > 0 and not is_open else None)

        events.append({
            "signal_idx": signal_idx,
            "entry_idx": entry_idx,
            "entry_price": round(entry_price, 4),
            "exit_idx": exit_idx,
            "exit_price": round(xp, 4) if not is_open else None,
            "exit_reason": exit_reason,
            "scaled": scaled,
            "scale_idx": scale_idx,
            "stop_trail": [round(s, 4) if np.isfinite(s) else None for s in stop_trail],
            "hold": hold,
            "ret_pct": round(ret_pct, 2) if ret_pct is not None else None,
        })

        i = exit_idx + 1 if not is_open else n

    return events


def _status_from_events(
    events: list[dict],
    n: int,
    cl: np.ndarray,
    atr: np.ndarray,
    st: dict,
    active_kpis_for_last: list[str],
    tf: str,
    K: float,
    T: int,
) -> dict:
    """Derive the screener-facing position status from a pre-computed event list."""
    flat_result = {
        "signal_action": "FLAT", "entry_bar_idx": None, "entry_price": None,
        "atr_stop": None, "bars_held": None, "exit_stage": None,
        "bearish_kpis": 0, "c4_scaled": False,
        "last_exit_bars_ago": None, "last_exit_reason": None,
    }
    if not events:
        return flat_result

    last = events[-1]
    if last["exit_reason"] != "Open":
        result = dict(flat_result)
        result["last_exit_bars_ago"] = (n - 1) - last["exit_idx"]
        result["last_exit_reason"] = last["exit_reason"]
        return result

    entry_idx = last["entry_idx"]
    scaled = last["scaled"]
    scale_idx = last["scale_idx"]
    kpis = active_kpis_for_last
    bars_held = (n - 1) - entry_idx
    combo_anchor = scale_idx if scale_idx is not None else entry_idx
    combo_bars = (n - 1) - combo_anchor

    def _kpi_bull(kpi_name, idx):
        s = st.get(kpi_name)
        if s is None or idx >= len(s):
            return False
        v = s.iloc[idx]
        return not pd.isna(v) and int(v) == 1

    nb_now = sum(1 for kk in kpis if not _kpi_bull(kk, n - 1))
    stage = "lenient" if bars_held <= T else "strict"

    atr[n - 1] if n - 1 < len(atr) else np.nan
    stop_trail = last.get("stop_trail", [])
    stop = stop_trail[-1] if stop_trail else None
    if stop is None:
        stop = float(cl[entry_idx]) * 0.95

    if combo_bars == 0:
        action = "ENTRY 1.5x" if scaled else "ENTRY 1x"
    else:
        action = "HOLD"

    return {
        "signal_action": action,
        "entry_bar_idx": entry_idx,
        "entry_price": last["entry_price"],
        "atr_stop": round(stop, 2) if np.isfinite(stop) else None,
        "bars_held": bars_held,
        "combo_bars": combo_bars,
        "exit_stage": stage,
        "bearish_kpis": nb_now,
        "c4_scaled": scaled,
        "last_exit_bars_ago": None,
        "last_exit_reason": None,
    }


def compute_position_status(
    df: pd.DataFrame,
    st: dict,
    c3_kpis: list,
    c4_kpis: list,
    tf: str,
) -> dict:
    """Thin wrapper: derive current position status from ``compute_position_events``.

    Returns the same dict shape as before (signal_action, entry_bar_idx, etc.)
    but delegates all position logic to the single-source-of-truth event engine.
    Only scans the last 500 bars for screener performance.
    """
    flat_result = {
        "signal_action": "FLAT", "entry_bar_idx": None, "entry_price": None,
        "atr_stop": None, "bars_held": None, "exit_stage": None,
        "bearish_kpis": 0, "c4_scaled": False,
        "last_exit_bars_ago": None, "last_exit_reason": None,
    }

    params = EXIT_PARAMS.get(tf)
    if not params or df is None or df.empty or len(df) < 20 or not c3_kpis:
        return flat_result
    if not {"High", "Low", "Close"}.issubset(df.columns):
        return flat_result

    n = len(df)
    scan_start = max(0, n - 500)
    events = compute_position_events(df, st, c3_kpis, c4_kpis, tf,
                                     scan_start=scan_start)

    T, K = params["T"], params["K"]
    cl = df["Close"].to_numpy(float)
    atr_arr = compute_atr(df, ATR_PERIOD).to_numpy(float)

    last = events[-1] if events else None
    active_kpis = (c4_kpis if last and last["scaled"] else c3_kpis) if last else c3_kpis
    return _status_from_events(events, n, cl, atr_arr, st, active_kpis, tf, K, T)


COMMISSION = 0.001  # 0.1% per trade
SLIPPAGE = 0.005    # 0.5% flat slippage per trade


# ---------------------------------------------------------------------------
# Polarity-aware position engine (Phase 20C strategies)
# ---------------------------------------------------------------------------

def _kpi_match_pol(st: dict, kpi_name: str, polarity: int, idx: int) -> bool:
    """Check if KPI state matches the expected polarity at bar idx."""
    s = st.get(kpi_name)
    if s is None or idx >= len(s):
        return False
    v = s.iloc[idx]
    if pd.isna(v):
        return False
    return int(v) == polarity


def _all_match_pol(st: dict, kpis: list, pols: list, idx: int) -> bool:
    return all(_kpi_match_pol(st, k, p, idx) for k, p in zip(kpis, pols))


def compute_polarity_position_events(
    df: pd.DataFrame,
    st: dict,
    c3_kpis: list[str],
    c3_pols: list[int],
    c4_kpis: list[str] | None,
    c4_pols: list[int] | None,
    tf: str,
    *,
    exit_kpis: list[str] | None = None,
    exit_pols: list[int] | None = None,
    scan_start: int | None = None,
) -> list[dict]:
    """Polarity-aware position engine for mixed-polarity combos.

    Identical to compute_position_events but checks each KPI against its
    expected polarity (not hardcoded bullish).

    Parameters
    ----------
    exit_kpis/exit_pols : optional separate exit KPI set (for cross-TF strategies
                          like dip_buy where entry and exit use different KPIs).
                          If None, uses the entry combo for exit checks.
    """
    params = EXIT_PARAMS.get(tf)
    if not params or df is None or df.empty or len(df) < 20 or not c3_kpis:
        return []
    if not {"High", "Low", "Close"}.issubset(df.columns):
        return []

    T, M, K = params["T"], params["M"], params["K"]
    cl = df["Close"].to_numpy(float)
    df["High"].to_numpy(float)
    df["Low"].to_numpy(float)
    op = df["Open"].to_numpy(float) if "Open" in df.columns else cl.copy()
    atr = compute_atr(df, ATR_PERIOD).to_numpy(float)
    n = len(df)

    if any(st.get(k) is None for k in c3_kpis):
        return []

    c4_avail = c4_kpis and c4_pols and all(st.get(k) is not None for k in c4_kpis)

    # v5 gates: SMA20 > SMA200
    sma_gate = None
    if tf in ("1D", "1W") and n >= 200:
        sma200 = pd.Series(cl).rolling(200, min_periods=200).mean().to_numpy()
        sma20 = pd.Series(cl).rolling(20, min_periods=20).mean().to_numpy()
        sma_gate = sma20 >= sma200

    overext_filter = None
    if tf in ("1D", "1W") and n > _OVEREXT_LOOKBACK:
        ref = np.empty(n, dtype=float)
        ref[:_OVEREXT_LOOKBACK] = np.nan
        ref[_OVEREXT_LOOKBACK:] = cl[:-_OVEREXT_LOOKBACK]
        with np.errstate(divide="ignore", invalid="ignore"):
            pct_chg = (cl - ref) / ref * 100
        overext_filter = ~(pct_chg > _OVEREXT_PCT)

    vol_spike_ok = None
    if "Volume" in df.columns:
        vol = df["Volume"].to_numpy(float)
        vol_ma20 = pd.Series(vol).rolling(20, min_periods=20).mean().to_numpy()
        with np.errstate(invalid="ignore"):
            spike_raw = (vol >= _VOL_SPIKE_MULT * vol_ma20).astype(float)
        spike_raw = np.nan_to_num(spike_raw, nan=0.0)
        vol_spike_ok = pd.Series(spike_raw).rolling(
            _VOL_SPIKE_LOOKBACK, min_periods=1
        ).max().to_numpy().astype(bool)

    start = scan_start if scan_start is not None else 0
    events: list[dict] = []
    i = start

    while i < n:
        c3_on = _all_match_pol(st, c3_kpis, c3_pols, i)
        c3_onset = c3_on and (i == 0 or not _all_match_pol(st, c3_kpis, c3_pols, i - 1))
        if not c3_onset:
            i += 1
            continue
        if sma_gate is not None and not sma_gate[i]:
            i += 1
            continue
        if overext_filter is not None and not overext_filter[i]:
            i += 1
            continue
        if vol_spike_ok is not None and not vol_spike_ok[i]:
            i += 1
            continue

        signal_idx = i
        fill_bar = i + 1
        if fill_bar >= n:
            break
        entry_price = float(op[fill_bar])
        if entry_price <= 0 or np.isnan(entry_price):
            i += 1
            continue

        scaled = c4_avail and _all_match_pol(st, c4_kpis, c4_pols, i)
        scale_idx = i if scaled else None
        active_kpis = c4_kpis if scaled else c3_kpis
        active_pols = c4_pols if scaled else c3_pols

        # Use exit-specific KPIs if provided, otherwise use entry combo
        _exit_kpis = exit_kpis if exit_kpis else active_kpis
        _exit_pols = exit_pols if exit_pols else active_pols

        stop_price = entry_price
        atr_val = atr[i]
        stop = (stop_price - K * atr_val
                if not np.isnan(atr_val) and atr_val > 0
                else stop_price * 0.95)
        bars_since_reset = 0
        entry_idx = fill_bar
        stop_trail: list[float] = [stop]

        exit_idx = None
        exit_reason = None

        j = fill_bar + 1
        while j < n:
            bars_since_reset += 1
            c = float(cl[j])
            if np.isnan(c):
                stop_trail.append(stop_trail[-1] if stop_trail else stop)
                j += 1
                continue

            if c < stop:
                exit_idx = j
                exit_reason = "ATR stop"
                break

            if not scaled and c4_avail and _all_match_pol(st, c4_kpis, c4_pols, j):
                scaled = True
                scale_idx = j
                active_kpis = c4_kpis
                active_pols = c4_pols
                if not exit_kpis:
                    _exit_kpis = active_kpis
                    _exit_pols = active_pols

            nk = len(_exit_kpis)
            nb = sum(1 for k, p in zip(_exit_kpis, _exit_pols)
                     if not _kpi_match_pol(st, k, p, j))
            bars_held = j - entry_idx

            if bars_held <= T:
                if nb >= nk:
                    exit_idx = j
                    exit_reason = "Full invalidation"
                    break
            else:
                if nb >= 2:
                    exit_idx = j
                    exit_reason = f"{nb}/{nk} KPIs invalid"
                    break

            if bars_since_reset >= M:
                if nb == 0:
                    stop_price = c
                    a_val = atr[j] if j < len(atr) else np.nan
                    stop = (stop_price - K * a_val
                            if not np.isnan(a_val) and a_val > 0
                            else stop)
                    bars_since_reset = 0
                else:
                    exit_idx = j
                    exit_reason = "Checkpoint exit"
                    break

            stop_trail.append(stop)
            j += 1

        if exit_idx is None:
            exit_idx = n - 1
            exit_reason = "Open"

        while len(stop_trail) < (exit_idx - entry_idx + 1):
            stop_trail.append(stop_trail[-1] if stop_trail else stop)

        is_open = exit_reason == "Open"
        exit_fill = min(exit_idx + 1, n - 1) if not is_open and exit_idx < n - 1 else exit_idx
        xp = float(op[exit_fill]) if exit_fill != exit_idx else float(cl[exit_idx])
        hold = exit_idx - entry_idx
        cost = COMMISSION + SLIPPAGE
        weight = 1.5 if scaled else 1.0
        ret_pct = (((xp - entry_price) / entry_price - cost) * 100 * weight
                   if entry_price > 0 and not is_open else None)

        events.append({
            "signal_idx": signal_idx,
            "entry_idx": entry_idx,
            "entry_price": round(entry_price, 4),
            "exit_idx": exit_idx,
            "exit_price": round(xp, 4) if not is_open else None,
            "exit_reason": exit_reason,
            "scaled": scaled,
            "scale_idx": scale_idx,
            "stop_trail": [round(s, 4) if np.isfinite(s) else None for s in stop_trail],
            "hold": hold,
            "ret_pct": round(ret_pct, 2) if ret_pct is not None else None,
        })

        i = exit_idx + 1 if not is_open else n

    return events


def compute_polarity_position_status(
    df: pd.DataFrame,
    st: dict,
    setup: dict,
    tf: str,
) -> dict:
    """Derive position status for a polarity_combo strategy setup.

    Parameters
    ----------
    setup : dict from config.json strategy_setups (entry_type == "polarity_combo")
    tf    : current timeframe being viewed
    """
    flat_result = {
        "signal_action": "FLAT", "entry_bar_idx": None, "entry_price": None,
        "atr_stop": None, "bars_held": None, "exit_stage": None,
        "bearish_kpis": 0, "c4_scaled": False, "combo_bars": None,
        "last_exit_bars_ago": None, "last_exit_reason": None,
    }

    entry_tf = setup.get("entry_tf", tf)
    exit_tf = setup.get("exit_tf", tf)

    if entry_tf != tf:
        return flat_result

    # TF-specific combos take precedence over global combos
    combos_by_tf = setup.get("combos_by_tf", {})
    tf_combos = combos_by_tf.get(tf, {})
    combos = tf_combos if tf_combos else setup.get("combos", {})

    c3_def = combos.get("c3", {})
    c4_def = combos.get("c4")

    c3_kpis = c3_def.get("kpis", [])
    c3_pols = c3_def.get("pols", [])
    c4_kpis = c4_def.get("kpis") if c4_def else None
    c4_pols = c4_def.get("pols") if c4_def else None

    exit_def = setup.get("exit_combos")
    exit_kpis = exit_def.get("kpis") if exit_def else None
    exit_pols = exit_def.get("pols") if exit_def else None

    params = EXIT_PARAMS.get(exit_tf)
    if not params or df is None or df.empty or len(df) < 20 or not c3_kpis:
        return flat_result

    n = len(df)
    scan_start = max(0, n - 500)
    events = compute_polarity_position_events(
        df, st, c3_kpis, c3_pols, c4_kpis, c4_pols, exit_tf,
        exit_kpis=exit_kpis, exit_pols=exit_pols,
        scan_start=scan_start,
    )

    T, K = params["T"], params["K"]
    cl = df["Close"].to_numpy(float)
    atr_arr = compute_atr(df, ATR_PERIOD).to_numpy(float)

    last_active_kpis = c3_kpis
    last_active_pols = c3_pols
    if events and events[-1].get("scaled") and c4_kpis:
        last_active_kpis = c4_kpis
        last_active_pols = c4_pols

    _exit_k = exit_kpis if exit_kpis else last_active_kpis
    _exit_p = exit_pols if exit_pols else last_active_pols

    return _status_from_polarity_events(
        events, n, cl, atr_arr, st, _exit_k, _exit_p, tf, K, T)


def _status_from_polarity_events(
    events: list[dict],
    n: int,
    cl: np.ndarray,
    atr: np.ndarray,
    st: dict,
    exit_kpis: list[str],
    exit_pols: list[int],
    tf: str,
    K: float,
    T: int,
) -> dict:
    """Derive screener-facing status from polarity events."""
    flat_result = {
        "signal_action": "FLAT", "entry_bar_idx": None, "entry_price": None,
        "atr_stop": None, "bars_held": None, "exit_stage": None,
        "bearish_kpis": 0, "c4_scaled": False, "combo_bars": None,
        "last_exit_bars_ago": None, "last_exit_reason": None,
    }
    if not events:
        return flat_result

    last = events[-1]
    if last["exit_reason"] != "Open":
        result = dict(flat_result)
        result["last_exit_bars_ago"] = (n - 1) - last["exit_idx"]
        result["last_exit_reason"] = last["exit_reason"]
        return result

    entry_idx = last["entry_idx"]
    scaled = last["scaled"]
    scale_idx = last["scale_idx"]
    bars_held = (n - 1) - entry_idx
    combo_anchor = scale_idx if scale_idx is not None else entry_idx
    combo_bars = (n - 1) - combo_anchor

    nb_now = sum(1 for k, p in zip(exit_kpis, exit_pols)
                 if not _kpi_match_pol(st, k, p, n - 1))
    stage = "lenient" if bars_held <= T else "strict"

    stop_trail = last.get("stop_trail", [])
    stop = stop_trail[-1] if stop_trail else None
    if stop is None:
        stop = float(cl[entry_idx]) * 0.95

    if combo_bars == 0:
        action = "ENTRY 1.5x" if scaled else "ENTRY 1x"
    else:
        action = "HOLD"

    return {
        "signal_action": action,
        "entry_bar_idx": entry_idx,
        "entry_price": last["entry_price"],
        "atr_stop": round(stop, 2) if np.isfinite(stop) else None,
        "bars_held": bars_held,
        "combo_bars": combo_bars,
        "exit_stage": stage,
        "bearish_kpis": nb_now,
        "c4_scaled": scaled,
        "last_exit_bars_ago": None,
        "last_exit_reason": None,
    }

# Trailing-12-month bar counts per timeframe
_L12M_BARS = {"4H": 6 * 252, "1D": 252, "1W": 52, "2W": 26, "1M": 12}


def compute_trailing_pnl(
    df: pd.DataFrame,
    st: dict,
    c3_kpis: list,
    c4_kpis: list,
    tf: str,
) -> dict:
    """Trailing 12-month P&L derived from ``compute_position_events``.

    Returns dict with:
      l12m_pnl: float or None  — cumulative return %
      l12m_trades: int          — number of closed trades
      l12m_hit_rate: float or None — win rate %
    """
    empty = {"l12m_pnl": None, "l12m_trades": 0, "l12m_hit_rate": None}
    if not EXIT_PARAMS.get(tf) or df is None or df.empty or len(df) < 20 or not c3_kpis:
        return empty

    lookback = _L12M_BARS.get(tf, 252)
    start = max(0, len(df) - lookback)
    events = compute_position_events(df, st, c3_kpis, c4_kpis, tf,
                                     scan_start=start)

    closed = [e for e in events if e["exit_reason"] != "Open" and e["ret_pct"] is not None]
    if not closed:
        return {"l12m_pnl": 0.0, "l12m_trades": 0, "l12m_hit_rate": None}

    total_ret = sum(e["ret_pct"] for e in closed)
    wins = sum(1 for e in closed if e["ret_pct"] >= 0)
    hr = (wins / len(closed)) * 100

    return {
        "l12m_pnl": round(total_ret, 2),
        "l12m_trades": len(closed),
        "l12m_hit_rate": round(hr, 1),
    }


def compute_polarity_trailing_pnl(
    df: pd.DataFrame,
    st: dict,
    setup: dict,
    tf: str,
) -> dict:
    """Trailing 12-month P&L for a polarity_combo strategy."""
    empty = {"l12m_pnl": None, "l12m_trades": 0, "l12m_hit_rate": None}

    entry_tf = setup.get("entry_tf", tf)
    exit_tf = setup.get("exit_tf", tf)
    if entry_tf != tf:
        return empty

    # TF-specific combos take precedence over global combos
    combos_by_tf = setup.get("combos_by_tf", {})
    tf_combos = combos_by_tf.get(tf, {})
    combos = tf_combos if tf_combos else setup.get("combos", {})

    c3_def = combos.get("c3", {})
    c4_def = combos.get("c4")
    c3_kpis = c3_def.get("kpis", [])
    c3_pols = c3_def.get("pols", [])
    c4_kpis = c4_def.get("kpis") if c4_def else None
    c4_pols = c4_def.get("pols") if c4_def else None

    exit_def = setup.get("exit_combos")
    exit_kpis = exit_def.get("kpis") if exit_def else None
    exit_pols = exit_def.get("pols") if exit_def else None

    if not EXIT_PARAMS.get(exit_tf) or df is None or df.empty or len(df) < 20 or not c3_kpis:
        return empty

    lookback = _L12M_BARS.get(exit_tf, 252)
    start = max(0, len(df) - lookback)
    events = compute_polarity_position_events(
        df, st, c3_kpis, c3_pols, c4_kpis, c4_pols, exit_tf,
        exit_kpis=exit_kpis, exit_pols=exit_pols,
        scan_start=start,
    )

    closed = [e for e in events if e["exit_reason"] != "Open" and e["ret_pct"] is not None]
    if not closed:
        return {"l12m_pnl": 0.0, "l12m_trades": 0, "l12m_hit_rate": None}

    total_ret = sum(e["ret_pct"] for e in closed)
    wins = sum(1 for e in closed if e["ret_pct"] >= 0)
    hr = (wins / len(closed)) * 100
    return {
        "l12m_pnl": round(total_ret, 2),
        "l12m_trades": len(closed),
        "l12m_hit_rate": round(hr, 1),
    }

"""Build screener summary rows from enriched OHLCV data and KPI states."""

from __future__ import annotations

import logging
import math
from typing import Dict

import pandas as pd

from apps.dashboard.strategy import (
    compute_position_status, compute_trailing_pnl,
    compute_polarity_position_status, compute_polarity_trailing_pnl,
)

logger = logging.getLogger(__name__)


def build_screener_rows(
    *,
    all_data: dict,
    timeframes: list[str],
    cfg_kpi_weights: dict,
    cfg_alerts_lookback_bars: int,
    cfg_combo_kpis_by_tf: dict,
    cfg_combo_3_kpis: list,
    cfg_combo_4_kpis: list,
    symbol_display: dict,
    symbol_meta: dict,
    data_health: dict,
    stoch_mtm_thresholds: dict | None = None,
    strategy_setups: dict | None = None,
) -> tuple[dict[str, list[dict]], dict[str, dict[str, dict]], dict]:
    """
    Build screener rows and state cache from enriched data.
    Returns: (rows_by_tf, by_symbol, state_cache)
    """
    from trading_dashboard.kpis.catalog import KPI_BREAKOUT_ORDER, KPI_TREND_ORDER, compute_kpi_state_map

    try:
        from apps.dashboard.sector_map import load_sector_map
        _sector_map = load_sector_map()
    except Exception:
        _sector_map = {}

    rows_by_tf: dict[str, list[dict]] = {tf: [] for tf in timeframes}
    by_symbol: dict[str, dict[str, dict]] = {}
    state_cache: dict[tuple[str, str], dict[str, pd.Series]] = {}

    for sym, tf_map in all_data.items():
        by_symbol[sym] = {}
        for tf, df in tf_map.items():
            if df is None or df.empty:
                continue
            st = compute_kpi_state_map(df, stoch_mtm_thresholds=stoch_mtm_thresholds)
            state_cache[(sym, tf)] = st

            last = df.index[-1] if len(df.index) else None
            last_iso = pd.to_datetime(last).isoformat() if last is not None else None

            trend_states = []
            trend_states_h3 = []
            for k in KPI_TREND_ORDER:
                s = st.get(k)
                raw = s.iloc[-1] if (s is not None and len(s)) else None
                v = int(raw) if raw is not None and not pd.isna(raw) else -2
                trend_states.append(v)
                raw3 = s.iloc[-4] if (s is not None and len(s) >= 4) else None
                v3 = int(raw3) if raw3 is not None and not pd.isna(raw3) else -2
                trend_states_h3.append(v3)
            cur_sum = int(sum(v for v in trend_states if v in (1, -1)))
            prev_sum = int(sum(v for v in trend_states_h3 if v in (1, -1)))
            trend_delta = cur_sum - prev_sum
            trend_neu = int(sum(1 for v in trend_states if v == 0))
            trend_na = int(sum(1 for v in trend_states if v == -2))

            trend_score = 0.0
            for k, v in zip(KPI_TREND_ORDER, trend_states):
                w = float(cfg_kpi_weights.get(k, 1.0))
                if v in (1, -1):
                    trend_score += w * float(v)
            trend_score = float(round(trend_score, 4))

            kpi_state_map = dict(zip(KPI_TREND_ORDER, trend_states))

            # Combo definitions: timeframe-specific > global
            tf_combos = cfg_combo_kpis_by_tf.get(tf, {})
            c3_kpis = tf_combos.get("combo_3", cfg_combo_3_kpis)
            c4_kpis = tf_combos.get("combo_4", cfg_combo_4_kpis)

            combo_3 = all(kpi_state_map.get(k) == 1 for k in c3_kpis)
            combo_4 = all(kpi_state_map.get(k) == 1 for k in c4_kpis)

            combo_3_new = False
            combo_4_new = False
            if len(df) >= 2:
                def _prev_combo(kpi_list: list) -> bool:
                    prev_idx = df.index[-2]
                    for k in kpi_list:
                        s = st.get(k)
                        if s is None or len(s) < 2 or prev_idx not in s.index:
                            return False
                        val = s.loc[prev_idx]
                        if pd.isna(val) or int(val) != 1:
                            return False
                    return True
                if combo_3:
                    combo_3_new = not _prev_combo(c3_kpis)
                if combo_4:
                    combo_4_new = not _prev_combo(c4_kpis)

            # Bars since last combo (C3 or C4 was active)
            last_combo_bars: int | None = None
            _scan_len = min(len(df), 200)
            for _bi in range(1, _scan_len + 1):
                _idx = -_bi
                _any_combo = False
                for _ck in [c3_kpis, c4_kpis]:
                    if all(
                        (st.get(_kk) is not None and len(st.get(_kk)) >= _bi
                         and pd.notna(st.get(_kk).iloc[_idx])
                         and int(st.get(_kk).iloc[_idx]) == 1)
                        for _kk in _ck
                    ):
                        _any_combo = True
                        break
                if _any_combo:
                    last_combo_bars = _bi - 1
                    break

            pos_status = compute_position_status(df, st, c3_kpis, c4_kpis, tf)
            trailing_pnl = compute_trailing_pnl(df, st, c3_kpis, c4_kpis, tf)

            # Multi-strategy polarity positions
            strat_statuses: dict[str, dict] = {}
            _setups = strategy_setups or {}
            for skey, sdef in _setups.items():
                if sdef.get("entry_type") != "polarity_combo":
                    continue
                s_entry_tf = sdef.get("entry_tf", tf)
                if s_entry_tf != tf:
                    continue
                try:
                    ps = compute_polarity_position_status(df, st, sdef, tf)
                    tp = compute_polarity_trailing_pnl(df, st, sdef, tf)
                    strat_statuses[skey] = {
                        "signal_action": ps["signal_action"],
                        "entry_price": ps["entry_price"],
                        "atr_stop": ps["atr_stop"],
                        "bars_held": ps["bars_held"],
                        "combo_bars": ps.get("combo_bars"),
                        "c4_scaled": ps["c4_scaled"],
                        "l12m_pnl": tp["l12m_pnl"],
                        "l12m_trades": tp["l12m_trades"],
                        "l12m_hit_rate": tp["l12m_hit_rate"],
                    }
                except Exception as e:
                    logger.warning("Strategy %s failed for %s/%s: %s", skey, sym, tf, e)

            n = int(max(1, min(int(cfg_alerts_lookback_bars), len(df))))
            bull_events = 0
            bear_events = 0
            for k in KPI_BREAKOUT_ORDER:
                s = st.get(k)
                if s is None or not len(s):
                    continue
                tail = s.tail(n)
                bull_events += int((tail == 1).sum())
                bear_events += int((tail == -1).sum())
            breakout_score = int(bull_events - bear_events)

            weekly_source = (symbol_meta.get(sym, {}) or {}).get("weekly_source")
            bars = (data_health.get(sym, {}).get(tf, {}) or {}).get("bars")

            # Last-2-bar delta for quick list display (per timeframe).
            last_close = None
            prev_close = None
            delta_close = None
            delta_pct = None
            try:
                if df is not None and (not df.empty) and ("Close" in df.columns) and (len(df) >= 2):
                    c1 = float(df["Close"].iloc[-1])
                    c0 = float(df["Close"].iloc[-2])
                    if math.isfinite(c1) and math.isfinite(c0):
                        last_close = c1
                        prev_close = c0
                        delta_close = c1 - c0
                    if c0 != 0:
                        delta_pct = (delta_close / c0) * 100.0
            except Exception as e:
                logger.warning("Could not compute last-2-bar delta: %s", e)

            # Per-KPI last-bar states {kpi_name: int}
            kpi_states: Dict[str, int] = {}
            for k in KPI_TREND_ORDER:
                s = st.get(k)
                kpi_states[k] = int(s.iloc[-1]) if (s is not None and len(s) and pd.notna(s.iloc[-1])) else -2
            for k in KPI_BREAKOUT_ORDER:
                s = st.get(k)
                kpi_states[k] = int(s.iloc[-1]) if (s is not None and len(s) and pd.notna(s.iloc[-1])) else -2

            # 13-bar conviction trend: per bar, ratio of bull vs bear trend KPIs
            _conv10_bars = min(13, len(df))
            _conv10: list[float] = []
            _all_kpi_keys = list(KPI_TREND_ORDER)
            for _bi in range(_conv10_bars, 0, -1):
                _idx = -_bi
                _b, _r, _tot = 0, 0, 0
                for _kk in _all_kpi_keys:
                    _ss = st.get(_kk)
                    if _ss is None or len(_ss) < _bi:
                        continue
                    _sv = int(_ss.iloc[_idx]) if pd.notna(_ss.iloc[_idx]) else -2
                    if _sv in (1, -1):
                        _tot += 1
                        if _sv == 1:
                            _b += 1
                        else:
                            _r += 1
                _conv10.append(round((_b - _r) / max(_tot, 1), 3))

            # Per-dimension aggregates for spider charts
            try:
                from trading_dashboard.indicators.registry import get_dimension_map as _gdm, DIMENSIONS as _DIMS
                _dmap = _gdm()
                dim_bull: Dict[str, int] = {d: 0 for d in _DIMS}
                dim_bear: Dict[str, int] = {d: 0 for d in _DIMS}
                dim_total: Dict[str, int] = {d: 0 for d in _DIMS}
                for kpi, state_val in kpi_states.items():
                    dk = _dmap.get(kpi)
                    if dk and dk in _DIMS:
                        dim_total[dk] += 1
                        if state_val == 1:
                            dim_bull[dk] += 1
                        elif state_val == -1:
                            dim_bear[dk] += 1
            except Exception:
                dim_bull = {}
                dim_bear = {}
                dim_total = {}

            # Sparkline: last 20 closes normalized 0-1
            spark = []
            try:
                _closes = df["Close"].dropna().iloc[-20:].astype(float).tolist()
                if len(_closes) >= 2:
                    _mn, _mx = min(_closes), max(_closes)
                    _rng = _mx - _mn if _mx > _mn else 1.0
                    spark = [round((_c - _mn) / _rng, 2) for _c in _closes]
            except Exception:
                spark = []

            _sm = _sector_map.get(sym, {})
            _fund = _sm.get("fundamentals", {})
            rec = {
                "symbol": sym,
                "name": symbol_display.get(sym) or symbol_display.get(sym.upper()) or "",
                "tf": tf,
                "sector": _sm.get("sector", ""),
                "industry": _sm.get("industry", ""),
                "geo": _sm.get("geo", ""),
                "sector_etf": _sm.get("sector_etf", "") or _sm.get("benchmark_etf", ""),
                "industry_etf": _sm.get("industry_etf", ""),
                "trend_score": trend_score,
                "breakout_score": breakout_score,
                "trend_delta": trend_delta,
                "trend_neutral": trend_neu,
                "trend_na": trend_na,
                "breakout_bull": int(bull_events),
                "breakout_bear": int(bear_events),
                "last": last_iso,
                "last_close": last_close,
                "prev_close": prev_close,
                "delta_close": delta_close,
                "delta_pct": delta_pct,
                "weekly_source": weekly_source,
                "bars": bars,
                "trend_conflict": False,
                "combo_3": combo_3,
                "combo_4": combo_4,
                "combo_3_new": combo_3_new,
                "combo_4_new": combo_4_new,
                "combo_3_kpis": c3_kpis,
                "combo_4_kpis": c4_kpis,
                "kpi_states": kpi_states,
                "dim_bull": dim_bull,
                "dim_bear": dim_bear,
                "dim_total": dim_total,
                "spark": spark,
                "conv10": _conv10,
                "last_combo_bars": last_combo_bars,
                "signal_action": pos_status["signal_action"],
                "entry_price": pos_status["entry_price"],
                "atr_stop": pos_status["atr_stop"],
                "bars_held": pos_status["bars_held"],
                "combo_bars": pos_status.get("combo_bars"),
                "exit_stage": pos_status["exit_stage"],
                "bearish_kpis": pos_status["bearish_kpis"],
                "c4_scaled": pos_status["c4_scaled"],
                "last_exit_bars_ago": pos_status.get("last_exit_bars_ago"),
                "last_exit_reason": pos_status.get("last_exit_reason"),
                "strat_statuses": strat_statuses,
                "l12m_pnl": trailing_pnl["l12m_pnl"],
                "l12m_trades": trailing_pnl["l12m_trades"],
                "l12m_hit_rate": trailing_pnl["l12m_hit_rate"],
                "recommendation": _fund.get("recommendation", ""),
                "market_cap": _fund.get("market_cap"),
                "trailing_pe": _fund.get("trailing_pe"),
                "pe_vs_sector": None,
            }
            by_symbol[sym][tf] = rec
            if tf in rows_by_tf:
                rows_by_tf[tf].append(rec)

    for sym, tf_map in by_symbol.items():
        signs: list[int] = []
        for tf in timeframes:
            r = tf_map.get(tf)
            if not r:
                continue
            v = float(r.get("trend_score") or 0.0)
            sgn = 1 if v > 0 else (-1 if v < 0 else 0)
            if sgn != 0:
                signs.append(sgn)
        conflict = len(set(signs)) > 1
        if conflict:
            for tf in list(tf_map.keys()):
                tf_map[tf]["trend_conflict"] = True

    # Compute sector TrendScore delta (stock TS - sector ETF TS)
    for sym, tf_map in by_symbol.items():
        for tf, rec in tf_map.items():
            bench = rec.get("sector_etf", "")
            if not bench or bench not in by_symbol:
                rec["sector_ts_delta"] = None
                continue
            bench_rec = by_symbol[bench].get(tf)
            if bench_rec:
                stock_ts = float(rec.get("trend_score") or 0)
                bench_ts = float(bench_rec.get("trend_score") or 0)
                rec["sector_ts_delta"] = round(stock_ts - bench_ts, 1)
            else:
                rec["sector_ts_delta"] = None

    # Compute market TrendScore delta (stock TS - national index TS)
    try:
        from apps.dashboard.sector_map import get_national_index
    except Exception:
        get_national_index = None
    if get_national_index is not None:
        for sym, tf_map in by_symbol.items():
            for tf, rec in tf_map.items():
                mkt = get_national_index(sym)
                if not mkt or mkt not in by_symbol:
                    rec["market_ts_delta"] = None
                    rec["market_index"] = None
                    continue
                mkt_rec = by_symbol[mkt].get(tf)
                if mkt_rec:
                    stock_ts = float(rec.get("trend_score") or 0)
                    mkt_ts = float(mkt_rec.get("trend_score") or 0)
                    rec["market_ts_delta"] = round(stock_ts - mkt_ts, 1)
                    rec["market_index"] = mkt
                else:
                    rec["market_ts_delta"] = None
                    rec["market_index"] = None

    # Compute P/E vs sector ETF (% difference)
    for sym, tf_map in by_symbol.items():
        sm_entry = _sector_map.get(sym, {})
        try:
            stock_pe = float((sm_entry.get("fundamentals") or {}).get("trailing_pe") or 0) or None
        except (TypeError, ValueError):
            stock_pe = None
        sector_etf = sm_entry.get("sector_etf") or sm_entry.get("benchmark_etf") or ""
        sector_pe = None
        if sector_etf:
            try:
                sector_pe = float((_sector_map.get(sector_etf, {}).get("fundamentals") or {}).get("trailing_pe") or 0) or None
            except (TypeError, ValueError):
                sector_pe = None
        for tf, rec in tf_map.items():
            if stock_pe and sector_pe and sector_pe > 0:
                rec["pe_vs_sector"] = round(((stock_pe - sector_pe) / sector_pe) * 100, 1)
            else:
                rec["pe_vs_sector"] = None

    return rows_by_tf, by_symbol, state_cache

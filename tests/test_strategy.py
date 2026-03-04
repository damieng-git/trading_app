"""Tests for the Entry v5 + Exit Flow v4 strategy engine (apps/dashboard/strategy.py)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apps.dashboard.strategy import (
    ATR_PERIOD,
    EXIT_PARAMS,
    compute_atr,
    compute_position_events,
    compute_position_status,
    compute_trailing_pnl,
)


@pytest.fixture
def strategy_ohlcv() -> pd.DataFrame:
    """500-bar DataFrame with a clear uptrend for strategy testing.

    Volume has periodic spikes (every 3rd bar is 3× average) to satisfy the
    v5 volume-spike entry gate (1.5× Vol_MA20 within 5 bars).
    """
    np.random.seed(99)
    n = 500
    dates = pd.bdate_range("2023-01-01", periods=n, freq="B")
    trend = np.linspace(100, 200, n) + np.cumsum(np.random.randn(n) * 0.5)
    high = trend + np.abs(np.random.randn(n)) * 2
    low = trend - np.abs(np.random.randn(n)) * 2
    vol = np.random.randint(100_000, 500_000, n)
    vol[::3] = 1_500_000  # spike every 3 bars → v5 vol gate always passes
    return pd.DataFrame(
        {"Open": trend + np.random.randn(n) * 0.3, "High": high, "Low": low,
         "Close": trend, "Volume": vol},
        index=dates,
    )


def _make_kpi_series(df: pd.DataFrame, *, bull_from: int = 0, bull_to: int | None = None) -> pd.Series:
    """KPI series: 1 (bull) from bull_from to bull_to, -1 elsewhere."""
    n = len(df)
    vals = np.full(n, -1, dtype=int)
    vals[bull_from: bull_to] = 1
    return pd.Series(vals, index=df.index, dtype=int)


class TestComputeAtr:
    def test_shape(self, strategy_ohlcv):
        result = compute_atr(strategy_ohlcv)
        assert len(result) == len(strategy_ohlcv)

    def test_positive(self, strategy_ohlcv):
        result = compute_atr(strategy_ohlcv)
        assert (result.dropna() > 0).all()

    def test_custom_period(self, strategy_ohlcv):
        r14 = compute_atr(strategy_ohlcv, 14)
        r7 = compute_atr(strategy_ohlcv, 7)
        assert not r14.equals(r7)


class TestExitParams:
    def test_all_timeframes_present(self):
        for tf in ("4H", "1D", "1W"):
            assert tf in EXIT_PARAMS
            params = EXIT_PARAMS[tf]
            assert "T" in params and "M" in params and "K" in params

    def test_atr_period(self):
        assert ATR_PERIOD == 14


class TestComputePositionStatus:
    def test_flat_when_no_kpis(self, strategy_ohlcv):
        result = compute_position_status(strategy_ohlcv, {}, ["A"], ["B"], "1D")
        assert result["signal_action"] == "FLAT"

    def test_flat_when_df_too_short(self):
        df = pd.DataFrame(
            {"Open": [100], "High": [105], "Low": [95], "Close": [100], "Volume": [1000]},
            index=pd.DatetimeIndex(["2025-01-01"]),
        )
        result = compute_position_status(df, {}, ["A"], ["B"], "1D")
        assert result["signal_action"] == "FLAT"

    def test_flat_when_unknown_tf(self, strategy_ohlcv):
        st = {"A": _make_kpi_series(strategy_ohlcv, bull_from=0)}
        result = compute_position_status(strategy_ohlcv, st, ["A"], ["A"], "5M")
        assert result["signal_action"] == "FLAT"

    def test_entry_detected(self, strategy_ohlcv):
        """All C3 KPIs bullish from bar 200 onward should trigger an entry."""
        len(strategy_ohlcv)
        st = {
            "K1": _make_kpi_series(strategy_ohlcv, bull_from=200),
            "K2": _make_kpi_series(strategy_ohlcv, bull_from=200),
            "K3": _make_kpi_series(strategy_ohlcv, bull_from=200),
        }
        result = compute_position_status(
            strategy_ohlcv, st, ["K1", "K2", "K3"], ["K1", "K2", "K3"], "1W"
        )
        assert result["signal_action"] != "FLAT"
        assert result["entry_price"] is not None
        assert result["entry_price"] > 0

    def test_exit_on_atr_stop(self, strategy_ohlcv):
        """Force a crash after entry to trigger ATR stop."""
        df = strategy_ohlcv.copy()
        df.iloc[210:215, df.columns.get_loc("Close")] = 50.0
        df.iloc[210:215, df.columns.get_loc("Low")] = 45.0
        st = {
            "K1": _make_kpi_series(df, bull_from=200, bull_to=250),
            "K2": _make_kpi_series(df, bull_from=200, bull_to=250),
        }
        result = compute_position_status(df, st, ["K1", "K2"], ["K1", "K2"], "1W")
        if result["signal_action"] == "FLAT":
            assert result["last_exit_reason"] is not None

    def test_result_keys(self, strategy_ohlcv):
        st = {"K1": _make_kpi_series(strategy_ohlcv, bull_from=0)}
        result = compute_position_status(strategy_ohlcv, st, ["K1"], ["K1"], "1D")
        expected_keys = {
            "signal_action", "entry_bar_idx", "entry_price", "atr_stop",
            "bars_held", "exit_stage", "bearish_kpis", "c4_scaled",
            "last_exit_bars_ago", "last_exit_reason",
        }
        assert set(result.keys()) == expected_keys

    def test_empty_df(self):
        result = compute_position_status(pd.DataFrame(), {}, [], [], "1D")
        assert result["signal_action"] == "FLAT"

    def test_none_df(self):
        result = compute_position_status(None, {}, [], [], "1D")
        assert result["signal_action"] == "FLAT"


class TestComputePositionEvents:
    """Tests for the single-source-of-truth event engine."""

    def test_empty_on_bad_inputs(self):
        assert compute_position_events(None, {}, [], [], "1D") == []
        assert compute_position_events(pd.DataFrame(), {}, ["A"], ["B"], "1D") == []

    def test_empty_on_unknown_tf(self, strategy_ohlcv):
        st = {"K1": _make_kpi_series(strategy_ohlcv, bull_from=0)}
        assert compute_position_events(strategy_ohlcv, st, ["K1"], ["K1"], "5M") == []

    def test_returns_list_of_dicts(self, strategy_ohlcv):
        st = {
            "K1": _make_kpi_series(strategy_ohlcv, bull_from=200),
            "K2": _make_kpi_series(strategy_ohlcv, bull_from=200),
        }
        events = compute_position_events(strategy_ohlcv, st, ["K1", "K2"], ["K1", "K2"], "1W")
        assert isinstance(events, list)
        if events:
            ev = events[0]
            assert "signal_idx" in ev
            assert "entry_idx" in ev
            assert "entry_price" in ev
            assert "exit_idx" in ev
            assert "exit_reason" in ev
            assert "scaled" in ev
            assert "stop_trail" in ev
            assert "hold" in ev
            assert "ret_pct" in ev

    def test_entry_price_is_next_bar_open(self, strategy_ohlcv):
        st = {
            "K1": _make_kpi_series(strategy_ohlcv, bull_from=200),
            "K2": _make_kpi_series(strategy_ohlcv, bull_from=200),
        }
        events = compute_position_events(strategy_ohlcv, st, ["K1", "K2"], ["K1", "K2"], "1W")
        assert len(events) > 0
        ev = events[0]
        assert ev["entry_idx"] == ev["signal_idx"] + 1
        expected_price = round(float(strategy_ohlcv["Open"].iloc[ev["entry_idx"]]), 4)
        assert ev["entry_price"] == expected_price

    def test_scan_start_limits_lookback(self, strategy_ohlcv):
        st = {
            "K1": _make_kpi_series(strategy_ohlcv, bull_from=100, bull_to=150),
        }
        all_events = compute_position_events(strategy_ohlcv, st, ["K1"], ["K1"], "1W")
        late_events = compute_position_events(strategy_ohlcv, st, ["K1"], ["K1"], "1W",
                                              scan_start=200)
        assert len(late_events) <= len(all_events)

    def test_atr_stop_exit(self, strategy_ohlcv):
        df = strategy_ohlcv.copy()
        df.iloc[210:215, df.columns.get_loc("Close")] = 50.0
        df.iloc[210:215, df.columns.get_loc("Low")] = 45.0
        st = {
            "K1": _make_kpi_series(df, bull_from=200, bull_to=250),
            "K2": _make_kpi_series(df, bull_from=200, bull_to=250),
        }
        events = compute_position_events(df, st, ["K1", "K2"], ["K1", "K2"], "1W")
        atr_exits = [e for e in events if e["exit_reason"] == "ATR stop"]
        assert len(atr_exits) > 0

    def test_open_position_has_no_ret_pct(self, strategy_ohlcv):
        st = {
            "K1": _make_kpi_series(strategy_ohlcv, bull_from=490),
        }
        events = compute_position_events(strategy_ohlcv, st, ["K1"], ["K1"], "1W")
        open_positions = [e for e in events if e["exit_reason"] == "Open"]
        for op in open_positions:
            assert op["ret_pct"] is None

    def test_stop_trail_length_matches_hold(self, strategy_ohlcv):
        st = {
            "K1": _make_kpi_series(strategy_ohlcv, bull_from=200),
            "K2": _make_kpi_series(strategy_ohlcv, bull_from=200),
        }
        events = compute_position_events(strategy_ohlcv, st, ["K1", "K2"], ["K1", "K2"], "1W")
        for ev in events:
            expected_len = ev["exit_idx"] - ev["entry_idx"] + 1
            assert len(ev["stop_trail"]) == expected_len


class TestStatusDelegatesToEvents:
    """Verify compute_position_status delegates to compute_position_events."""

    def test_status_entry_matches_events(self, strategy_ohlcv):
        st = {
            "K1": _make_kpi_series(strategy_ohlcv, bull_from=200),
            "K2": _make_kpi_series(strategy_ohlcv, bull_from=200),
            "K3": _make_kpi_series(strategy_ohlcv, bull_from=200),
        }
        status = compute_position_status(strategy_ohlcv, st, ["K1", "K2", "K3"],
                                         ["K1", "K2", "K3"], "1W")
        assert status["signal_action"] != "FLAT"
        assert status["entry_price"] is not None
        assert status["entry_price"] > 0


class TestTrailingPnlDelegatesToEvents:
    """Verify compute_trailing_pnl delegates to compute_position_events."""

    def test_returns_expected_keys(self, strategy_ohlcv):
        st = {
            "K1": _make_kpi_series(strategy_ohlcv, bull_from=200),
            "K2": _make_kpi_series(strategy_ohlcv, bull_from=200),
        }
        result = compute_trailing_pnl(strategy_ohlcv, st, ["K1", "K2"],
                                      ["K1", "K2"], "1W")
        assert "l12m_pnl" in result
        assert "l12m_trades" in result
        assert "l12m_hit_rate" in result

    def test_empty_on_bad_inputs(self):
        result = compute_trailing_pnl(None, {}, [], [], "1D")
        assert result["l12m_trades"] == 0

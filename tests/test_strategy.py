"""Tests for the Entry v5 + Exit Flow v4 strategy engine (apps/dashboard/strategy.py)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from apps.dashboard.strategy import (
    ATR_PERIOD,
    EXIT_PARAMS,
    compute_arch_a_position_events,
    compute_arch_a_position_status,
    compute_arch_a_trailing_pnl,
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
        for tf in ("1D", "1W"):
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


# ---------------------------------------------------------------------------
# Architecture A (Pullback-A) tests
# ---------------------------------------------------------------------------

def _make_arch_a_ohlcv(n: int = 600, seed: int = 42) -> pd.DataFrame:
    """OHLCV DataFrame designed to satisfy Pullback-A gates at a known bar.

    Structure:
    - Bars 0-299: strong uptrend (SMA50W > SMA200W will eventually hold).
    - Bars 300+: price dips (RSI14 < 50 achievable) then recovers.
    - MACD hist crosses above zero at bar ~400 (gentle dip then lift).
    - Chandelier Exit stays False at entry bar.
    """
    np.random.seed(seed)
    dates = pd.bdate_range("2015-01-01", periods=n, freq="B")
    # Uptrend with a clear dip zone — proportional to n
    base = np.interp(np.arange(n), [0, n * 0.5, n * 0.63, n], [50, 120, 95, 130])
    noise = np.random.randn(n) * 0.5
    close = base + noise
    high = close + np.abs(np.random.randn(n)) * 1.5
    low = close - np.abs(np.random.randn(n)) * 1.5
    open_ = close + np.random.randn(n) * 0.3
    vol = np.random.randint(100_000, 500_000, n)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=dates,
    )


class TestComputeArchAPositionEvents:
    """Tests for the Pullback-A five-gate position engine."""

    def test_empty_on_none_df(self):
        assert compute_arch_a_position_events(None, "1D") == []

    def test_empty_on_empty_df(self):
        assert compute_arch_a_position_events(pd.DataFrame(), "1D") == []

    def test_empty_on_unknown_tf(self):
        df = _make_arch_a_ohlcv()
        assert compute_arch_a_position_events(df, "4H") == []

    def test_empty_on_short_df(self):
        df = _make_arch_a_ohlcv(n=20)
        assert compute_arch_a_position_events(df, "1D") == []

    def test_returns_list_of_dicts_with_required_keys(self):
        df = _make_arch_a_ohlcv()
        events = compute_arch_a_position_events(df, "1W")
        if events:
            required = {"entry_idx", "entry_price", "exit_idx", "exit_reason",
                        "ret_pct", "stop_trail", "scaled"}
            assert required.issubset(events[0].keys())

    def test_entry_price_is_next_bar_open(self):
        """Entry fills at the open of the bar after the signal bar."""
        df = _make_arch_a_ohlcv()
        events = compute_arch_a_position_events(df, "1W")
        for e in events:
            sig = e["signal_idx"]
            fill = e["entry_idx"]
            assert fill == sig + 1, f"expected fill at sig+1, got sig={sig} fill={fill}"

    def test_scaled_is_always_false(self):
        """Pullback-A has no scale-up — scaled must always be False."""
        df = _make_arch_a_ohlcv()
        events = compute_arch_a_position_events(df, "1W")
        for e in events:
            assert e["scaled"] is False

    def test_open_position_has_no_ret_pct(self):
        """The last open position must not have a ret_pct (not yet closed)."""
        df = _make_arch_a_ohlcv()
        events = compute_arch_a_position_events(df, "1W")
        open_events = [e for e in events if e["exit_reason"] == "Open"]
        for e in open_events:
            assert e["ret_pct"] is None

    def test_stop_trail_length_matches_hold(self):
        """stop_trail must have one entry per bar held."""
        df = _make_arch_a_ohlcv()
        events = compute_arch_a_position_events(df, "1W")
        for e in events:
            hold = e["exit_idx"] - e["entry_idx"]
            assert len(e["stop_trail"]) == hold + 1, (
                f"stop_trail length {len(e['stop_trail'])} != hold+1 {hold+1}"
            )

    def test_weekly_df_used_for_gate1_on_1d(self):
        """Passing weekly_df should not crash and may produce different results than None."""
        df = _make_arch_a_ohlcv(n=600)
        # Use the same df as both daily and weekly (values differ, but structure is valid).
        events_with = compute_arch_a_position_events(df, "1D", weekly_df=df)
        events_without = compute_arch_a_position_events(df, "1D", weekly_df=None)
        # Both must be lists; content may differ — just verify no crash.
        assert isinstance(events_with, list)
        assert isinstance(events_without, list)

    def test_scan_start_limits_lookback(self):
        """scan_start must exclude entries before the cutoff bar."""
        df = _make_arch_a_ohlcv()
        all_events = compute_arch_a_position_events(df, "1W")
        late_start = len(df) - 100
        late_events = compute_arch_a_position_events(df, "1W", scan_start=late_start)
        for e in late_events:
            assert e["entry_idx"] >= late_start


class TestComputeArchAPositionStatus:
    """Tests for the screener-facing status wrapper."""

    def test_flat_on_bad_inputs(self):
        ps = compute_arch_a_position_status(None, "1D")
        assert ps["signal_action"] == "FLAT"

    def test_flat_on_inactive_tf(self):
        df = _make_arch_a_ohlcv()
        ps = compute_arch_a_position_status(df, "4H")
        assert ps["signal_action"] == "FLAT"

    def test_returns_required_keys(self):
        df = _make_arch_a_ohlcv()
        ps = compute_arch_a_position_status(df, "1W")
        for key in ("signal_action", "entry_price", "atr_stop", "bars_held",
                    "c4_scaled", "last_exit_bars_ago", "last_exit_reason"):
            assert key in ps

    def test_c4_scaled_always_false(self):
        df = _make_arch_a_ohlcv()
        ps = compute_arch_a_position_status(df, "1W")
        assert ps["c4_scaled"] is False

    def test_entry_price_positive_when_in_position(self):
        """If a position is open, entry_price must be a positive number."""
        df = _make_arch_a_ohlcv()
        ps = compute_arch_a_position_status(df, "1W")
        if ps["signal_action"] in ("ENTRY 1x", "HOLD"):
            assert ps["entry_price"] is not None and ps["entry_price"] > 0


class TestComputeArchATrailingPnl:
    """Tests for the 12m/24m P&L wrapper."""

    def test_returns_required_keys(self):
        df = _make_arch_a_ohlcv()
        result = compute_arch_a_trailing_pnl(df, "1W")
        for key in ("l12m_pnl", "l12m_trades", "l12m_hit_rate", "l12m_max_dd",
                    "l24m_pnl", "l24m_trades", "l24m_hit_rate", "l24m_max_dd"):
            assert key in result

    def test_empty_on_bad_inputs(self):
        result = compute_arch_a_trailing_pnl(None, "1D")
        assert result["l12m_trades"] == 0
        assert result["l24m_trades"] == 0

    def test_empty_on_inactive_tf(self):
        df = _make_arch_a_ohlcv()
        result = compute_arch_a_trailing_pnl(df, "4H")
        assert result["l12m_trades"] == 0

    def test_l12m_subset_of_l24m(self):
        """l12m trade count must be <= l24m trade count."""
        df = _make_arch_a_ohlcv()
        result = compute_arch_a_trailing_pnl(df, "1W")
        assert result["l12m_trades"] <= result["l24m_trades"]

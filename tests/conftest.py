"""Shared test fixtures for the trading_dashboard test suite."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    """Generate a synthetic 200-bar daily OHLCV DataFrame for testing."""
    np.random.seed(42)
    n = 200
    dates = pd.bdate_range("2024-01-01", periods=n, freq="B")
    close = 100.0 + np.cumsum(np.random.randn(n) * 1.5)
    high = close + np.abs(np.random.randn(n)) * 2
    low = close - np.abs(np.random.randn(n)) * 2
    open_ = close + np.random.randn(n) * 0.5
    volume = (np.random.rand(n) * 1_000_000 + 100_000).astype(int)

    return pd.DataFrame({
        "Open": open_,
        "High": high,
        "Low": low,
        "Close": close,
        "Volume": volume,
    }, index=dates)


@pytest.fixture
def sample_ohlcv_short() -> pd.DataFrame:
    """10-bar minimal DataFrame for edge-case testing."""
    np.random.seed(7)
    n = 10
    dates = pd.bdate_range("2025-01-01", periods=n, freq="B")
    close = 50.0 + np.cumsum(np.random.randn(n))
    return pd.DataFrame({
        "Open": close + np.random.randn(n) * 0.2,
        "High": close + np.abs(np.random.randn(n)),
        "Low": close - np.abs(np.random.randn(n)),
        "Close": close,
        "Volume": (np.random.rand(n) * 500_000 + 50_000).astype(int),
    }, index=dates)


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


@pytest.fixture
def ohlcv_with_nans(sample_ohlcv):
    """OHLCV data with NaN values for edge case testing."""
    df = sample_ohlcv.copy()
    df.iloc[10, df.columns.get_loc("Close")] = float("nan")
    df.iloc[20, df.columns.get_loc("Volume")] = float("nan")
    df.iloc[30, df.columns.get_loc("High")] = float("nan")
    return df


@pytest.fixture
def empty_ohlcv():
    """Empty OHLCV DataFrame."""
    return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])


@pytest.fixture
def ohlcv_missing_columns():
    """OHLCV data missing required columns."""
    dates = pd.date_range("2020-01-01", periods=100, freq="D")
    return pd.DataFrame({"Close": np.random.randn(100).cumsum() + 100}, index=dates)


@pytest.fixture
def mock_yfinance(monkeypatch):
    """Mock yfinance.download to avoid network calls in tests."""
    def fake_download(tickers, **kwargs):
        dates = pd.date_range("2024-01-01", periods=100, freq="B")
        np.random.seed(42)
        prices = 100 + np.cumsum(np.random.randn(100) * 2)
        return pd.DataFrame({
            "Open": prices * 0.99,
            "High": prices * 1.02,
            "Low": prices * 0.98,
            "Close": prices,
            "Volume": np.random.randint(1_000_000, 5_000_000, 100),
        }, index=dates)

    monkeypatch.setattr("yfinance.download", fake_download)
    return fake_download

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

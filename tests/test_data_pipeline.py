"""Tests for the data pipeline: DataStore, IncrementalUpdater, enrichment."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_dashboard.data.store import DataStore


@pytest.fixture
def tmp_store(tmp_path) -> DataStore:
    enriched = tmp_path / "enriched"
    raw = tmp_path / "raw"
    return DataStore(enriched_dir=enriched, raw_dir=raw, fmt="parquet", cache_ttl_hours=0)


@pytest.fixture
def mini_df() -> pd.DataFrame:
    np.random.seed(42)
    n = 50
    dates = pd.bdate_range("2025-01-01", periods=n, freq="B")
    close = 100.0 + np.cumsum(np.random.randn(n))
    return pd.DataFrame(
        {"Open": close + 0.1, "High": close + 1, "Low": close - 1,
         "Close": close, "Volume": np.random.randint(1000, 10000, n)},
        index=dates,
    )


class TestDataStoreIO:
    def test_save_load_enriched(self, tmp_store, mini_df):
        tmp_store.save_enriched("TEST", "1D", mini_df)
        loaded = tmp_store.load_enriched("TEST", "1D")
        assert loaded is not None
        assert len(loaded) == len(mini_df)
        assert list(loaded.columns) == list(mini_df.columns)

    def test_save_load_raw(self, tmp_store, mini_df):
        tmp_store.save_raw("TEST", "1W", mini_df)
        loaded = tmp_store.load_raw("TEST", "1W")
        assert loaded is not None
        assert len(loaded) == len(mini_df)

    def test_load_missing_returns_none(self, tmp_store):
        assert tmp_store.load_enriched("MISSING", "1D") is None
        assert tmp_store.load_raw("MISSING", "1D") is None

    def test_list_enriched_symbols(self, tmp_store, mini_df):
        tmp_store.save_enriched("AAPL", "1D", mini_df)
        tmp_store.save_enriched("MSFT", "1D", mini_df)
        syms = tmp_store.list_enriched_symbols("1D")
        assert "AAPL" in syms
        assert "MSFT" in syms

    def test_load_all_enriched(self, tmp_store, mini_df):
        tmp_store.save_enriched("SYM", "1D", mini_df)
        tmp_store.save_enriched("SYM", "1W", mini_df)
        all_data = tmp_store.load_all_enriched("SYM", ["1D", "1W"])
        assert "1D" in all_data
        assert "1W" in all_data


class TestEnrichmentMeta:
    def test_content_hash(self, mini_df):
        h1 = DataStore.compute_raw_hash(mini_df)
        assert isinstance(h1, str)
        assert len(h1) == 12

        modified = mini_df.copy()
        modified.iloc[-1, modified.columns.get_loc("Close")] = 999.0
        h2 = DataStore.compute_raw_hash(modified)
        assert h1 != h2

    def test_empty_hash(self):
        assert DataStore.compute_raw_hash(pd.DataFrame()) == "empty"
        assert DataStore.compute_raw_hash(None) == "empty"

    def test_enrichment_is_current(self, tmp_store, mini_df):
        raw_hash = DataStore.compute_raw_hash(mini_df)
        cfg_hash = "test_config_hash"
        tmp_store.save_enriched("SYM", "1D", mini_df, raw_hash=raw_hash, indicator_config_hash=cfg_hash)
        assert tmp_store.enrichment_is_current("SYM", "1D", raw_hash, cfg_hash)
        assert not tmp_store.enrichment_is_current("SYM", "1D", "different_hash", cfg_hash)
        assert not tmp_store.enrichment_is_current("SYM", "1D", raw_hash, "different_config")

    def test_config_hash(self, tmp_path):
        cfg_file = tmp_path / "test_config.json"
        cfg_file.write_text('{"key": "value"}')
        h1 = DataStore.compute_config_hash(cfg_file)
        assert isinstance(h1, str)
        assert len(h1) == 12
        assert DataStore.compute_config_hash(tmp_path / "nonexistent.json") == "default"


class TestIncrementalUpdater:
    def test_merge_new_bars(self, tmp_store, mini_df):
        from trading_dashboard.data.incremental import IncrementalUpdater

        updater = IncrementalUpdater(tmp_store)
        result = updater.merge_new_bars("SYM", "1D", mini_df)
        assert len(result) == len(mini_df)

        new_bar = pd.DataFrame(
            {"Open": [105], "High": [106], "Low": [104], "Close": [105.5], "Volume": [5000]},
            index=pd.DatetimeIndex(["2025-04-01"]),
        )
        result2 = updater.merge_new_bars("SYM", "1D", new_bar)
        assert len(result2) == len(mini_df) + 1

    def test_needs_update(self, tmp_store, mini_df):
        from trading_dashboard.data.incremental import IncrementalUpdater

        updater = IncrementalUpdater(tmp_store)
        assert updater.needs_update("SYM", "1D")
        # needs_update uses bar-date comparison (max_age_hours ignored).
        # Merge a bar dated today so the symbol is considered current.
        today_bar = pd.DataFrame(
            {"Open": [100.0], "High": [101.0], "Low": [99.0], "Close": [100.5], "Volume": [5000]},
            index=pd.DatetimeIndex([pd.Timestamp.now().normalize()]),
        )
        updater.merge_new_bars("SYM", "1D", today_bar)
        assert not updater.needs_update("SYM", "1D")


class TestMockYfinance:
    """Verify mock_yfinance fixture returns synthetic data without network calls."""

    def test_mock_yfinance_returns_synthetic_ohlcv(self, mock_yfinance):
        import yfinance as yf

        df = yf.download("AAPL", period="1mo", progress=False)
        assert len(df) == 100
        assert "Close" in df.columns
        assert "Open" in df.columns
        assert "High" in df.columns
        assert "Low" in df.columns
        assert "Volume" in df.columns


class TestEnrichment:
    def test_translate_and_compute(self, sample_ohlcv):
        from trading_dashboard.data.enrichment import translate_and_compute_indicators

        enriched, specs = translate_and_compute_indicators(sample_ohlcv)
        assert len(enriched) == len(sample_ohlcv)
        assert len(specs) > 10
        assert "Close" in enriched.columns
        for spec in specs:
            assert spec.key
            assert spec.title

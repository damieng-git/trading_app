"""Integration smoke tests — verify end-to-end data flow without network calls."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest


class TestScreenerBuilder:
    def test_build_screener_rows(self, sample_ohlcv):
        from trading_dashboard.data.enrichment import translate_and_compute_indicators
        from apps.dashboard.screener_builder import build_screener_rows

        enriched, _ = translate_and_compute_indicators(sample_ohlcv)
        all_data = {"TEST": {"1D": enriched}}
        rows_by_tf, by_symbol, state_cache = build_screener_rows(
            all_data=all_data,
            timeframes=["1D"],
            cfg_kpi_weights={"Nadaraya-Watson Smoother": 3.0},
            cfg_alerts_lookback_bars=3,
            cfg_combo_kpis_by_tf={},
            cfg_combo_3_kpis=["Nadaraya-Watson Smoother", "Madrid Ribbon", "Volume + MA20"],
            cfg_combo_4_kpis=["Nadaraya-Watson Smoother", "Madrid Ribbon", "GK Trend Ribbon", "cRSI"],
            symbol_display={"TEST": "Test Corp"},
            symbol_meta={},
            data_health={},
        )
        assert "1D" in rows_by_tf
        assert len(rows_by_tf["1D"]) == 1
        row = rows_by_tf["1D"][0]
        assert row["symbol"] == "TEST"
        assert isinstance(row["trend_score"], float)
        assert isinstance(row["kpi_states"], dict)

    def test_screener_row_has_position_status(self, sample_ohlcv):
        from trading_dashboard.data.enrichment import translate_and_compute_indicators
        from apps.dashboard.screener_builder import build_screener_rows

        enriched, _ = translate_and_compute_indicators(sample_ohlcv)
        rows_by_tf, _, _ = build_screener_rows(
            all_data={"TEST": {"1D": enriched}},
            timeframes=["1D"],
            cfg_kpi_weights={},
            cfg_alerts_lookback_bars=3,
            cfg_combo_kpis_by_tf={},
            cfg_combo_3_kpis=["Nadaraya-Watson Smoother", "Madrid Ribbon", "Volume + MA20"],
            cfg_combo_4_kpis=["Nadaraya-Watson Smoother", "Madrid Ribbon", "GK Trend Ribbon", "cRSI"],
            symbol_display={},
            symbol_meta={},
            data_health={},
        )
        row = rows_by_tf["1D"][0]
        assert "signal_action" in row
        assert row["signal_action"] in ("FLAT", "HOLD", "ENTRY 1x", "ENTRY 1.5x", "SCALE to 1.5x")


class TestDataExporter:
    def test_export_symbol_data(self, sample_ohlcv):
        from trading_dashboard.data.enrichment import translate_and_compute_indicators
        from apps.dashboard.data_exporter import export_symbol_data

        enriched, _ = translate_and_compute_indicators(sample_ohlcv)
        result = export_symbol_data("TEST", "1D", enriched, display_name="Test Corp")
        assert result["symbol"] == "TEST"
        assert result["timeframe"] == "1D"
        assert isinstance(result["x"], list)
        assert isinstance(result["c"], dict)
        assert "Close" in result["c"]
        assert len(result["x"]) == len(enriched)

    def test_export_json_serializable(self, sample_ohlcv):
        from trading_dashboard.data.enrichment import translate_and_compute_indicators
        from apps.dashboard.data_exporter import export_symbol_data_json

        enriched, _ = translate_and_compute_indicators(sample_ohlcv)
        json_str = export_symbol_data_json("TEST", "1D", enriched)
        parsed = json.loads(json_str)
        assert parsed["symbol"] == "TEST"

    def test_empty_df_returns_empty(self):
        from apps.dashboard.data_exporter import export_symbol_data

        result = export_symbol_data("TEST", "1D", pd.DataFrame())
        assert result == {}


class TestKPITimeline:
    def test_compute_kpi_timeline_matrix(self, sample_ohlcv):
        from trading_dashboard.data.enrichment import translate_and_compute_indicators
        from apps.dashboard.figures import compute_kpi_timeline_matrix

        enriched, _ = translate_and_compute_indicators(sample_ohlcv)
        result = compute_kpi_timeline_matrix(enriched)
        assert "kpis" in result
        assert "z" in result
        assert isinstance(result["kpis"], list)
        assert len(result["z"]) == len(result["kpis"])

    def test_empty_df(self):
        from apps.dashboard.figures import compute_kpi_timeline_matrix

        result = compute_kpi_timeline_matrix(pd.DataFrame())
        assert result == {"kpis": [], "z": [], "custom": []}

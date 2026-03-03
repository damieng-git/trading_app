"""Tests for config_loader.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.dashboard.config_loader import (
    BuildConfig,
    BuildPaths,
    TIMEFRAME_REGISTRY,
    get_timeframe,
    load_build_config,
    resolve_paths,
)


class TestTimeframeRegistry:
    def test_all_timeframes_registered(self):
        for tf in ("4H", "1D", "1W"):
            assert tf in TIMEFRAME_REGISTRY

    def test_get_timeframe(self):
        tf = get_timeframe("1D")
        assert tf.key == "1D"
        assert tf.max_plot_bars > 0
        assert tf.min_bars > 0

    def test_unknown_timeframe_raises(self):
        with pytest.raises(KeyError):
            get_timeframe("5M")


class TestLoadBuildConfig:
    def test_returns_build_config(self):
        cfg = load_build_config()
        assert isinstance(cfg, BuildConfig)

    def test_has_symbols(self):
        cfg = load_build_config()
        assert isinstance(cfg.symbols, list)
        assert len(cfg.symbols) > 0

    def test_has_timeframes(self):
        cfg = load_build_config()
        assert set(cfg.timeframes) >= {"4H", "1D", "1W"}

    def test_has_kpi_weights(self):
        cfg = load_build_config()
        assert isinstance(cfg.kpi_weights, dict)

    def test_combo_kpis(self):
        cfg = load_build_config()
        assert isinstance(cfg.combo_3_kpis, list)
        assert isinstance(cfg.combo_4_kpis, list)
        assert len(cfg.combo_3_kpis) >= 2
        assert len(cfg.combo_4_kpis) >= 2

    def test_dashboard_mode_valid(self):
        cfg = load_build_config()
        assert cfg.dashboard_mode in {"lazy_server", "lazy_static", "monolithic"}


class TestResolvePaths:
    def test_returns_build_paths(self):
        cfg = load_build_config()
        paths = resolve_paths(cfg)
        assert isinstance(paths, BuildPaths)

    def test_paths_are_path_objects(self):
        cfg = load_build_config()
        paths = resolve_paths(cfg)
        assert isinstance(paths.output_data_dir, Path)
        assert isinstance(paths.output_stock_data_dir, Path)
        assert isinstance(paths.dashboard_shell_html, Path)

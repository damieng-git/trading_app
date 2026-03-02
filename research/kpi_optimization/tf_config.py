"""
Shared timeframe configuration for KPI optimization scripts.

Provides argparse setup, horizon defaults, and output path resolution
so that each phase script can be run with `--timeframe 1D` etc.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List, NamedTuple

REPO_DIR = Path(__file__).resolve().parents[2]
ENRICHED_DIR = REPO_DIR / "data" / "feature_store" / "enriched" / "sample_100" / "stock_data"
OUTPUTS_ROOT = Path(__file__).resolve().parent / "outputs"


class TFConfig(NamedTuple):
    timeframe: str
    horizons: List[int]
    default_horizon: int
    min_bars: int
    horizon_labels: List[str]


TIMEFRAME_CONFIGS: Dict[str, TFConfig] = {
    "1M": TFConfig("1M", [1, 2, 4], 2, 24, ["1m", "2m", "4m"]),
    "2W": TFConfig("2W", [1, 2, 6], 2, 40, ["2w", "4w", "12w"]),
    "1W": TFConfig("1W", [1, 4, 13], 4, 52, ["1w", "4w", "13w"]),
    "1D": TFConfig("1D", [1, 5, 20], 5, 200, ["1d", "5d", "20d"]),
    "4H": TFConfig("4H", [2, 6, 20], 6, 200, ["8h", "24h", "80h"]),
}

VALID_TIMEFRAMES = list(TIMEFRAME_CONFIGS.keys())


def parse_timeframe_arg(description: str = "") -> TFConfig:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--timeframe", "-tf", default="1W",
        choices=VALID_TIMEFRAMES,
        help="Timeframe to analyze (default: 1W)",
    )
    args = parser.parse_args()
    return TIMEFRAME_CONFIGS[args.timeframe]


def output_dir_for(tf: str, phase: str) -> Path:
    return OUTPUTS_ROOT / tf / phase


def phase1_csv_for(tf: str) -> Path:
    return OUTPUTS_ROOT / tf / "phase1" / "kpi_scorecard.csv"


def phase2_json_for(tf: str) -> Path:
    return OUTPUTS_ROOT / tf / "phase2" / "indicator_config_optimised.json"

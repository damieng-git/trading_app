"""Shared pytest config for the skill's tests.

Registers the ``network`` marker and skips those tests by default so CI never
hits yfinance. Run them explicitly with:  pytest --run-network
"""

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-network", action="store_true", default=False,
        help="run tests marked @pytest.mark.network (live yfinance calls)")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "network: live yfinance smoke test; skipped unless --run-network")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-network"):
        return
    skip = pytest.mark.skip(reason="needs --run-network (live yfinance)")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)

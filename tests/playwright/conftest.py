"""Playwright fixtures for the trading dashboard UI test suite.

Browser connects to the staging server (localhost:8051) by default.
Override with: pytest --base-url=http://localhost:8050

Artifacts (screenshots, videos, traces) are saved to
tests/playwright/artifacts/ on failure only.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Browser, BrowserContext, Page

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ARTIFACTS_DIR = Path(__file__).parent / "artifacts"
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Config — base_url is provided by pytest-base-url plugin (--base-url flag)
# Default is set in pytest.ini / pyproject.toml or passed on the CLI.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Browser context — one per test, with tracing + video on failure
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def browser_context_args():
    """Extra kwargs passed to browser.new_context()."""
    return {
        "record_video_dir": str(ARTIFACTS_DIR / "videos"),
        "record_video_size": {"width": 1280, "height": 900},
        "viewport": {"width": 1280, "height": 900},
    }


@pytest.fixture
def context(browser: Browser, browser_context_args: dict) -> BrowserContext:
    ctx = browser.new_context(**browser_context_args)
    ctx.tracing.start(screenshots=True, snapshots=True, sources=False)
    ctx._trace_started = True
    yield ctx
    # Trace is stopped in the page fixture teardown (saves on failure, discards on pass)
    # Only close the context here if tracing was already stopped by the page fixture
    if getattr(ctx, "_trace_started", False):
        ctx.tracing.stop()   # discard — test passed, no artifact needed
    ctx.close()


@pytest.fixture
def page(context: BrowserContext, request, base_url: str) -> Page:
    pg = context.new_page()

    # Collect console errors for assertion in tests
    pg._console_errors: list[str] = []
    pg.on("console", lambda msg: pg._console_errors.append(msg.text)
          if msg.type == "error" else None)

    yield pg

    # ----- Save artifacts on failure -----
    failed = hasattr(request.node, "rep_call") and request.node.rep_call.failed
    if failed:
        test_name = request.node.name.replace("/", "_")
        # Screenshot
        screenshot_path = ARTIFACTS_DIR / "screenshots" / f"{test_name}.png"
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(screenshot_path), full_page=True)
        # Trace — stop and save; mark as handled so context fixture doesn't double-stop
        trace_path = ARTIFACTS_DIR / "traces" / f"{test_name}.zip"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        context.tracing.stop(path=str(trace_path))
        context._trace_started = False   # prevent double-stop in context fixture
        print(f"\n[playwright] screenshot → {screenshot_path}")
        print(f"[playwright] trace      → {trace_path}")
        print(f"[playwright] view trace: playwright show-trace {trace_path}")


# Hook to capture pass/fail result on the node before fixture teardown
@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)

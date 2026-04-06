"""Smoke tests — dashboard loads correctly and core UI elements are present.

Run against staging:
    pytest tests/playwright/test_dashboard.py -v

Run against a different URL:
    pytest tests/playwright/test_dashboard.py -v --base-url=http://localhost:8050
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TABS = [
    ("tabScreener", "Screener"),
    ("tabChart",    "Charts"),
    ("tabStrategy", "Strategy"),
    ("tabPnl",      "P&L"),
    ("tabScan",     "Scan"),
    ("tabInfo",     "Info"),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDashboardLoads:
    """Verify the shell page loads and basic structure is intact."""

    def test_page_loads_without_http_error(self, page: Page, base_url: str):
        """Page returns 200 and the body is non-empty."""
        response = page.goto(base_url, wait_until="domcontentloaded", timeout=15_000)
        assert response is not None, "No response received"
        assert response.status == 200, f"Expected HTTP 200, got {response.status}"

    def test_page_title_set(self, page: Page, base_url: str):
        """Page has a non-empty <title>."""
        page.goto(base_url, wait_until="domcontentloaded", timeout=15_000)
        assert page.title() != "", "Page title should not be empty"

    def test_no_js_errors_on_load(self, page: Page, base_url: str):
        """No JavaScript errors should fire during initial page load."""
        page.goto(base_url, wait_until="domcontentloaded", timeout=15_000)
        # Wait for the nav tabs to be present — confirms JS has initialised
        page.wait_for_selector("#tabChart", timeout=10_000)
        assert page._console_errors == [], (
            f"JS errors on load: {page._console_errors}"
        )


class TestTabsVisible:
    """All six nav tabs must be present and visible after load."""

    @pytest.mark.parametrize("tab_id,label", TABS)
    def test_tab_visible(self, page: Page, base_url: str, tab_id: str, label: str):
        page.goto(base_url, wait_until="domcontentloaded", timeout=15_000)
        tab = page.locator(f"#{tab_id}")
        expect(tab).to_be_visible(timeout=5_000)

    def test_screener_tab_active_by_default(self, page: Page, base_url: str):
        """Screener is the default tab when no prior state exists (JS default: currentTab = 'screener')."""
        page.goto(base_url, wait_until="domcontentloaded", timeout=15_000)
        page.wait_for_selector("#tabScreener", timeout=10_000)
        screener_tab = page.locator("#tabScreener")
        expect(screener_tab).to_have_attribute("aria-selected", "true", timeout=5_000)


class TestSidebarAndSymbols:
    """Sidebar and symbol list must render."""

    def test_sidebar_visible(self, page: Page, base_url: str):
        # Sidebar is hidden on Screener/PnL/Scan tabs — ensure Chart tab is active first
        page.goto(base_url, wait_until="domcontentloaded", timeout=15_000)
        page.wait_for_selector("#tabChart", timeout=10_000)
        page.locator("#tabChart").click()
        expect(page.locator("#sidebar")).to_be_visible(timeout=5_000)

    def test_symbol_list_present(self, page: Page, base_url: str):
        """#symbolList element must exist (may still be loading symbols)."""
        page.goto(base_url, wait_until="domcontentloaded", timeout=15_000)
        expect(page.locator("#symbolList")).to_be_attached(timeout=5_000)

    def test_symbol_list_populates(self, page: Page, base_url: str):
        """At least one symbol should appear in #symbolList within 10 s."""
        page.goto(base_url, wait_until="domcontentloaded", timeout=15_000)
        page.wait_for_selector("#tabChart", timeout=10_000)
        # Sidebar (and symbolList) is only visible on the Chart tab
        page.locator("#tabChart").click()
        # Symbol items rendered by dashboard.js use class "symRow"
        first_symbol = page.locator("#symbolList .symRow").first
        expect(first_symbol).to_be_visible(timeout=10_000)


class TestTabNavigation:
    """Clicking each tab must not produce JS errors or a blank panel."""

    @pytest.mark.parametrize("tab_id,label", TABS)
    def test_tab_click_no_js_error(self, page: Page, base_url: str, tab_id: str, label: str):
        # Use domcontentloaded — networkidle never fires while SSE streams are open
        page.goto(base_url, wait_until="domcontentloaded", timeout=15_000)
        page.wait_for_selector(f"#{tab_id}", timeout=10_000)
        page._console_errors.clear()
        page.locator(f"#{tab_id}").click()
        page.wait_for_timeout(500)   # allow any deferred rendering
        assert page._console_errors == [], (
            f"JS errors after clicking {label} tab: {page._console_errors}"
        )

"""Screener tab tests — verify the screener table populates with data.

The screener data is embedded in dashboard_shell.html (no extra API calls needed),
so these tests are fast.

Run:
    pytest tests/playwright/test_screener.py -v
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


class TestScreenerPopulates:
    """Screener tab must render a table with at least one data row."""

    def _goto_screener(self, page: Page, base_url: str) -> None:
        """Navigate to the dashboard and ensure the Screener tab is active."""
        page.goto(base_url, wait_until="domcontentloaded", timeout=15_000)
        page.wait_for_selector("#tabScreener", timeout=10_000)
        page.locator("#tabScreener").click()
        # screenerWrap becomes visible when screener tab is active
        expect(page.locator("#screenerWrap")).to_be_visible(timeout=5_000)

    def test_screener_table_renders(self, page: Page, base_url: str):
        """#screener must contain a <table> after the tab loads."""
        self._goto_screener(page, base_url)
        table = page.locator("#screener table")
        expect(table).to_be_visible(timeout=10_000)

    def test_screener_has_data_rows(self, page: Page, base_url: str):
        """Screener table must have at least one data row (not just section headers)."""
        self._goto_screener(page, base_url)
        # Data rows are plain <tr> inside <tbody>; section headers have class "scr-shdr"
        data_rows = page.locator("#screener tbody tr:not(.scr-shdr)")
        expect(data_rows.first).to_be_visible(timeout=10_000)
        count = data_rows.count()
        assert count > 0, f"Expected screener rows, got {count}"

    def test_screener_search_filters_rows(self, page: Page, base_url: str):
        """Typing in the search box should reduce the number of visible rows."""
        self._goto_screener(page, base_url)

        # Wait for rows to appear
        data_rows = page.locator("#screener tbody tr:not(.scr-shdr)")
        expect(data_rows.first).to_be_visible(timeout=10_000)
        total_before = data_rows.count()

        if total_before < 2:
            pytest.skip("Need at least 2 screener rows to test filtering")

        # Type a search term unlikely to match all symbols
        page.locator("#screenerSearch").fill("ZZZZZ_NO_MATCH")
        page.wait_for_timeout(400)  # debounce

        # Either zero rows or fewer rows than before
        total_after = data_rows.count()
        assert total_after < total_before, (
            f"Search filter had no effect: {total_before} rows before, {total_after} after"
        )

    def test_screener_strategy_filter_buttons_present(self, page: Page, base_url: str):
        """Strategy filter buttons (All, dip_buy, swing, etc.) must be present."""
        self._goto_screener(page, base_url)
        # Filter buttons use class "btn" inside #screenerFilters
        all_btn = page.locator("#screenerFilters .btn").first
        expect(all_btn).to_be_visible(timeout=5_000)

    def test_no_js_errors_on_screener(self, page: Page, base_url: str):
        """No JS console errors should occur while the screener tab renders."""
        page.goto(base_url, wait_until="domcontentloaded", timeout=15_000)
        page.wait_for_selector("#tabScreener", timeout=10_000)
        page._console_errors.clear()
        page.locator("#tabScreener").click()

        # Wait for table to render before checking
        expect(page.locator("#screener table")).to_be_visible(timeout=10_000)

        assert page._console_errors == [], (
            f"JS errors on screener tab: {page._console_errors}"
        )

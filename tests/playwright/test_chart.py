"""Chart rendering tests — verify that selecting a symbol loads a chart.

These tests are slower than the smoke tests because they wait for /fig API
responses and Plotly to finish rendering.

Run:
    pytest tests/playwright/test_chart.py -v
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

# A symbol guaranteed to have enriched data in the feature store AND
# present in the default "all" group. Update if symbol is removed —
# any entry in data/feature_store/enriched/dashboard/stock_data/ works.
KNOWN_SYMBOL = "000001.SS"


class TestChartRenders:
    """Clicking a symbol in the sidebar must trigger a chart render."""

    def _goto_chart_tab(self, page: Page, base_url: str) -> None:
        """Navigate to the dashboard, switch to Chart tab, wait for sidebar."""
        page.goto(base_url, wait_until="domcontentloaded", timeout=15_000)
        page.wait_for_selector("#tabChart", timeout=10_000)
        page.locator("#tabChart").click()
        expect(page.locator("#sidebar")).to_be_visible(timeout=5_000)

    def _click_symbol(self, page: Page, symbol: str) -> None:
        """Click a specific symbol row in the sidebar by ticker.

        .symName shows the display name, but its title attribute is:
        "DisplayName (TICKER)" — so we match on title containing the ticker.
        """
        sym_name = page.locator(f"#symbolList .symRow .symName[title*='{symbol}']").first
        expect(sym_name).to_be_visible(timeout=10_000)
        # Click the parent symRow (the clickable container)
        sym_name.locator("..").click()

    def _wait_for_render_complete(self, page: Page) -> None:
        """Wait until #chartUpper has finished rendering (skeleton class removed)."""
        # skeleton class is added during fetch and removed when done (data or no-data)
        page.wait_for_function(
            "() => !document.getElementById('chartUpper').classList.contains('skeleton')",
            timeout=20_000,
        )
        # Also ensure the element has some content (innerHTML not empty)
        page.wait_for_function(
            "() => (document.getElementById('chartUpper').innerHTML || '').trim().length > 0",
            timeout=5_000,
        )

    def test_chart_renders_after_symbol_click(self, page: Page, base_url: str):
        """Clicking a symbol with known data renders a Plotly chart in #chartUpper."""
        self._goto_chart_tab(page, base_url)
        self._click_symbol(page, KNOWN_SYMBOL)
        self._wait_for_render_complete(page)

        # Plotly adds js-plotly-plot class directly to the chart container element
        expect(page.locator(f"#chartUpper.js-plotly-plot")).to_be_visible(timeout=5_000)

    def test_chart_has_svg(self, page: Page, base_url: str):
        """A rendered Plotly chart must contain SVG elements (actual plot content)."""
        self._goto_chart_tab(page, base_url)
        self._click_symbol(page, KNOWN_SYMBOL)
        self._wait_for_render_complete(page)

        svg = page.locator("#chartUpper svg.main-svg")
        expect(svg).to_be_visible(timeout=5_000)

    def test_no_data_symbol_shows_message(self, page: Page, base_url: str):
        """A symbol with no cached data should show a 'No data available' message, not crash."""
        self._goto_chart_tab(page, base_url)

        # Click any symbol — if it has no data we check for the message;
        # if it has data we just verify the chart rendered (either outcome is valid)
        first_sym = page.locator("#symbolList .symRow").first
        expect(first_sym).to_be_visible(timeout=10_000)
        first_sym.click()

        self._wait_for_render_complete(page)

        # After render: either a Plotly chart OR a "No data" message — never blank
        has_chart = page.locator("#chartUpper.js-plotly-plot").count() > 0
        has_message = "no data" in (page.locator("#chartUpper").inner_text().lower())
        assert has_chart or has_message, (
            "#chartUpper is blank after render — expected either a chart or a 'no data' message"
        )

    def test_switching_symbols_rerenders(self, page: Page, base_url: str):
        """Clicking a second symbol after the first must trigger a new render."""
        self._goto_chart_tab(page, base_url)

        symbols = page.locator("#symbolList .symRow")
        expect(symbols.first).to_be_visible(timeout=10_000)

        if symbols.count() < 2:
            pytest.skip("Need at least 2 symbols in the list for this test")

        # Load first symbol
        symbols.nth(0).click()
        self._wait_for_render_complete(page)

        # Load second symbol — skeleton should appear and then clear again
        symbols.nth(1).click()
        self._wait_for_render_complete(page)

        # chartUpper must have content after second render
        content = page.locator("#chartUpper").inner_html()
        assert content.strip() != "", "#chartUpper is empty after switching symbols"

    def test_no_js_errors_during_chart_render(self, page: Page, base_url: str):
        """No JS console errors should occur while rendering a chart."""
        self._goto_chart_tab(page, base_url)
        self._click_symbol(page, KNOWN_SYMBOL)
        page._console_errors.clear()

        self._wait_for_render_complete(page)
        expect(page.locator("#chartUpper.js-plotly-plot")).to_be_visible(timeout=5_000)

        assert page._console_errors == [], (
            f"JS errors during chart render: {page._console_errors}"
        )

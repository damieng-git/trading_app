#!/usr/bin/env python3
"""Test TF filter synchronization across Screener, Charts, and P&L tabs."""
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

URL = "http://46.224.149.54/"
OUT_DIR = Path("/root/damiverse_apps/screenshots")
OUT_DIR.mkdir(exist_ok=True)

RESULTS = []


async def check_tf_active(page, expected_tf: str) -> bool:
    """Check if the expected TF button has 'active' class. data-tf can be 4H, 1D, 1W."""
    btn = page.locator(f'.tab-tf-btn[data-tf="{expected_tf}"]').first
    if not await btn.count():
        return False
    has_active = await btn.evaluate("el => el.classList.contains('active')")
    return has_active


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(30000)

            await page.goto(URL, wait_until="networkidle")
            await page.wait_for_timeout(2000)

            # --- 1. Screener tab: initial snapshot, then click 4H ---
            await page.screenshot(path=OUT_DIR / "sync_01_screener_before.png", full_page=False)
            print("Saved sync_01_screener_before.png")

            screener_4h = page.locator('#screenerWrap .tab-tf-btn[data-tf="4H"]').first
            await screener_4h.scroll_into_view_if_needed()
            await screener_4h.click()
            await page.wait_for_timeout(500)
            await page.screenshot(path=OUT_DIR / "sync_02_screener_after_4H_click.png", full_page=False)
            print("Saved sync_02_screener_after_4H_click.png")

            # --- 2. Switch to Charts tab, check if 4H is active ---
            await page.locator('#tabChart').click()
            await page.wait_for_timeout(1000)
            await page.screenshot(path=OUT_DIR / "sync_03_charts_after_4H.png", full_page=False)
            print("Saved sync_03_charts_after_4H.png")

            charts_4h_active = await check_tf_active(page, "4H")
            RESULTS.append(("Charts tab after Screener 4H click", "4H", charts_4h_active))

            # --- 3. On Charts, click W (1W) ---
            w_btn = page.locator('.tab-tf-btn[data-tf="1W"]').first
            await w_btn.click()
            await page.wait_for_timeout(500)
            await page.screenshot(path=OUT_DIR / "sync_04_charts_after_W_click.png", full_page=False)
            print("Saved sync_04_charts_after_W_click.png")

            # --- 4. Switch to P&L, check if W is active ---
            await page.locator('#tabPnl').click()
            await page.wait_for_timeout(1500)  # P&L may need to build
            await page.screenshot(path=OUT_DIR / "sync_05_pnl_after_W.png", full_page=False)
            print("Saved sync_05_pnl_after_W.png")

            pnl_w_active = await check_tf_active(page, "1W")
            RESULTS.append(("P&L tab after Charts W click", "1W", pnl_w_active))

            # Print summary
            print("\n--- TF Sync Results ---")
            for label, tf, active in RESULTS:
                status = "✓ ACTIVE" if active else "✗ NOT ACTIVE"
                print(f"  {label}: {tf} {status}")

        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())

"""
Playwright browser factory with WSLg support and anti-bot measures.

Uses persistent context so cookies (including consent) survive
across runs. Only need to accept cookies once.
"""

from __future__ import annotations

import os
from pathlib import Path

from playwright.async_api import BrowserContext, async_playwright


USER_DATA_DIR = Path.home() / ".db_scanner_browser_data"
VIEWPORT_PRESETS = [
    {"width": 1920, "height": 1080},
    {"width": 1680, "height": 1050},
    {"width": 1600, "height": 900},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
]


def _is_headed() -> bool:
    debug_env = os.environ.get("DEBUG_VISUAL", "").strip().lower()
    if debug_env == "true":
        return True
    if debug_env == "false":
        return False
    return bool(os.environ.get("DISPLAY"))


ANTI_BOT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => false });
window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['de-DE', 'de', 'en-US', 'en'] });
"""


class BrowserFactory:
    """
    Async context manager yielding (browser, context).

    Uses ``launch_persistent_context`` so cookies / storage persist
    in ``~/.db_scanner_browser_data`` across runs.
    """

    def __init__(self) -> None:
        self._headed = _is_headed()
        self._playwright = None
        self._context: BrowserContext | None = None

    @staticmethod
    def _random_viewport() -> dict:
        import random
        return random.choice(VIEWPORT_PRESETS)

    async def __aenter__(self):
        pw = await async_playwright().start()
        self._playwright = pw

        USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-infobars",
            "--disable-dev-shm-usage",
        ]

        if not self._headed:
            launch_args.append("--headless=new")

        viewport = self._random_viewport()

        self._context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(USER_DATA_DIR),
            channel="chrome",
            headless=not self._headed,
            args=launch_args,
            viewport=viewport,
            locale="de-DE",
            timezone_id="Europe/Berlin",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            extra_http_headers={
                "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )

        await self._context.add_init_script(ANTI_BOT_SCRIPT)

        browser = self._context.browser
        if browser is None:
            raise RuntimeError("Browser not available from persistent context")
        return browser, self._context

    async def __aexit__(self, *args) -> None:
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass

import asyncio
import random

from playwright.async_api import Page

from .config import AppConfig


class BaseScraper:
    def __init__(self, page: Page, config: AppConfig):
        self.page = page
        self.config = config

    async def _click_first(self, selectors: list[str]) -> bool:
        for sel in selectors:
            try:
                el = self.page.locator(sel).first
                if await el.is_visible(timeout=3000):
                    await el.click()
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    async def _delay(min_ms: int = 600, max_ms: int = 1400) -> None:
        await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))

    @staticmethod
    async def _human_type(field, text: str) -> None:
        """Type text character by character with per-keystroke jitter (60–180 ms)."""
        await field.click()
        for ch in text:
            await field.type(ch)
            await asyncio.sleep(random.uniform(0.06, 0.18))

import os
from playwright.async_api import async_playwright, Browser, BrowserContext, Playwright

class BrowserManager:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.playwright: Playwright = None
        self.browser: Browser = None
        self.context: BrowserContext = None

    async def start(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless)

    async def create_context(self, video_dir: str = None) -> BrowserContext:
        self.context = await self.browser.new_context(
            record_video_dir=video_dir,
            record_video_size={"width": 1280, "height": 720}
        )
        return self.context

    async def start_tracing(self):
        if self.context:
            await self.context.tracing.start(screenshots=True, snapshots=True, sources=True)

    async def stop_tracing(self, path: str):
        if self.context:
            await self.context.tracing.stop(path=path)

    async def close(self):
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

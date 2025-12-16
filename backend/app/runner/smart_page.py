from playwright.async_api import Page, TimeoutError
from app.ai.engine import ai_engine

class SmartPage:
    def __init__(self, page: Page):
        self.page = page

    async def click(self, selector: str, **kwargs):
        try:
            await self.page.click(selector, **kwargs)
        except TimeoutError:
            print(f"Selector {selector} failed. Attempting self-healing...")
            # Capture DOM snapshot
            try:
                snapshot = await self.page.accessibility.snapshot()
                dom_str = str(snapshot)
                
                new_selector = ai_engine.heal_selector(selector, dom_str)
                print(f"AI suggested new selector: {new_selector}")
                
                if new_selector and new_selector != selector:
                    await self.page.click(new_selector, **kwargs)
                    return new_selector # Return healed selector
            except Exception as e:
                print(f"Self-healing failed: {e}")
            raise

    async def fill(self, selector: str, value: str, **kwargs):
        try:
            await self.page.fill(selector, value, **kwargs)
        except TimeoutError:
            print(f"Selector {selector} failed. Attempting self-healing...")
            try:
                snapshot = await self.page.accessibility.snapshot()
                dom_str = str(snapshot)
                
                new_selector = ai_engine.heal_selector(selector, dom_str)
                print(f"AI suggested new selector: {new_selector}")
                
                if new_selector and new_selector != selector:
                    await self.page.fill(new_selector, value, **kwargs)
                    return new_selector
            except Exception as e:
                print(f"Self-healing failed: {e}")
            raise
            
    def __getattr__(self, name):
        return getattr(self.page, name)

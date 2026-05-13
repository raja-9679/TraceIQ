"""AI engine — failure analysis + selector healing.

Refactored to call through `app.ai.providers.provider` so the backing LLM
(OpenAI / Anthropic / null-stub) is chosen at process start via env vars.
"""
from app.ai.providers import provider


class AIEngine:
    def analyze_failure(self, error_log: str, dom_snapshot: str) -> str:
        if provider.name == "null":
            return "AI Analysis unavailable (no LLM provider configured)"

        prompt = (
            "Analyze the following UI test failure and explain in plain English "
            "what likely went wrong.\n\n"
            f"Error:\n{error_log}\n\n"
            f"DOM snapshot (truncated):\n{dom_snapshot[:2000]}"
        )
        result = provider.complete(prompt, max_tokens=512)
        return result or "AI Analysis unavailable (provider returned empty)"

    def heal_selector(self, broken_selector: str, dom_snapshot: str) -> str:
        if provider.name == "null":
            return ""

        prompt = (
            f"The selector '{broken_selector}' did not match any element.\n"
            "Given the DOM snapshot below, return ONLY the most likely "
            "corrected selector (CSS or XPath). No explanation.\n\n"
            f"{dom_snapshot}"
        )
        return provider.complete(prompt, max_tokens=128)


ai_engine = AIEngine()

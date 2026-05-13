"""LLM provider abstraction.

Today only OpenAI is wired in three places (backend AI engine, execution-engine
selector heal, execution-engine failure analyzer). This module gives all three
a single interface so the provider can be swapped via the `LLM_PROVIDER` env
var without touching call sites.
"""
from __future__ import annotations

import os
from typing import List, Optional, Protocol

from app.core.config import settings


class LLMProvider(Protocol):
    name: str

    def complete(self, prompt: str, *, system: Optional[str] = None, max_tokens: int = 1024) -> str:
        """Return a single completion string for `prompt`.

        Implementations should swallow provider-level errors and return an
        empty string when the request cannot be made; callers treat empty as
        "no AI available right now" and fall back to non-AI behavior.
        """
        ...


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-4o") -> None:
        from openai import OpenAI
        self._client = OpenAI(api_key=api_key)
        self._model = model

    def complete(self, prompt: str, *, system: Optional[str] = None, max_tokens: int = 1024) -> str:
        try:
            messages: List[dict] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=max_tokens,
            )
            content = resp.choices[0].message.content or ""
            return content.strip()
        except Exception as exc:  # noqa: BLE001 — provider errors must not bubble up
            print(f"[LLM] OpenAI call failed: {exc}")
            return ""


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-opus-4-7") -> None:
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise RuntimeError(
                "anthropic package not installed; add it to backend/requirements.txt"
            ) from exc
        self._client = Anthropic(api_key=api_key)
        self._model = model

    def complete(self, prompt: str, *, system: Optional[str] = None, max_tokens: int = 1024) -> str:
        try:
            kwargs = {
                "model": self._model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                kwargs["system"] = system
            resp = self._client.messages.create(**kwargs)
            text_parts = []
            for block in resp.content:
                if getattr(block, "type", None) == "text":
                    text_parts.append(block.text)
            return "".join(text_parts).strip()
        except Exception as exc:  # noqa: BLE001
            print(f"[LLM] Anthropic call failed: {exc}")
            return ""


class NullProvider:
    """Provider used when no API key is configured. Always returns "".

    Lets callers code against the same interface without guarding every call
    on a key-present check.
    """
    name = "null"

    def complete(self, prompt: str, *, system: Optional[str] = None, max_tokens: int = 1024) -> str:
        return ""


def _detect_provider_name() -> str:
    explicit = os.getenv("LLM_PROVIDER")
    if explicit:
        return explicit.strip().lower()
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY"):
        return "openai"
    return "null"


def build_default_provider() -> LLMProvider:
    name = _detect_provider_name()
    if name == "anthropic":
        key = os.getenv("ANTHROPIC_API_KEY", "")
        if not key:
            print("[LLM] LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is empty; using null provider")
            return NullProvider()
        model = os.getenv("LLM_MODEL", "claude-opus-4-7")
        return AnthropicProvider(api_key=key, model=model)
    if name == "openai":
        key = settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY", "")
        if not key:
            return NullProvider()
        model = os.getenv("LLM_MODEL", "gpt-4o")
        return OpenAIProvider(api_key=key, model=model)
    return NullProvider()


# Module-level singleton — built once at import time. Re-import if env vars
# change (typically only at process start).
provider: LLMProvider = build_default_provider()

"""LLM provider abstraction.

Today only OpenAI is wired in three places (backend AI engine, execution-engine
selector heal, execution-engine failure analyzer). This module gives all three
a single interface so the provider can be swapped via the `LLM_PROVIDER` env
var without touching call sites.
"""
from __future__ import annotations

import os
import time
from typing import List, Optional, Protocol

from app.core.config import settings
from app.services.llm_usage import llm_tokens_quota_exceeded, record_llm_usage


def _quota_blocked(provider_name: str) -> bool:
    """Plan-level monthly token cap (see services/llm_usage.py). When the
    ambient workspace is over its `monthly_llm_tokens` limit the call is
    skipped and callers fall back to their non-AI behavior."""
    if llm_tokens_quota_exceeded():
        print(f"[LLM] {provider_name} call skipped: workspace over monthly token quota")
        return True
    return False


class LLMProvider(Protocol):
    name: str

    def complete(self, prompt: str, *, system: Optional[str] = None, max_tokens: int = 1024) -> str:
        """Return a single completion string for `prompt`.

        Implementations should swallow provider-level errors and return an
        empty string when the request cannot be made; callers treat empty as
        "no AI available right now" and fall back to non-AI behavior.
        """
        ...


class OpenAICompatibleProvider:
    """Chat-completions provider for any OpenAI-wire-compatible endpoint.

    Covers OpenAI itself plus Gemini (via Google's compat endpoint), Ollama
    (local, free), Groq/OpenRouter free tiers, LM Studio, vLLM — anything that
    serves POST {base_url}/chat/completions.
    """

    def __init__(self, name: str, api_key: str, model: str, base_url: Optional[str] = None) -> None:
        from openai import OpenAI
        self.name = name
        self._client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
        self._model = model

    def complete(self, prompt: str, *, system: Optional[str] = None, max_tokens: int = 1024) -> str:
        if _quota_blocked(self.name):
            return ""
        started = time.monotonic()
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
            usage = getattr(resp, "usage", None)
            record_llm_usage(
                provider=self.name, model=self._model,
                input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                output_tokens=getattr(usage, "completion_tokens", 0) or 0,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            content = resp.choices[0].message.content or ""
            return content.strip()
        except Exception as exc:  # noqa: BLE001 — provider errors must not bubble up
            record_llm_usage(
                provider=self.name, model=self._model,
                latency_ms=int((time.monotonic() - started) * 1000),
                success=False, error=str(exc),
            )
            print(f"[LLM] {self.name} call failed: {exc}")
            return ""


GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-opus-4-8") -> None:
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise RuntimeError(
                "anthropic package not installed; add it to backend/requirements.txt"
            ) from exc
        self._client = Anthropic(api_key=api_key)
        self._model = model

    def complete(self, prompt: str, *, system: Optional[str] = None, max_tokens: int = 1024) -> str:
        if _quota_blocked(self.name):
            return ""
        started = time.monotonic()
        try:
            kwargs = {
                "model": self._model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                kwargs["system"] = system
            resp = self._client.messages.create(**kwargs)
            usage = getattr(resp, "usage", None)
            record_llm_usage(
                provider=self.name, model=self._model,
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            text_parts = []
            for block in resp.content:
                if getattr(block, "type", None) == "text":
                    text_parts.append(block.text)
            return "".join(text_parts).strip()
        except Exception as exc:  # noqa: BLE001
            record_llm_usage(
                provider=self.name, model=self._model,
                latency_ms=int((time.monotonic() - started) * 1000),
                success=False, error=str(exc),
            )
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
    if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
        return "gemini"
    if settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY"):
        return "openai"
    if os.getenv("OLLAMA_BASE_URL"):
        return "ollama"
    return "null"


def build_default_provider() -> LLMProvider:
    name = _detect_provider_name()
    model = os.getenv("LLM_MODEL", "")
    if name == "anthropic":
        key = os.getenv("ANTHROPIC_API_KEY", "")
        if not key:
            print("[LLM] LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is empty; using null provider")
            return NullProvider()
        return AnthropicProvider(api_key=key, model=model or "claude-opus-4-8")
    if name == "openai":
        key = settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY", "")
        if not key:
            return NullProvider()
        return OpenAICompatibleProvider("openai", api_key=key, model=model or "gpt-4o")
    if name == "gemini":
        # Google's OpenAI-compatible endpoint. The Gemini API has a generous
        # free tier, so this doubles as the free hosted option.
        key = os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")
        if not key:
            print("[LLM] LLM_PROVIDER=gemini but GEMINI_API_KEY is empty; using null provider")
            return NullProvider()
        return OpenAICompatibleProvider(
            "gemini", api_key=key, model=model or "gemini-2.0-flash",
            base_url=GEMINI_OPENAI_BASE_URL,
        )
    if name == "ollama":
        # Local, free, no API key. From inside Docker use
        # OLLAMA_BASE_URL=http://host.docker.internal:11434/v1
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        return OpenAICompatibleProvider(
            "ollama", api_key="ollama", model=model or "llama3.1",
            base_url=base_url,
        )
    if name in ("openai-compatible", "custom"):
        # Generic escape hatch: Groq/OpenRouter free tiers, LM Studio, vLLM…
        base_url = os.getenv("LLM_BASE_URL", "")
        if not base_url or not model:
            print("[LLM] LLM_PROVIDER=openai-compatible needs LLM_BASE_URL and LLM_MODEL; using null provider")
            return NullProvider()
        key = os.getenv("LLM_API_KEY", "") or "not-needed"
        return OpenAICompatibleProvider("openai-compatible", api_key=key, model=model, base_url=base_url)
    return NullProvider()


# Module-level singleton — built once at import time. Re-import if env vars
# change (typically only at process start).
provider: LLMProvider = build_default_provider()

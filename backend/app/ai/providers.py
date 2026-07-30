"""LLM provider abstraction.

Today only OpenAI is wired in three places (backend AI engine, execution-engine
selector heal, execution-engine failure analyzer). This module gives all three
a single interface so the provider can be swapped via the `LLM_PROVIDER` env
var without touching call sites.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, List, Optional, Protocol

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


def _eff(key: str) -> str:
    """Effective config value: admin-saved DB override, else environment.

    Local import — instance_settings imports app config, and this module is
    imported broadly; keep the dependency one-directional at import time.
    """
    from app.services.instance_settings import effective
    return str(effective(key) or "")


def _detect_provider_name() -> str:
    explicit = _eff("LLM_PROVIDER")
    if explicit:
        return explicit.strip().lower()
    if _eff("ANTHROPIC_API_KEY"):
        return "anthropic"
    if _eff("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
        return "gemini"
    if _eff("OPENAI_API_KEY"):
        return "openai"
    if _eff("OLLAMA_BASE_URL"):
        return "ollama"
    return "null"


def build_default_provider() -> LLMProvider:
    name = _detect_provider_name()
    model = _eff("LLM_MODEL")
    if name == "anthropic":
        key = _eff("ANTHROPIC_API_KEY")
        if not key:
            print("[LLM] LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is empty; using null provider")
            return NullProvider()
        return AnthropicProvider(api_key=key, model=model or "claude-opus-4-8")
    if name == "openai":
        key = _eff("OPENAI_API_KEY")
        if not key:
            return NullProvider()
        return OpenAICompatibleProvider("openai", api_key=key, model=model or "gpt-4o")
    if name == "gemini":
        # Google's OpenAI-compatible endpoint. The Gemini API has a generous
        # free tier, so this doubles as the free hosted option.
        key = _eff("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
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
        base_url = _eff("OLLAMA_BASE_URL") or "http://localhost:11434/v1"
        return OpenAICompatibleProvider(
            "ollama", api_key="ollama", model=model or "llama3.1",
            base_url=base_url,
        )
    if name in ("openai-compatible", "custom"):
        # Generic escape hatch: Groq/OpenRouter free tiers, LM Studio, vLLM…
        base_url = _eff("LLM_BASE_URL")
        if not base_url or not model:
            print("[LLM] LLM_PROVIDER=openai-compatible needs LLM_BASE_URL and LLM_MODEL; using null provider")
            return NullProvider()
        key = _eff("LLM_API_KEY") or "not-needed"
        return OpenAICompatibleProvider("openai-compatible", api_key=key, model=model, base_url=base_url)
    return NullProvider()


_AI_KEYS = ("LLM_PROVIDER", "LLM_MODEL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
            "GEMINI_API_KEY", "OLLAMA_BASE_URL", "LLM_BASE_URL", "LLM_API_KEY")


class _ProviderProxy:
    """Rebuilds the legacy single provider whenever the effective AI config
    changes (effective() is TTL-cached, so the signature check is cheap)."""

    def __init__(self) -> None:
        self._built: Optional[LLMProvider] = None
        self._sig: Optional[tuple] = None

    def _resolve(self) -> LLMProvider:
        sig = tuple(_eff(k) for k in _AI_KEYS)
        if self._built is None or sig != self._sig:
            self._built = build_default_provider()
            self._sig = sig
        return self._built


_legacy_proxy = _ProviderProxy()


# ---------------------------------------------------------------------------
# Saved provider configs (llm_provider_config table, admin-managed)
# ---------------------------------------------------------------------------

_CFG_TTL_SECONDS = 15.0
_cfg_lock = threading.Lock()
_cfg_cache: List[Dict[str, Any]] = []
_cfg_cache_at: float = 0.0
_built_from_cfg: Dict[int, tuple] = {}   # id -> (updated_at, LLMProvider)


def _load_provider_configs_sync() -> List[Dict[str, Any]]:
    from sqlalchemy import text
    from app.core.secrets import decrypt_secret
    # Shares the small sync pool instance_settings already keeps around.
    from app.services.instance_settings import _sync_engine
    with _sync_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT id, name, provider_type, model, base_url, api_key_encrypted,"
            " is_active, is_default, updated_at FROM llm_provider_config"
        )).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        api_key = ""
        if r.api_key_encrypted:
            try:
                api_key = decrypt_secret(r.api_key_encrypted)
            except Exception:
                print(f"[LLM] cannot decrypt api key for provider config '{r.name}' "
                      "(SECRET_KEY rotated?); treating as keyless")
        out.append({
            "id": r.id, "name": r.name, "provider_type": r.provider_type,
            "model": r.model, "base_url": r.base_url, "api_key": api_key,
            "is_active": r.is_active, "is_default": r.is_default,
            "updated_at": r.updated_at,
        })
    return out


def _provider_configs() -> List[Dict[str, Any]]:
    """TTL-cached saved configs; empty list (legacy behavior) when the table
    is missing or the DB is unavailable."""
    global _cfg_cache, _cfg_cache_at
    now = time.monotonic()
    with _cfg_lock:
        if now - _cfg_cache_at < _CFG_TTL_SECONDS:
            return _cfg_cache
    try:
        fresh = _load_provider_configs_sync()
    except Exception:
        fresh = []
    with _cfg_lock:
        _cfg_cache, _cfg_cache_at = fresh, now
    return fresh


def invalidate_provider_config_cache() -> None:
    global _cfg_cache_at
    with _cfg_lock:
        _cfg_cache_at = 0.0


def build_provider_from_config(cfg: Dict[str, Any]) -> LLMProvider:
    """Instantiate a provider from a saved config row. The instance's `name`
    is the config's display name so LLMUsageEvent rows attribute per config."""
    ptype = (cfg.get("provider_type") or "").strip().lower()
    model = cfg.get("model") or ""
    key = cfg.get("api_key") or ""
    base_url = cfg.get("base_url") or None
    label = cfg.get("name") or ptype
    built: LLMProvider
    if ptype == "anthropic":
        if not key:
            return NullProvider()
        built = AnthropicProvider(api_key=key, model=model or "claude-opus-4-8")
    elif ptype == "openai":
        if not key:
            return NullProvider()
        built = OpenAICompatibleProvider(label, api_key=key, model=model or "gpt-4o",
                                         base_url=base_url)
    elif ptype == "gemini":
        if not key:
            return NullProvider()
        built = OpenAICompatibleProvider(label, api_key=key,
                                         model=model or "gemini-2.0-flash",
                                         base_url=base_url or GEMINI_OPENAI_BASE_URL)
    elif ptype == "ollama":
        built = OpenAICompatibleProvider(label, api_key="ollama",
                                         model=model or "llama3.1",
                                         base_url=base_url or "http://localhost:11434/v1")
    elif ptype in ("openai-compatible", "custom"):
        if not base_url or not model:
            return NullProvider()
        built = OpenAICompatibleProvider(label, api_key=key or "not-needed",
                                         model=model, base_url=base_url)
    else:
        return NullProvider()
    built.name = label
    return built


def _built(cfg: Dict[str, Any]) -> LLMProvider:
    cached = _built_from_cfg.get(cfg["id"])
    if cached and cached[0] == cfg["updated_at"]:
        return cached[1]
    instance = build_provider_from_config(cfg)
    _built_from_cfg[cfg["id"]] = (cfg["updated_at"], instance)
    return instance


def get_provider(provider_id: Optional[int] = None) -> LLMProvider:
    """The provider to use for a call.

    provider_id names a saved ACTIVE config; missing/inactive ids fall back to
    the default. Default = the saved config marked is_default; with no saved
    configs at all the legacy single-provider resolution (instance settings /
    env) applies, so pre-registry installs behave exactly as before.
    """
    configs = _provider_configs()
    cfg = None
    if provider_id is not None:
        cfg = next((c for c in configs if c["id"] == provider_id and c["is_active"]), None)
        if cfg is None:
            print(f"[LLM] provider config {provider_id} missing or inactive; using default")
    if cfg is None:
        cfg = next((c for c in configs if c["is_default"] and c["is_active"]), None)
    if cfg is not None:
        return _built(cfg)
    return _legacy_proxy._resolve()


class _DefaultProviderProxy:
    """Module-level `provider`: always the CURRENT default (saved default
    config if one exists, else legacy env/instance-settings resolution)."""

    @property
    def name(self) -> str:
        return get_provider().name

    def complete(self, prompt: str, *, system: Optional[str] = None, max_tokens: int = 1024) -> str:
        return get_provider().complete(prompt, system=system, max_tokens=max_tokens)


provider: LLMProvider = _DefaultProviderProxy()

"""Effective runtime config for execution workers (WP8).

Served by `GET /api/internal/worker-config` (shared worker secret, optional
mTLS) and polled by every worker at start and every 60 s. Each value is the
instance setting when one is set, else `None`, which tells the worker to keep
its OWN environment value — not the backend's. That is the whole fallback
story: an unreachable backend, an older backend without this endpoint, and an
unset setting all leave a worker exactly as its env configured it.

What stays env-only on the worker, by construction (a worker must be able to
start, reach Redis and authenticate before it can fetch anything):
REDIS_URL / REDIS_*, WORKER_WEBHOOK_SECRET / WEBHOOK_SECRET, BACKEND_URL, the
mTLS client cert/key/CA, MINIO_* credentials and endpoint, WORKER_REGION,
APPIUM_URL / device-cloud credentials, and process-shape settings read once at
start (MAX_JOBS_BEFORE_RESTART, ARTIFACTS_DIR).

The LLM key is the only secret here. It is returned only to an authenticated
worker and never logged on either side.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.services.instance_settings import effective

# instance setting -> the env var name the worker code reads.
WORKER_ENV_MAP = {
    "WORKER_DEFAULT_TIMEOUT_MS": "DEFAULT_TIMEOUT",
    "WORKER_MAX_JOB_DURATION_MS": "MAX_JOB_DURATION_MS",
    "WORKER_RUNTIME_HEAL_ENABLED": "RUNTIME_HEAL_ENABLED",
    "WORKER_HAR_CAPTURE_ENABLED": "HAR_CAPTURE_ENABLED",
}

CONFIG_VERSION = 1


def _env_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value) if value > 0 else None
    text = str(value).strip()
    return text or None


def _legacy_ai_llm() -> Optional[Dict[str, Any]]:
    from app.ai.providers import _detect_provider_name
    name = _detect_provider_name()
    if name == "null":
        return None
    key_for = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY",
               "gemini": "GEMINI_API_KEY", "openai-compatible": "LLM_API_KEY",
               "custom": "LLM_API_KEY"}
    base_for = {"ollama": "OLLAMA_BASE_URL", "openai-compatible": "LLM_BASE_URL",
                "custom": "LLM_BASE_URL"}
    return {
        "provider": "openai-compatible" if name == "custom" else name,
        "model": effective("LLM_MODEL") or None,
        "api_key": (effective(key_for[name]) or None) if name in key_for else None,
        "base_url": (effective(base_for[name]) or None) if name in base_for else None,
    }


def _inherited_llm() -> Optional[Dict[str, Any]]:
    """The backend's own default provider: the saved-provider registry's
    default when any are saved, else the legacy single-provider settings."""
    from app.ai.providers import _provider_configs
    configs = [c for c in _provider_configs() if c.get("is_active")]
    if configs:
        chosen = next((c for c in configs if c.get("is_default")), configs[0])
        return {"provider": chosen["provider_type"], "model": chosen.get("model") or None,
                "api_key": chosen.get("api_key") or None,
                "base_url": chosen.get("base_url") or None}
    return _legacy_ai_llm()


def worker_llm() -> Optional[Dict[str, Any]]:
    provider = str(effective("WORKER_LLM_PROVIDER") or "").strip().lower()
    if not provider:
        return None                       # worker keeps its own env
    if provider == "inherit":
        return _inherited_llm() or {"provider": "null", "model": None,
                                    "api_key": None, "base_url": None}
    return {
        "provider": provider,
        "model": effective("WORKER_LLM_MODEL") or None,
        "api_key": effective("WORKER_LLM_API_KEY") or None,
        "base_url": effective("WORKER_LLM_BASE_URL") or None,
    }


def effective_worker_config() -> Dict[str, Any]:
    return {
        "version": CONFIG_VERSION,
        "env": {env: _env_value(effective(key)) for key, env in WORKER_ENV_MAP.items()},
        "llm": worker_llm(),
    }

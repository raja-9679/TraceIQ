"""Suite setup/teardown hooks, run-scoped data and their reports.

Suite settings (the free-form ``TestSuite.settings`` JSON) may carry:

* ``setup_steps`` / ``teardown_steps`` — lists in the case step schema;
* ``data_seed`` — makes ``{{fake.*}}`` deterministic (inherited down the chain
  like the other execution flags; a run's own ``data_seed`` wins).

Hooks are NOT merged like headers. Each case gets its own *chain* — one frame
per suite from the outermost ancestor down to the case's suite, cut at the
nearest suite with ``inherit_settings=false`` (that suite's own hooks still
apply; its ancestors' do not — the same boundary settings inheritance uses).
The worker runs setup outermost-first and teardown innermost-first; see
``execution-engine/src/core/suite-hooks.ts`` for the per-job semantics.

Reports come back from the worker as ``hook_report`` and are rebuilt field by
field here before landing in ``result_payload.hooks`` / ``.cleanup`` — like
step evidence they can come from a local worker, i.e. from any API-key holder.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from typing import Any, Dict, List, Optional

HOOK_KEYS = ("setup_steps", "teardown_steps")
MAX_HOOK_STEPS = 50
MAX_SEED_CHARS = 64

_CHAIN_CACHE_KEY = "traceiq_suite_hook_chains"


class SuiteHookError(ValueError):
    """Malformed hook settings; the message is user-facing (400)."""


# ---------------------------------------------------------------------------
# Settings validation
# ---------------------------------------------------------------------------

def normalize_seed(value: Any) -> Optional[str]:
    """A seed as stored: trimmed string, None when empty. Ints are accepted
    (CI systems pass build numbers) and stored as their decimal text."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        value = str(int(value)) if float(value).is_integer() else str(value)
    if not isinstance(value, str):
        raise SuiteHookError("data_seed must be a string or a number")
    value = value.strip()
    if not value:
        return None
    if len(value) > MAX_SEED_CHARS:
        raise SuiteHookError(f"data_seed is longer than {MAX_SEED_CHARS} characters")
    return value


def _normalize_steps(raw: Any, key: str) -> List[Dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SuiteHookError(f"{key} must be a list of steps")
    if len(raw) > MAX_HOOK_STEPS:
        raise SuiteHookError(f"{key} has more than {MAX_HOOK_STEPS} steps")
    out = []
    for i, step in enumerate(raw):
        if not isinstance(step, dict) or not isinstance(step.get("type"), str) or not step["type"].strip():
            raise SuiteHookError(f"{key}[{i}] must be a step object with a type")
        if step.get("params") is not None and not isinstance(step["params"], dict):
            raise SuiteHookError(f"{key}[{i}].params must be an object")
        if not step.get("id"):
            # Same as case steps: the editor and evidence key on step ids.
            step = {**step, "id": f"step-{uuid.uuid4().hex[:8]}"}
        out.append(step)
    return out


def validate_suite_settings(settings: Any) -> Any:
    """Validate + normalise the hook/seed keys of a suite settings blob.

    Returns a new dict (other keys untouched). Raises SuiteHookError."""
    if not isinstance(settings, dict):
        return settings
    out = dict(settings)
    for key in HOOK_KEYS:
        if key in out:
            steps = _normalize_steps(out[key], key)
            if steps:
                out[key] = steps
            else:
                out.pop(key)
    if "review_interval_days" in out:
        from app.services.ownership_review import OwnershipError, normalize_interval
        try:
            interval = normalize_interval(out["review_interval_days"])
        except OwnershipError as exc:
            raise SuiteHookError(str(exc))
        if interval is None:
            out.pop("review_interval_days")
        else:
            out["review_interval_days"] = interval
    if "data_seed" in out:
        seed = normalize_seed(out["data_seed"])
        if seed is None:
            out.pop("data_seed")
        else:
            out["data_seed"] = seed
    return out


# ---------------------------------------------------------------------------
# Per-case hook chain
# ---------------------------------------------------------------------------

def _frame(suite) -> Optional[Dict[str, Any]]:
    settings = suite.settings if isinstance(suite.settings, dict) else {}
    setup = [s for s in (settings.get("setup_steps") or []) if isinstance(s, dict)]
    teardown = [s for s in (settings.get("teardown_steps") or []) if isinstance(s, dict)]
    if not setup and not teardown:
        return None
    return {
        "suite_id": suite.id,
        "suite_name": suite.name,
        "setup_steps": setup[:MAX_HOOK_STEPS],
        "teardown_steps": teardown[:MAX_HOOK_STEPS],
    }


def hook_chain_sync(suite_id: Optional[int], session) -> List[Dict[str, Any]]:
    """Frames for a case in `suite_id`, outermost first.

    Walks up the parent chain and stops AFTER a suite with
    inherit_settings=false. Suites without hooks contribute no frame (the
    worker matches frames by suite id, so gaps are harmless). Cached per
    session — one dispatch looks up the same few suites for every case."""
    if suite_id is None:
        return []
    from app.models import TestSuite
    cache = session.info.setdefault(_CHAIN_CACHE_KEY, {})
    if suite_id in cache:
        return cache[suite_id]
    frames: List[Dict[str, Any]] = []
    seen = set()
    current = session.get(TestSuite, suite_id)
    project_id = getattr(current, "project_id", None)
    # Never cross a project boundary: hooks run with this project's secrets,
    # and a parent_id pointing elsewhere must not pull foreign steps in.
    while current is not None and current.id not in seen and current.project_id == project_id:
        seen.add(current.id)
        frame = _frame(current)
        if frame:
            frames.insert(0, frame)
        if not current.inherit_settings or not current.parent_id:
            break
        current = session.get(TestSuite, current.parent_id)
    cache[suite_id] = frames
    return frames


# ---------------------------------------------------------------------------
# Run context ({{run.*}})
# ---------------------------------------------------------------------------

def run_short_token(run_id: int) -> str:
    """{{run.short}}: 8 lowercase base32 chars, HMAC-derived so it is not just
    the run id in disguise (it ends up in usernames/emails on systems under
    test) and does not collide across TraceIQ instances the way ids do."""
    from app.core.config import settings
    key = hmac.new(settings.SECRET_KEY.encode(), b"traceiq/run-short/v1", hashlib.sha256).digest()
    mac = hmac.new(key, str(int(run_id)).encode(), hashlib.sha256).digest()
    return base64.b32encode(mac).decode().lower()[:8]


def run_context(run, data_seed: Optional[str], started_at) -> Dict[str, Any]:
    """`settings.run` in every job: the worker's source for {{run.*}}."""
    return {
        "id": run.id,
        "short": run_short_token(run.id),
        "started_at": started_at.replace(microsecond=0).isoformat() + "Z",
        "data_seed": data_seed,
    }


# ---------------------------------------------------------------------------
# Worker report -> result_payload
# ---------------------------------------------------------------------------

_PHASE_STATUSES = {"passed", "failed", "skipped"}
_CLEANUP_STATUSES = {"ok", "failed", "skipped"}
MAX_PHASES = 50
MAX_CLEANUPS = 100


def _str(value: Any, limit: int) -> Optional[str]:
    return value[:limit] if isinstance(value, str) and value else None


def _int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _phases(raw: Any, phase: str) -> List[Dict[str, Any]]:
    out = []
    for item in (raw if isinstance(raw, list) else [])[:MAX_PHASES]:
        if not isinstance(item, dict) or _int(item.get("suite_id")) is None:
            continue
        entry: Dict[str, Any] = {
            "suite_id": item["suite_id"],
            "phase": phase,
            "status": item.get("status") if item.get("status") in _PHASE_STATUSES else "failed",
        }
        for key, limit in (("suite_name", 200), ("error", 1000)):
            v = _str(item.get(key), limit)
            if v:
                entry[key] = v
        if _int(item.get("duration_ms")) is not None:
            entry["duration_ms"] = max(0, item["duration_ms"])
        steps = []
        for st in (item.get("steps") if isinstance(item.get("steps"), list) else [])[:MAX_HOOK_STEPS]:
            if not isinstance(st, dict) or _int(st.get("index")) is None:
                continue
            s: Dict[str, Any] = {
                "index": st["index"],
                "type": _str(st.get("type"), 60) or "unknown",
                "status": st.get("status") if st.get("status") in _PHASE_STATUSES else "failed",
            }
            err = _str(st.get("error"), 1000)
            if err:
                s["error"] = err
            if _int(st.get("duration_ms")) is not None:
                s["duration_ms"] = max(0, st["duration_ms"])
            steps.append(s)
        entry["steps"] = steps
        out.append(entry)
    return out


def _cleanups(raw: Any) -> List[Dict[str, Any]]:
    out = []
    for item in (raw if isinstance(raw, list) else [])[:MAX_CLEANUPS]:
        if not isinstance(item, dict):
            continue
        entry: Dict[str, Any] = {
            "method": (_str(item.get("method"), 10) or "DELETE").upper(),
            "url": _str(item.get("url"), 500) or "",
            "status": item.get("status") if item.get("status") in _CLEANUP_STATUSES else "failed",
            "origin": "setup" if item.get("origin") == "setup" else "case",
        }
        for key in ("http_status", "suite_id"):
            if _int(item.get(key)) is not None:
                entry[key] = item[key]
        err = _str(item.get("error"), 1000)
        if err:
            entry["error"] = err
        out.append(entry)
    return out


def hook_warnings(hooks: Dict[str, Any], cleanup: List[Dict[str, Any]]) -> List[str]:
    """Teardown/cleanup failures: warnings on the result, never a red case."""
    out = []
    for t in hooks.get("teardown") or []:
        if t.get("status") == "failed":
            out.append(f"Teardown failed ({t.get('suite_name') or 'suite %s' % t['suite_id']}): {t.get('error', '')}")
    for c in cleanup:
        if c.get("status") == "failed":
            out.append(f"Cleanup {c['method']} {c['url']} failed: {c.get('error', '')}")
    return out


def hook_payload(raw: Any) -> Dict[str, Any]:
    """result_payload keys for a worker `hook_report` (empty dict if none)."""
    if not isinstance(raw, dict):
        return {}
    hooks = {"setup": _phases(raw.get("setup"), "setup"),
             "teardown": _phases(raw.get("teardown"), "teardown")}
    cleanup = _cleanups(raw.get("cleanup"))
    out: Dict[str, Any] = {}
    if hooks["setup"] or hooks["teardown"]:
        out["hooks"] = hooks
    if cleanup:
        out["cleanup"] = cleanup
    warnings = hook_warnings(hooks, cleanup)
    if warnings:
        out["hook_warnings"] = warnings[:50]
    return out

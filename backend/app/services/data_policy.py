"""Capture policy — what a run is permitted to record, and how much of it.

Before this existed, every project captured everything: full-page screenshots,
video, Playwright traces with DOM snapshots, optionally a HAR, plus request and
response bodies persisted verbatim into Postgres. There was no control at all,
which is what put TraceIQ out of reach of any deployment handling regulated
data.

Two layers:

*Project* — `Project.data_policy`, a nullable JSON blob following the same
convention as `quality_gate_policy`, `ci_settings` and `security_settings`
(None means "use the built-in default").

*Instance* — `MAX_CAPTURE_LEVEL`, an instance setting no project may exceed.
This is the control that makes a scoped deployment defensible: set the instance
to `minimal` and no project, however misconfigured, can cause a HAR or a trace
to be written. That is a property an auditor verifies once rather than
re-verifying per project forever.

Both directions fail safe. An unrecognised level — from a typo, a newer
backend, or a hand-edited blob — resolves to `standard`, never to `full`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

#: Ordered least to most revealing. Index is the comparison key.
CAPTURE_LEVELS = ("none", "minimal", "standard", "full")

DEFAULT_CAPTURE_LEVEL = "standard"

_LEVEL_INDEX = {level: index for index, level in enumerate(CAPTURE_LEVELS)}


def normalize_capture_level(level: Any) -> str:
    """Coerce anything into a known level, defaulting to `standard`.

    Never returns `full` for unrecognised input: a typo must not widen what is
    captured.
    """
    if not isinstance(level, str):
        return DEFAULT_CAPTURE_LEVEL
    key = level.strip().lower()
    return key if key in _LEVEL_INDEX else DEFAULT_CAPTURE_LEVEL


def cap_capture_level(requested: Any, ceiling: Any) -> str:
    """Clamp a project's requested level to the instance ceiling.

    An unset or unparseable ceiling resolves to `standard` rather than to
    "unlimited" — an operator who has not configured one should not thereby
    grant the most revealing setting, and a typo in an instance setting must
    not silently lift the ceiling.
    """
    requested_level = normalize_capture_level(requested)
    ceiling_level = normalize_capture_level(ceiling)
    if _LEVEL_INDEX[requested_level] <= _LEVEL_INDEX[ceiling_level]:
        return requested_level
    return ceiling_level


def _string_list(value: Any) -> List[str]:
    """Coerce a policy field to a list of non-empty strings.

    The blob is user-editable JSON, so a scalar where a list belongs is an
    ordinary mistake rather than an attack. Dropping it beats raising: a
    malformed redaction hint must not fail every run in the project.
    """
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def effective_data_policy(
    project_policy: Optional[Dict[str, Any]],
    ceiling: Any = None,
) -> Dict[str, Any]:
    """Resolve the policy that will be attached to a job.

    Only known keys survive, so nothing unexpected in a hand-edited blob
    reaches the worker.
    """
    blob = project_policy if isinstance(project_policy, dict) else {}

    patterns = blob.get("redact_patterns")
    # None and [] mean different things and both are meaningful: None asks for
    # the built-in pattern set, [] asks for no value scanning at all.
    resolved_patterns = _string_list(patterns) if isinstance(patterns, list) else None

    return {
        "capture_level": cap_capture_level(blob.get("capture_level"), ceiling),
        "store_bodies": bool(blob.get("store_bodies", True)),
        "redact_headers": _string_list(blob.get("redact_headers")),
        "redact_body_fields": _string_list(blob.get("redact_body_fields")),
        "redact_patterns": resolved_patterns,
        "mask_selectors": _string_list(blob.get("mask_selectors")),
        "retention_days": int(blob.get("retention_days") or 0),
    }


def resolve_for_project(project: Any) -> Dict[str, Any]:
    """Effective policy for a project, reading the instance ceiling.

    Kept separate from `effective_data_policy` so the pure resolution logic
    stays testable without the instance-settings machinery (and its DB).
    """
    try:
        from app.services.instance_settings import effective as instance_effective
        ceiling = instance_effective("MAX_CAPTURE_LEVEL")
    except Exception:  # noqa: BLE001 — DB down must not fail dispatch
        ceiling = None
    return effective_data_policy(getattr(project, "data_policy", None), ceiling)

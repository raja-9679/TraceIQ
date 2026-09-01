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

#: The patterns applied when a project does NOT set `redact_patterns` (absent,
#: as distinct from an empty list meaning "scan for nothing"). Imported rather
#: than restated so the API cannot advertise a default set that differs from
#: what redaction actually applies. redaction.py does not import this module,
#: so there is no cycle.
from app.services.redaction import DEFAULT_PATTERNS  # noqa: E402

#: Value patterns a project may ask for. Mirrors the engine's redact.ts and
#: app/services/redaction.py — a name not in here would silently scan for
#: nothing, so the API rejects it rather than accepting a typo.
AVAILABLE_PATTERNS = ("pan", "aadhaar", "jwt", "email", "phone")

#: Artifact kinds, mirroring execution-engine/src/core/artifact-store.ts. Used
#: to tell the UI what the chosen level actually permits.
ARTIFACT_KINDS = ("screenshot", "console_log", "network_log", "visual_diff",
                  "video", "trace", "har")

_LEVEL_ALLOWS = {
    "none": frozenset(),
    "minimal": frozenset({"screenshot"}),
    "standard": frozenset({"screenshot", "console_log", "network_log", "visual_diff"}),
    "full": frozenset(ARTIFACT_KINDS),
}

#: Fields a caller may set, and the shape each must have.
_LIST_FIELDS = ("redact_headers", "redact_body_fields", "mask_selectors")
_WRITABLE_FIELDS = (
    "capture_level", "store_bodies", "redact_patterns", "retention_days",
) + _LIST_FIELDS

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


def artifact_allowed(kind: str, level: str) -> bool:
    """Whether `level` permits an artifact of `kind`. Mirrors the engine."""
    return kind in _LEVEL_ALLOWS.get(normalize_capture_level(level), frozenset())


def _validated_string_list(field: str, value: Any) -> List[str]:
    """Coerce and validate a list-of-strings field from an API payload.

    Stricter than `_string_list`: that one is for reading a blob already in the
    database, where dropping junk beats failing every run in the project. This
    one is for a human submitting a form, where telling them the field is wrong
    is more useful than silently discarding it.
    """
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list of strings")
    out: List[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{field} must contain only strings")
        if item.strip():
            out.append(item.strip())
    return out


def validate_data_policy(payload: Any) -> Dict[str, Any]:
    """Validate an incoming policy patch, returning only the keys it sets.

    Absent keys stay absent rather than being defaulted, so a PUT that changes
    the retention window cannot silently reset the redaction fields it did not
    mention. Unknown keys raise instead of being dropped — silently ignoring a
    misspelled field makes the UI appear to save something it did not.
    """
    if not isinstance(payload, dict):
        raise ValueError("data policy must be an object")

    unknown = sorted(set(payload) - set(_WRITABLE_FIELDS))
    if unknown:
        raise ValueError(
            f"unknown data-policy field(s): {', '.join(unknown)}; "
            f"expected any of {', '.join(sorted(_WRITABLE_FIELDS))}")

    out: Dict[str, Any] = {}

    if "capture_level" in payload:
        raw = payload["capture_level"]
        level = str(raw).strip().lower() if isinstance(raw, str) else ""
        if level not in _LEVEL_INDEX:
            raise ValueError(
                f"capture_level must be one of {', '.join(CAPTURE_LEVELS)}")
        out["capture_level"] = level

    if "store_bodies" in payload:
        out["store_bodies"] = bool(payload["store_bodies"])

    for field in _LIST_FIELDS:
        if field in payload:
            out[field] = _validated_string_list(field, payload[field])

    if "redact_patterns" in payload:
        patterns = _validated_string_list("redact_patterns", payload["redact_patterns"])
        bad = [p for p in patterns if p.lower() not in AVAILABLE_PATTERNS]
        if bad:
            raise ValueError(
                f"unknown redact_patterns: {', '.join(bad)}; "
                f"expected any of {', '.join(AVAILABLE_PATTERNS)}")
        out["redact_patterns"] = [p.lower() for p in patterns]

    if "retention_days" in payload:
        raw = payload["retention_days"]
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError("retention_days must be a non-negative integer")
        if raw < 0:
            raise ValueError("retention_days must be a non-negative integer")
        out["retention_days"] = raw

    return out


def describe_for_project(project: Any, ceiling: Any = None) -> Dict[str, Any]:
    """The read model behind GET /api/projects/{id}/data-policy.

    Reports the stored blob AND the effective policy after the instance
    ceiling, plus whether the two differ. A screen showing `full` while
    MAX_CAPTURE_LEVEL caps at `standard` would be telling the operator they
    are recording video when they are not.
    """
    stored = getattr(project, "data_policy", None)
    effective = effective_data_policy(stored, ceiling)
    requested = normalize_capture_level(
        (stored or {}).get("capture_level") if isinstance(stored, dict) else None)
    ceiling_level = normalize_capture_level(ceiling)

    return {
        "stored": stored if isinstance(stored, dict) else None,
        "effective": effective,
        "instance_max_capture_level": ceiling_level,
        "requested_capture_level": requested,
        "clamped": effective["capture_level"] != requested,
        "available_capture_levels": list(CAPTURE_LEVELS),
        "available_patterns": list(AVAILABLE_PATTERNS),
        "default_patterns": list(DEFAULT_PATTERNS),
        "permits": {
            kind: artifact_allowed(kind, effective["capture_level"])
            for kind in ARTIFACT_KINDS
        },
    }

"""Shareable read-only report links (WP12a, decision R2).

A link is a random token. Only its SHA-256 is stored, so a database dump does
not hand out working links; the token is shown once, when the link is made.
Links expire (default 7 days, at most 90) and can be revoked.

What a link shows is DELIBERATELY REDUCED and built by whitelisting fields,
never by deleting them from a richer object, so a field added to a report
later stays private until someone decides otherwise. Public views carry test
names, statuses, counts, durations, pass rates and dates. Never: artifact
URLs (traces, videos, screenshots, HAR), error text or stack traces, failure
cluster titles (derived from error text), step selectors, request URLs or
headers, git details, or any email address.

The view is computed at view time from what the link's creator can STILL
see: a creator who lost access (or was deactivated) takes their links down
with them, and a workspace summary only ever covers projects where the
creator is an editor - the people who could share each project on its own.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional

KINDS = ("run_report", "project_summary", "workspace_summary")
DEFAULT_DAYS = 7
MAX_DAYS = 90
TOKEN_BYTES = 32


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def expiry(days: Optional[int], now: Optional[datetime] = None) -> datetime:
    """Expiry for a requested lifetime; refuses anything outside 1..90."""
    now = now or datetime.utcnow()
    if days is None:
        days = DEFAULT_DAYS
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_DAYS:
        raise ValueError(f"expires_in_days must be between 1 and {MAX_DAYS}")
    return now + timedelta(days=days)


def state(link: Any, now: Optional[datetime] = None) -> str:
    """'active' | 'revoked' | 'expired'."""
    now = now or datetime.utcnow()
    if link.revoked_at is not None:
        return "revoked"
    if link.expires_at <= now:
        return "expired"
    return "active"


def describe(link: Any, creator: Any = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Authenticated list view: no token, no hash."""
    return {
        "id": link.id, "kind": link.kind, "project_id": link.project_id,
        "workspace_id": link.workspace_id, "target_id": link.target_id,
        "window_days": link.window_days, "label": link.label,
        "created_at": link.created_at, "expires_at": link.expires_at,
        "revoked_at": link.revoked_at, "last_viewed_at": link.last_viewed_at,
        "view_count": link.view_count, "state": state(link, now),
        "created_by": ({"id": creator.id, "full_name": creator.full_name}
                       if creator is not None else None),
    }


# ---------------------------------------------------------------------------
# Public payloads: whitelists
# ---------------------------------------------------------------------------


def _iso(v: Any) -> Optional[str]:
    return v.isoformat() if isinstance(v, datetime) else (v if isinstance(v, str) else None)


def _status(v: Any) -> str:
    return str(v.value if hasattr(v, "value") else v)


def _num(v: Any) -> Any:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _stats(s: Dict[str, Any]) -> Dict[str, Any]:
    return {k: _num(s.get(k)) for k in ("runs", "passed", "failed", "pass_rate", "failures")}


def public_run_report(run: Any, results: Iterable[Any], project_name: Optional[str]) -> Dict[str, Any]:
    total = int(run.total_tests or 0)
    passed = int(run.passed_tests or 0)
    return {
        "kind": "run_report",
        "project_name": project_name,
        "run": {
            "id": run.id,
            "suite_name": run.suite_name,
            "status": _status(run.status),
            "browser": run.browser,
            "total_tests": total,
            "passed_tests": passed,
            "failed_tests": int(run.failed_tests or 0),
            "pass_rate": round(100.0 * passed / total, 1) if total else None,
            "duration_ms": _num(run.duration_ms),
            "created_at": _iso(run.created_at),
            "finalized_at": _iso(getattr(run, "finalized_at", None)),
        },
        "results": [{"test_name": r.test_name, "status": _status(r.status),
                     "duration_ms": _num(r.duration_ms)} for r in results],
    }


def public_summary(snap: Dict[str, Any]) -> Dict[str, Any]:
    """Executive summary (project or workspace scope) for the public view."""
    out: Dict[str, Any] = {
        "kind": "project_summary" if snap.get("scope") == "project" else "workspace_summary",
        "name": snap.get("project_name") if snap.get("scope") == "project" else snap.get("workspace_name"),
        "window_days": snap.get("window_days"),
        "generated_at": snap.get("generated_at"),
        "headline": snap.get("headline"),
        "current": _stats(snap.get("current") or {}),
        "previous": _stats(snap.get("previous") or {}),
        "trend": {k: _num((snap.get("trend") or {}).get(k)) for k in ("pass_rate", "runs", "failures")},
        "daily": [{"date": d.get("date"), "runs": _num(d.get("runs")),
                   "pass_rate": _num(d.get("pass_rate"))} for d in snap.get("daily") or []],
        "top_failing": [{"test_name": t.get("test_name"), "failures": _num(t.get("failures"))}
                        for t in snap.get("top_failing") or []],
        "open_clusters": _num(snap.get("open_clusters")),
        "flaky_count": _num(snap.get("flaky_count")),
        "incidents": {k: _num((snap.get("incidents") or {}).get(k))
                      for k in ("escaped", "open", "open_critical")},
    }
    rd = snap.get("readiness")
    # The verdict only: reasons can name gate details we have not vetted.
    out["readiness"] = {"verdict": rd.get("verdict")} if rd else None
    if snap.get("scope") == "workspace":
        out["verdicts"] = {k: _num(v) for k, v in (snap.get("verdicts") or {}).items()}
        out["projects"] = [{
            "name": p.get("project_name"), "pass_rate": _num(p.get("pass_rate")),
            "runs": _num(p.get("runs")), "failures": _num(p.get("failures")),
            "trend_pass_rate": _num(p.get("trend_pass_rate")),
            "verdict": p.get("verdict"), "flaky_count": _num(p.get("flaky_count")),
        } for p in snap.get("projects") or []]
    return out

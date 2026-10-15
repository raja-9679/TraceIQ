"""Access-control matrix: row/cell shapes, safety gates, verdicts, findings.

A matrix is a grid of targets × identities with an expected outcome per cell.
Targets are a test case (runs as a normal TestRun under the persona), a raw
HTTP request, or a page URL with an optional element selector. Identities are
saved Personas plus an optional "anonymous" column (no session at all).

The worker only *observes* (status, element presence, redacted evidence); the
verdict is computed here so there is one implementation of the rules and an
old worker image can't disagree with a newer backend about what "denied" means.

The rules that make this safe to point at a real application:

* **Opt-in and allowlisted.** Request/page targets must be on the project's
  security allowlist (`Project.security_settings.allowed_domains`, the same
  list active scans use) and security testing must be enabled for the project.
* **Mutations need attestation.** A non-GET/HEAD/OPTIONS request — including
  an ID-swap owner request — needs the caller's explicit `authorized: true`,
  the same attestation an active scan requires.
* **A deny passes only on an explicit denial**: a status in the deny set
  (401/403/404 by default, plus the cell's own expected status) or an element
  that is absent from a page that did load. A timeout, a network error, a
  refused target or a 5xx is `inconclusive` — never evidence of denial,
  because "the server fell over" is not "the server said no".
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

ANONYMOUS = "anonymous"
DEFAULT_DENY_STATUSES: Tuple[int, ...] = (401, 403, 404)
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
MAX_ROWS = 200
MAX_COLUMNS = 20
FINDING_SCAN_TYPE = "access_matrix"
FINDING_CATEGORY = "access_control"

_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------

class Extractor(BaseModel):
    """How to pull the owner's resource id out of the owner response."""
    source: Literal["json", "regex", "header"] = "json"
    # json: dot path (`data.items.0.id`); regex: first capture group (or the
    # whole match); header: header name (e.g. Location).
    expr: str = Field(min_length=1, max_length=500)


class SwapSpec(BaseModel):
    """ID-swap replay: fetch as the owner, extract an id, request it as everyone."""
    owner_persona_id: int
    method: str = "GET"
    url: str = Field(min_length=1, max_length=2000)
    body: Optional[Any] = None
    extractor: Extractor
    # Token in the row's URL/body replaced by the extracted id.
    placeholder: str = Field(default="{id}", min_length=1, max_length=40)

    @field_validator("method")
    @classmethod
    def _upper(cls, v: str) -> str:
        return (v or "GET").upper()


class CellSpec(BaseModel):
    expect: Literal["allow", "deny", "skip"] = "skip"
    # allow: the exact status required. deny: an extra status that also counts
    # as a denial (e.g. 302 to a login page).
    status: Optional[int] = Field(default=None, ge=100, le=599)


class MatrixRow(BaseModel):
    key: str
    label: str = Field(default="", max_length=200)
    kind: Literal["case", "request", "page"]
    test_case_id: Optional[int] = None
    method: str = "GET"
    url: Optional[str] = Field(default=None, max_length=2000)
    body: Optional[Any] = None
    headers: Optional[Dict[str, str]] = None
    selector: Optional[str] = Field(default=None, max_length=500)
    swap: Optional[SwapSpec] = None
    cells: Dict[str, CellSpec] = Field(default_factory=dict)

    @field_validator("key")
    @classmethod
    def _key(cls, v: str) -> str:
        if not _KEY_RE.match(v or ""):
            raise ValueError("row key must be 1-64 chars of letters, digits, '_' or '-'")
        return v

    @field_validator("method")
    @classmethod
    def _method(cls, v: str) -> str:
        v = (v or "GET").upper()
        if v not in {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError(f"unsupported method {v}")
        return v

    @model_validator(mode="after")
    def _shape(self) -> "MatrixRow":
        if self.kind == "case":
            if not self.test_case_id:
                raise ValueError(f"row {self.key}: a case row needs test_case_id")
            if self.swap:
                raise ValueError(f"row {self.key}: ID swap applies to request/page rows only")
        else:
            if not self.url:
                raise ValueError(f"row {self.key}: a {self.kind} row needs a url")
            if self.swap and self.swap.placeholder not in (self.url or ""):
                raise ValueError(
                    f"row {self.key}: the url must contain the swap placeholder "
                    f"{self.swap.placeholder}")
        if self.kind == "page" and self.method != "GET":
            raise ValueError(f"row {self.key}: page rows are always GET")
        return self


def column_keys(persona_ids: Iterable[int], include_anonymous: bool) -> List[str]:
    keys = [str(int(p)) for p in persona_ids]
    if include_anonymous:
        keys.append(ANONYMOUS)
    return keys


def parse_rows(rows: Any, columns: List[str]) -> List[MatrixRow]:
    """Validate a matrix's rows. Raises ValueError with a user-facing message."""
    if not isinstance(rows, list):
        raise ValueError("rows must be a list")
    if len(rows) > MAX_ROWS:
        raise ValueError(f"a matrix holds at most {MAX_ROWS} rows")
    parsed = [MatrixRow.model_validate(r) for r in rows]
    seen = set()
    valid_cols = set(columns)
    for r in parsed:
        if r.key in seen:
            raise ValueError(f"duplicate row key {r.key}")
        seen.add(r.key)
        unknown = [k for k in r.cells if k not in valid_cols]
        if unknown:
            raise ValueError(f"row {r.key}: cells for unknown columns {unknown}")
        if r.swap and str(r.swap.owner_persona_id) not in valid_cols:
            raise ValueError(f"row {r.key}: the swap owner must be one of the matrix's personas")
    return parsed


def deny_set(deny_statuses: Optional[Iterable[int]]) -> List[int]:
    vals = [int(s) for s in (deny_statuses or DEFAULT_DENY_STATUSES)]
    bad = [s for s in vals if not 300 <= s <= 599]
    if bad:
        # A 2xx in the deny set would turn "access granted" into a pass.
        raise ValueError(f"deny statuses must be 3xx-5xx, got {bad}")
    return sorted(set(vals))


def plan_cells(rows: List[MatrixRow], columns: List[str]) -> List[Tuple[MatrixRow, str, CellSpec]]:
    """Every (row, column) that executes — skip cells and absent cells don't."""
    out = []
    for r in rows:
        for col in columns:
            cell = r.cells.get(col)
            if cell and cell.expect != "skip":
                out.append((r, col, cell))
    return out


# ---------------------------------------------------------------------------
# Safety gates
# ---------------------------------------------------------------------------

def resolve_url(url: str, base_url: Optional[str]) -> str:
    url = (url or "").strip()
    if url.startswith("/"):
        if not base_url:
            raise ValueError(f"relative url {url} needs the matrix base_url")
        return base_url.rstrip("/") + url
    return url


def host_allowed(url: str, allowed_domains: Iterable[str]) -> bool:
    """Exact host or subdomain match — the same rule active scans use."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    for d in allowed_domains or []:
        d = (d or "").lower().strip()
        if d and (host == d or host.endswith("." + d)):
            return True
    return False


def is_mutating(method: Optional[str]) -> bool:
    return (method or "GET").upper() not in SAFE_METHODS


def safety_problems(rows: List[MatrixRow], base_url: Optional[str], *,
                    security_enabled: bool, allowed_domains: List[str],
                    authorized: bool, columns: Optional[List[str]] = None
                    ) -> List[Tuple[int, str]]:
    """(http_status, message) for every reason this matrix may not execute.

    Only rows with at least one executing cell are checked, so a row parked as
    all-skip doesn't block the rest of the matrix.
    """
    problems: List[Tuple[int, str]] = []
    live = rows if columns is None else [
        r for r in rows if any(c.expect != "skip" for k, c in r.cells.items() if k in columns)]
    network_rows = [r for r in live if r.kind != "case"]
    if network_rows and not security_enabled:
        problems.append((403, "Security testing is disabled for this project — enable it "
                              "and allowlist the target hosts in Security settings"))
        return problems
    for r in network_rows:
        urls = [(r.method, r.url)]
        if r.swap:
            urls.append((r.swap.method, r.swap.url))
        for method, raw in urls:
            try:
                url = resolve_url(raw, base_url)
            except ValueError as e:
                problems.append((400, f"row {r.key}: {e}"))
                continue
            if not url.lower().startswith(("http://", "https://")):
                problems.append((400, f"row {r.key}: {url} is not an http(s) url"))
            elif not host_allowed(url, allowed_domains):
                problems.append((403, f"row {r.key}: host of {url} is not on this "
                                      "project's security allowlist"))
            if is_mutating(method) and not authorized:
                problems.append((400, f"row {r.key}: {method} is a mutating request — "
                                      "attest authorization (authorized=true) to send it"))
    return problems


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------

_STATUS_IN_ERROR = re.compile(r"(?:status|got|received|returned)\D{0,12}([1-5]\d\d)\b", re.I)


def judge_case(expect: str, run_status: Optional[str], error: Optional[str],
               deny_statuses: Iterable[int]) -> Tuple[str, str]:
    """Verdict for a case row from its TestRun's final status.

    A failed journey is only proof of denial when the failure names an explicit
    denial status (an `expect status 200 but got 403` assertion). A failure
    for any other reason — a timeout, a changed selector — is inconclusive.
    """
    status = (run_status or "").lower()
    if status in ("", "pending", "running"):
        return "inconclusive", "the case did not finish"
    denied = {int(s) for s in deny_statuses}
    codes = [int(m) for m in _STATUS_IN_ERROR.findall(error or "")]
    explicit = next((c for c in codes if c in denied), None)
    if expect == "deny":
        if status == "passed":
            return "fail", "the journey completed — access was granted"
        if explicit is not None:
            return "pass", f"denied with {explicit}"
        return "inconclusive", ("the case failed without an explicit denial status; a "
                                "timeout or broken step is not proof of denial")
    if status == "passed":
        return "pass", "the journey completed"
    if status == "error":
        return "inconclusive", "the run errored before the case could finish"
    if explicit is not None:
        return "fail", f"denied with {explicit}"
    return "fail", "the journey failed under this identity"


def judge_observation(kind: str, expect: str, observed: Optional[Dict[str, Any]],
                      deny_statuses: Iterable[int], expected_status: Optional[int] = None,
                      has_selector: bool = False) -> Tuple[str, str]:
    """Verdict for a request/page cell from what the worker observed."""
    if not observed:
        return "inconclusive", "no result from a worker"
    kind_err = observed.get("error_kind")
    if kind_err:
        detail = observed.get("error") or kind_err
        return "inconclusive", f"{kind_err}: {detail}"[:300]
    status = observed.get("status")
    if not isinstance(status, int):
        return "inconclusive", "no HTTP status observed"
    denied = set(int(s) for s in deny_statuses)
    element = observed.get("element_present") if (kind == "page" and has_selector) else None
    ok = 200 <= status < 300

    if expect == "deny":
        if status in denied or (expected_status is not None and status == expected_status):
            return "pass", f"denied with {status}"
        if kind == "page" and has_selector:
            if element is False and ok:
                return "pass", "the protected element is absent"
            if element is True:
                return "fail", f"the protected element is visible (status {status}) — access granted"
            if element is None and ok:
                return "inconclusive", "element presence was not observed"
        if ok:
            return "fail", f"status {status} — access granted"
        return "inconclusive", f"status {status} is neither a success nor an explicit denial"

    # allow
    if expected_status is not None:
        if status == expected_status:
            return "pass", f"status {status} as expected"
        return "fail", f"expected {expected_status}, got {status}"
    if status in denied:
        return "fail", f"denied with {status}"
    if kind == "page" and has_selector:
        if element is True:
            return "pass", "the element is visible"
        if element is False:
            return "fail", "the element is missing"
        return "inconclusive", "element presence was not observed"
    if ok:
        return "pass", f"status {status}"
    if 300 <= status < 400:
        return "inconclusive", f"redirected ({status}) without an explicit allow or deny"
    return "fail", f"status {status}"


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------

def finding_fingerprint(matrix_id: int, row_key: str, persona_key: str) -> str:
    raw = f"{FINDING_CATEGORY}|matrix:{matrix_id}|row:{row_key}|persona:{persona_key}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def summarize(cells: Iterable[Any]) -> Dict[str, Any]:
    """Counts by verdict, plus the two failure kinds kept apart on purpose: a
    granted DENY is a security finding, a refused ALLOW is a regression."""
    counts = {"pass": 0, "fail": 0, "inconclusive": 0, "pending": 0}
    granted, regressions = 0, 0
    for c in cells:
        counts[c.verdict] = counts.get(c.verdict, 0) + 1
        if c.verdict == "fail" and c.expect == "deny":
            granted += 1
        elif c.verdict == "fail" and c.expect == "allow":
            regressions += 1
    return {**counts, "total": sum(counts.values()),
            "access_granted": granted, "allow_regressions": regressions}


def apply_findings(session, matrix, matrix_run, cells, persona_names: Dict[str, str]) -> Dict[str, int]:
    """Create/update/resolve `access_control` findings for a finished run (sync
    session). Same lifecycle as scan findings: identity is the fingerprint;
    a false_positive verdict sticks; a fixed cell resolves its finding; a
    resolved finding that regresses reopens. Inconclusive cells change nothing.
    """
    from datetime import datetime

    from sqlmodel import select

    from app.models import SecurityFinding

    rows = {r.get("key"): r for r in (matrix_run.snapshot or {}).get("rows", [])}
    fps = {finding_fingerprint(matrix.id, c.row_key, c.persona_key): c
           for c in cells if c.expect == "deny" and c.verdict in ("pass", "fail")}
    if not fps:
        return {"created": 0, "updated": 0, "resolved": 0}
    existing = {f.fingerprint: f for f in session.exec(select(SecurityFinding).where(
        SecurityFinding.project_id == matrix.project_id,
        SecurityFinding.scan_type == FINDING_SCAN_TYPE,
        SecurityFinding.fingerprint.in_(list(fps)))).all()}
    created = updated = resolved = 0
    now = datetime.utcnow()
    for fp, cell in fps.items():
        found = existing.get(fp)
        row = rows.get(cell.row_key, {})
        who = persona_names.get(cell.persona_key, cell.persona_key)
        if cell.verdict == "fail":
            obs = cell.observed or {}
            title = f"Access granted that should be denied: {row.get('label') or cell.row_key} as {who}"
            evidence = (f"observed status {obs.get('status')}"
                        + (f", element present={obs.get('element_present')}"
                           if obs.get("element_present") is not None else "")
                        + f"; {cell.reason or ''}")[:1000]
            desc = (f"Access matrix '{matrix.name}' (run #{matrix_run.id}) expects "
                    f"{who} to be denied {row.get('method', 'GET')} "
                    f"{obs.get('url') or row.get('url') or ''}, but access was granted.")
            if found:
                found.title, found.evidence, found.description = title[:300], evidence, desc
                found.target_url = obs.get("url") or found.target_url
                if found.status == "resolved":
                    found.status, found.resolved_at = "open", None
                session.add(found)
                updated += 1
            else:
                session.add(SecurityFinding(
                    project_id=matrix.project_id, scan_type=FINDING_SCAN_TYPE,
                    category=FINDING_CATEGORY, severity="high", title=title[:300],
                    description=desc, evidence=evidence, target_url=obs.get("url"),
                    fingerprint=fp, status="open"))
                created += 1
        elif found and found.status in ("open", "acknowledged"):
            found.status, found.resolved_at = "resolved", now
            session.add(found)
            resolved += 1
    return {"created": created, "updated": updated, "resolved": resolved}


# ---------------------------------------------------------------------------
# Persona validation (shared with POST /api/runs persona_id / persona_ids)
# ---------------------------------------------------------------------------

async def validate_personas(session, persona_ids: Iterable[int], workspace_id: Optional[int],
                            project_id: int, user_id: int, field: str = "persona_id") -> List[Any]:
    """Load and authorize personas for use in `project_id`.

    A persona carries live session cookies for some identity; one from another
    workspace (or another project) must never be usable here. A workspace-wide
    persona isn't covered by the project's access, so using one requires the
    same workspace-editor role that managing personas requires.
    """
    from fastapi import HTTPException

    from app.models import Persona

    out = []
    needs_ws_editor = False
    for pid in persona_ids:
        persona = await session.get(Persona, pid)
        if (not persona or persona.workspace_id != workspace_id
                or (persona.project_id and persona.project_id != project_id)):
            raise HTTPException(status_code=400,
                                detail=f"{field} does not belong to this project")
        needs_ws_editor = needs_ws_editor or persona.project_id is None
        out.append(persona)
    if needs_ws_editor:
        from app.api.personas import _ensure_editor
        await _ensure_editor(workspace_id, user_id, session)
    return out

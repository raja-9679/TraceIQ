"""Database-level tenant isolation: Postgres row-level security (WP15a, H1).

A SECOND wall behind the Python access checks, never instead of them. Every
endpoint still checks access in `access_service`; this exists for the query
that forgot its WHERE — the bug class `tests/integration/test_tenant_isolation_db.py`
can only sample.

How it works with ONE database user
-----------------------------------
Every deployment (compose, AIO, Helm) connects as the user that OWNS the
tables, and in the shipped compose files that user is the Postgres
superuser. Owners skip RLS unless FORCE is set, and superusers skip it even
then, so "turn on RLS" alone protects nothing there.

So the policies are applied to a separate NOLOGIN role, `<database>_rls`,
which the migration creates and grants to the app user. An enforcing request
transaction runs

    SELECT set_config('traceiq.rls_mode', 'enforce', true),
           set_config('traceiq.workspace_ids', '3,7', true),
           set_config('traceiq.project_ids', '', true),
           set_config('role', '<database>_rls', true)

i.e. `SET LOCAL ROLE` into a role that is neither owner nor superuser, so the
policies apply. Everything else — Celery, beat, migrations, scripts, pg_dump,
unauthenticated endpoints — never switches role and is exactly as before:
the owner is not subject to non-forced RLS, so its plans do not even carry
the policy quals. That is what makes `off` mean "unchanged".

Where the role cannot be created (no CREATEROLE), the fallback is FORCE ROW
LEVEL SECURITY on the planned tables, applied only while the mode is
`enforce` (`reconcile_sync`). The policies are additionally gated on the
`traceiq.rls_mode` GUC, so a forced owner session that never set it
(Celery, beat, migrations) still sees every row. If the app user is a
superuser or BYPASSRLS AND the role is missing, enforcement is impossible and
the settings API refuses to turn it on.

What this does and does not protect against
-------------------------------------------
It protects against an application query, ORM or raw, that reads or writes
another tenant's rows because its WHERE (or a join, or an id taken from the
request) was wrong. It does NOT protect against SQL injection: the request's
own connection can `RESET ROLE` / `set_config` its way out, exactly as it can
for any GUC- or role-based scheme in a single-login deployment. Nor does it
cover sessions that are not request sessions (Celery tasks, sessions an
endpoint opens itself with `async_session_factory()` / `get_session_context()`),
or tables not yet in RLS_POLICIES.

Modes (instance setting RLS_MODE)
---------------------------------
off      nothing is bound; zero queries added per request.
log      nothing is refused and the database is not involved during the
         request. Sampled requests (RLS_LOG_SAMPLE_PERCENT) remember the ids
         of covered rows they loaded or wrote as ORM objects; when the request
         ends, one query per touched table asks Postgres — with the same
         predicate the policy uses — which of them are outside the caller's
         scope, and counts those as violations. (A policy-side counter was
         rejected: the policy qual runs on every row a scan VISITS, not only
         rows the query returns, so hash joins and seq scans would report
         rows the application never saw.) Column-only selects and raw SQL are
         not seen by log mode; deletes are not either.
enforce  out-of-scope rows are invisible and cannot be written.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

from sqlalchemy import event, text

logger = logging.getLogger(__name__)

MODES = ("off", "log", "enforce")
POLICY_NAME = "traceiq_tenant"
MODE_GUC = "traceiq.rls_mode"
WS_GUC = "traceiq.workspace_ids"
PJ_GUC = "traceiq.project_ids"

# ---------------------------------------------------------------------------
# The plan. Predicates are templates over {WS} (workspace ids in scope) and
# {PJ} (project ids a project-scoped API key is narrowed to; empty = no
# narrowing). Outer columns are TABLE-qualified so the same text works inside
# CREATE POLICY and in the log-mode shadow query, and so a subquery's own
# columns cannot capture them.
#
# The frozen copy in alembic/versions/e012b3c4d601_*.py must match
# policy_sql() for every table it creates — tests/test_rls_plan.py compares
# them. A table added later needs its own migration (and an entry here).
# ---------------------------------------------------------------------------

def _in_project(expr: str) -> str:
    # Uncorrelated IN → one hashed subplan per query (the tenant's projects),
    # not a per-row lookup; the id arrays are InitPlans, parsed once.
    return (f"({expr}) IN (SELECT p.id FROM project p "
            "WHERE p.workspace_id = ANY ({WS}) "
            "AND (cardinality({PJ}) = 0 OR p.id = ANY ({PJ})))")


_SUITE_PROJECT = "(SELECT s.project_id FROM testsuite s WHERE s.id = {t}.test_suite_id)"

RLS_POLICIES: Dict[str, str] = {
    # Workspace-keyed: workspace_id directly. Cheapest — no join.
    "project": ("project.workspace_id = ANY ({WS}) "
                "AND (cardinality({PJ}) = 0 OR project.id = ANY ({PJ}))"),
    "persona": ("persona.workspace_id = ANY ({WS}) "
                "AND (cardinality({PJ}) = 0 OR persona.project_id IS NULL "
                "OR persona.project_id = ANY ({PJ}))"),
    # Project-keyed.
    "testsuite": _in_project("testsuite.project_id"),
    # Legacy rows may predate project_id on cases/runs; access_service falls
    # back to the suite's project, so the policy does too.
    "testcase": _in_project("COALESCE(testcase.project_id, "
                            + _SUITE_PROJECT.format(t="testcase") + ")"),
    "testrun": _in_project("COALESCE(testrun.project_id, "
                           + _SUITE_PROJECT.format(t="testrun") + ")"),
    # No project column: one primary-key lookup per row into testrun. Under
    # the role testrun's own policy applies to that lookup too.
    "testcaseresult": _in_project(
        "(SELECT COALESCE(r.project_id, (SELECT s.project_id FROM testsuite s "
        "WHERE s.id = r.test_suite_id)) FROM testrun r "
        "WHERE r.id = testcaseresult.test_run_id)"),
    "projectsecret": _in_project("projectsecret.project_id"),
    "securityfinding": _in_project(
        "COALESCE(securityfinding.project_id, "
        "(SELECT sc.project_id FROM securityscan sc WHERE sc.id = securityfinding.scan_id), "
        "(SELECT r.project_id FROM testrun r WHERE r.id = securityfinding.run_id))"),
    "authsession": _in_project("authsession.project_id"),
    # The artifact index: there is no artifact table (keys are derived from
    # run ids, and GET /artifacts checks the run, which testrun covers). These
    # are the tables that hold object-store keys.
    "mobileappbuild": _in_project("mobileappbuild.project_id"),
    "visualcomparison": _in_project("visualcomparison.project_id"),
    "visualbaseline": _in_project(
        "(SELECT COALESCE(c.project_id, (SELECT s.project_id FROM testsuite s "
        "WHERE s.id = c.test_suite_id)) FROM testcase c "
        "WHERE c.id = visualbaseline.test_case_id)"),
    # Added at integration (migration e142b3c4d615): tables WP15d / WP16b /
    # WP16c created alongside WP15a. All carry project_id. hararchive holds
    # object-store keys; projectapispecversion holds full API specs.
    "accessibilitycheck": _in_project("accessibilitycheck.project_id"),
    "accessibilityviolation": _in_project("accessibilityviolation.project_id"),
    "accessibilitywaiver": _in_project("accessibilitywaiver.project_id"),
    "hararchive": _in_project("hararchive.project_id"),
    "projectapispecversion": _in_project("projectapispecversion.project_id"),
    "caseownernotice": _in_project("caseownernotice.project_id"),
    "repairoutcome": _in_project("repairoutcome.project_id"),
}

# Tenant-linked tables that deliberately have NO policy, with the reason.
RLS_EXEMPT: Dict[str, str] = {
    "userworkspace": "membership is what the scope is computed FROM, before a scope exists",
    "userprojectaccess": "direct project grants are an input to the scope computation",
    "teamprojectaccess": "team project grants are an input to the scope computation",
    "userteam": "team membership is an input to the scope computation",
    "usertestcaseaccess": "case-level grants are an input to the scope computation",
    "apikey": "looked up by hash while resolving the principal, before any scope exists",
    "workspaceinvitation": "an invitee reads the invitation of a workspace they are not yet in",
    "teaminvitation": "an invitee reads the invitation of a team they are not yet in",
}

# Tenant-linked tables not covered YET. A backlog, frozen: tests/test_rls_plan.py
# holds the same set and fails if this grows. A NEW table goes in RLS_POLICIES
# (with a migration) or RLS_EXEMPT (with a reason) — never here.
RLS_DEFERRED = frozenset({
    "accessmatrix", "accessmatrixcell", "accessmatrixrun", "accessreviewcampaign",
    "accessreviewitem", "agentcontrol", "agentsession", "assistantconversation",
    "assistantmessage", "benchmarkgroup", "businessjourney", "caseproposal",
    "compliancecontrol", "controlevidence", "controlexception", "costbudgetalert",
    "deployment", "estate", "estatedomain", "estatedomainresult", "estatescan",
    "evidencepack", "externaltestreport", "failurecluster", "flagcatalogentry",
    "flakecasescoredaily", "flakedailysnapshot", "flakerecord", "gatepolicyversion",
    "incident", "incidentwebhook", "issueticket", "issuetrackerconfig",
    "journeyconfig", "journeyimport", "llmusageevent", "maintenancewindow",
    "monitorcheck", "monitoredpage", "pagebudgetsample", "pagechangeevent",
    "pagescript", "pagingchannel", "pagingevent", "policydecisionlog",
    "projectapispec", "projectcostbudget", "projectenvironment",
    "projectnotificationpref", "releasesignoff", "reportschedule", "requirement",
    "requirementlink", "securityscan", "selectorhealproposal", "sharelink",
    "sitecrawl", "sitecrawlissue", "sitecrawlrun", "statuspage", "team",
    "testcaserevision", "testschedule", "trustcenter", "trustdocument",
    "trustincidentpost", "usagerecord", "vendorhealthdaily", "visualreviewcomment",
    "webhookcapture", "webhookdelivery", "workspacesubscription", "workspacewebhook",
})


def _fill(table: str, ws: str, pj: str) -> str:
    return RLS_POLICIES[table].replace("{WS}", ws).replace("{PJ}", pj)


# `(SELECT f())::int[]` rather than `f()`: a scalar subquery becomes an
# InitPlan evaluated once per query instead of once per row, and the cast
# stops `= ANY ((SELECT ...))` being read as ANY(subquery).
_GUC_WS = f"(SELECT traceiq_rls_ids('{WS_GUC}'))::int[]"
_GUC_PJ = f"(SELECT traceiq_rls_ids('{PJ_GUC}'))::int[]"


def policy_sql(table: str) -> str:
    """The USING / WITH CHECK expression of the table's policy. Restricts only
    when the transaction says 'enforce'; any other session sees every row."""
    return (f"CASE (SELECT current_setting('{MODE_GUC}', true)) "
            f"WHEN 'enforce' THEN COALESCE(({_fill(table, _GUC_WS, _GUC_PJ)}), false) "
            "ELSE true END")


def shadow_predicate(table: str) -> str:
    """The same predicate with bind parameters, for log mode."""
    return _fill(table, "CAST(:ws AS integer[])", "CAST(:pj AS integer[])")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _setting(key: str, default: Any) -> Any:
    try:
        from app.services import instance_settings
        value = instance_settings.effective(key)
    except Exception:  # noqa: BLE001 — never let config resolution break auth
        return default
    return default if value is None else value


_warned_unknown: set = set()


def normalise_mode(raw: Any) -> str:
    mode = str(raw or "off").strip().lower()
    if mode not in MODES:
        # A typo must not become 'enforce' (it could refuse every request)
        # nor pass silently.
        if mode not in _warned_unknown:
            _warned_unknown.add(mode)
            logger.warning("RLS_MODE %r is not one of %s; treating it as 'off'", raw, MODES)
        return "off"
    return mode


def current_mode() -> str:
    return normalise_mode(_setting("RLS_MODE", "off"))


def sample_percent() -> int:
    try:
        return max(0, min(100, int(_setting("RLS_LOG_SAMPLE_PERCENT", 100))))
    except (TypeError, ValueError):
        return 100


# ---------------------------------------------------------------------------
# Mechanism: SET ROLE into <db>_rls, or FORCE RLS, or impossible.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Mechanism:
    kind: str                  # role | force | unavailable | not_installed
    role: Optional[str] = None
    detail: str = ""

    @property
    def effective(self) -> bool:
        return self.kind in ("role", "force")


_DETECT_SQL = text("""
SELECT traceiq_rls_role() AS role,
       r.oid IS NOT NULL AS role_exists,
       CASE WHEN r.oid IS NULL THEN false
            ELSE pg_has_role(current_user, r.oid, 'MEMBER') END AS member,
       (SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user) AS privileged
FROM (SELECT traceiq_rls_role() AS name) n
LEFT JOIN pg_roles r ON r.rolname = n.name
""")


def detect_sync(conn) -> Mechanism:
    if conn.dialect.name != "postgresql":
        return Mechanism("not_installed", detail="not a Postgres database")
    installed = conn.execute(text(
        "SELECT to_regprocedure('traceiq_rls_role()') IS NOT NULL")).scalar()
    if not installed:
        return Mechanism("not_installed", detail="migration e012b3c4d601 has not run")
    row = conn.execute(_DETECT_SQL).mappings().one()
    if row["role_exists"] and row["member"]:
        return Mechanism("role", role=row["role"])
    if not row["privileged"]:
        return Mechanism("force", role=row["role"], detail=(
            f"role {row['role']} is missing or not granted to the app user; "
            "using FORCE ROW LEVEL SECURITY instead"))
    return Mechanism("unavailable", role=row["role"], detail=(
        f"the app connects as a superuser/BYPASSRLS user, which RLS never applies to, "
        f"and role {row['role']} does not exist or is not granted to it. Create it "
        "(see docs/OPERATIONS.md → Tenant isolation) or connect as a plain user"))


_mech_lock = threading.Lock()
_mechanism: Optional[Mechanism] = None
_mechanism_at = 0.0
_MECH_TTL = 300.0


def _cached_mechanism() -> Optional[Mechanism]:
    with _mech_lock:
        if _mechanism is not None and time.monotonic() - _mechanism_at < _MECH_TTL:
            return _mechanism
    return None


def _remember(mech: Mechanism) -> Mechanism:
    global _mechanism, _mechanism_at
    with _mech_lock:
        _mechanism, _mechanism_at = mech, time.monotonic()
    return mech


async def mechanism(session) -> Mechanism:
    cached = _cached_mechanism()
    if cached is not None:
        return cached
    try:
        mech = await session.run_sync(lambda s: detect_sync(s.connection()))
    except Exception as exc:  # noqa: BLE001
        logger.warning("rls: cannot detect the enforcement mechanism (%s)", exc)
        return Mechanism("not_installed", detail=str(exc))
    return _remember(mech)


def reconcile_sync(conn, mode: str) -> Dict[str, Any]:
    """Bring table flags in line with the mode. Idempotent and lock-free when
    nothing changes. Only the FORCE fallback needs DDL at runtime: with the
    role mechanism the tables stay ENABLEd (set by the migration) and never
    FORCEd, which costs owner sessions nothing."""
    mech = _remember(detect_sync(conn))
    out: Dict[str, Any] = {"mechanism": mech.kind, "changed": []}
    if mech.kind == "not_installed":
        return out
    want_force = mech.kind == "force" and mode == "enforce"
    rows = conn.execute(text(
        "SELECT c.relname, c.relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = current_schema() AND c.relkind = 'r' "
        "AND c.relname = ANY(:t)"), {"t": list(RLS_POLICIES)}).fetchall()
    to_change = [name for name, forced in rows if bool(forced) != want_force]
    if to_change:
        conn.execute(text("SET LOCAL lock_timeout = '5s'"))
        verb = "FORCE" if want_force else "NO FORCE"
        for name in sorted(to_change):
            conn.execute(text(f'ALTER TABLE "{name}" {verb} ROW LEVEL SECURITY'))
        out["changed"] = sorted(to_change)
    if mech.kind == "role":
        # Tables created after the migration get privileges from the default
        # ACL it set; this repairs anything created another way.
        missing = conn.execute(text(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'p') "
            "AND NOT has_table_privilege(:r, c.oid, 'SELECT,INSERT,UPDATE,DELETE')"),
            {"r": mech.role}).scalar()
        if missing:
            role = mech.role.replace('"', '""')
            conn.execute(text(f'GRANT ALL ON ALL TABLES IN SCHEMA public TO "{role}"'))
            conn.execute(text(
                f'GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO "{role}"'))
            out["granted"] = int(missing)
    return out


async def reconcile(mode: Optional[str] = None) -> Dict[str, Any]:
    """Startup / settings-save entry point; its own connection and commit."""
    from app.core.database import engine
    mode = mode or current_mode()
    async with engine.begin() as conn:
        return await conn.run_sync(reconcile_sync, mode)


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

@dataclass
class Scope:
    mode: str                                  # log | enforce
    workspace_ids: Tuple[int, ...]
    project_ids: Tuple[int, ...] = ()          # non-empty = narrowed (project-scoped key)
    actor: str = ""
    path: str = ""
    role: Optional[str] = None                 # SET ROLE target (enforce, role mechanism)
    seen: Dict[str, set] = field(default_factory=dict)  # log mode: table -> ids


_INFO_KEY = "traceiq_rls_scope"
_SEEN_CAP = 1000

# Every workspace the user has ANY relationship with. Deliberately the tenant
# wall, not RBAC: a guest with one project grant in workspace B gets B in
# scope and Python still decides which of B's projects they may read. Must
# stay a superset of what access_service / rbac_service allow, or enforce mode
# refuses something the application permits.
_SCOPE_SQL = text("""
SELECT workspace_id FROM userworkspace WHERE user_id = :u
UNION SELECT t.workspace_id FROM userteam ut JOIN team t ON t.id = ut.team_id WHERE ut.user_id = :u
UNION SELECT w.id FROM workspace w JOIN usersystemrole r ON r.tenant_id = w.tenant_id
      WHERE r.user_id = :u
UNION SELECT w.id FROM workspace w JOIN tenant tn ON tn.id = w.tenant_id WHERE tn.owner_id = :u
UNION SELECT w.id FROM workspace w WHERE w.demo_owner_id = :u
UNION SELECT p.workspace_id FROM userprojectaccess a JOIN project p ON p.id = a.project_id
      WHERE a.user_id = :u
UNION SELECT p.workspace_id FROM teamprojectaccess a JOIN userteam ut ON ut.team_id = a.team_id
      JOIN project p ON p.id = a.project_id WHERE ut.user_id = :u
UNION SELECT p.workspace_id FROM usertestcaseaccess a JOIN testcase c ON c.id = a.test_case_id
      JOIN project p ON p.id = COALESCE(c.project_id,
           (SELECT s.project_id FROM testsuite s WHERE s.id = c.test_suite_id))
      WHERE a.user_id = :u
""")


async def compute_scope(session, principal, mode: str) -> Scope:
    user = principal.user
    key = getattr(principal, "api_key", None)
    if key is not None:
        # An API key is minted FOR a workspace (and optionally one project):
        # it never reaches past that, whatever else its creator can see.
        key_ws, key_pj = getattr(key, "workspace_id", None), getattr(key, "project_id", None)
        ws: Tuple[int, ...] = (int(key_ws),) if key_ws else ()
        pj: Tuple[int, ...] = (int(key_pj),) if key_pj else ()
        actor = f"api_key:{getattr(key, 'id', '?')}"
    else:
        # On the connection, not session.execute: no autoflush, and sqlmodel
        # warns on every session.execute call.
        conn = await session.connection()
        rows = (await conn.execute(_SCOPE_SQL, {"u": user.id})).scalars().all()
        ws = tuple(sorted({int(r) for r in rows if r is not None}))
        pj = ()
        actor = f"user:{user.id}"
    return Scope(mode=mode, workspace_ids=ws, project_ids=pj, actor=actor)


def _ids(values: Iterable[int]) -> str:
    return ",".join(str(int(v)) for v in values)


def _apply_sync(connection, scope: Optional[Scope]) -> None:
    if scope is None or scope.mode != "enforce":
        connection.execute(text(
            f"SELECT set_config('{MODE_GUC}', '', true), set_config('role', 'none', true)"))
        return
    params = {"ws": _ids(scope.workspace_ids), "pj": _ids(scope.project_ids)}
    sql = (f"SELECT set_config('{MODE_GUC}', 'enforce', true), "
           f"set_config('{WS_GUC}', :ws, true), set_config('{PJ_GUC}', :pj, true)")
    if scope.role:
        sql += ", set_config('role', :role, true)"
        params["role"] = scope.role
    connection.execute(text(sql), params)


def get_scope(session) -> Optional[Scope]:
    return session.info.get(_INFO_KEY)


async def _set_scope(session, scope: Optional[Scope], *, was_enforcing: bool = False) -> None:
    if scope is None:
        session.info.pop(_INFO_KEY, None)
    else:
        session.info[_INFO_KEY] = scope
    enforcing = scope is not None and scope.mode == "enforce"
    # Mid-transaction: apply now. Otherwise after_begin applies it to the
    # next transaction — calling session.connection() here would begin one.
    if (enforcing or was_enforcing) and session.in_transaction():
        conn = await session.connection()
        await conn.run_sync(_apply_sync, scope)


async def bind_principal(session, principal, request=None) -> Optional[Scope]:
    """Called by get_current_principal for every authenticated request."""
    mode = current_mode()
    if mode == "off":
        return None
    if mode == "log" and random.random() * 100 >= sample_percent():
        return None
    install()
    try:
        scope = await compute_scope(session, principal, mode)
    except Exception:
        if mode == "enforce":
            # Fail closed: an enforcing request with an unknown scope must
            # not run as the owner.
            logger.exception("rls: scope computation failed")
            from fastapi import HTTPException
            raise HTTPException(status_code=503, detail="Tenant scope unavailable")
        logger.warning("rls: scope computation failed in log mode", exc_info=True)
        return None
    if request is not None:
        scope.path = f"{request.method} {request.url.path}"
    if mode == "enforce":
        mech = await mechanism(session)
        if mech.kind == "role":
            scope.role = mech.role
        elif not mech.effective:
            _warn_ineffective(mech)
    await _set_scope(session, scope)
    return scope


_warned_ineffective = False


def _warn_ineffective(mech: Mechanism) -> None:
    global _warned_ineffective
    if not _warned_ineffective:
        _warned_ineffective = True
        logger.error("RLS_MODE=enforce but enforcement is not possible: %s (%s)",
                     mech.kind, mech.detail)


async def bypass(session, reason: str) -> None:
    """Drop the scope for the rest of this request. For paths that read across
    tenants on purpose (instance-admin operator endpoints)."""
    scope = get_scope(session)
    if scope is None:
        return
    logger.debug("rls: scope bypassed (%s) for %s", reason, scope.actor)
    await _set_scope(session, None, was_enforcing=scope.mode == "enforce")


async def extend(session, workspace_ids: Sequence[int]) -> None:
    """Widen the scope when the request itself just created access (a new
    workspace the caller now administers) and writes into it next."""
    scope = get_scope(session)
    if scope is None or scope.project_ids:
        return
    new = tuple(sorted(set(scope.workspace_ids) | {int(w) for w in workspace_ids}))
    if new != scope.workspace_ids:
        scope.workspace_ids = new
        await _set_scope(session, scope)


# ---------------------------------------------------------------------------
# SQLAlchemy listeners
# ---------------------------------------------------------------------------

_installed = False
_install_lock = threading.Lock()


def _after_begin(session, transaction, connection):
    scope = session.info.get(_INFO_KEY)
    if scope is not None and scope.mode == "enforce":
        _apply_sync(connection, scope)


def _note(session, table: str, ident: Any) -> None:
    scope = session.info.get(_INFO_KEY) if session is not None else None
    if scope is None or scope.mode != "log" or ident is None:
        return
    bucket = scope.seen.setdefault(table, set())
    if len(bucket) < _SEEN_CAP:
        bucket.add(ident)


def _make_load_listener(table: str):
    def on_load(target, context):
        _note(context.session, table, getattr(target, "id", None))
    return on_load


def _after_flush(session, flush_context):
    scope = session.info.get(_INFO_KEY)
    if scope is None or scope.mode != "log":
        return
    for obj in list(session.new) + list(session.dirty):
        table = getattr(getattr(obj, "__table__", None), "name", None)
        if table in RLS_POLICIES:
            _note(session, table, getattr(obj, "id", None))


def install() -> None:
    global _installed
    if _installed:
        return
    with _install_lock:
        if _installed:
            return
        from sqlalchemy.orm import Session
        from sqlmodel import SQLModel
        import app.models  # noqa: F401 — registers the mappers
        event.listen(Session, "after_begin", _after_begin)
        event.listen(Session, "after_flush", _after_flush)
        for mapper in SQLModel._sa_registry.mappers:
            table = getattr(mapper.local_table, "name", None)
            if table in RLS_POLICIES:
                event.listen(mapper.class_, "load", _make_load_listener(table))
        _installed = True


# ---------------------------------------------------------------------------
# Log mode: the end-of-request shadow check, and the counters
# ---------------------------------------------------------------------------

_counts: Counter = Counter()
_since = time.time()
_REDIS_KEY = "traceiq:rls:violations"
_REDIS_SINCE = "traceiq:rls:since"


def _shadow_sync(sync_session, scope: Scope) -> Dict[str, list]:
    conn = sync_session.connection()
    found: Dict[str, list] = {}
    for table, ids in scope.seen.items():
        if not ids:
            continue
        rows = conn.execute(text(
            f"SELECT {table}.id FROM {table} WHERE {table}.id = ANY(CAST(:ids AS integer[])) "
            f"AND NOT COALESCE(({shadow_predicate(table)}), false) LIMIT 100"),
            {"ids": sorted(ids), "ws": list(scope.workspace_ids),
             "pj": list(scope.project_ids)}).scalars().all()
        if rows:
            found[table] = list(rows)
    return found


async def finish(session) -> None:
    """get_session teardown. Never raises."""
    scope = session.info.pop(_INFO_KEY, None)
    if scope is None or scope.mode != "log" or not any(scope.seen.values()):
        return
    try:
        try:
            found = await session.run_sync(_shadow_sync, scope)
        except Exception:  # noqa: BLE001 — a failed transaction: retry on a clean one
            await session.rollback()
            found = await session.run_sync(_shadow_sync, scope)
    except Exception:  # noqa: BLE001
        logger.warning("rls: log-mode check failed", exc_info=True)
        return
    for table, ids in found.items():
        await record_violation(table, len(ids), scope, ids[:5])


async def record_violation(table: str, count: int, scope: Scope, sample_ids: list) -> None:
    _counts[table] += count
    logger.warning(
        "rls(log): %d %s row(s) outside the caller's tenant scope — enforce mode "
        "would hide or refuse them (actor=%s, request=%s, ids=%s)",
        count, table, scope.actor, scope.path or "?", sample_ids)
    try:
        import asyncio
        from app.core.redis import RedisClient
        r = RedisClient.get_instance()

        async def _push():
            await r.hincrby(_REDIS_KEY, table, count)
            await r.setnx(_REDIS_SINCE, str(int(time.time())))
        await asyncio.wait_for(_push(), timeout=0.5)
    except Exception:  # noqa: BLE001 — the process counter is the fallback
        pass


async def violation_counts() -> Dict[str, Any]:
    try:
        import asyncio
        from app.core.redis import RedisClient
        r = RedisClient.get_instance()
        by_table = await asyncio.wait_for(r.hgetall(_REDIS_KEY), timeout=0.5)
        since = await asyncio.wait_for(r.get(_REDIS_SINCE), timeout=0.5)
        by_table = {k: int(v) for k, v in (by_table or {}).items()}
        return {"source": "instance", "by_table": by_table,
                "total": sum(by_table.values()),
                "since": int(since) if since else None}
    except Exception:  # noqa: BLE001
        return {"source": "process", "by_table": dict(_counts),
                "total": sum(_counts.values()), "since": int(_since)}


async def reset_violation_counts() -> None:
    _counts.clear()
    try:
        from app.core.redis import RedisClient
        r = RedisClient.get_instance()
        await r.delete(_REDIS_KEY, _REDIS_SINCE)
    except Exception:  # noqa: BLE001
        pass


async def status(session) -> Dict[str, Any]:
    """Operator readout: is enforcement actually possible here, are the
    policies installed, and what has log mode seen."""
    mode = current_mode()
    out: Dict[str, Any] = {
        "mode": mode, "configured": str(_setting("RLS_MODE", "off") or "off"),
        "sample_percent": sample_percent(),
    }
    try:
        def _probe(s):
            conn = s.connection()
            mech = _remember(detect_sync(conn))
            rows = conn.execute(text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                "EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid "
                f"AND p.polname = '{POLICY_NAME}') "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = current_schema() AND c.relname = ANY(:t)"),
                {"t": list(RLS_POLICIES)}).fetchall()
            return mech, rows
        mech, rows = await session.run_sync(_probe)
        tables = {r[0]: {"enabled": bool(r[1]), "forced": bool(r[2]), "policy": bool(r[3])}
                  for r in rows}
    except Exception as exc:  # noqa: BLE001
        mech, tables = Mechanism("not_installed", detail=str(exc)), {}
    out["mechanism"] = mech.kind
    out["mechanism_detail"] = mech.detail
    out["role"] = mech.role
    out["tables"] = [{"table": t, **tables.get(t, {"enabled": False, "forced": False,
                                                   "policy": False})}
                     for t in sorted(RLS_POLICIES)]
    out["policies_installed"] = all(t["policy"] and t["enabled"] for t in out["tables"])
    out["enforcing"] = (mode == "enforce" and mech.effective and out["policies_installed"]
                        and (mech.kind != "force"
                             or all(t["forced"] for t in out["tables"])))
    out["violations"] = await violation_counts()
    return out

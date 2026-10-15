# Targeted Runs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `POST /api/runs` run a chosen set of cases (`case_ids`), let impact analysis be scoped to a suite, and give the GitHub Action a `targeted` mode with strict full-suite fallback — while the gate, PR comment and outputs always say when cases were skipped.

**Architecture:** Two nullable columns on `TestRun` record how a run's cases were selected. `create_run` validates `case_ids` against the suite's subtree and creates one run per selected case × browser × device using each case's own suite settings. Impact analysis optionally restricts to a suite subtree. The quality gate gains an informational `coverage` check. The Action's selection decision moves into a pure, tested module.

**Tech Stack:** FastAPI + SQLModel + Alembic (backend), pytest/pytest-asyncio (unit via `./run-tests.sh`, live Postgres via `./run-tests-live.sh`), Node 20 GitHub Action bundled with `@vercel/ncc`, `node:test`, FastMCP (Python) MCP server.

**Spec:** `docs/superpowers/specs/2026-10-14-targeted-runs-design.md`

## Global Constraints

- Work in the worktree `/home/raja/Work/repos/TraceIQ-targeted`, branch `feat/targeted-runs` (off `feature/enterprise-auth-ai`).
- Every commit subject ends with ` [skip ci]`. Never add a `Co-Authored-By` trailer. Never push `main`, tags, or open a PR.
- `POST /api/runs` response shape is unchanged: a list of `TestRunRead`.
- `suite_id` stays required on `POST /api/runs`.
- `case_ids`: max **500** after de-duplication; empty list rejected; combining with `case_id` or `tags` rejected — all **400**.
- `selection_mode` values are exactly `"full"`, `"targeted"`, `"case"`; NULL = created before this change.
- The gate's `coverage` check is **always `passed=True`** (informational).
- Action input `mode` defaults to `suite`; existing workflows must behave exactly as before.
- Strict fallback: full suite when impact analysis errors, `unmatched_files` is non-empty, `matched_cases` is empty, every matched case is quarantined, or run creation with `case_ids` fails.
- Backend unit tests: `cd backend && ./run-tests.sh <path> -q`. Live DB: `cd backend && ./run-tests-live.sh <path> -q`. Migrations: `./run-tests-live.sh --migrations`.
- New Alembic revision id: `a7b8c9d0e1f2`, `down_revision = 'f2a3b4c5d6e7'`.

## Review Focus

1. A `case_ids` entry from **another project** (or another suite in the same project) — must be refused with 400, never run under this suite's settings/permissions. → Task 3 test `test_rejects_cases_outside_the_suite`.
2. A selection whose cases are **all quarantined** — must 400, not create runs that the worker then errors with "No test cases found". → Task 3 test `test_all_quarantined_selection_is_refused`.
3. A changed file matched only by a case **outside** the scoped suite — must appear in `unmatched_files` so the Action falls back. → Task 4 test `test_file_matched_only_outside_suite_is_unmatched`.
4. A commit that has **both** a targeted and a full run — gate must say "full suite", not "targeted". → Task 5 test `test_full_run_wins_over_targeted`.
5. The Action on a **non-PR** event (push) with `mode: targeted` — must run the full suite with a clear reason, not crash or run nothing. → Task 6 test `fallback when not a pull request`.

---

### Task 1: Selection columns on TestRun

**Files:**
- Modify: `backend/app/models.py` (class `TestRunBase`, after `local_worker_id`, ~line 533)
- Create: `backend/app/alembic/versions/a7b8c9d0e1f2_testrun_selection.py`
- Test: `backend/tests/test_run_selection_fields.py`

**Interfaces:**
- Produces: `TestRun.selection_mode: Optional[str]`, `TestRun.selection_total: Optional[int]`, both also on `TestRunRead` (inherits `TestRunBase`).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_run_selection_fields.py
"""TestRun records how its cases were selected (targeted runs, 2026-10)."""
from app.models import TestRun, TestRunRead


def test_selection_fields_default_to_none():
    run = TestRun(test_suite_id=1)
    assert run.selection_mode is None
    assert run.selection_total is None


def test_selection_fields_are_exposed_on_read_model():
    read = TestRunRead(id=1, test_suite_id=1, selection_mode="targeted", selection_total=37)
    assert read.selection_mode == "targeted"
    assert read.selection_total == 37
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && ./run-tests.sh tests/test_run_selection_fields.py -q`
Expected: FAIL (`TestRun` has no attribute `selection_mode` / validation error).

- [ ] **Step 3: Add the fields**

In `backend/app/models.py`, inside `class TestRunBase`, directly after the `local_worker_id` field:

```python
    # How this run's cases were chosen (targeted runs, 2026-10):
    # "full" = whole suite, "targeted" = POST /runs case_ids, "case" = single
    # case_id; NULL = created before selection tracking. selection_total is
    # how many cases the requested suite held, so the gate can say "N of M".
    selection_mode: Optional[str] = Field(default=None)
    selection_total: Optional[int] = Field(default=None)
```

- [ ] **Step 4: Add the migration**

```python
# backend/app/alembic/versions/a7b8c9d0e1f2_testrun_selection.py
"""testrun selection_mode / selection_total (targeted runs)

Revision ID: a7b8c9d0e1f2
Revises: f2a3b4c5d6e7
Create Date: 2026-10-14 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('testrun', sa.Column('selection_mode', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('testrun', sa.Column('selection_total', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('testrun', 'selection_total')
    op.drop_column('testrun', 'selection_mode')
```

- [ ] **Step 5: Run tests**

Run: `cd backend && ./run-tests.sh tests/test_run_selection_fields.py tests/test_migration_chain.py tests/test_purge_plan.py -q`
Expected: PASS.
Run: `cd backend && ./run-tests-live.sh --migrations`
Expected: upgrade == models (`alembic check` clean), downgrade to base empty, bridge OK.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models.py backend/app/alembic/versions/a7b8c9d0e1f2_testrun_selection.py backend/tests/test_run_selection_fields.py
git commit -m "feat(runs): record how a run's cases were selected [skip ci]"
```

---

### Task 2: Suite-subtree helpers

**Files:**
- Modify: `backend/app/services/test_service.py` (add two static methods to `TestService`, next to `collect_cases_recursive`)
- Test: `backend/tests/integration/test_targeted_runs_db.py` (new; Tasks 3–4 add to it)

**Interfaces:**
- Produces:
  - `TestService.subtree_suite_ids(suite_id: int, session: AsyncSession) -> List[int]` — the suite and **all** descendants, any execution mode.
  - `TestService.subtree_cases(suite_id: int, session: AsyncSession) -> List[TestCase]` — every case in that subtree.

- [ ] **Step 1: Write the failing test (with the shared fixtures)**

```python
# backend/tests/integration/test_targeted_runs_db.py
"""Targeted runs against a real Postgres.

    ./run-tests-live.sh tests/integration/test_targeted_runs_db.py -q
"""
from __future__ import annotations

import os
from typing import NamedTuple

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.core.auth import AuthPrincipal                      # noqa: E402
from app.core.config import db_url_for, settings            # noqa: E402
from app.models import (                                     # noqa: E402
    ExecutionMode, FlakeRecord, Project, Tenant, TestCase, TestRun, TestSuite,
    User, UserSystemRole, UserWorkspace, Workspace,
)
from app.services.rbac_service import rbac_service           # noqa: E402
from app.services.test_service import test_service           # noqa: E402


@pytest_asyncio.fixture(loop_scope="function")
async def session():
    engine = create_async_engine(db_url_for(settings.DATABASE_URL, sync=False), future=True)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        await s.exec(text("TRUNCATE users, tenant, workspace CASCADE"))
        await s.commit()
        from app.core.rbac_init import init_rbac
        await init_rbac(s)
        yield s
    await engine.dispose()


class Scene(NamedTuple):
    user: User
    project_id: int
    other_project_id: int
    root_id: int          # root suite (SEPARATE)
    child_id: int         # child of root (CONTINUOUS)
    sibling_id: int       # another top-level suite in the same project
    root_cases: list      # 2 cases in root
    child_cases: list     # 2 cases in child
    sibling_case: int
    foreign_case: int     # case in another project


@pytest_asyncio.fixture(loop_scope="function")
async def scene(session) -> Scene:
    user = User(email="dev@corp.example.com", full_name="Dev", hashed_password="x", is_verified=True)
    session.add(user)
    await session.flush()
    tenant = Tenant(name="Corp", owner_id=user.id)
    session.add(tenant)
    await session.flush()
    ws = Workspace(name="Corp ws", tenant_id=tenant.id)
    session.add(ws)
    await session.flush()
    ws_admin = await rbac_service.get_role_by_name(session, "Workspace Admin")
    tenant_admin = await rbac_service.get_role_by_name(session, "Tenant Admin")
    session.add_all([
        UserSystemRole(user_id=user.id, role_id=tenant_admin.id, tenant_id=tenant.id),
        UserWorkspace(user_id=user.id, workspace_id=ws.id, role="admin", role_id=ws_admin.id),
    ])
    project = Project(name="Shop", workspace_id=ws.id)
    other = Project(name="Other", workspace_id=ws.id)
    session.add_all([project, other])
    await session.flush()
    root = TestSuite(name="Checkout", project_id=project.id, execution_mode=ExecutionMode.SEPARATE,
                     settings={"headers": {"X-Suite": "root"}})
    sibling = TestSuite(name="Search", project_id=project.id)
    foreign_suite = TestSuite(name="Elsewhere", project_id=other.id)
    session.add_all([root, sibling, foreign_suite])
    await session.flush()
    child = TestSuite(name="Payments", project_id=project.id, parent_id=root.id,
                      execution_mode=ExecutionMode.CONTINUOUS,
                      settings={"headers": {"X-Suite": "child"}})
    session.add(child)
    await session.flush()

    def case(name, suite, proj):
        return TestCase(name=name, test_suite_id=suite.id, project_id=proj.id, steps=[])
    rc = [case("cart", root, project), case("address", root, project)]
    cc = [case("card", child, project), case("upi", child, project)]
    sc = case("query", sibling, project)
    fc = case("other", foreign_suite, other)
    session.add_all(rc + cc + [sc, fc])
    await session.commit()
    return Scene(user=user, project_id=project.id, other_project_id=other.id,
                 root_id=root.id, child_id=child.id, sibling_id=sibling.id,
                 root_cases=[c.id for c in rc], child_cases=[c.id for c in cc],
                 sibling_case=sc.id, foreign_case=fc.id)


async def test_subtree_covers_every_descendant_in_any_mode(session, scene):
    ids = await test_service.subtree_suite_ids(scene.root_id, session)
    assert set(ids) == {scene.root_id, scene.child_id}
    cases = await test_service.subtree_cases(scene.root_id, session)
    assert {c.id for c in cases} == set(scene.root_cases + scene.child_cases)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && ./run-tests-live.sh tests/integration/test_targeted_runs_db.py -q`
Expected: FAIL — `TestService` has no attribute `subtree_suite_ids`.

- [ ] **Step 3: Implement the helpers**

In `backend/app/services/test_service.py`, inside `class TestService`, directly before `collect_cases_recursive`:

```python
    @staticmethod
    async def subtree_suite_ids(suite_id: int, session: AsyncSession) -> List[int]:
        """The suite and every descendant module, whatever its execution mode.

        Unlike collect_cases_recursive (which stops at SEPARATE sub-modules
        because they run as their own units), this is the full scope a
        targeted run may select from."""
        ids: List[int] = [suite_id]
        frontier = [suite_id]
        while frontier:
            res = await session.exec(select(TestSuite.id).where(TestSuite.parent_id.in_(frontier)))
            frontier = [sid for sid in res.all() if sid not in ids]
            ids.extend(frontier)
        return ids

    @staticmethod
    async def subtree_cases(suite_id: int, session: AsyncSession) -> List[TestCase]:
        ids = await TestService.subtree_suite_ids(suite_id, session)
        res = await session.exec(select(TestCase).where(TestCase.test_suite_id.in_(ids)))
        return list(res.all())
```

- [ ] **Step 4: Run tests**

Run: `cd backend && ./run-tests-live.sh tests/integration/test_targeted_runs_db.py -q`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/test_service.py backend/tests/integration/test_targeted_runs_db.py
git commit -m "feat(runs): suite subtree helpers for targeted selection [skip ci]"
```

---

### Task 3: `POST /api/runs` accepts `case_ids` and stamps selection

**Files:**
- Modify: `backend/app/api/endpoints/test_runs.py` (`RunCreateContext`; `create_run`)
- Test: `backend/tests/integration/test_targeted_runs_db.py` (append)

**Interfaces:**
- Consumes: `TestService.subtree_cases`, `TestRun.selection_mode/selection_total` (Tasks 1–2).
- Produces: `RunCreateContext.case_ids: Optional[List[int]]`; runs stamped `selection_mode` ∈ {`full`,`targeted`,`case`} and `selection_total`.

- [ ] **Step 1: Write the failing tests** (append to `test_targeted_runs_db.py`)

```python
from app.api.endpoints import test_runs            # noqa: E402


@pytest.fixture(autouse=True)
def no_queue(monkeypatch):
    """Runs are created and committed; nothing is sent to Celery."""
    from app import worker
    monkeypatch.setattr(worker.run_test_suite, "delay", lambda *a, **kw: None)


async def _create(session, scene, *, case_ids=None, case_id=None, tags=None, browser=None):
    return await test_runs.create_run(
        suite_id=scene.root_id, case_id=case_id, browser=browser, device=None, tags=tags,
        context=test_runs.RunCreateContext(case_ids=case_ids, git_commit="abc123"),
        session=session, principal=AuthPrincipal(user=scene.user, api_key=None))


async def test_targeted_runs_only_the_selected_cases(session, scene):
    picked = [scene.root_cases[0], scene.child_cases[1]]
    runs = await _create(session, scene, case_ids=picked, browser=["chromium"])
    assert sorted(r.test_case_id for r in runs) == sorted(picked)
    assert {r.selection_mode for r in runs} == {"targeted"}
    assert {r.selection_total for r in runs} == {4}   # root + child cases


async def test_each_case_uses_its_own_suite_settings(session, scene):
    runs = await _create(session, scene, case_ids=[scene.child_cases[0]], browser=["chromium"])
    assert runs[0].test_suite_id == scene.child_id
    assert runs[0].request_headers.get("X-Suite") == "child"
    assert "Payments" in (runs[0].suite_name or "")


async def test_duplicates_are_collapsed(session, scene):
    c = scene.root_cases[0]
    runs = await _create(session, scene, case_ids=[c, c, c], browser=["chromium"])
    assert [r.test_case_id for r in runs] == [c]


async def test_rejects_cases_outside_the_suite(session, scene):
    for bad in (scene.sibling_case, scene.foreign_case, 999999):
        with pytest.raises(HTTPException) as exc:
            await _create(session, scene, case_ids=[scene.root_cases[0], bad])
        assert exc.value.status_code == 400
        assert str(bad) in str(exc.value.detail)
    await session.rollback()
    assert (await session.exec(select(TestRun))).all() == []


async def test_rejects_empty_oversized_and_ambiguous_selections(session, scene):
    for kwargs in ({"case_ids": []},
                   {"case_ids": list(range(1, 502))},
                   {"case_ids": [scene.root_cases[0]], "case_id": scene.root_cases[1]},
                   {"case_ids": [scene.root_cases[0]], "tags": ["smoke"]}):
        with pytest.raises(HTTPException) as exc:
            await _create(session, scene, **kwargs)
        assert exc.value.status_code == 400


async def test_quarantined_cases_are_left_out(session, scene):
    q, ok = scene.root_cases
    session.add(FlakeRecord(test_case_id=q, is_quarantined=True))
    await session.commit()
    runs = await _create(session, scene, case_ids=[q, ok], browser=["chromium"])
    assert [r.test_case_id for r in runs] == [ok]


async def test_all_quarantined_selection_is_refused(session, scene):
    q = scene.root_cases[0]
    session.add(FlakeRecord(test_case_id=q, is_quarantined=True))
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await _create(session, scene, case_ids=[q])
    assert exc.value.status_code == 400
    assert "quarantined" in exc.value.detail


async def test_full_and_single_case_runs_are_stamped(session, scene):
    full = await _create(session, scene, browser=["chromium"])
    assert full and {r.selection_mode for r in full} == {"full"}
    assert {r.selection_total for r in full} == {4}
    single = await _create(session, scene, case_id=scene.root_cases[0], browser=["chromium"])
    assert {r.selection_mode for r in single} == {"case"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && ./run-tests-live.sh tests/integration/test_targeted_runs_db.py -q`
Expected: FAIL — `RunCreateContext` has no field `case_ids` (ValidationError) and runs lack stamps.

- [ ] **Step 3: Add `case_ids` to the request model**

In `RunCreateContext` (after `persona_id`):

```python
    # Targeted run: execute exactly these cases (each with its own suite's
    # inherited settings). Must all lie in suite_id's subtree. Not combinable
    # with case_id or tags. See docs/superpowers/specs/2026-10-14-targeted-runs-design.md.
    case_ids: Optional[List[int]] = None
```

and a module constant above `create_run`:

```python
MAX_TARGETED_CASES = 500
```

- [ ] **Step 4: Validate the selection**

In `create_run`, immediately after the quota check block (before `effective_settings = ...`), insert:

```python
    # ── Targeted selection (case_ids) ───────────────────────────────────
    targeted_cases: List[TestCase] = []
    scope_cases = await test_service.subtree_cases(suite_id, session)
    selection_total = len(scope_cases)
    requested = context.case_ids if context else None
    if requested is not None:
        if case_id is not None or tags:
            raise HTTPException(status_code=400,
                                detail="case_ids cannot be combined with case_id or tags")
        wanted = list(dict.fromkeys(requested))   # de-duplicate, keep order
        if not wanted:
            raise HTTPException(status_code=400, detail="case_ids is empty")
        if len(wanted) > MAX_TARGETED_CASES:
            raise HTTPException(status_code=400,
                                detail=f"case_ids exceeds {MAX_TARGETED_CASES} cases")
        by_id = {c.id: c for c in scope_cases}
        outside = [cid for cid in wanted if cid not in by_id]
        if outside:
            raise HTTPException(status_code=400,
                                detail=f"case_ids not in suite {suite_id}: {outside}")
        from app.models import FlakeRecord
        quarantined = set((await session.exec(
            select(FlakeRecord.test_case_id).where(
                FlakeRecord.test_case_id.in_(wanted),
                FlakeRecord.is_quarantined == True))).all())  # noqa: E712
        targeted_cases = [by_id[cid] for cid in wanted if cid not in quarantined]
        if not targeted_cases:
            raise HTTPException(status_code=400,
                                detail="all selected cases are quarantined")
```

Note: the existing `try:` block that later re-raises `HTTPException` starts after this point, so these 400s propagate unchanged.

- [ ] **Step 5: Create targeted runs and stamp every path**

(a) In the SEPARATE branch of `process_suite` and the CONTINUOUS branch, and in the single-case branch, add two keyword arguments to each `TestRun(...)` constructor, right after `user_id=current_user.id,`:

```python
                                selection_mode="full",
                                selection_total=selection_total,
```

— use `"full"` in both `process_suite` branches and `"case"` in the `if case_id:` branch.

(b) Replace the dispatch decision

```python
        if case_id:
```

with

```python
        if targeted_cases:
            for case in targeted_cases:
                case_settings = await test_service.get_effective_settings(case.test_suite_id, session)
                case_path = await test_service.get_suite_path(case.test_suite_id, session)
                case_browsers, case_devices = _resolve_matrix(case, case_settings)
                for target_browser in case_browsers:
                    for target_device in case_devices:
                        run = TestRun(
                            status=TestStatus.PENDING,
                            test_suite_id=case.test_suite_id,
                            test_case_id=case.id,
                            project_id=suite.project_id,
                            suite_name=case_path,
                            test_case_name=case.name,
                            request_headers=case_settings.get("headers", {}),
                            request_params=case_settings.get("params", {}),
                            allowed_domains=case_settings.get("allowed_domains", []),
                            domain_settings=case_settings.get("domain_settings", {}),
                            browser=target_browser,
                            device=target_device,
                            user_id=current_user.id,
                            selection_mode="targeted",
                            selection_total=selection_total,
                            **run_defaults,
                        )
                        session.add(run)
                        await session.flush()
                        created_runs.append(run)
        elif case_id:
```

- [ ] **Step 6: Run tests**

Run: `cd backend && ./run-tests-live.sh tests/integration/test_targeted_runs_db.py -q`
Expected: PASS (9 tests).
Run: `cd backend && ./run-tests.sh -q -p no:warnings --ignore=tests/e2e`
Expected: all pass (the persona `create_run` unit test in `test_fix_first.py` must still pass — it monkeypatches `get_effective_settings`; if it now fails because `subtree_cases` hits its fake session, add to that test: `monkeypatch.setattr(test_runs.test_service, "subtree_cases", lambda *a, **kw: _async([]))` with `async def _async(v): return v` — do not weaken its assertion).

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/endpoints/test_runs.py backend/tests/integration/test_targeted_runs_db.py backend/tests/test_fix_first.py
git commit -m "feat(runs): POST /runs accepts case_ids for targeted runs [skip ci]"
```

---

### Task 4: Impact analysis scoped to a suite

**Files:**
- Modify: `backend/app/models.py` (`ImpactAnalysisRequest`, ~line 1717)
- Modify: `backend/app/api/agent_ownership.py` (`impact_analysis`)
- Test: `backend/tests/integration/test_targeted_runs_db.py` (append)

**Interfaces:**
- Consumes: `TestService.subtree_suite_ids` (Task 2).
- Produces: `ImpactAnalysisRequest.suite_id: Optional[int]`.

- [ ] **Step 1: Write the failing tests** (append)

```python
from app.api import agent_ownership                 # noqa: E402
from app.models import ImpactAnalysisRequest        # noqa: E402


async def _map(session, case_id, paths):
    case = await session.get(TestCase, case_id)
    case.code_paths = paths
    session.add(case)
    await session.commit()


async def _impact(session, scene, files, suite_id=None):
    return await agent_ownership.impact_analysis(
        ImpactAnalysisRequest(project_id=scene.project_id, changed_files=files, suite_id=suite_id),
        principal=AuthPrincipal(user=scene.user, api_key=None), session=session)


async def test_suite_scope_limits_matches(session, scene):
    await _map(session, scene.root_cases[0], ["src/cart/"])
    await _map(session, scene.sibling_case, ["src/cart/"])
    scoped = await _impact(session, scene, ["src/cart/a.ts"], suite_id=scene.root_id)
    assert [c.id for c in scoped.matched_cases] == [scene.root_cases[0]]
    unscoped = await _impact(session, scene, ["src/cart/a.ts"])
    assert {c.id for c in unscoped.matched_cases} == {scene.root_cases[0], scene.sibling_case}


async def test_file_matched_only_outside_suite_is_unmatched(session, scene):
    await _map(session, scene.sibling_case, ["src/search/"])
    r = await _impact(session, scene, ["src/search/q.ts"], suite_id=scene.root_id)
    assert r.matched_cases == []
    assert r.unmatched_files == ["src/search/q.ts"]


async def test_suite_scope_counts_unmapped_cases_in_scope_only(session, scene):
    r = await _impact(session, scene, ["x.ts"], suite_id=scene.root_id)
    assert r.cases_without_code_paths == 4
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && ./run-tests-live.sh tests/integration/test_targeted_runs_db.py -q`
Expected: FAIL — `ImpactAnalysisRequest` has no field `suite_id`.

- [ ] **Step 3: Implement**

`backend/app/models.py`, `ImpactAnalysisRequest`, after `include_no_code_paths`:

```python
    # Restrict matching to this suite and its descendants (targeted runs):
    # a changed file covered only by cases outside it counts as unmatched.
    suite_id: Optional[int] = None
```

`backend/app/api/agent_ownership.py`, in `impact_analysis`, replace

```python
    res = await session.exec(
        select(TestCase).where(TestCase.project_id == body.project_id)
    )
    cases: List[TestCase] = list(res.all())
```

with

```python
    stmt = select(TestCase).where(TestCase.project_id == body.project_id)
    if body.suite_id is not None:
        scope_suite = await session.get(TestSuite, body.suite_id)
        if not scope_suite or scope_suite.project_id != body.project_id:
            raise HTTPException(status_code=400, detail="suite_id is not in this project")
        from app.services.test_service import test_service
        scope_ids = await test_service.subtree_suite_ids(body.suite_id, session)
        stmt = stmt.where(TestCase.test_suite_id.in_(scope_ids))
    res = await session.exec(stmt)
    cases: List[TestCase] = list(res.all())
```

(Both counts — `cases_without_code_paths` and `unmatched_files` — are already derived from `cases`, so they become scope-local automatically.)

- [ ] **Step 4: Run tests**

Run: `cd backend && ./run-tests-live.sh tests/integration/test_targeted_runs_db.py -q`
Expected: PASS (12 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/app/api/agent_ownership.py backend/tests/integration/test_targeted_runs_db.py
git commit -m "feat(impact): impact analysis can be scoped to a suite [skip ci]"
```

---

### Task 5: Informational `coverage` gate check

**Files:**
- Modify: `backend/app/api/endpoints/quality.py` (new function `coverage_check`; call it in `evaluate_gate_for_runs`)
- Test: `backend/tests/test_gate_coverage.py`

**Interfaces:**
- Consumes: `TestRun.selection_mode/selection_total`, `test_case_id`.
- Produces: `coverage_check(finished_runs: list) -> QualityGateCheck` with `name="coverage"`, `passed=True`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_gate_coverage.py
"""The gate says how much of the suite a commit's runs covered — and never fails on it."""
from types import SimpleNamespace as R

from app.api.endpoints.quality import coverage_check


def run(case, mode, total=37):
    return R(test_case_id=case, selection_mode=mode, selection_total=total)


def test_targeted_only_reports_n_of_m():
    c = coverage_check([run(1, "targeted"), run(2, "targeted"), run(2, "targeted")])
    assert c.name == "coverage" and c.passed is True
    assert c.actual == "targeted — 2 of 37 cases ran"


def test_full_run_wins_over_targeted():
    c = coverage_check([run(1, "targeted"), run(None, "full")])
    assert c.actual == "full suite"


def test_runs_without_selection_data_are_unknown():
    c = coverage_check([run(1, None, None)])
    assert c.passed is True
    assert c.actual.startswith("unknown")


def test_single_case_runs_count_as_partial():
    c = coverage_check([run(5, "case")])
    assert c.actual == "targeted — 1 of 37 cases ran"


def test_no_runs_is_unknown_not_a_failure():
    assert coverage_check([]).passed is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && ./run-tests.sh tests/test_gate_coverage.py -q`
Expected: FAIL — cannot import `coverage_check`.

- [ ] **Step 3: Implement**

In `backend/app/api/endpoints/quality.py`, above `evaluate_gate_for_runs`:

```python
def coverage_check(finished_runs: list) -> QualityGateCheck:
    """How much of the suite the evaluated runs covered. Informational only —
    it never fails the gate (targeted runs are allowed to pass, labelled
    partial); it exists so nobody reads a 4-case pass as a full pass."""
    modes = {getattr(r, "selection_mode", None) for r in finished_runs}
    if "full" in modes:
        actual = "full suite"
    elif finished_runs and modes <= {"targeted", "case"}:
        ran = {r.test_case_id for r in finished_runs if r.test_case_id is not None}
        total = max((r.selection_total or 0) for r in finished_runs)
        actual = f"targeted — {len(ran)} of {total} cases ran"
    else:
        actual = "unknown (runs predate selection tracking)"
    return QualityGateCheck(name="coverage", passed=True, actual=actual,
                            threshold="informational",
                            detail=None if actual == "full suite" else "partial coverage — not every case ran")
```

and in `evaluate_gate_for_runs`, right after the `pass_rate` check is appended:

```python
    checks.append(coverage_check(finished_runs))
```

- [ ] **Step 4: Run tests**

Run: `cd backend && ./run-tests.sh tests/test_gate_coverage.py -q && ./run-tests.sh -q -p no:warnings --ignore=tests/e2e`
Expected: PASS; full unit suite green (any existing test asserting the exact list of gate check names must be updated to include `coverage`, keeping its other assertions).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/endpoints/quality.py backend/tests/test_gate_coverage.py
git commit -m "feat(gate): informational coverage check labels partial runs [skip ci]"
```

---

### Task 6: GitHub Action `mode: targeted`

**Files:**
- Create: `integrations/github-action/src/selection.js`
- Create: `integrations/github-action/src/selection.test.js`
- Modify: `integrations/github-action/src/index.js` (`triggerRun`, `runImpactAnalysis`, `selectionSection`, `main`)
- Modify: `integrations/github-action/action.yml` (input `mode`; outputs), `integrations/github-action/package.json` (`test` script), `integrations/github-action/README.md`
- Rebuild: `integrations/github-action/dist/`

**Interfaces:**
- Consumes: `POST /api/runs` body `case_ids`; impact analysis body `suite_id` (Tasks 3–4).
- Produces: `decideSelection({mode, isPullRequest, impact, impactError}) -> {kind: 'suite'|'targeted'|'fallback', caseIds: number[], skippedQuarantined: object[], reason: string|null}`.

- [ ] **Step 1: Write the failing tests**

```js
// integrations/github-action/src/selection.test.js
const test = require('node:test');
const assert = require('node:assert/strict');
const { decideSelection } = require('./selection');

const c = (id, extra = {}) => ({ id, name: `case ${id}`, ...extra });

test('suite mode always runs the suite', () => {
    const d = decideSelection({ mode: 'suite', isPullRequest: true, impact: null, impactError: null });
    assert.equal(d.kind, 'suite');
});

test('targeted runs exactly the matched cases when every file is covered', () => {
    const d = decideSelection({ mode: 'targeted', isPullRequest: true, impactError: null,
        impact: { matched_cases: [c(1), c(2)], unmatched_files: [] } });
    assert.deepEqual(d, { kind: 'targeted', caseIds: [1, 2], skippedQuarantined: [], reason: null });
});

test('fallback when any changed file maps to no test', () => {
    const d = decideSelection({ mode: 'targeted', isPullRequest: true, impactError: null,
        impact: { matched_cases: [c(1)], unmatched_files: ['src/billing.ts'] } });
    assert.equal(d.kind, 'fallback');
    assert.match(d.reason, /src\/billing\.ts.*no mapped test/);
});

test('fallback when nothing matched', () => {
    const d = decideSelection({ mode: 'targeted', isPullRequest: true, impactError: null,
        impact: { matched_cases: [], unmatched_files: [] } });
    assert.equal(d.kind, 'fallback');
    assert.match(d.reason, /no test case matched/);
});

test('fallback when impact analysis failed', () => {
    const d = decideSelection({ mode: 'targeted', isPullRequest: true, impact: null,
        impactError: new Error('HTTP 502') });
    assert.equal(d.kind, 'fallback');
    assert.match(d.reason, /impact analysis failed.*502/);
});

test('fallback when not a pull request', () => {
    const d = decideSelection({ mode: 'targeted', isPullRequest: false, impact: null, impactError: null });
    assert.equal(d.kind, 'fallback');
    assert.match(d.reason, /not a pull request/);
});

test('quarantined matches are skipped and reported; all-quarantined falls back', () => {
    const q = c(3, { flake: { is_quarantined: true, flake_score: 0.6 } });
    const some = decideSelection({ mode: 'targeted', isPullRequest: true, impactError: null,
        impact: { matched_cases: [c(1), q], unmatched_files: [] } });
    assert.deepEqual(some.caseIds, [1]);
    assert.deepEqual(some.skippedQuarantined.map((x) => x.id), [3]);
    const all = decideSelection({ mode: 'targeted', isPullRequest: true, impactError: null,
        impact: { matched_cases: [q], unmatched_files: [] } });
    assert.equal(all.kind, 'fallback');
    assert.match(all.reason, /quarantined/);
});
```

and in `package.json` `scripts`: `"test": "node --test src/"`.

- [ ] **Step 2: Run to verify they fail**

Run: `cd integrations/github-action && npm test`
Expected: FAIL — `Cannot find module './selection'`.

- [ ] **Step 3: Implement `selection.js`**

```js
// integrations/github-action/src/selection.js
// Pure decision: which cases a TraceIQ PR run executes. Strict by design —
// any doubt about coverage runs the full suite, with the reason kept for the
// PR comment (docs/superpowers/specs/2026-10-14-targeted-runs-design.md).

function fallback(reason) {
    return { kind: 'fallback', caseIds: [], skippedQuarantined: [], reason };
}

function decideSelection({ mode, isPullRequest, impact, impactError }) {
    if (mode !== 'targeted') return { kind: 'suite', caseIds: [], skippedQuarantined: [], reason: null };
    if (!isPullRequest) return fallback('not a pull request — no diff to select from');
    if (impactError) return fallback(`impact analysis failed (${impactError.message || impactError})`);
    const unmatched = impact?.unmatched_files || [];
    if (unmatched.length) {
        const shown = unmatched.slice(0, 3).map((f) => `\`${f}\``).join(', ');
        const more = unmatched.length > 3 ? ` and ${unmatched.length - 3} more` : '';
        return fallback(`${shown}${more} ${unmatched.length === 1 ? 'has' : 'have'} no mapped test`);
    }
    const matched = impact?.matched_cases || [];
    if (!matched.length) return fallback('no test case matched the changed files');
    const skippedQuarantined = matched.filter((c) => c.flake && c.flake.is_quarantined);
    const runnable = matched.filter((c) => !(c.flake && c.flake.is_quarantined));
    if (!runnable.length) return fallback('every matched test case is quarantined');
    return { kind: 'targeted', caseIds: runnable.map((c) => c.id), skippedQuarantined, reason: null };
}

module.exports = { decideSelection };
```

- [ ] **Step 4: Run tests**

Run: `cd integrations/github-action && npm test`
Expected: PASS (7 tests).

- [ ] **Step 5: Wire it into `index.js`**

(a) `triggerRun` — add a `caseIds` parameter and send it in the body:

```js
async function triggerRun(baseUrl, headers, suiteId, caseId, browser, gitCtx, triggeredBy, agentId, caseIds) {
```

and inside `body`, after `agent_id: agentId,`:

```js
        ...(caseIds && caseIds.length ? { case_ids: caseIds } : {}),
```

(b) `runImpactAnalysis` — add `suiteId` and send it:

```js
async function runImpactAnalysis(baseUrl, headers, projectId, changedFiles, suiteId) {
    return await http('POST', `${baseUrl}/api/runs/impact-analysis`, headers, {
        project_id: projectId,
        changed_files: changedFiles,
        include_no_code_paths: false,
        ...(suiteId ? { suite_id: suiteId } : {}),
    });
}
```

and update the comment above it: the run executes only these cases in `mode: targeted`.

(c) At the top: `const { decideSelection } = require('./selection');`

(d) In `main()`, after the existing input reads add
`const mode = (core.getInput('mode') || 'suite').trim().toLowerCase();`
and, just before `let selection = …`, add `let impactError = null;`. In the
impact block change the call to
`await runImpactAnalysis(baseUrl, headers, projectId, changedFiles, mode === 'targeted' ? suiteId : undefined)`
and in its `catch (err)` add `impactError = err;` as the first line (leave the
rest of the catch as is). In `targeted` mode, the three early `selection.reason`
branches (no project-id / not a PR / no token) must also set
`impactError = new Error(selection.reason)` when `!!gitCtx.prNumber` — so a PR
without project-id or token falls back with that reason. After the impact
block and before triggering:

```js
        const decision = decideSelection({
            mode, isPullRequest: !!gitCtx.prNumber,
            impact: selection.mode === 'impact' ? { matched_cases: selection.matchedCases, unmatched_files: selection.unmatchedFiles } : null,
            impactError,
        });
        let created;
        if (decision.kind === 'targeted') {
            try {
                created = await triggerRun(baseUrl, headers, suiteId, undefined, browser, gitCtx, triggeredBy, agentId, decision.caseIds);
            } catch (err) {
                decision.kind = 'fallback';
                decision.reason = `targeted run was refused (${err.message})`;
                decision.caseIds = [];
            }
        }
        if (!created) {
            created = await triggerRun(baseUrl, headers, suiteId, caseId, browser, gitCtx, triggeredBy, agentId);
        }
        selection.decision = decision;
```

replacing the existing single `const created = await triggerRun(...)` line. `impactError` is the caught error from the impact block (`null` otherwise); in `suite` mode the impact block keeps its current behaviour.

(e) Outputs, next to the existing `core.setOutput` calls:

```js
        const totalCases = finalRuns.find((r) => r && r.selection_total)?.selection_total;
        const ranCases = new Set(finalRuns.filter((r) => r && r.test_case_id).map((r) => r.test_case_id)).size;
        core.setOutput('selection-mode', decision.kind);
        core.setOutput('cases-run', String(decision.kind === 'targeted' ? ranCases : (totalCases ?? '')));
        core.setOutput('cases-total', String(totalCases ?? ''));
        core.setOutput('fallback-reason', decision.reason || '');
```

(f) `selectionSection` — prepend a header line based on `selection.decision`:

```js
    const d = selection.decision;
    if (d && d.kind === 'targeted') {
        lines.push(`**Targeted run: ${d.caseIds.length} of ${selection.casesTotal ?? '?'} cases** — only the cases this diff touches ran.`, '');
        for (const q of d.skippedQuarantined) lines.push(`- skipped (quarantined): ${mdCell(q.name)}`);
        const review = (selection.matchedCases || []).filter((c) => c.suggested_action === 'review');
        for (const c of review) lines.push(`- ran, **may need editing**: ${mdCell(c.name)} — ${mdCell((c.reasons || []).join('; '))}`);
        if (d.skippedQuarantined.length || review.length) lines.push('');
    } else if (d && d.kind === 'fallback') {
        lines.push(`**Full suite** (fallback: ${mdCell(d.reason)})`, '');
    }
```

and set `selection.casesTotal = totalCases` before `buildComment` is called. Replace the old sentence "The full configured suite was executed; …" with it only when `!d || d.kind !== 'targeted'`.

- [ ] **Step 6: `action.yml` and README**

`action.yml` inputs (after `case-id`):

```yaml
  mode:
    description: >-
      'suite' (default) runs the whole suite. 'targeted' runs only the cases
      impact analysis maps to the PR's changed files, and falls back to the
      full suite if any changed file has no mapped test, nothing matched,
      every match is quarantined, or impact analysis fails. Needs project-id
      and a github-token.
    required: false
    default: 'suite'
```

outputs:

```yaml
  selection-mode:
    description: "suite | targeted | fallback"
  cases-run:
    description: "Number of cases executed"
  cases-total:
    description: "Number of cases in the suite"
  fallback-reason:
    description: "Why a targeted run fell back to the full suite (empty otherwise)"
```

README: add a "Targeted runs" section showing `mode: targeted` with `project-id`, the four fallback conditions, and that the gate's `coverage` check reads "targeted — N of M cases ran".

- [ ] **Step 7: Rebuild and test**

Run: `cd integrations/github-action && npm ci && npm test && npm run build`
Expected: tests pass; `dist/index.js` regenerated; `git diff --stat dist/` non-empty.

- [ ] **Step 8: Commit**

```bash
git add integrations/github-action/src integrations/github-action/action.yml integrations/github-action/package.json integrations/github-action/README.md integrations/github-action/dist
git commit -m "feat(action): targeted mode runs only impacted cases, strict fallback [skip ci]"
```

---

### Task 7: MCP `run_suite(case_ids)` and scoped `select_tests_for_diff`

**Files:**
- Modify: `integrations/mcp-server/src/traceiq_mcp/client.py` (`create_run`, `impact_analysis`)
- Modify: `integrations/mcp-server/src/traceiq_mcp/tools/runs.py` (`run_suite`)
- Modify: `integrations/mcp-server/src/traceiq_mcp/tools/impact.py` (`select_tests_for_diff`)
- Test: `integrations/mcp-server/tests/test_targeted.py` (create `tests/` if absent)

**Interfaces:**
- Consumes: Task 3/4 API fields.
- Produces: `run_suite(..., case_ids: Optional[List[int]] = None)`, `select_tests_for_diff(..., suite_id: Optional[int] = None)`.

- [ ] **Step 1: Write the failing test**

```python
# integrations/mcp-server/tests/test_targeted.py
import asyncio

from traceiq_mcp.client import TraceIQClient


def test_create_run_sends_case_ids_in_body(monkeypatch):
    sent = {}

    async def fake_request(self, method, path, **kw):
        sent.update(method=method, path=path, params=kw.get("params"), json=kw.get("json"))
        return []

    monkeypatch.setattr(TraceIQClient, "_request", fake_request)
    client = TraceIQClient(base_url="http://x", api_key="tiq_test")
    asyncio.run(client.create_run(suite_id=4, case_ids=[7, 8]))
    assert sent["params"] == {"suite_id": 4}
    assert sent["json"]["case_ids"] == [7, 8]


def test_impact_analysis_sends_suite_id(monkeypatch):
    sent = {}

    async def fake_request(self, method, path, **kw):
        sent.update(json=kw.get("json"))
        return {"matched_cases": [], "cases_without_code_paths": 0, "unmatched_files": []}

    monkeypatch.setattr(TraceIQClient, "_request", fake_request)
    client = TraceIQClient(base_url="http://x", api_key="tiq_test")
    asyncio.run(client.impact_analysis(1, ["a.ts"], False, suite_id=4))
    assert sent["json"]["suite_id"] == 4
```

(`TraceIQClient(base_url=, api_key=, agent_id=)` and `async def _request(self, method, path, **kw)` are the real signatures.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd integrations/mcp-server && python -m pytest tests/test_targeted.py -q`
Expected: FAIL — unexpected keyword `case_ids` / `suite_id`.

- [ ] **Step 3: Implement**

`client.create_run`: add parameter `case_ids: Optional[List[int]] = None` and add `"case_ids": case_ids,` to the `self._clean({...})` body dict.
`client.impact_analysis`: change to

```python
    async def impact_analysis(self, project_id: int, changed_files: List[str],
                              include_no_code_paths: bool = False,
                              suite_id: Optional[int] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"project_id": project_id, "changed_files": changed_files,
                                "include_no_code_paths": include_no_code_paths}
        if suite_id is not None:
            body["suite_id"] = suite_id
        return await self._request("POST", "/api/runs/impact-analysis", json=body)
```
`tools/runs.py:run_suite`: add `case_ids: Optional[List[int]] = None`, pass `case_ids=case_ids` to `create_run`, and extend the docstring: "case_ids runs exactly those cases (must be inside suite_id; not combinable with case_id or tags) — the natural follow-up to select_tests_for_diff(suite_id=…)."
`tools/impact.py:select_tests_for_diff`: add `suite_id: Optional[int] = None`, pass it through, docstring: "suite_id restricts matches to that suite; files matched only outside it are reported unmatched."

- [ ] **Step 4: Run tests**

Run: `cd integrations/mcp-server && python -m pytest tests/test_targeted.py -q && python -m traceiq_mcp.smoke_test`
Expected: PASS; smoke test reports the full inventory OK.

- [ ] **Step 5: Commit**

```bash
git add integrations/mcp-server/src integrations/mcp-server/tests/test_targeted.py
git commit -m "feat(mcp): run_suite(case_ids) and suite-scoped select_tests_for_diff [skip ci]"
```

---

### Task 8: Docs, full verification, push

**Files:**
- Modify: `CLAUDE.md` (AI-agent integration surface: one bullet), `backend/app/AGENT_GUIDE.md` (the "Test a dev server…" table: add a row)

- [ ] **Step 1: Docs**

`CLAUDE.md`, in "AI-agent integration surface", add:

```markdown
- **Targeted runs (2026-10)** — `POST /api/runs` body `case_ids` runs exactly
  those cases (each with its own suite's settings; must be inside `suite_id`;
  ≤500; not with `case_id`/`tags`; quarantined ones dropped, all-quarantined
  → 400). Runs carry `selection_mode` (`full|targeted|case`) and
  `selection_total`; the gate adds an always-passing `coverage` check
  ("targeted — N of M cases ran"). Impact analysis takes `suite_id`. The
  GitHub Action's `mode: targeted` falls back to the full suite on any
  unmapped file, no match, all-quarantined or an impact error.
```

`AGENT_GUIDE.md` table row:

```markdown
| Run only what a diff touches | `select_tests_for_diff(suite_id=…)` → `run_suite(suite_id=…, case_ids=[…])` |
```

- [ ] **Step 2: Full verification**

Run:
```bash
cd backend && ./run-tests.sh -q -p no:warnings --ignore=tests/e2e
cd backend && ./run-tests-live.sh tests/integration -q
cd backend && ./run-tests-live.sh --migrations
cd integrations/github-action && npm test
cd integrations/mcp-server && python -m traceiq_mcp.smoke_test
```
Expected: all green (record counts in the commit message).

- [ ] **Step 3: Commit and push the branch**

```bash
git add CLAUDE.md backend/app/AGENT_GUIDE.md
git commit -m "docs: targeted runs [skip ci]"
git push origin feat/targeted-runs
```

- [ ] **Step 4: Hand back for the end-to-end check**

Report to the user: branch ready; the end-to-end check (rebuild backend image, migrate the local DB, targeted run of 2 cases, gate reads "targeted — 2 of N") happens after they approve merging into `feature/enterprise-auth-ai`, because it migrates their local database.

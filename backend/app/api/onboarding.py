"""First-run onboarding — a sample project in one click, and a checklist.

POST /api/onboarding/sample-project {workspace_id}
    Creates a "Sample Project" with a "Getting Started" suite containing
    three runnable demo cases against stable public targets (example.com,
    httpbin.org) — a UI journey, an API check, and an accessibility scan —
    so a new user can hit Run Now and see real results (screenshots,
    assertions, artifacts) within a minute of signing up.

Idempotent: if the workspace already has a "Sample Project" the caller can
access, it is returned with created=false instead of duplicating.

GET /api/projects/{id}/getting-started
    The "Get started" card on Overview: which first steps this project has
    behind it (a test exists, it has run, a run passed, it runs by itself,
    someone else was invited). Every answer is read from the data that step
    creates, so nothing is stored and a step cannot be "done" without having
    happened. The card disappears once all are done.
"""
import uuid
from typing import Any, Dict, List

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.reliability_reports import require_project
from app.core.auth import AuthPrincipal, get_current_principal, get_current_user
from app.core.database import get_session
from app.models import (
    ExecutionMode, Project, TestCase, TestRun, TestSchedule, TestStatus, TestSuite, User,
    UserWorkspace, WorkspaceInvitation,
)
from app.services import run_scope
from app.services.case_revisions import record_revision
from app.services.workspace_service import workspace_service

router = APIRouter()

SAMPLE_PROJECT_NAME = "Sample Project"
SAMPLE_SUITE_NAME = "Getting Started"


def _step(step_type: str, **kwargs: Any) -> Dict[str, Any]:
    return {"id": str(uuid.uuid4()), "type": step_type, **kwargs}


def _sample_cases() -> List[Dict[str, Any]]:
    """Three demo cases showcasing different pillars, against public targets
    that are stable enough to pass for years."""
    return [
        {
            "name": "UI — homepage loads and shows content",
            "tags": ["sample", "ui"],
            "steps": [
                _step("goto", value="https://example.com",
                      params={"wait_until": "domcontentloaded"}),
                _step("expect-title", value="Example Domain"),
                _step("expect-visible", selector="h1",
                      intent="the page's main heading"),
                _step("expect-text", selector="h1", value="Example Domain"),
                _step("screenshot", value="homepage"),
            ],
        },
        {
            "name": "API — JSON endpoint responds with the expected shape",
            "tags": ["sample", "api"],
            "steps": [
                _step("http-request", value="https://httpbin.org/json", params={
                    "method": "GET",
                    "assertions": [
                        {"type": "status", "value": "200"},
                        {"type": "json-path", "path": "slideshow.title", "operator": "exists"},
                    ],
                }),
            ],
        },
        {
            "name": "Accessibility — no serious WCAG violations",
            "tags": ["sample", "a11y"],
            "steps": [
                _step("goto", value="https://example.com",
                      params={"wait_until": "domcontentloaded"}),
                _step("check-accessibility", params={"impact": "serious"}),
            ],
        },
    ]


class SampleProjectRequest(BaseModel):
    workspace_id: int


@router.post("/onboarding/sample-project")
async def create_sample_project(
    body: SampleProjectRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    # Creates a project: a workspace-wide write (key: own workspace, no
    # project scope, editor or above).
    from app.services.access_service import access_service
    if not await access_service.workspace_membership(current_user.id, body.workspace_id,
                                                     session, write=True):
        raise HTTPException(status_code=403, detail="Not a member of this workspace")

    # Idempotency: one sample project per workspace is plenty.
    existing = await session.exec(
        select(Project).where(
            Project.workspace_id == body.workspace_id,
            Project.name == SAMPLE_PROJECT_NAME,
        ))
    project = existing.first()
    if project:
        suite = (await session.exec(
            select(TestSuite).where(
                TestSuite.project_id == project.id,
                TestSuite.name == SAMPLE_SUITE_NAME,
            ))).first()
        return {
            "created": False,
            "project_id": project.id,
            "suite_id": suite.id if suite else None,
            "detail": "Sample project already exists in this workspace",
        }

    project = await workspace_service.create_project(
        name=SAMPLE_PROJECT_NAME,
        workspace_id=body.workspace_id,
        creator_id=current_user.id,
        session=session,
        description="Seeded by onboarding — three runnable demo tests against public targets. Safe to delete.",
        commit=False,
    )

    suite = TestSuite(
        name=SAMPLE_SUITE_NAME,
        description="Run me! A UI journey, an API check, and an accessibility scan.",
        execution_mode=ExecutionMode.SEPARATE,
        project_id=project.id,
        created_by_id=current_user.id,
        updated_by_id=current_user.id,
    )
    session.add(suite)
    await session.flush()

    case_ids = []
    for spec in _sample_cases():
        case = TestCase(
            name=spec["name"],
            steps=spec["steps"],
            tags=spec["tags"],
            test_suite_id=suite.id,
            project_id=project.id,
            created_by_id=current_user.id,
            updated_by_id=current_user.id,
        )
        session.add(case)
        await session.flush()
        await record_revision(session, case, "create", user_id=current_user.id)
        case_ids.append(case.id)

    await session.commit()
    return {
        "created": True,
        "project_id": project.id,
        "suite_id": suite.id,
        "case_ids": case_ids,
    }


# ---------------------------------------------------------------------------
# Getting-started checklist
# ---------------------------------------------------------------------------

class GettingStartedStep(BaseModel):
    key: str            # test | run | passed | schedule | team
    done: bool


class GettingStarted(BaseModel):
    project_id: int
    steps: List[GettingStartedStep]
    done: int
    total: int
    complete: bool
    #: A suite to open the test editor in, when the project has one.
    first_suite_id: Optional[int] = None
    #: The newest run, to link "see it pass" to.
    latest_run_id: Optional[int] = None


@router.get("/projects/{project_id}/getting-started", response_model=GettingStarted)
async def getting_started(
    project_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> GettingStarted:
    # Viewer access; project-scoped API keys are held to their project.
    project = await require_project(project_id, principal, session)

    async def first(stmt) -> Optional[int]:
        return (await session.exec(stmt.limit(1))).first()

    first_suite = await first(
        select(TestSuite.id).where(TestSuite.project_id == project.id).order_by(TestSuite.id))
    has_test = await first(
        select(TestCase.id).join(TestSuite, TestCase.test_suite_id == TestSuite.id)
        .where(TestSuite.project_id == project.id)) is not None
    # Validation runs try out a proposed repair; they are not "the user ran a
    # test" (run_scope.counted is the project-wide rule for that).
    runs = select(TestRun.id).where(TestRun.project_id == project.id, run_scope.counted(TestRun))
    latest_run = await first(runs.order_by(TestRun.id.desc()))
    has_pass = await first(runs.where(TestRun.status == TestStatus.PASSED)) is not None
    has_schedule = await first(
        select(TestSchedule.id).where(TestSchedule.project_id == project.id)) is not None

    steps = [
        GettingStartedStep(key="test", done=has_test),
        GettingStartedStep(key="run", done=latest_run is not None),
        GettingStartedStep(key="passed", done=has_pass),
        GettingStartedStep(key="schedule", done=has_schedule),
    ]

    # "Invite a teammate" is offered only to people who can invite, and never
    # tells anyone else how many people the workspace has.
    from app.services.rbac_service import rbac_service
    if principal.api_key is None and await rbac_service.has_permission(
            session, principal.user.id, "workspace:manage_users", workspace_id=project.workspace_id):
        others = await first(
            select(UserWorkspace.user_id).where(
                UserWorkspace.workspace_id == project.workspace_id,
                UserWorkspace.user_id != principal.user.id))
        invited = await first(
            select(WorkspaceInvitation.id).where(WorkspaceInvitation.workspace_id == project.workspace_id))
        steps.append(GettingStartedStep(key="team", done=others is not None or invited is not None))

    done = sum(1 for s in steps if s.done)
    return GettingStarted(
        project_id=project.id, steps=steps, done=done, total=len(steps),
        complete=done == len(steps), first_suite_id=first_suite, latest_run_id=latest_run)

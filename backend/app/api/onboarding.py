"""First-run onboarding — seed a working sample project in one click.

POST /api/onboarding/sample-project {workspace_id}
    Creates a "Sample Project" with a "Getting Started" suite containing
    three runnable demo cases against stable public targets (example.com,
    httpbin.org) — a UI journey, an API check, and an accessibility scan —
    so a new user can hit Run Now and see real results (screenshots,
    assertions, artifacts) within a minute of signing up.

Idempotent: if the workspace already has a "Sample Project" the caller can
access, it is returned with created=false instead of duplicating.
"""
import uuid
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_session
from app.models import ExecutionMode, Project, TestCase, TestSuite, User, UserWorkspace
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
    membership = await session.exec(
        select(UserWorkspace).where(
            UserWorkspace.workspace_id == body.workspace_id,
            UserWorkspace.user_id == current_user.id,
        ))
    if not membership.first():
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

from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel
from sqlmodel import SQLModel, Field, Relationship
from sqlalchemy import Column, JSON, String, Enum as SAEnum
from enum import Enum

# Import settings models
from app.settings_models import UserSettings


class Tenant(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    owner_id: int = Field(foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationships
    workspaces: List["Workspace"] = Relationship(back_populates="tenant")


class Permission(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    scope: str  # 'global', 'org', 'project' (or resource type)
    action: str  # 'create', 'read', 'update', 'delete', 'execute', 'manage_users'
    # 'test_case', 'test_run', 'project', 'team' (optional refinement)
    resource: str
    description: Optional[str] = None


class RolePermission(SQLModel, table=True):
    role_id: int = Field(foreign_key="role.id", primary_key=True)
    permission_id: int = Field(foreign_key="permission.id", primary_key=True)


class Role(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    tenant_id: Optional[int] = Field(
        default=None, foreign_key="tenant.id")  # Null means system role
    description: Optional[str] = None

    # Relationships
    permissions: List["Permission"] = Relationship(link_model=RolePermission)


class UserSystemRole(SQLModel, table=True):
    user_id: int = Field(foreign_key="users.id", primary_key=True)
    role_id: int = Field(foreign_key="role.id", primary_key=True)
    tenant_id: Optional[int] = Field(
        default=None, foreign_key="tenant.id", primary_key=True)  # Scoped to tenant


class UserWorkspace(SQLModel, table=True):
    user_id: int = Field(foreign_key="users.id", primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", primary_key=True)
    role: str = Field(default="member")  # DEPRECATED: use role_id
    role_id: Optional[int] = Field(default=None, foreign_key="role.id")


class UserTeam(SQLModel, table=True):
    user_id: int = Field(foreign_key="users.id", primary_key=True)
    team_id: int = Field(foreign_key="team.id", primary_key=True)


class TeamProjectAccess(SQLModel, table=True):
    team_id: int = Field(foreign_key="team.id", primary_key=True)
    project_id: int = Field(foreign_key="project.id", primary_key=True)
    access_level: str = Field(default="editor")  # DEPRECATED: use role_id
    role_id: Optional[int] = Field(
        default=None, foreign_key="role.id")  # New RBAC


class UserProjectAccess(SQLModel, table=True):
    user_id: int = Field(foreign_key="users.id", primary_key=True)
    project_id: int = Field(foreign_key="project.id", primary_key=True)
    access_level: str = Field(default="editor")  # DEPRECATED: use role_id
    role_id: Optional[int] = Field(
        default=None, foreign_key="role.id")  # New RBAC


class UserTestCaseAccess(SQLModel, table=True):
    user_id: int = Field(foreign_key="users.id", primary_key=True)
    test_case_id: int = Field(foreign_key="testcase.id", primary_key=True)
    access_level: str = Field(default="editor")  # editor, viewer


class Workspace(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    description: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationships
    users: List["User"] = Relationship(
        back_populates="workspaces", link_model=UserWorkspace)
    projects: List["Project"] = Relationship(back_populates="workspace")
    teams: List["Team"] = Relationship(back_populates="workspace")
    tenant_id: Optional[int] = Field(default=None, foreign_key="tenant.id")
    tenant: Optional[Tenant] = Relationship(back_populates="workspaces")


class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    description: Optional[str] = None
    workspace_id: int = Field(foreign_key="workspace.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationships
    workspace: Workspace = Relationship(back_populates="projects")
    test_suites: List["TestSuite"] = Relationship(back_populates="project")
    teams: List["Team"] = Relationship(
        back_populates="projects", link_model=TeamProjectAccess)
    users: List["User"] = Relationship(
        back_populates="projects", link_model=UserProjectAccess)


class ProjectRead(SQLModel):
    id: int
    name: str
    description: Optional[str] = None
    workspace_id: int
    created_at: datetime


class ProjectReadWithAccess(ProjectRead):
    access_level: Optional[str] = None  # admin, editor, viewer


class Team(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    workspace_id: int = Field(foreign_key="workspace.id")

    # Relationships
    workspace: Workspace = Relationship(back_populates="teams")
    users: List["User"] = Relationship(
        back_populates="teams", link_model=UserTeam)
    projects: List["Project"] = Relationship(
        back_populates="teams", link_model=TeamProjectAccess)


class TestStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


class ExecutionMode(str, Enum):
    CONTINUOUS = "continuous"
    SEPARATE = "separate"
    PARALLEL = "parallel"


class RunTrigger(str, Enum):
    HUMAN = "human"
    SCHEDULE = "schedule"
    API_AGENT = "api_agent"
    CI = "ci"
    WEBHOOK = "webhook"


class TestSuiteBase(SQLModel):
    name: str
    description: Optional[str] = None
    execution_mode: ExecutionMode = Field(default=ExecutionMode.CONTINUOUS, sa_column=Column(
        SAEnum(ExecutionMode, name="executionmode", values_callable=lambda obj: [e.value for e in obj])))
    parent_id: Optional[int] = Field(default=None, foreign_key="testsuite.id")
    project_id: Optional[int] = Field(
        default=None, foreign_key="project.id")  # Added Project link
    settings: Optional[Dict[str, Any]] = Field(
        default={"headers": {}, "params": {}}, sa_column=Column(JSON))
    inherit_settings: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by_id: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_by_id: Optional[int] = Field(default=None, foreign_key="users.id")


class TestSuite(TestSuiteBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)

    test_cases: List["TestCase"] = Relationship(back_populates="test_suite")
    parent: Optional["TestSuite"] = Relationship(
        back_populates="sub_modules",
        sa_relationship_kwargs={"remote_side": "TestSuite.id"}
    )
    sub_modules: List["TestSuite"] = Relationship(back_populates="parent")
    project: Optional["Project"] = Relationship(
        back_populates="test_suites")  # Relationship

    created_by: Optional["User"] = Relationship(
        sa_relationship_kwargs={"foreign_keys": "TestSuite.created_by_id"})
    updated_by: Optional["User"] = Relationship(
        sa_relationship_kwargs={"foreign_keys": "TestSuite.updated_by_id"})


class TestSuiteParent(TestSuiteBase):
    id: int


class TestSuiteRead(TestSuiteBase):
    id: int
    parent: Optional[TestSuiteParent] = None
    total_test_cases: int = 0
    total_sub_modules: int = 0


class TestSuiteReadWithChildren(TestSuiteRead):
    test_cases: List["TestCaseRead"] = []
    sub_modules: List["TestSuiteRead"] = []
    effective_settings: Dict[str, Any] = {"headers": {}, "params": {}}


class TestSuiteUpdate(SQLModel):
    name: Optional[str] = None
    description: Optional[str] = None
    execution_mode: Optional[ExecutionMode] = None
    project_id: Optional[int] = None
    settings: Optional[Dict[str, Any]] = None
    inherit_settings: Optional[bool] = None


class TestStep(BaseModel):
    id: str
    type: str  # 'goto', 'click', 'fill', 'check', 'expect', 'http-request', 'feed-check'
    selector: Optional[str] = None
    value: Optional[str] = None
    params: Optional[dict] = None


class TestCaseBase(SQLModel):
    name: str
    steps: List[TestStep] = Field(
        default=[], sa_column=Column(JSON))  # List of TestSteps
    test_suite_id: Optional[int] = Field(
        default=None, foreign_key="testsuite.id")
    # Redundant but helpful for direct access
    project_id: Optional[int] = Field(default=None, foreign_key="project.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by_id: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_by_id: Optional[int] = Field(default=None, foreign_key="users.id")


class TestCase(TestCaseBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    test_suite: Optional[TestSuite] = Relationship(back_populates="test_cases")
    project: Optional["Project"] = Relationship()
    created_by: Optional["User"] = Relationship(
        sa_relationship_kwargs={"foreign_keys": "TestCase.created_by_id"})
    updated_by: Optional["User"] = Relationship(
        sa_relationship_kwargs={"foreign_keys": "TestCase.updated_by_id"})

    # Granular Access
    user_access: List["User"] = Relationship(
        back_populates="test_case_overrides", link_model=UserTestCaseAccess)


class TestCaseRead(TestCaseBase):
    id: int


class TestCaseUpdate(SQLModel):
    name: Optional[str] = None
    steps: Optional[List[TestStep]] = None
    test_suite_id: Optional[int] = None
    project_id: Optional[int] = None


class TestRunBase(SQLModel):
    test_suite_id: int = Field(foreign_key="testsuite.id")
    test_case_id: Optional[int] = Field(
        default=None, foreign_key="testcase.id")
    project_id: Optional[int] = Field(
        default=None, foreign_key="project.id")  # Link to project
    suite_name: Optional[str] = None
    test_case_name: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    status: TestStatus = Field(default=TestStatus.PENDING)
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = Field(default=0)
    duration_ms: Optional[float] = Field(default=None)
    error_message: Optional[str] = Field(default=None)
    trace_url: Optional[str] = Field(default=None)
    video_url: Optional[str] = Field(default=None)
    screenshots: Optional[List[str]] = Field(
        default=[], sa_column=Column(JSON))
    response_status: Optional[int] = Field(default=None)
    request_headers: Optional[dict] = Field(default={}, sa_column=Column(JSON))
    request_params: Optional[dict] = Field(default={}, sa_column=Column(JSON))
    response_headers: Optional[dict] = Field(
        default={}, sa_column=Column(JSON))
    allowed_domains: Optional[List[Any]] = Field(
        default=[], sa_column=Column(JSON))
    domain_settings: Optional[dict] = Field(default={}, sa_column=Column(JSON))
    network_events: Optional[List[dict]] = Field(
        default=[], sa_column=Column(JSON))
    execution_log: Optional[List[dict]] = Field(
        default=[], sa_column=Column(JSON))
    browser: str = Field(default="chromium")
    device: Optional[str] = Field(default=None)
    user_id: Optional[int] = Field(default=None, foreign_key="users.id")
    # AI-powered analysis of test failures (populated by controller)
    ai_analysis: Optional[dict] = Field(default=None, sa_column=Column(JSON))

    # Change-awareness: pin a run to the code change that triggered it.
    # Populated by AI agents (via API key) or CI integrations (GitHub Action).
    git_commit: Optional[str] = Field(default=None, index=True)
    git_branch: Optional[str] = Field(default=None)
    git_pr_url: Optional[str] = Field(default=None)
    git_repo: Optional[str] = Field(default=None)
    # Who/what initiated this run. `agent_id` is free-form so a coding agent
    # can identify itself (e.g. "claude-code", "cursor", custom CI bot).
    triggered_by: RunTrigger = Field(
        default=RunTrigger.HUMAN,
        sa_column=Column(SAEnum(RunTrigger, name="runtrigger",
                                values_callable=lambda obj: [e.value for e in obj])),
    )
    agent_id: Optional[str] = Field(default=None)
    # API key id if this run was triggered by a service account (FK soft;
    # column added separately to keep the legacy/agent path migration-friendly).
    api_key_id: Optional[int] = Field(default=None)


class UserRead(SQLModel):
    id: int
    email: str
    full_name: str


class UserReadDetailed(UserRead):
    role: Optional[str] = None
    last_login_at: Optional[datetime] = None
    is_active: bool = True
    status: str = "active"
    workspace: Optional[str] = None  # For Tenant Admin view


class TestRun(TestRunBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    results: List["TestCaseResult"] = Relationship(
        back_populates="test_run", sa_relationship_kwargs={"cascade": "all, delete-orphan"})
    user: Optional["User"] = Relationship(back_populates="test_runs")
    project: Optional["Project"] = Relationship()


class TestCaseResultRead(SQLModel):
    id: int
    test_name: str
    status: TestStatus
    duration_ms: float
    error_message: Optional[str] = None
    video_url: Optional[str] = None
    trace_url: Optional[str] = None
    screenshots: Optional[List[str]] = []
    response_status: Optional[int] = None
    response_headers: Optional[dict] = {}
    response_body: Optional[str] = None
    request_headers: Optional[dict] = {}
    request_body: Optional[str] = None
    request_url: Optional[str] = None
    request_method: Optional[str] = None
    request_params: Optional[dict] = {}


class TestRunRead(TestRunBase):
    id: int
    results: List[TestCaseResultRead] = []
    user: Optional[UserRead] = None


class TestCaseResult(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    test_run_id: int = Field(foreign_key="testrun.id")
    test_name: str
    status: TestStatus
    duration_ms: float
    error_message: Optional[str] = None
    trace_url: Optional[str] = None
    video_url: Optional[str] = None
    screenshots: Optional[List[str]] = Field(
        default=[], sa_column=Column(JSON))
    response_status: Optional[int] = Field(default=None)
    response_headers: Optional[dict] = Field(
        default={}, sa_column=Column(JSON))
    response_body: Optional[str] = Field(default=None)
    request_headers: Optional[dict] = Field(default={}, sa_column=Column(JSON))
    request_body: Optional[str] = Field(default=None)
    request_url: Optional[str] = Field(default=None)
    request_method: Optional[str] = Field(default=None)
    request_params: Optional[dict] = Field(default={}, sa_column=Column(JSON))
    ai_analysis: Optional[str] = None

    test_run: TestRun = Relationship(back_populates="results")


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    full_name: str
    hashed_password: str

    # Relationships
    settings: Optional["UserSettings"] = Relationship(
        back_populates="user", sa_relationship_kwargs={"uselist": False})
    test_runs: List["TestRun"] = Relationship(back_populates="user")
    workspaces: List[Workspace] = Relationship(
        back_populates="users", link_model=UserWorkspace)
    teams: List[Team] = Relationship(
        back_populates="users", link_model=UserTeam)
    projects: List[Project] = Relationship(
        back_populates="users", link_model=UserProjectAccess)
    test_case_overrides: List[TestCase] = Relationship(
        back_populates="user_access", link_model=UserTestCaseAccess)

    is_active: bool = True
    last_login_at: Optional[datetime] = Field(default=None)


class TestScheduleBase(SQLModel):
    name: str
    description: Optional[str] = None
    project_id: int = Field(foreign_key="project.id")
    test_suite_id: Optional[int] = Field(default=None, foreign_key="testsuite.id")
    test_case_id: Optional[int] = Field(default=None, foreign_key="testcase.id")
    browser: str = Field(default="chromium")
    device: Optional[str] = Field(default=None)
    cron_expression: str
    is_active: bool = Field(default=True)
    next_run_at: Optional[datetime] = Field(default=None)
    last_run_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by_id: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_by_id: Optional[int] = Field(default=None, foreign_key="users.id")


class TestSchedule(TestScheduleBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project: Optional["Project"] = Relationship()
    test_suite: Optional["TestSuite"] = Relationship()
    test_case: Optional["TestCase"] = Relationship()
    created_by: Optional["User"] = Relationship(
        sa_relationship_kwargs={"foreign_keys": "TestSchedule.created_by_id"}
    )
    updated_by: Optional["User"] = Relationship(
        sa_relationship_kwargs={"foreign_keys": "TestSchedule.updated_by_id"}
    )


class TestScheduleRead(TestScheduleBase):
    id: int


class TestScheduleUpdate(SQLModel):
    name: Optional[str] = None
    description: Optional[str] = None
    cron_expression: Optional[str] = None
    is_active: Optional[bool] = None
    browser: Optional[str] = None
    device: Optional[str] = None


class AuditLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    entity_type: str  # 'suite', 'case', 'workspace', 'team', 'project'
    entity_id: int
    action: str  # 'create', 'update', 'delete', 'import', 'invite'
    user_id: Optional[int] = Field(default=None, foreign_key="users.id")
    workspace_id: Optional[int] = Field(
        default=None, foreign_key="workspace.id")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    changes: Optional[dict] = Field(default={}, sa_column=Column(JSON))

    user: Optional["User"] = Relationship()


class AuditLogRead(SQLModel):
    id: int
    entity_type: str
    entity_id: int
    action: str
    user_id: Optional[int]
    timestamp: datetime
    changes: Optional[dict]
    user: Optional[UserRead] = None


class TeamInvitation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True)
    team_id: int = Field(foreign_key="team.id")
    invited_by_id: int = Field(foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WorkspaceInvitation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True)
    workspace_id: int = Field(foreign_key="workspace.id")
    role: str = Field(default="member")
    invited_by_id: int = Field(foreign_key="users.id")
    token: str = Field(unique=True, index=True)
    expires_at: datetime
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Project Scoping (Optional)
    project_id: Optional[int] = Field(default=None)
    project_role: Optional[str] = Field(default=None)


# =============================================================================
# AI-agent integration tables (Phase A)
# =============================================================================


class ApiKey(SQLModel, table=True):
    """Service-account credential for non-human callers (CI, AI agents).

    Only the hashed secret is stored. `prefix` is the first 8 characters of
    the raw key and is shown in the UI so users can identify a key without
    revealing it. Keys are scoped to a workspace; the optional `project_id`
    further narrows scope. Role is referenced via `role_id` so revocation +
    permission changes flow through existing RBAC.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)
    project_id: Optional[int] = Field(default=None, foreign_key="project.id")
    name: str
    prefix: str = Field(index=True)
    hashed_key: str
    role_id: Optional[int] = Field(default=None, foreign_key="role.id")
    created_by_id: int = Field(foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_used_at: Optional[datetime] = Field(default=None)
    expires_at: Optional[datetime] = Field(default=None)
    revoked_at: Optional[datetime] = Field(default=None)


class ApiKeyCreate(SQLModel):
    name: str
    workspace_id: int
    project_id: Optional[int] = None
    role_id: Optional[int] = None
    expires_in_days: Optional[int] = None


class ApiKeyRead(SQLModel):
    id: int
    workspace_id: int
    project_id: Optional[int]
    name: str
    prefix: str
    created_at: datetime
    last_used_at: Optional[datetime]
    expires_at: Optional[datetime]
    revoked_at: Optional[datetime]


class ApiKeyCreateResponse(ApiKeyRead):
    # Plaintext secret is returned exactly once at creation time.
    secret: str


class RefreshToken(SQLModel, table=True):
    """Rotating refresh token for long-running human sessions.

    Stored hashed; the raw token is returned to the client once and rotated
    on every /api/auth/refresh call. `revoked_at` enables family-based
    revocation (rotate-on-use detects token replay).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    hashed_token: str = Field(index=True)
    family_id: str = Field(index=True)
    expires_at: datetime
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_used_at: Optional[datetime] = Field(default=None)
    revoked_at: Optional[datetime] = Field(default=None)
    user_agent: Optional[str] = Field(default=None)
    ip_address: Optional[str] = Field(default=None)


class WorkspaceWebhook(SQLModel, table=True):
    """Outbound webhook target registered by a workspace.

    On run events the dispatcher POSTs a signed JSON payload to `url`.
    `event_filter` is a comma-separated list of event names; empty matches
    all events. `secret` is used for HMAC-SHA256 request signing.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)
    project_id: Optional[int] = Field(default=None, foreign_key="project.id")
    name: str
    url: str
    secret: str
    event_filter: Optional[str] = Field(default=None)
    is_active: bool = Field(default=True)
    created_by_id: int = Field(foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_delivery_at: Optional[datetime] = Field(default=None)
    last_delivery_status: Optional[int] = Field(default=None)
    failure_count: int = Field(default=0)


class WorkspaceWebhookCreate(SQLModel):
    name: str
    url: str
    workspace_id: int
    project_id: Optional[int] = None
    event_filter: Optional[str] = None


class WorkspaceWebhookRead(SQLModel):
    id: int
    workspace_id: int
    project_id: Optional[int]
    name: str
    url: str
    event_filter: Optional[str]
    is_active: bool
    created_at: datetime
    last_delivery_at: Optional[datetime]
    last_delivery_status: Optional[int]
    failure_count: int


class VisualBaseline(SQLModel, table=True):
    """Pinned screenshot baseline for the `expect-visual-match` step.

    Phase B scaffolding: storage and lookup are wired; the actual perceptual
    diff happens in the execution worker (deferred — see SCOPE_NOTES.md).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    test_case_id: int = Field(foreign_key="testcase.id", index=True)
    step_id: str = Field(index=True)
    browser: str = Field(default="chromium")
    device: Optional[str] = Field(default=None)
    viewport: Optional[str] = Field(default=None)
    image_url: str
    mask_regions: Optional[List[dict]] = Field(default=[], sa_column=Column(JSON))
    tolerance: float = Field(default=0.01)
    created_by_id: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class VisualBaselineRead(SQLModel):
    id: int
    test_case_id: int
    step_id: str
    browser: str
    device: Optional[str]
    viewport: Optional[str]
    image_url: str
    tolerance: float
    created_at: datetime

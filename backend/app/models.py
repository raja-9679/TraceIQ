from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel
from sqlmodel import SQLModel, Field, Relationship
from sqlalchemy import Column, JSON, String, Enum as SAEnum, UniqueConstraint
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

    # Phase D: daily cap on AI-driven test generation calls. 0 = unlimited.
    ai_generation_limit_daily: int = Field(default=100)

    # Max number of runs that may be RUNNING concurrently across this
    # workspace's projects. Enforced at dispatch — runs over the cap stay
    # PENDING and are retried until a slot frees. 0 = unlimited.
    max_concurrent_runs: int = Field(default=0)


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
    # Phase E: agent provenance. created_by_agent_id is the free-form
    # X-Agent-Id header value of the caller at create time; agent_session_id
    # is the X-Agent-Session-Id (a UUID the agent mints once per session).
    # Lets policies and audits distinguish "an agent made this in session X"
    # from "a human made this".
    created_by_agent_id: Optional[str] = Field(default=None)
    agent_session_id: Optional[str] = Field(default=None, index=True)


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
    # Phase B: semantic-selector layer. `intent` is a natural-language
    # description of what the step should target (e.g. "primary checkout
    # button"). The runner first tries `selector`; on miss it falls back to
    # an LLM-based resolver using `intent` + DOM snapshot. `intent` is the
    # durable contract; `selector` is disposable and may be auto-rewritten
    # by proactive healing.
    intent: Optional[str] = None


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

    # Phase E: agent provenance (see TestSuiteBase for shape + intent).
    created_by_agent_id: Optional[str] = Field(default=None)
    agent_session_id: Optional[str] = Field(default=None, index=True)

    # Phase D: agent-ownership metadata.
    # `code_paths` is a list of file-path prefixes (or glob patterns) this
    # test exercises. The impact-analysis endpoint matches PR-changed files
    # against this list to decide which cases to run for a given diff.
    code_paths: Optional[List[str]] = Field(default=[], sa_column=Column(JSON))
    # `is_ai_authored` is set true for any case created by an AI agent (via
    # /api/cases/generate or the proposal queue). Combined with run history,
    # the tautology detector uses this to surface suspect tests.
    is_ai_authored: bool = Field(default=False)
    ai_confidence: Optional[float] = Field(default=None)
    last_human_reviewed_at: Optional[datetime] = Field(default=None)
    last_human_reviewed_by_id: Optional[int] = Field(default=None, foreign_key="users.id")

    # Auth sessions: `is_auth_setup` marks the case whose successful run
    # captures the project's Playwright storageState (it always runs without
    # a stored session). `use_auth_session` lets a case opt out of starting
    # from the stored session (e.g. tests of the login flow itself).
    is_auth_setup: bool = Field(default=False)
    use_auth_session: bool = Field(default=True)

    # Data-driven tests: a list of row objects. At dispatch the case expands
    # into one execution per row; steps reference values as {{data.KEY}}.
    dataset: Optional[List[dict]] = Field(default=None, sa_column=Column(JSON))

    # Test-management metadata: free-form `tags` for filtering/organising and
    # a coarse `priority` (e.g. "critical" | "high" | "medium" | "low"). Tags
    # can select cases at run time (POST /api/runs?tags=smoke).
    tags: Optional[List[str]] = Field(default=[], sa_column=Column(JSON))
    priority: Optional[str] = Field(default=None)


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
    is_auth_setup: Optional[bool] = None
    use_auth_session: Optional[bool] = None
    dataset: Optional[List[dict]] = None
    tags: Optional[List[str]] = None
    priority: Optional[str] = None


class ProjectEnvironment(SQLModel, table=True):
    """A named deployment target for a project (dev/staging/prod).

    `variables` are non-sensitive key-values referenced in steps as
    `{{env.KEY}}`. `base_url` is prefixed onto relative goto URLs so one
    suite can run against any environment. Sensitive values belong in
    ProjectSecret, not here."""
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_projectenvironment_project_name"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    name: str
    base_url: Optional[str] = Field(default=None)
    variables: dict = Field(default={}, sa_column=Column(JSON))
    is_default: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ProjectSecret(SQLModel, table=True):
    """Write-only secret referenced in steps as `{{secret.KEY}}`.

    Encrypted at rest (Fernet, key derived from SECRET_KEY). The read API
    only ever returns key names; plaintext is decrypted at dispatch time
    and travels only inside the job payload to workers."""
    __table_args__ = (UniqueConstraint("project_id", "key", name="uq_projectsecret_project_key"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    key: str
    value_encrypted: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AuthSession(SQLModel, table=True):
    """Reusable Playwright storageState (cookies + localStorage) per project.

    Captured from the project's auth-setup case on a successful run and
    injected into browser contexts of later runs so tests start already
    logged in. The raw state is never exposed through the read API — only
    metadata (age/freshness) is."""
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", unique=True, index=True)
    storage_state: dict = Field(default={}, sa_column=Column(JSON))
    captured_by_case_id: Optional[int] = Field(default=None, foreign_key="testcase.id")
    captured_at: datetime = Field(default_factory=datetime.utcnow)
    # After this many minutes the state is considered stale and is no longer
    # injected; re-run the auth-setup case to refresh it.
    max_age_minutes: int = Field(default=720)


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
    # Which ProjectEnvironment this run executed against (None = project
    # default environment, or none configured).
    environment_id: Optional[int] = Field(
        default=None, foreign_key="projectenvironment.id")
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

    # Phase C: deployment-comparison primitive. Setting baseline_run_id makes
    # this a comparison run — same suite, possibly different `target_url`,
    # results compared against the baseline run's results.
    baseline_run_id: Optional[int] = Field(default=None)
    target_url: Optional[str] = Field(default=None)
    # Optional persona injected at runtime so the run executes "as" a
    # specific user (logged-in, admin, etc.). Phase B feature.
    persona_id: Optional[int] = Field(default=None)


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

    # Phase B: smart retry + flake separation.
    retry_count: int = Field(default=0)
    confidence: Optional[float] = Field(default=None)  # 0.0–1.0; how sure we are the failure is real
    is_flaky: bool = Field(default=False)

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

    # Email verification. Existing users default unverified; verification is
    # informational (login is not blocked on it) plus a resend flow.
    is_verified: bool = Field(default=False)
    email_verified_at: Optional[datetime] = Field(default=None)


class AccountToken(SQLModel, table=True):
    """Single-use token for password reset and email verification.

    Only the SHA-256 hash of the raw token is stored (same scheme as
    RefreshToken). `purpose` is 'password_reset' | 'email_verification'.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    purpose: str = Field(index=True)
    hashed_token: str = Field(index=True, unique=True)
    expires_at: datetime
    used_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)


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


# =============================================================================
# Phase B — Resilience: personas, proactive healing, flake quarantine
# =============================================================================


class Persona(SQLModel, table=True):
    """Reusable session artifact — cookies / localStorage / auth headers — so
    tests don't have to re-execute fragile login flows on every run.

    `session_state` mirrors Playwright's `storageState` shape so it can be
    handed directly to `browser.newContext({ storageState })`.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)
    project_id: Optional[int] = Field(default=None, foreign_key="project.id")
    name: str
    description: Optional[str] = None
    # Storage state JSON (Playwright shape): { cookies: [...], origins: [...] }.
    session_state: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    # Auth headers to merge into every request initiated by this persona.
    auth_headers: Optional[dict] = Field(default={}, sa_column=Column(JSON))
    # Optional auto-login recipe: a list of steps the runner executes once,
    # then captures storageState as a refreshed session_state.
    login_steps: Optional[List[dict]] = Field(default=[], sa_column=Column(JSON))
    refresh_after_hours: Optional[int] = Field(default=24)
    last_refreshed_at: Optional[datetime] = Field(default=None)
    created_by_id: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PersonaCreate(SQLModel):
    workspace_id: int
    name: str
    description: Optional[str] = None
    project_id: Optional[int] = None
    session_state: Optional[dict] = None
    auth_headers: Optional[dict] = None
    login_steps: Optional[List[dict]] = None
    refresh_after_hours: Optional[int] = 24


class PersonaRead(SQLModel):
    id: int
    workspace_id: int
    project_id: Optional[int]
    name: str
    description: Optional[str]
    auth_headers: Optional[dict]
    refresh_after_hours: Optional[int]
    last_refreshed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class SelectorHealProposal(SQLModel, table=True):
    """Proactive selector-heal proposal generated after a successful run.

    The post-run beat task diffs the stored `step.selector` against the DOM
    captured during execution. If the LLM concludes that the same `intent`
    now maps to a different selector with high confidence, it inserts a
    proposal. A reviewer (human or auto-apply policy) decides whether to
    accept it.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    test_case_id: int = Field(foreign_key="testcase.id", index=True)
    step_id: str = Field(index=True)
    old_selector: Optional[str] = None
    new_selector: str
    intent: Optional[str] = None
    confidence: float = Field(default=0.0)
    rationale: Optional[str] = None
    source_run_id: Optional[int] = Field(default=None, foreign_key="testrun.id")
    status: str = Field(default="pending")  # pending | accepted | rejected | auto_applied
    created_at: datetime = Field(default_factory=datetime.utcnow)
    decided_at: Optional[datetime] = Field(default=None)
    decided_by_id: Optional[int] = Field(default=None, foreign_key="users.id")


class SelectorHealProposalRead(SQLModel):
    id: int
    test_case_id: int
    step_id: str
    old_selector: Optional[str]
    new_selector: str
    intent: Optional[str]
    confidence: float
    rationale: Optional[str]
    source_run_id: Optional[int]
    status: str
    created_at: datetime


class FlakeRecord(SQLModel, table=True):
    """Per (test_case, step) flake-tracking row.

    A test is flagged flaky when its recent retry stream shows alternating
    pass/fail under identical conditions. Quarantined flakes are skipped at
    dispatch time so they don't gate AI-agent regressions.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    test_case_id: int = Field(foreign_key="testcase.id", index=True)
    step_id: Optional[str] = Field(default=None, index=True)
    flake_score: float = Field(default=0.0)  # 0.0 = stable, 1.0 = pure flake
    is_quarantined: bool = Field(default=False, index=True)
    first_observed_at: datetime = Field(default_factory=datetime.utcnow)
    last_observed_at: datetime = Field(default_factory=datetime.utcnow)
    sample_count: int = Field(default=0)
    last_failure_message: Optional[str] = None


# =============================================================================
# Phase C — Coverage: AI-driven case generation, comparison runs
# =============================================================================


class CaseGenerationRequest(SQLModel):
    """Input for POST /api/cases/generate (test-from-intent)."""
    description: str
    target_url: Optional[str] = None
    test_suite_id: int
    case_name: Optional[str] = None
    # Phase D: "propose" enqueues a CaseProposal for human review;
    # "direct" creates the case immediately (admin/editor only). Default
    # depends on caller — API-key (agent) callers are forced to "propose".
    mode: Optional[str] = None  # "direct" | "propose"
    code_paths: Optional[List[str]] = None


class CaseFromOpenAPIRequest(SQLModel):
    """Input for POST /api/cases/from-openapi (test-from-schema)."""
    schema_url: Optional[str] = None
    schema_inline: Optional[dict] = None
    test_suite_id: int
    base_url: Optional[str] = None
    operations: Optional[List[str]] = None  # specific operationIds to cover


class ComparisonRunRequest(SQLModel):
    """Input for POST /api/runs/comparison.

    Re-runs the same suite that produced `baseline_run_id` against a new
    `target_url` (typically a staging env), so functional + visual deltas
    can be surfaced.
    """
    baseline_run_id: int
    target_url: str
    browser: Optional[str] = "chromium"
    device: Optional[str] = None


# =============================================================================
# Phase D — Agent owns the test suite
# =============================================================================


class CaseProposalAction(str, Enum):
    """Verb for a CaseProposal — what should happen if accepted."""
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    MOVE = "move"


class CaseProposal(SQLModel, table=True):
    """Agent-proposed change to a test case, awaiting human review.

    Mirrors the SelectorHealProposal pattern: agents do not write directly
    to TestCase rows; they queue proposals that a human (or, with a
    workspace-level auto-approve threshold, the system) accepts.

    On accept, the action is applied: a new TestCase is created, or an
    existing one is updated / deleted / moved.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    # For CREATE: the target suite to put the new case in.
    # For UPDATE / DELETE / MOVE: identifies the existing case.
    test_suite_id: Optional[int] = Field(default=None, foreign_key="testsuite.id")
    target_case_id: Optional[int] = Field(default=None, foreign_key="testcase.id")
    action: CaseProposalAction = Field(
        sa_column=Column(SAEnum(
            CaseProposalAction, name="caseproposalaction",
            values_callable=lambda obj: [e.value for e in obj],
        )),
    )
    # Free-form proposed payload. Interpretation depends on `action`:
    #   CREATE: { name, steps, code_paths, intent }
    #   UPDATE: { name?, steps?, code_paths?, ... }
    #   DELETE: { reason }
    #   MOVE:   { new_test_suite_id }
    payload: Optional[dict] = Field(default={}, sa_column=Column(JSON))
    rationale: Optional[str] = None
    ai_confidence: float = Field(default=0.0)
    agent_id: Optional[str] = None
    source_run_id: Optional[int] = Field(default=None, foreign_key="testrun.id")
    status: str = Field(default="pending", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    decided_at: Optional[datetime] = Field(default=None)
    decided_by_id: Optional[int] = Field(default=None, foreign_key="users.id")
    decision_note: Optional[str] = None
    # Phase E: agent session this proposal belongs to. Lets the reviewer (or
    # an auto-approval policy) tell whether a delete proposal targets an
    # entity the same agent created in the same session.
    created_by_agent_id: Optional[str] = Field(default=None)
    agent_session_id: Optional[str] = Field(default=None, index=True)


class CaseProposalRead(SQLModel):
    id: int
    project_id: int
    test_suite_id: Optional[int]
    target_case_id: Optional[int]
    action: str
    payload: Optional[dict]
    rationale: Optional[str]
    ai_confidence: float
    agent_id: Optional[str]
    source_run_id: Optional[int]
    status: str
    created_at: datetime
    decided_at: Optional[datetime]


class CaseProposalCreate(SQLModel):
    project_id: int
    test_suite_id: Optional[int] = None
    target_case_id: Optional[int] = None
    action: CaseProposalAction
    payload: Optional[dict] = None
    rationale: Optional[str] = None
    ai_confidence: float = 0.0


class ImpactAnalysisRequest(SQLModel):
    """Input for POST /api/runs/impact-analysis.

    Given a set of file paths that changed in a PR, return the subset of
    test cases that exercise those paths (per their `code_paths` field).
    """
    project_id: int
    changed_files: List[str]
    include_no_code_paths: bool = False  # if True, also list cases without code_paths


class ImpactedCase(SQLModel):
    id: int
    name: str
    test_suite_id: Optional[int]
    is_ai_authored: bool
    matched_paths: List[str]


class ImpactAnalysisResponse(SQLModel):
    matched_cases: List[ImpactedCase]
    cases_without_code_paths: int
    unmatched_files: List[str]

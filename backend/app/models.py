from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel
from sqlmodel import SQLModel, Field, Relationship
from sqlalchemy import Column, JSON, String, Text, Enum as SAEnum, UniqueConstraint
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
    # Release-gate policy for this project (PLATFORM_VISION.md §5). None → the
    # built-in DEFAULT_QUALITY_GATE is used. Shape mirrors QualityGatePolicy.
    quality_gate_policy: Optional[Dict[str, Any]] = Field(
        default=None, sa_column=Column(JSON))
    # CI/PR-reporting settings (PLATFORM_VISION.md §5, item 4). None → the
    # built-in DEFAULT_CI_SETTINGS (disabled). Opt-in and CI/VCS-agnostic:
    # reporting is keyed off run_id, so git is optional. Shape mirrors CiSettings.
    ci_settings: Optional[Dict[str, Any]] = Field(
        default=None, sa_column=Column(JSON))
    # Active security-scan settings (PLATFORM_VISION.md P-4). None → the
    # built-in DEFAULT_SECURITY_SETTINGS (disabled). Enforces the
    # authorized-target allowlist. Shape mirrors SecuritySettings.
    security_settings: Optional[Dict[str, Any]] = Field(
        default=None, sa_column=Column(JSON))

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


class ExecutorType(str, Enum):
    """Which kind of worker executes a test/run — the keystone of the
    multi-executor platform (see PLATFORM_VISION.md §2).

    A test case declares its executor; the run denormalises it at dispatch and
    passes it in the job payload so a worker can branch. `ui_playwright` is the
    original (and default) behaviour; everything else is a future pillar.

    Persisted as a plain string (not a native DB enum) on purpose: new executor
    types can be added here without an ALTER TYPE migration.
    """
    UI_PLAYWRIGHT = "ui_playwright"   # interpreted browser journey (today's core)
    RAW_PLAYWRIGHT = "raw_playwright"  # uploaded .spec.ts run via `playwright test`
    SELENIUM = "selenium"             # (converter target / legacy import)
    API = "api"                       # API/contract testing
    LOAD = "load"                     # k6/Locust performance runs (time-series result)
    SECURITY = "security"             # ZAP/nuclei DAST scans (findings result)


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
    # Which kind of worker runs this case. Defaults to the original
    # interpreted-browser path; other values route to future executor workers
    # (raw Playwright, load, security, …). See ExecutorType / PLATFORM_VISION.md.
    executor: ExecutorType = Field(
        default=ExecutorType.UI_PLAYWRIGHT,
        sa_column=Column(String, nullable=False,
                         server_default=ExecutorType.UI_PLAYWRIGHT.value))
    # For executor=raw_playwright: the uploaded Playwright spec source, run
    # verbatim via `playwright test` on the worker (not the step interpreter).
    # NULL for step-based (ui_playwright) cases. See PLATFORM_VISION.md §4.
    raw_script: Optional[str] = Field(default=None, sa_column=Column(Text))
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
    # Denormalised from the case(s) at dispatch so results, filtering, and the
    # webhook payload know which executor produced this run. See ExecutorType.
    executor: ExecutorType = Field(
        default=ExecutorType.UI_PLAYWRIGHT,
        sa_column=Column(String, nullable=False,
                         server_default=ExecutorType.UI_PLAYWRIGHT.value))
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
    result_kind: Optional[str] = None
    result_payload: Optional[dict] = None


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

    # Type-aware result payload for non-UI executors (see ExecutorType /
    # PLATFORM_VISION.md §2). The step-oriented columns above describe a UI
    # journey; load and security runs don't fit them. `result_kind` names the
    # shape (e.g. "load", "security") and `result_payload` holds the
    # executor-specific data (time-series metrics, findings list, …). Both are
    # NULL for the classic ui_playwright path, keeping it backward compatible.
    result_kind: Optional[str] = Field(default=None)
    result_payload: Optional[dict] = Field(default=None, sa_column=Column(JSON))

    # Triage: the failure cluster this result was fingerprinted into (item 2).
    cluster_id: Optional[int] = Field(default=None, foreign_key="failurecluster.id", index=True)

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

    # MFA (TOTP). `mfa_secret` holds the Fernet-encrypted base32 secret; it is
    # set at setup but MFA is only enforced once `mfa_enabled` is True (after a
    # code is verified). See app/core/totp.py.
    mfa_enabled: bool = Field(default=False)
    mfa_secret: Optional[str] = Field(default=None)


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

    # Synthetic monitoring (PLATFORM_VISION.md §5). When is_monitor=true, each
    # scheduled run is treated as a production health check: consecutive-failure
    # streaks drive DOWN/RECOVERY alerts and every check feeds uptime/SLA stats.
    # These are user-configurable, so they live on the Base (create/read).
    is_monitor: bool = Field(default=False)
    # Fire a DOWN alert only after this many consecutive failing checks
    # (1 = alert on the first failure). Guards against single-blip noise.
    alert_after_failures: int = Field(default=1)
    # Send a RECOVERY alert when a down monitor passes again.
    alert_on_recovery: bool = Field(default=True)


class TestSchedule(TestScheduleBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    # Monitor runtime state (server-maintained, not user input — kept off the
    # Base so it isn't accepted in create/update payloads). Streaks and uptime
    # are derived from MonitorCheck rows; these cache the current status and
    # the last state we alerted on (so we alert on transitions, not every run).
    monitor_state: Optional[str] = Field(default=None)   # "up" | "down" | None(unknown)
    last_alert_state: Optional[str] = Field(default=None)  # last state an alert was sent for
    last_checked_at: Optional[datetime] = Field(default=None)

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
    monitor_state: Optional[str] = None
    last_checked_at: Optional[datetime] = None


class TestScheduleUpdate(SQLModel):
    name: Optional[str] = None
    description: Optional[str] = None
    cron_expression: Optional[str] = None
    is_active: Optional[bool] = None
    browser: Optional[str] = None
    device: Optional[str] = None
    is_monitor: Optional[bool] = None
    alert_after_failures: Optional[int] = None
    alert_on_recovery: Optional[bool] = None


class MonitorCheck(SQLModel, table=True):
    """One synthetic-monitoring health check — the outcome of a single
    monitor-triggered run. Rows accumulate per monitor and are the source of
    truth for uptime %, SLA windows, and the consecutive-failure streak that
    drives alerting. See app/tasks/monitor_tasks.py."""
    id: Optional[int] = Field(default=None, primary_key=True)
    schedule_id: int = Field(foreign_key="testschedule.id", index=True)
    run_id: Optional[int] = Field(default=None, foreign_key="testrun.id")
    # The run's terminal status, stored as a plain string (not the native
    # teststatus enum) to keep this table decoupled and the migration trivial;
    # Pydantic coerces it back to TestStatus on read.
    status: TestStatus = Field(sa_column=Column(String, nullable=False))
    is_up: bool  # True iff the run passed (status == PASSED)
    checked_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class MonitorCheckRead(SQLModel):
    id: int
    run_id: Optional[int] = None
    status: TestStatus
    is_up: bool
    checked_at: datetime


class MonitorStatusRead(SQLModel):
    """Computed health snapshot for one monitor."""
    schedule_id: int
    name: str
    is_active: bool
    state: str  # "up" | "down" | "unknown"
    consecutive_failures: int
    total_checks: int
    uptime_24h: Optional[float] = None  # 0–100, null when no checks in window
    uptime_7d: Optional[float] = None
    last_checked_at: Optional[datetime] = None
    recent_checks: List[MonitorCheckRead] = []


class SecurityFinding(SQLModel, table=True):
    """A single security finding for a run (PLATFORM_VISION.md P-4).

    Phase 1 populates these from passive analysis of already-captured responses
    (scan_type="passive"); the ZAP/nuclei executors (phases 2–4) will write to
    the same table with their own scan_type, so this is the unified findings
    result-model. run_id has ON DELETE CASCADE (set in the migration) so
    findings are cleaned up when a run is deleted/purged."""
    id: Optional[int] = Field(default=None, primary_key=True)
    # A finding attaches to a run (passive analysis) OR a SecurityScan (ZAP);
    # both are nullable so either source works.
    run_id: Optional[int] = Field(default=None, foreign_key="testrun.id", index=True)
    scan_id: Optional[int] = Field(default=None, foreign_key="securityscan.id", index=True)
    project_id: Optional[int] = Field(default=None, foreign_key="project.id", index=True)
    scan_type: str = Field(default="passive")  # "passive" | "zap" | "nuclei" | …
    category: str   # missing-header | insecure-cookie | info-disclosure | insecure-transport | dast
    severity: str   # high | medium | low | info
    title: str
    description: Optional[str] = None
    evidence: Optional[str] = None
    target_url: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SecurityScan(SQLModel, table=True):
    """An active/authenticated DAST scan of a target URL (PLATFORM_VISION.md
    P-4, item 6). Long-running and async: created PENDING, driven to RUNNING then
    COMPLETED/ERROR by the ZAP scan task. Findings link via scan_id."""
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    target_url: str
    scan_type: str = Field(default="baseline")  # "baseline" (spider+passive) | "active"
    authenticated: bool = Field(default=False)   # scanned with the project's stored auth session
    status: str = Field(default="pending")       # pending | running | completed | error
    requested_by_id: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = Field(default=None)
    finished_at: Optional[datetime] = Field(default=None)
    counts: Optional[dict] = Field(default=None, sa_column=Column(JSON))  # severity → count
    error: Optional[str] = Field(default=None)


class SecuritySettings(SQLModel):
    """Per-project active-scan config. Disabled by default; scans are refused
    unless the target host is on the authorized allowlist."""
    enabled: bool = False
    # Hostnames the project is authorized to scan. A scan target must match one.
    allowed_domains: List[str] = []
    # Active (attacking) scans are destructive — separate opt-in on top of the
    # global SECURITY_ACTIVE_SCAN_ENABLED flag.
    allow_active_scan: bool = False


DEFAULT_SECURITY_SETTINGS = SecuritySettings()


class SecurityScanRequest(SQLModel):
    target_url: str
    scan_type: str = "baseline"     # "baseline" | "active"
    authenticated: bool = False     # use the project's stored auth session
    # Explicit attestation that the caller is authorized to scan the target.
    authorized: bool = False


class SecurityFindingRead(SQLModel):
    id: int
    run_id: Optional[int] = None
    scan_id: Optional[int] = None
    scan_type: str
    category: str
    severity: str
    title: str
    description: Optional[str] = None
    evidence: Optional[str] = None
    target_url: Optional[str] = None
    created_at: datetime


class SecurityScanRead(SQLModel):
    id: int
    project_id: int
    target_url: str
    scan_type: str
    authenticated: bool
    status: str
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    counts: Optional[dict] = None
    error: Optional[str] = None
    findings: List[SecurityFindingRead] = []


class SecurityScanResult(SQLModel):
    """Response for a scan trigger / findings listing."""
    run_id: int
    scan_type: str = "passive"
    counts: Dict[str, int] = {}
    findings: List[SecurityFindingRead] = []


# =============================================================================
# Quality dashboard + release gate (PLATFORM_VISION.md §5)
# =============================================================================


class QualityTrendPoint(SQLModel):
    date: str  # YYYY-MM-DD
    runs: int
    passed_runs: int
    pass_rate: float  # 0–100


class QualitySnapshot(SQLModel):
    """Aggregated project quality across a rolling window — the unified view of
    run health, flakiness, monitor uptime and security posture."""
    project_id: int
    window_days: int
    total_runs: int
    finished_runs: int
    passed_runs: int
    failed_runs: int
    pass_rate: float  # 0–100, over finished runs
    trend: List[QualityTrendPoint] = []
    flaky_tests: int = 0
    quarantined_tests: int = 0
    monitors_total: int = 0
    monitors_up: int = 0
    monitors_down: int = 0
    down_monitor_names: List[str] = []
    security_findings: Dict[str, int] = {}  # severity → count in window


class QualityGatePolicy(SQLModel):
    """Thresholds a release must satisfy. Stored per-project on
    Project.quality_gate_policy; unset fields fall back to these defaults."""
    min_pass_rate: float = 100.0
    max_high_severity_findings: int = 0
    max_medium_severity_findings: Optional[int] = None  # None = no limit
    require_monitors_up: bool = False
    # Performance budgets, checked against the worst web-vitals sample across
    # the evaluated runs' results. 0 = budget not enforced.
    max_lcp_ms: int = 0
    max_cls: float = 0.0
    max_ttfb_ms: int = 0
    # Require the team's own CI results (ingested JUnit reports) to be green.
    # Fails closed when required but no report exists for the commit/project.
    require_external_tests_pass: bool = False


class QualityGateCheck(SQLModel):
    name: str
    passed: bool
    actual: str
    threshold: str
    detail: Optional[str] = None


class QualityGateResult(SQLModel):
    project_id: int
    passed: bool
    git_commit: Optional[str] = None
    git_branch: Optional[str] = None
    evaluated_run_ids: List[int] = []
    checks: List[QualityGateCheck] = []


DEFAULT_QUALITY_GATE = QualityGatePolicy()


# =============================================================================
# CI / PR reporting (PLATFORM_VISION.md §5, item 4) — CI- and VCS-agnostic
# =============================================================================


class CiSettings(SQLModel):
    """Per-project CI/PR-reporting configuration. Opt-in: disabled by default so
    teams that don't use CI (or git) are unaffected."""
    enabled: bool = False
    # When true, a CI consumer should treat a failed quality gate as blocking.
    enforce_gate: bool = True
    # Hint for VCS-based consumers (e.g. the GitHub Action) to post a PR comment.
    # Ignored when there is no PR/VCS context — reporting still works by run_id.
    post_pr_comment: bool = True


DEFAULT_CI_SETTINGS = CiSettings()


class ReportTestResult(SQLModel):
    test_name: str
    status: TestStatus
    duration_ms: Optional[float] = None
    error_message: Optional[str] = None
    trace_url: Optional[str] = None


class RunReport(SQLModel):
    """A consolidated, presentation-ready report for a single run. Identified by
    run_id (git-agnostic); the `git` block is populated only when the run
    carries VCS context. `markdown` is ready to paste into a PR comment, Slack
    message, or any CI log."""
    run_id: int
    project_id: Optional[int] = None
    status: TestStatus
    suite_name: Optional[str] = None
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    duration_ms: Optional[float] = None
    results: List[ReportTestResult] = []
    security: Dict[str, int] = {}          # severity → count for this run
    git: Optional[Dict[str, Any]] = None   # {commit, branch, pr_url, repo} if present
    gate: Optional[QualityGateResult] = None
    markdown: str = ""


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


# =============================================================================
# Issue-tracker / defect integration (Jira, iTop, GitHub) — create tickets
# from a run and attach its artifacts (trace/video/screenshots).
# =============================================================================


class IssueTrackerConfig(SQLModel, table=True):
    """Workspace-scoped connection to an external tracker. The credential is
    stored Fernet-encrypted (never returned by the API)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)
    provider: str                      # "jira" | "itop" | "github"
    name: str                          # display label
    base_url: str
    auth_user: Optional[str] = Field(default=None)   # email/username (not secret); token-only for github
    auth_secret_encrypted: str                        # Fernet-encrypted token/password
    # Provider defaults: jira {project_key, issue_type}; itop {class, org_id};
    # github {repo}. Plus optional {priority}.
    settings: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    enabled: bool = Field(default=True)
    created_by_id: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class IssueTicket(SQLModel, table=True):
    """A ticket created in an external tracker from a TraceIQ run/result."""
    id: Optional[int] = Field(default=None, primary_key=True)
    config_id: int = Field(foreign_key="issuetrackerconfig.id", index=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)
    run_id: Optional[int] = Field(default=None, foreign_key="testrun.id", index=True)
    result_id: Optional[int] = Field(default=None)  # optional TestCaseResult id
    cluster_id: Optional[int] = Field(default=None, index=True)  # optional FailureCluster id
    provider: str
    external_key: Optional[str] = Field(default=None)  # e.g. "PROJ-123"
    url: Optional[str] = Field(default=None)
    summary: str
    status: str = Field(default="pending")  # pending | created | error
    attachments_uploaded: int = Field(default=0)
    attachments_total: int = Field(default=0)
    error: Optional[str] = Field(default=None)
    created_by_id: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class IssueTrackerConfigCreate(SQLModel):
    provider: str
    name: str
    base_url: str
    auth_user: Optional[str] = None
    auth_secret: str                       # plaintext; encrypted on write
    settings: Optional[dict] = None
    enabled: bool = True


class IssueTrackerConfigUpdate(SQLModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    auth_user: Optional[str] = None
    auth_secret: Optional[str] = None      # only re-encrypted when provided
    settings: Optional[dict] = None
    enabled: Optional[bool] = None


class IssueTrackerConfigRead(SQLModel):
    id: int
    workspace_id: int
    provider: str
    name: str
    base_url: str
    auth_user: Optional[str] = None
    settings: Optional[dict] = None
    enabled: bool
    created_at: datetime
    # NB: auth_secret_encrypted is deliberately never exposed.


class IssueTicketCreate(SQLModel):
    config_id: int
    summary: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    result_id: Optional[int] = None
    attach_trace: bool = True
    attach_video: bool = True
    attach_screenshots: bool = True


class IssueTicketRead(SQLModel):
    id: int
    config_id: int
    run_id: Optional[int] = None
    provider: str
    external_key: Optional[str] = None
    url: Optional[str] = None
    summary: str
    status: str
    attachments_uploaded: int
    attachments_total: int
    error: Optional[str] = None
    created_at: datetime


# =============================================================================
# Failure triage / de-duplication (PLATFORM_VISION.md §5, item 2)
# =============================================================================


class FailureCluster(SQLModel, table=True):
    """A group of failures sharing one root-cause signature within a project.
    Failing results are fingerprinted (app/services/failure_signature.py) and
    upserted here so one root cause is one triage item, not N."""
    __table_args__ = (UniqueConstraint("project_id", "signature", name="uq_cluster_project_signature"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    signature: str = Field(index=True)
    title: str
    category: str                       # selector|timeout|assertion|network|navigation|other
    status: str = Field(default="open")  # open|investigating|resolved|ignored
    occurrence_count: int = Field(default=0)
    first_seen_at: datetime = Field(default_factory=datetime.utcnow)
    last_seen_at: datetime = Field(default_factory=datetime.utcnow)
    last_run_id: Optional[int] = Field(default=None)
    sample_error: Optional[str] = Field(default=None, sa_column=Column(Text))
    assignee_id: Optional[int] = Field(default=None, foreign_key="users.id")
    resolution_note: Optional[str] = Field(default=None)
    resolved_at: Optional[datetime] = Field(default=None)  # set when status→resolved (MTTR)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class FailureClusterRead(SQLModel):
    id: int
    project_id: int
    signature: str
    title: str
    category: str
    status: str
    occurrence_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    last_run_id: Optional[int] = None
    sample_error: Optional[str] = None
    assignee_id: Optional[int] = None
    resolution_note: Optional[str] = None


class FailureClusterUpdate(SQLModel):
    status: Optional[str] = None
    assignee_id: Optional[int] = None
    resolution_note: Optional[str] = None


class FailureOccurrenceRead(SQLModel):
    result_id: int
    run_id: int
    test_name: str
    status: TestStatus
    created_at: datetime


class FailureClusterDetail(FailureClusterRead):
    occurrences: List[FailureOccurrenceRead] = []


# =============================================================================
# Scheduled quality reports (PLATFORM_VISION.md §5, item 4)
# =============================================================================


class ReportSchedule(SQLModel, table=True):
    """A recurring quality report for a project: on its cron, a summary
    (run health + effectiveness) is built and sent to the configured channels."""
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    name: str
    cron_expression: str
    window_days: int = Field(default=7)
    channels: Optional[List[str]] = Field(default=None, sa_column=Column(JSON))  # email|slack|teams
    recipients: Optional[List[str]] = Field(default=None, sa_column=Column(JSON))  # email addresses
    is_active: bool = Field(default=True)
    next_run_at: Optional[datetime] = Field(default=None)
    last_run_at: Optional[datetime] = Field(default=None)
    created_by_id: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ReportScheduleCreate(SQLModel):
    name: str
    cron_expression: str
    window_days: int = 7
    channels: Optional[List[str]] = None
    recipients: Optional[List[str]] = None
    is_active: bool = True


class ReportScheduleUpdate(SQLModel):
    name: Optional[str] = None
    cron_expression: Optional[str] = None
    window_days: Optional[int] = None
    channels: Optional[List[str]] = None
    recipients: Optional[List[str]] = None
    is_active: Optional[bool] = None


class ReportScheduleRead(SQLModel):
    id: int
    project_id: int
    name: str
    cron_expression: str
    window_days: int
    channels: Optional[List[str]] = None
    recipients: Optional[List[str]] = None
    is_active: bool
    next_run_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None


# =============================================================================
# Billing / metering (PLATFORM_VISION.md — commercial readiness)
# =============================================================================


class Plan(SQLModel, table=True):
    """A subscription plan. `limits` is a dict; 0 means unlimited for that
    metric. Seeded (free/pro/enterprise) by the billing migration."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)  # machine key: free|pro|enterprise
    display_name: str
    price_cents: int = Field(default=0)
    stripe_price_id: Optional[str] = Field(default=None)
    limits: dict = Field(default={}, sa_column=Column(JSON))  # monthly_runs, seats, concurrent_runs, retention_days, ai_daily
    is_active: bool = Field(default=True)


class WorkspaceSubscription(SQLModel, table=True):
    """One active subscription per workspace. None → the free plan."""
    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", unique=True, index=True)
    plan_id: int = Field(foreign_key="plan.id")
    status: str = Field(default="active")  # active|trialing|past_due|canceled
    current_period_start: Optional[datetime] = Field(default=None)
    current_period_end: Optional[datetime] = Field(default=None)
    stripe_customer_id: Optional[str] = Field(default=None)
    stripe_subscription_id: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class UsageRecord(SQLModel, table=True):
    """Metered usage per workspace per period per metric (e.g. runs)."""
    __table_args__ = (UniqueConstraint("workspace_id", "period", "metric", name="uq_usage_ws_period_metric"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)
    period: str = Field(index=True)  # "YYYY-MM"
    metric: str                       # "runs" | "ai_generations"
    count: int = Field(default=0)


class ExternalTestReport(SQLModel, table=True):
    """A test report ingested from a team's own CI (JUnit XML) — results
    TraceIQ displays and gates on but did not execute. Report-level rows;
    failing cases are kept as a truncated JSON detail list."""
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    source: str = Field(default="junit")   # junit | ... (future: trx, xunit)
    suite_name: Optional[str] = Field(default=None)
    git_commit: Optional[str] = Field(default=None, index=True)
    git_branch: Optional[str] = Field(default=None)
    tests: int = Field(default=0)
    failures: int = Field(default=0)
    errors: int = Field(default=0)
    skipped: int = Field(default=0)
    time_seconds: float = Field(default=0.0)
    failed_cases: Optional[List[dict]] = Field(default=None, sa_column=Column(JSON))  # [{name, classname, message}]
    uploaded_by: Optional[str] = Field(default=None)  # user email or api-key label
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class ExternalTestReportRead(SQLModel):
    id: int
    project_id: int
    source: str
    suite_name: Optional[str] = None
    git_commit: Optional[str] = None
    git_branch: Optional[str] = None
    tests: int
    failures: int
    errors: int
    skipped: int
    time_seconds: float
    failed_cases: Optional[List[dict]] = None
    uploaded_by: Optional[str] = None
    created_at: datetime


class LLMUsageEvent(SQLModel, table=True):
    """One row per LLM API call — the raw feed behind the AI-usage dashboard.

    Monthly per-workspace token totals are additionally rolled up into
    UsageRecord (metric="llm_tokens") so plan quotas can cap them the same way
    runs are capped. workspace_id is nullable: some calls (e.g. inline selector
    heal inside the Celery runner) happen before workspace context is known.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: Optional[int] = Field(default=None, foreign_key="workspace.id", index=True)
    project_id: Optional[int] = Field(default=None, index=True)
    run_id: Optional[int] = Field(default=None, index=True)
    provider: str = Field(index=True)   # anthropic|openai|gemini|ollama|openai-compatible
    model: str = Field(default="", index=True)
    feature: str = Field(default="unknown", index=True)  # failure_analysis|selector_heal|case_generation|...
    source: str = Field(default="backend")  # backend | execution-engine
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)
    latency_ms: int = Field(default=0)
    success: bool = Field(default=True)
    error: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class PlanRead(SQLModel):
    id: int
    name: str
    display_name: str
    price_cents: int
    limits: dict
    is_active: bool


class BillingStatus(SQLModel):
    workspace_id: int
    plan: PlanRead
    status: str
    period: str
    usage: dict           # metric -> used
    limits: dict          # metric -> limit (0 = unlimited)
    current_period_end: Optional[datetime] = None
    stripe_configured: bool = False


class MfaRecoveryCode(SQLModel, table=True):
    """Single-use MFA backup code. Only the SHA-256 hash is stored (same scheme
    as refresh/account tokens). Regenerated as a set; consumed at login."""
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    code_hash: str = Field(index=True)
    used_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# =============================================================================
# Requirements / ticket traceability (PLATFORM_VISION.md §5, item 5)
# Lightweight linking — NOT a full RTM (matrices rejected, gap analysis §23).
# =============================================================================


class RequirementLink(SQLModel, table=True):
    """Links a test case to an external requirement/ticket (free-form ref like
    'JIRA-123' or 'PRD-Login'). project_id is denormalised for per-project
    rollups. `source` allows optional title resolution from a tracker later."""
    __table_args__ = (UniqueConstraint("test_case_id", "ref", name="uq_reqlink_case_ref"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    test_case_id: int = Field(foreign_key="testcase.id", index=True)
    project_id: Optional[int] = Field(default=None, foreign_key="project.id", index=True)
    ref: str = Field(index=True)
    source: str = Field(default="manual")  # manual|jira|linear|github
    title: Optional[str] = Field(default=None)
    url: Optional[str] = Field(default=None)
    created_by_id: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RequirementLinkCreate(SQLModel):
    ref: str
    source: str = "manual"
    title: Optional[str] = None
    url: Optional[str] = None


class RequirementLinkRead(SQLModel):
    id: int
    test_case_id: int
    ref: str
    source: str
    title: Optional[str] = None
    url: Optional[str] = None
    created_at: datetime


class RequirementCoverage(SQLModel):
    """Rollup for one requirement ref within a project."""
    ref: str
    source: str
    title: Optional[str] = None
    url: Optional[str] = None
    test_count: int
    status: str            # passing | failing | mixed | unknown
    passing: int
    failing: int
    untested: int
    test_names: List[str] = []

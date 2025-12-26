from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel
from sqlmodel import SQLModel, Field, Relationship
from sqlalchemy import Column, JSON, String, Enum as SAEnum
from enum import Enum

# Import settings models
from app.settings_models import UserSettings

class TestStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"

class ExecutionMode(str, Enum):
    CONTINUOUS = "continuous"
    SEPARATE = "separate"

class TestSuiteBase(SQLModel):
    name: str
    description: Optional[str] = None
    execution_mode: ExecutionMode = Field(default=ExecutionMode.CONTINUOUS, sa_column=Column(SAEnum(ExecutionMode, name="executionmode", values_callable=lambda obj: [e.value for e in obj])))
    parent_id: Optional[int] = Field(default=None, foreign_key="testsuite.id")
    settings: Optional[Dict[str, Any]] = Field(default={"headers": {}, "params": {}}, sa_column=Column(JSON))
    inherit_settings: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class TestSuite(TestSuiteBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    
    test_cases: List["TestCase"] = Relationship(back_populates="test_suite")
    parent: Optional["TestSuite"] = Relationship(
        back_populates="sub_modules",
        sa_relationship_kwargs={"remote_side": "TestSuite.id"}
    )
    sub_modules: List["TestSuite"] = Relationship(back_populates="parent")

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
    steps: List[TestStep] = Field(default=[], sa_column=Column(JSON)) # List of TestSteps
    test_suite_id: Optional[int] = Field(default=None, foreign_key="testsuite.id")

class TestCase(TestCaseBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    test_suite: Optional[TestSuite] = Relationship(back_populates="test_cases")

class TestCaseRead(TestCaseBase):
    id: int

class TestRunBase(SQLModel):
    test_suite_id: int = Field(foreign_key="testsuite.id")
    test_case_id: Optional[int] = Field(default=None, foreign_key="testcase.id")
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
    screenshots: Optional[List[str]] = Field(default=[], sa_column=Column(JSON))
    response_status: Optional[int] = Field(default=None)
    request_headers: Optional[dict] = Field(default={}, sa_column=Column(JSON))
    request_params: Optional[dict] = Field(default={}, sa_column=Column(JSON))
    response_headers: Optional[dict] = Field(default={}, sa_column=Column(JSON))
    allowed_domains: Optional[List[Any]] = Field(default=[], sa_column=Column(JSON))
    domain_settings: Optional[dict] = Field(default={}, sa_column=Column(JSON))
    network_events: Optional[List[dict]] = Field(default=[], sa_column=Column(JSON))
    execution_log: Optional[List[dict]] = Field(default=[], sa_column=Column(JSON))
    browser: str = Field(default="chromium")
    device: Optional[str] = Field(default=None)

class TestRun(TestRunBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    results: List["TestCaseResult"] = Relationship(back_populates="test_run", sa_relationship_kwargs={"cascade": "all, delete-orphan"})

class TestCaseResultRead(SQLModel):
    id: int
    test_name: str
    status: TestStatus
    duration_ms: float
    error_message: Optional[str] = None
    screenshots: Optional[List[str]] = []

class TestRunRead(TestRunBase):
    id: int
    results: List[TestCaseResultRead] = []

class TestCaseResult(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    test_run_id: int = Field(foreign_key="testrun.id")
    test_name: str
    status: TestStatus
    duration_ms: float
    error_message: Optional[str] = None
    trace_url: Optional[str] = None
    video_url: Optional[str] = None
    screenshots: Optional[List[str]] = Field(default=[], sa_column=Column(JSON))
    ai_analysis: Optional[str] = None
    
    test_run: TestRun = Relationship(back_populates="results")

class User(SQLModel, table=True):
    __tablename__ = "users"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    full_name: str
    hashed_password: str
    
    # Relationship
    settings: Optional["UserSettings"] = Relationship(back_populates="user", sa_relationship_kwargs={"uselist": False})
    is_active: bool = True

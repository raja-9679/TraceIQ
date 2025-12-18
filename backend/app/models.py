from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel
from sqlmodel import SQLModel, Field, Relationship
from sqlalchemy import Column, JSON
from enum import Enum

class TestStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"

class TestSuiteBase(SQLModel):
    name: str
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class TestSuite(TestSuiteBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    test_cases: List["TestCase"] = Relationship(back_populates="test_suite")

class TestSuiteRead(TestSuiteBase):
    id: int
    test_cases: List["TestCase"] = []

class TestStep(BaseModel):
    id: str
    type: str  # 'goto', 'click', 'fill', 'check', 'expect'
    selector: Optional[str] = None
    value: Optional[str] = None
    params: Optional[dict] = None

class TestCase(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    steps: List[TestStep] = Field(default=[], sa_column=Column(JSON)) # List of TestSteps
    test_suite_id: Optional[int] = Field(default=None, foreign_key="testsuite.id")
    
    test_suite: Optional[TestSuite] = Relationship(back_populates="test_cases")

class TestRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    test_suite_id: int = Field(foreign_key="testsuite.id")
    test_case_id: Optional[int] = Field(default=None, foreign_key="testcase.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    status: TestStatus = Field(default=TestStatus.PENDING)
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = Field(default=0)
    duration_ms: Optional[float] = Field(default=None)
    error_message: Optional[str] = Field(default=None)
    trace_url: Optional[str] = Field(default=None)
    video_url: Optional[str] = Field(default=None)
    response_status: Optional[int] = Field(default=None)
    request_headers: Optional[dict] = Field(default={}, sa_column=Column(JSON))
    response_headers: Optional[dict] = Field(default={}, sa_column=Column(JSON))
    
    results: List["TestCaseResult"] = Relationship(back_populates="test_run")

class TestCaseResult(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    test_run_id: int = Field(foreign_key="testrun.id")
    test_name: str
    status: TestStatus
    duration_ms: float
    error_message: Optional[str] = None
    trace_url: Optional[str] = None
    video_url: Optional[str] = None
    ai_analysis: Optional[str] = None
    
    test_run: TestRun = Relationship(back_populates="results")

class User(SQLModel, table=True):
    __tablename__ = "users"
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    full_name: Optional[str] = None
    hashed_password: str
    is_active: bool = True

import re

from pydantic import field_validator
from sqlmodel import SQLModel, Field, Relationship, Column, JSON
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

class UserSettings(SQLModel, table=True):
    __tablename__ = "user_settings"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    
    # General Settings
    theme: str = Field(default="light")
    timezone: str = Field(default="UTC")
    date_format: str = Field(default="MM/DD/YYYY")
    
    # Test Execution Defaults
    default_browser: str = Field(default="chromium")
    default_device: str = Field(default="Desktop")
    default_timeout: int = Field(default=30000)
    auto_retry: bool = Field(default=False)
    max_retries: int = Field(default=3)
    parallel_execution: bool = Field(default=False)
    max_parallel_tests: int = Field(default=3)
    
    # Multi-Browser Testing
    multi_browser_enabled: bool = Field(default=False)
    selected_browsers: List[str] = Field(default=["chromium"], sa_column=Column(JSON))
    
    # Multi-Device Testing
    multi_device_enabled: bool = Field(default=False)
    selected_devices: List[str] = Field(default=["Desktop"], sa_column=Column(JSON))
    
    # Notifications
    # True: until 2026-10 this switch was never read and the run owner was
    # always emailed, so "on" is what every existing user actually had
    # (migration cbc2b3c4d5ee sets existing rows to match).
    email_notifications: bool = Field(default=True)
    notify_on_completion: bool = Field(default=True)
    notify_on_failure: bool = Field(default=True)
    daily_summary: bool = Field(default=False)
    notification_email: Optional[str] = Field(default=None)
    
    # Storage & Retention
    video_recording: str = Field(default="on-failure")
    screenshot_on_error: bool = Field(default=True)
    trace_files: bool = Field(default=True)
    retention_period: int = Field(default=30)
    auto_cleanup: bool = Field(default=True)
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Relationship
    user: Optional["User"] = Relationship(back_populates="settings")


class UserSettingsRead(SQLModel):
    id: int
    user_id: int
    theme: str
    timezone: str
    date_format: str
    default_browser: str
    default_device: str
    default_timeout: int
    auto_retry: bool
    max_retries: int
    parallel_execution: bool
    max_parallel_tests: int
    multi_browser_enabled: bool
    selected_browsers: List[str]
    multi_device_enabled: bool
    selected_devices: List[str]
    email_notifications: bool
    notify_on_completion: bool
    notify_on_failure: bool
    daily_summary: bool
    notification_email: Optional[str]
    video_recording: str
    screenshot_on_error: bool
    trace_files: bool
    retention_period: int
    auto_cleanup: bool


#: Deliberately strict: the address goes into an email's To: header, so no
#: whitespace, newlines, commas or angle brackets (header injection).
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]{1,64}@[A-Za-z0-9-]{1,63}(\.[A-Za-z0-9-]{1,63})+$")


def normalize_notification_email(value: Optional[str]) -> Optional[str]:
    """'' / None clear the override (fall back to the account email)."""
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    if len(v) > 254 or not _EMAIL_RE.match(v):
        raise ValueError("notification_email must be a single plain email address")
    return v


class UserSettingsUpdate(SQLModel):
    theme: Optional[str] = None
    timezone: Optional[str] = None
    date_format: Optional[str] = None
    default_browser: Optional[str] = None
    default_device: Optional[str] = None
    default_timeout: Optional[int] = None
    auto_retry: Optional[bool] = None
    max_retries: Optional[int] = None
    parallel_execution: Optional[bool] = None
    max_parallel_tests: Optional[int] = None
    multi_browser_enabled: Optional[bool] = None
    selected_browsers: Optional[List[str]] = None
    multi_device_enabled: Optional[bool] = None
    selected_devices: Optional[List[str]] = None
    email_notifications: Optional[bool] = None
    notify_on_completion: Optional[bool] = None
    notify_on_failure: Optional[bool] = None
    daily_summary: Optional[bool] = None
    notification_email: Optional[str] = None
    video_recording: Optional[str] = None
    screenshot_on_error: Optional[bool] = None
    trace_files: Optional[bool] = None
    retention_period: Optional[int] = None
    auto_cleanup: Optional[bool] = None

    @field_validator("notification_email")
    @classmethod
    def _notification_email(cls, v):
        return normalize_notification_email(v)

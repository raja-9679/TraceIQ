"""Restore-drill results reported by scripts/restore_drill.sh (WP13a).

The script runs on an operator's host with the database password in its
shell. Everything it sends here is the PASS/WARN/FAIL summary table — and the
one thing this module exists to guarantee is that a password, connection
string or token never ends up stored because a check's detail line happened
to quote one. The script already redacts the password; this is the second
lock: a payload that still looks like it carries a credential is REJECTED
(422), not silently scrubbed, so a regression in the script is noticed.

Unknown fields are refused too (`extra="forbid"`), so a future script cannot
start sending, say, DATABASE_URL under a new name and have it stored.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Shapes that mean a secret leaked into a field. Deliberately broad: a false
#: positive costs a 422 the operator can read; a false negative stores a password.
SECRET_PATTERNS = [
    # scheme://user:pass@ — but not the script's own redaction, user:***@
    re.compile(r"[a-z][a-z0-9+.-]*://[^\s/:@]*:(?!\*+@)[^\s@/]+@", re.I),
    re.compile(r"(?<![a-z])(pass(word)?|pwd|secret|token|api[_-]?key)[a-z_]*\s*[=:]\s*\S", re.I),
    re.compile(r"\bPGPASSWORD\b", re.I),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.I),
    re.compile(r"\btiq_[A-Za-z0-9_-]{8,}"),                          # a TraceIQ API key
]


def looks_secret(text: Optional[str]) -> bool:
    return bool(text) and any(p.search(text) for p in SECRET_PATTERNS)


class DrillCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["PASS", "WARN", "FAIL"]
    check: str = Field(min_length=1, max_length=80)
    detail: Optional[str] = Field(default=None, max_length=300)


class DrillReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["pass", "fail"]
    ran_at: datetime
    dump_name: Optional[str] = Field(default=None, max_length=200)
    target: Optional[str] = Field(default=None, max_length=200)
    duration_seconds: Optional[int] = Field(default=None, ge=0, le=7 * 24 * 3600)
    checks: List[DrillCheck] = Field(min_length=1, max_length=50)

    @field_validator("dump_name")
    @classmethod
    def _basename_only(cls, v: Optional[str]) -> Optional[str]:
        # A full path would tell every workspace where the operator keeps
        # backups; the file name is enough to identify the dump.
        if v and ("/" in v or "\\" in v):
            raise ValueError("dump_name must be a file name, not a path")
        return v

    @field_validator("ran_at")
    @classmethod
    def _naive_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is not None:
            v = (v - v.utcoffset()).replace(tzinfo=None)
        if v > datetime.utcnow() + timedelta(minutes=10):
            raise ValueError("ran_at is in the future")
        return v

    @model_validator(mode="after")
    def _consistent_and_clean(self):
        if any(c.status == "FAIL" for c in self.checks) and self.status != "fail":
            raise ValueError("status must be 'fail' when any check failed")
        if self.status == "fail" and not any(c.status == "FAIL" for c in self.checks):
            raise ValueError("status 'fail' needs at least one FAIL check")
        texts = [self.dump_name, self.target] + [t for c in self.checks for t in (c.check, c.detail)]
        if any(looks_secret(t) for t in texts):
            raise ValueError("the report appears to contain a credential (password, "
                             "connection string or token); nothing was stored")
        return self

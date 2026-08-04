"""Base model + tiny shared result shapes."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class TQModel(BaseModel):
    """Every output model tolerates unknown backend fields (forward compat)."""
    model_config = ConfigDict(extra="ignore")


class OkResult(TQModel):
    status: str = "success"
    message: Optional[str] = None


class AcceptedResult(TQModel):
    """202-style acknowledgement for async server work. Poll the referenced
    resource (e.g. get_run / get_security_scan) for the outcome."""
    status: str
    run_id: Optional[int] = None
    provider_id: Optional[int] = None
    detail: Optional[str] = None


class ArtifactUrl(TQModel):
    url: str
    expires_in_seconds: Optional[int] = None

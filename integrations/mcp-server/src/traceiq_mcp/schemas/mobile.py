"""Mobile app builds (Appium executor)."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from traceiq_mcp.schemas.common import TQModel


class AppBuild(TQModel):
    id: int
    project_id: int
    platform: str
    app_name: str
    version_name: Optional[str] = None
    build_number: Optional[str] = None
    package_id: Optional[str] = None
    file_size: Optional[int] = None
    original_filename: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    download_url: Optional[str] = None


class AppBuildList(TQModel):
    items: List[AppBuild] = []

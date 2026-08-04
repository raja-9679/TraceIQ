"""Mobile app builds — pick a binary for mobile_appium runs.

Uploading a build stays in the UI/CLI (multipart binary); agents list the
registry and pin a build onto a run via run_suite(app_build_id=...).
"""
from __future__ import annotations

from traceiq_mcp.app import mcp
from traceiq_mcp.client import new_client
from traceiq_mcp.schemas.mobile import AppBuild, AppBuildList


@mcp.tool()
async def list_app_builds(project_id: int) -> AppBuildList:
    """List uploaded mobile app builds (APK/AAB/IPA) for a project. Pass a
    build's id as run_suite(app_build_id=...) to pin a mobile run to it."""
    data = await new_client().list_app_builds(project_id)
    return AppBuildList(items=data)


@mcp.tool()
async def get_app_build(build_id: int) -> AppBuild:
    """One app build's metadata incl. a presigned download URL."""
    data = await new_client().get_app_build(build_id)
    return AppBuild.model_validate(data)

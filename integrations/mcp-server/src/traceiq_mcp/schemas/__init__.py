"""Typed output models for every MCP tool.

Each tool's return annotation is one of these Pydantic models, which is what
gives the tool its published `outputSchema` and validated `structuredContent`.
All models ignore unknown fields (see TQModel) so additive backend changes
never break deployed MCP servers.
"""
from traceiq_mcp.schemas.common import TQModel, OkResult, ArtifactUrl, AcceptedResult  # noqa: F401

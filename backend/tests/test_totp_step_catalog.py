"""The totp step is advertised to the editor, MCP and agents via /api/step-types."""
from app.api.agent_reference import list_step_types


async def _totp():
    catalog = await list_step_types()
    return next((s for s in catalog["step_types"] if s["type"] == "totp"), None)


async def test_totp_is_in_the_catalog():
    step = await _totp()
    assert step is not None
    assert {"params.secret", "params.variableName"} <= set(step["params"])


async def test_totp_example_uses_a_project_secret_never_a_raw_seed():
    step = await _totp()
    assert step["example"]["params"]["secret"].startswith("{{secret.")
    assert "{{otp}}" in step["notes"]

"""Integration test packs (WP13c, decision C7): schema, step catalogue,
variable handling. A pack using an unknown step type fails here (and CI)."""
import copy
import json

import pytest

from app.services import test_packs as tp

PACKS = tp.list_packs()


def test_the_three_packs_ship():
    assert {p["id"] for p in PACKS} >= {"sso-oidc", "scim-2", "outbound-webhooks"}


@pytest.mark.parametrize("pack", PACKS, ids=[p["id"] for p in PACKS])
def test_every_shipped_pack_is_valid_against_the_step_catalogue(pack):
    assert tp.validate_pack(pack) == []


def test_ids_are_unique_and_filenames_match():
    ids = [p["id"] for p in PACKS]
    assert len(ids) == len(set(ids))
    for path in tp.PACK_DIR.glob("*.json"):
        assert json.loads(path.read_text())["id"] == path.stem


def _good():
    return copy.deepcopy(tp.get_pack("scim-2"))


def test_unknown_step_type_fails():
    pack = _good()
    pack["suites"][0]["cases"][0]["steps"][0]["type"] = "teleport"
    assert any("unknown step type 'teleport'" in e for e in tp.validate_pack(pack))


def test_undeclared_variable_fails():
    pack = _good()
    pack["suites"][0]["cases"][0]["steps"][0]["value"] = "{{env.NOPE}}/x"
    assert any("{{env.NOPE}} is used but not declared" in e for e in tp.validate_pack(pack))


def test_secret_must_be_referenced_as_secret():
    pack = _good()
    step = pack["suites"][0]["cases"][0]["steps"][0]
    step["params"]["headers"]["Authorization"] = "Bearer {{env.SCIM_TOKEN}}"
    errors = tp.validate_pack(pack)
    assert any("must be referenced as {{secret.SCIM_TOKEN}}" in e for e in errors)


def test_secret_defaults_and_unused_variables_fail():
    pack = _good()
    pack["variables"][1]["default"] = "hunter2"
    pack["variables"].append({"name": "UNUSED", "description": "x", "secret": False})
    errors = tp.validate_pack(pack)
    assert any("must not have a default" in e for e in errors)
    assert any("UNUSED is declared but never used" in e for e in errors)


def test_templates_in_assertion_values_fail():
    pack = _good()
    pack["suites"][0]["cases"][0]["steps"][0]["params"]["assertions"].append(
        {"type": "json-path", "path": "id", "operator": "equals", "value": "{{x}}"})
    assert any("compared verbatim" in e for e in tp.validate_pack(pack))


def test_split_values_routes_secrets_and_defaults():
    pack = tp.get_pack("scim-2")
    out = tp.split_values(pack, {"SCIM_BASE_URL": "https://scim.example.com/v2", "SCIM_TOKEN": "t0k"})
    assert out["env"] == {"SCIM_BASE_URL": "https://scim.example.com/v2", "SCIM_TEST_DOMAIN": "example.com"}
    assert out["secret"] == {"SCIM_TOKEN": "t0k"}
    assert out["defaults_used"] == ["SCIM_TEST_DOMAIN"]
    assert out["missing"] == []


def test_split_values_reports_missing_and_rejects_unknown():
    pack = tp.get_pack("scim-2")
    assert sorted(tp.split_values(pack, {})["missing"]) == ["SCIM_BASE_URL", "SCIM_TOKEN"]
    with pytest.raises(tp.PackError):
        tp.split_values(pack, {"EVIL": "x"})
    with pytest.raises(tp.PackError):
        tp.split_values(pack, {"SCIM_BASE_URL": "x" * 5000})


def test_summary_never_carries_secret_defaults():
    for pack in PACKS:
        for v in tp.pack_summary(pack)["variables"]:
            if v["secret"]:
                assert v["default"] is None


def test_import_data_has_fresh_step_ids_and_provenance():
    pack = tp.get_pack("sso-oidc")
    a, b = tp.suite_import_data(pack), tp.suite_import_data(pack)
    assert a["name"] == "SSO (OpenID Connect) pack v1.0.0"
    ids_a = [s["id"] for c in a["test_cases"] for s in c["steps"]]
    ids_b = [s["id"] for c in b["test_cases"] for s in c["steps"]]
    assert len(set(ids_a)) == len(ids_a) and not set(ids_a) & set(ids_b)
    assert a["settings"]["test_pack"]["id"] == "sso-oidc"
    assert all(c["description"] and c["tags"] for c in a["test_cases"])
    assert "id" not in pack["suites"][0]["cases"][0]["steps"][0]   # the pack itself is untouched


def test_export_variables_round_trip_the_declarations_without_secret_values():
    pack = tp.get_pack("outbound-webhooks")
    tree = tp.suite_import_data(pack)
    out = {v["name"]: v for v in tp.export_variables(tree)}
    assert set(out) == {v["name"] for v in pack["variables"]}
    assert out["APP_API_TOKEN"]["secret"] is True and "default" not in out["APP_API_TOKEN"]
    assert out["WEBHOOK_EVENT"]["default"] == "order.created"


def test_export_variables_derives_names_for_a_hand_built_suite():
    tree = {"settings": {"headers": {"Authorization": "Bearer {{secret.TOKEN}}"}},
            "test_cases": [{"steps": [{"type": "goto", "value": "{{env.BASE}}/x"}]}], "sub_modules": []}
    assert tp.export_variables(tree) == [{"name": "BASE", "secret": False},
                                         {"name": "TOKEN", "secret": True}]


def test_webhook_pack_only_uses_real_wait_for_webhook_params():
    from app.api.agent_reference import _STEP_TYPES
    entry = next(e for e in _STEP_TYPES if e["type"] == "wait-for-webhook")
    allowed = {k.split(".", 1)[1] for k in entry["params"]}
    pack = tp.get_pack("outbound-webhooks")
    for _, case in tp._iter_cases(pack):
        for step in case["steps"]:
            if step["type"] == "wait-for-webhook":
                assert set(step["params"]) <= allowed

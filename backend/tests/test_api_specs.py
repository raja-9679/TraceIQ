"""OpenAPI `expect_schema` resolution at dispatch (WP11)."""
import pytest

from app.services import api_specs

SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "Pets", "version": "1.2"},
    "paths": {
        "/pets/{id}": {"get": {"operationId": "getPet", "responses": {
            "200": {"description": "ok", "content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/Pet"}}}},
            "404": {"$ref": "#/components/responses/NotFound"}}}},
        "/pets": {"post": {"responses": {"201": {"description": "created", "content": {
            "application/json": {"schema": {"type": "object", "required": ["id"],
                                            "properties": {"id": {"type": "integer"}}}}}}}}},
    },
    "components": {
        "schemas": {
            "Pet": {"type": "object", "required": ["id", "name"],
                    "properties": {"id": {"type": "integer"}, "name": {"type": "string"},
                                   "parent": {"$ref": "#/components/schemas/Pet"},
                                   "tag": {"$ref": "#/components/schemas/Tag"}}},
            "Tag": {"type": "string"},
            "Unused": {"type": "number"},
            "Err": {"type": "object"},
        },
        "responses": {"NotFound": {"description": "nf", "content": {"application/json": {
            "schema": {"$ref": "#/components/schemas/Err"}}}}},
    },
}


def test_resolves_by_operation_id_with_only_reachable_components():
    out = api_specs.resolve_openapi_schema(SPEC, {"operationId": "getPet"})
    assert out["source"] == "openapi GET /pets/{id} 200"
    schemas = out["schema"]["components"]["schemas"]
    assert set(schemas) == {"Pet", "Tag"}           # recursive Pet visited once; Unused dropped
    assert out["schema"]["allOf"] == [{"$ref": "#/components/schemas/Pet"}]


def test_resolves_by_method_and_path_and_explicit_status():
    out = api_specs.resolve_openapi_schema(SPEC, {"method": "post", "path": "/pets"})
    assert out["schema"]["allOf"][0]["required"] == ["id"]
    nf = api_specs.resolve_openapi_schema(SPEC, {"operationId": "getPet"}, status="404")
    assert "Err" in nf["schema"]["components"]["schemas"]


@pytest.mark.parametrize("ref,status,msg", [
    ({"operationId": "nope"}, None, "operationId 'nope'"),
    ({"method": "GET", "path": "/missing"}, None, "GET /missing"),
    ({}, None, "needs operationId"),
    ({"operationId": "getPet"}, "500", "no '500' response"),
])
def test_unresolvable_references_say_why(ref, status, msg):
    with pytest.raises(api_specs.SchemaRefError, match=msg):
        api_specs.resolve_openapi_schema(SPEC, ref, status)


def test_external_refs_are_refused():
    spec = {"openapi": "3.0.0", "paths": {"/a": {"get": {"responses": {"200": {"content": {
        "application/json": {"schema": {"$ref": "https://evil.example/schema.json"}}}}}}}}}
    with pytest.raises(api_specs.SchemaRefError, match="external"):
        api_specs.resolve_openapi_schema(spec, {"method": "get", "path": "/a"})


def test_step_resolution_inlines_or_records_an_error_never_drops():
    calls = []

    def loader(pid):
        calls.append(pid)
        return SPEC
    api_specs._CACHE.clear()
    steps = [
        {"type": "goto", "value": "/"},
        {"type": "http-request", "params": {"expect_schema": {"openapi": {"operationId": "getPet"}}}},
        {"type": "http-request", "params": {"expect_schema": {"openapi": {"operationId": "zzz"}}}},
        {"type": "http-request", "params": {"expect_schema": {"schema": {"type": "object"}}}},
    ]
    out = api_specs.resolve_step_schemas(steps, 1, loader)
    assert out[0] is steps[0] and out[3] is steps[3]
    assert out[1]["params"]["expect_schema"]["schema"]["allOf"]
    assert out[1]["params"]["expect_schema"]["openapi"] == {"operationId": "getPet"}
    assert "not in the project's OpenAPI spec" in out[2]["params"]["expect_schema"]["error"]
    api_specs.resolve_step_schemas(steps, 1, loader)
    assert calls == [1]                                  # cached per project
    api_specs._CACHE.clear()
    none = api_specs.resolve_step_schemas(steps, 2, lambda pid: None)
    assert "no stored spec" in none[1]["params"]["expect_schema"]["error"]
    assert api_specs.resolve_step_schemas(steps[:1], 1, loader) == steps[:1]


def test_spec_summary_and_validation():
    s = api_specs.spec_summary(SPEC)
    assert s["title"] == "Pets" and {"method": "POST", "path": "/pets", "operationId": None} in s["operations"]
    with pytest.raises(api_specs.SchemaRefError):
        api_specs.validate_spec({"paths": {}})


def test_dispatch_payload_carries_the_resolved_schema(monkeypatch):
    from types import SimpleNamespace
    from app import worker
    api_specs._CACHE.clear()
    monkeypatch.setattr(api_specs, "load_spec_sync", lambda pid: SPEC if pid == 7 else None)
    case = SimpleNamespace(id=1, name="c", project_id=7, executor=None, raw_script=None, steps=[
        {"id": "a", "type": "http-request", "value": "https://x/pets/1",
         "params": {"expect_schema": {"openapi": {"operationId": "getPet"}}}}])
    payload = worker._case_payload(case)
    es = payload["steps"][0]["params"]["expect_schema"]
    assert es["source"] == "openapi GET /pets/{id} 200" and "Pet" in es["schema"]["components"]["schemas"]
    # The stored case is untouched — resolution happens on the job copy only.
    assert "schema" not in case.steps[0]["params"]["expect_schema"]

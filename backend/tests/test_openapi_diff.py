"""OpenAPI breaking-change diff (WP16b, V2): fixtures for every rule, and the
false positives a naive differ reports.

Run with:
    cd backend && ./run-tests.sh tests/test_openapi_diff.py -q
"""
import copy

import pytest

from app.services import openapi_diff as od
from app.services.sarif import build_spec_diff_sarif


def spec(paths=None, schemas=None, version="3.0.3", **extra):
    doc = {"openapi": version, "info": {"title": "Shop", "version": "1"}, "paths": paths or {}}
    if schemas:
        doc["components"] = {"schemas": schemas}
    doc.update(extra)
    return doc


def get_op(response_schema=None, status="200", params=None, op_id="getPet", **op):
    out = {"operationId": op_id,
           "responses": {status: {"description": "ok", "content": {
               "application/json": {"schema": response_schema or {"type": "object"}}}}}}
    if params is not None:
        out["parameters"] = params
    out.update(op)
    return out


def post_op(body_schema, required=True, op_id="createPet", **op):
    out = {"operationId": op_id,
           "requestBody": {"required": required, "content": {"application/json": {"schema": body_schema}}},
           "responses": {"201": {"description": "created"}}}
    out.update(op)
    return out


PET = {"type": "object", "required": ["id", "name"],
       "properties": {"id": {"type": "integer"}, "name": {"type": "string"},
                      "tag": {"type": "string"}}}


def changes(old, new):
    return od.diff_specs(old, new)["changes"]


def rules(old, new, severity=None):
    return sorted(c.rule for c in changes(old, new) if severity is None or c.severity == severity)


def breaking(old, new):
    return rules(old, new, od.BREAKING)


def nonbreaking(old, new):
    return rules(old, new, od.NON_BREAKING)


def one_get(schema, **kw):
    return spec({"/pets/{id}": {"get": get_op(schema, **kw)}})


def one_post(schema, **kw):
    return spec({"/pets": {"post": post_op(schema, **kw)}})


# ---------------------------------------------------------------------------
# Identity / false positives
# ---------------------------------------------------------------------------

def test_identical_specs_have_no_changes():
    s = one_get(PET)
    assert changes(s, copy.deepcopy(s)) == []


def test_renamed_ref_with_identical_shape_is_not_a_change():
    old = spec({"/pets/{id}": {"get": get_op({"$ref": "#/components/schemas/Pet"})}}, {"Pet": PET})
    new = spec({"/pets/{id}": {"get": get_op({"$ref": "#/components/schemas/Animal"})}}, {"Animal": PET})
    assert changes(old, new) == []


def test_inline_vs_ref_with_identical_shape_is_not_a_change():
    old = spec({"/pets/{id}": {"get": get_op(PET)}})
    new = spec({"/pets/{id}": {"get": get_op({"$ref": "#/components/schemas/Pet"})}}, {"Pet": PET})
    assert changes(old, new) == []


def test_reordered_enum_is_not_a_change():
    old = one_get({"type": "string", "enum": ["a", "b", "c"]})
    new = one_get({"type": "string", "enum": ["c", "a", "b"]})
    assert changes(old, new) == []


def test_reordered_required_list_and_properties_are_not_changes():
    old = one_get(PET)
    new = one_get({"type": "object", "required": ["name", "id"],
                   "properties": {"tag": {"type": "string"}, "name": {"type": "string"},
                                  "id": {"type": "integer"}}})
    assert changes(old, new) == []


def test_allof_composition_equal_to_flat_object_is_not_a_change():
    base = {"type": "object", "required": ["id"], "properties": {"id": {"type": "integer"}}}
    ext = {"type": "object", "required": ["name"],
           "properties": {"name": {"type": "string"}, "tag": {"type": "string"}}}
    old = one_get(PET)
    new = spec({"/pets/{id}": {"get": get_op({"allOf": [{"$ref": "#/components/schemas/Base"}, ext]})}},
               {"Base": base})
    assert changes(old, new) == []


def test_allof_wrapper_around_a_single_ref_is_not_a_change():
    old = spec({"/pets/{id}": {"get": get_op({"$ref": "#/components/schemas/Pet"})}}, {"Pet": PET})
    new = spec({"/pets/{id}": {"get": get_op({"allOf": [{"$ref": "#/components/schemas/Pet"}]})}}, {"Pet": PET})
    assert changes(old, new) == []


def test_openapi_30_nullable_equals_31_type_array():
    old = one_get({"type": "string", "nullable": True}, )
    new = spec({"/pets/{id}": {"get": get_op({"type": ["string", "null"]})}}, version="3.1.0")
    assert changes(old, new) == []


def test_renamed_path_parameter_is_not_a_change():
    p_old = {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}
    p_new = {"name": "petId", "in": "path", "required": True, "schema": {"type": "integer"}}
    old = spec({"/pets/{id}": {"get": get_op(PET, params=[p_old])}})
    new = spec({"/pets/{petId}": {"get": get_op(PET, params=[p_new])}})
    assert changes(old, new) == []


def test_header_parameter_names_are_case_insensitive():
    h = lambda n: {"name": n, "in": "header", "required": True, "schema": {"type": "string"}}  # noqa: E731
    old = one_get(PET, params=[h("X-Request-Id")])
    new = one_get(PET, params=[h("x-request-id")])
    assert changes(old, new) == []


def test_typeless_object_with_properties_is_inferred():
    old = one_get(PET)
    new = one_get({k: v for k, v in PET.items() if k != "type"})
    assert changes(old, new) == []


def test_description_only_changes_are_non_breaking():
    old = one_get(PET, summary="Get a pet")
    new_schema = copy.deepcopy(PET)
    new_schema["properties"]["name"]["description"] = "The pet's name"
    new = one_get(new_schema, summary="Fetch one pet")
    assert breaking(old, new) == []
    assert set(nonbreaking(old, new)) == {"description_changed"}


def test_recursive_schema_terminates_and_compares():
    node = {"type": "object", "properties": {"name": {"type": "string"},
                                            "children": {"type": "array", "items": {"$ref": "#/components/schemas/Node"}}}}
    old = spec({"/tree": {"get": get_op({"$ref": "#/components/schemas/Node"})}}, {"Node": node})
    new_node = copy.deepcopy(node)
    del new_node["properties"]["name"]
    new = spec({"/tree": {"get": get_op({"$ref": "#/components/schemas/Node"})}}, {"Node": new_node})
    assert changes(old, copy.deepcopy(old)) == []
    assert breaking(old, new) == ["response_property_removed"]


def test_ref_cycle_without_structure_does_not_hang():
    old = spec({"/x": {"get": get_op({"$ref": "#/components/schemas/A"})}},
               {"A": {"$ref": "#/components/schemas/B"}, "B": {"$ref": "#/components/schemas/A"}})
    assert od.diff_specs(old, copy.deepcopy(old))["breaking_count"] == 0


def test_external_ref_is_reported_not_followed():
    old = one_get({"$ref": "https://example.com/pet.json"})
    new = one_get({"$ref": "https://example.com/pet-v2.json"})
    assert nonbreaking(old, new) == ["unresolved_ref"]
    assert breaking(old, new) == []


def test_reordered_oneof_variants_are_not_a_change():
    a, b = {"type": "string"}, {"type": "integer"}
    assert changes(one_get({"oneOf": [a, b]}), one_get({"oneOf": [b, a]})) == []


# ---------------------------------------------------------------------------
# Operations and responses
# ---------------------------------------------------------------------------

def test_removed_operation_is_breaking_added_is_not():
    old = spec({"/pets/{id}": {"get": get_op(PET), "delete": get_op(None, op_id="del")}})
    new = spec({"/pets/{id}": {"get": get_op(PET)}, "/pets": {"post": post_op(PET)}})
    assert breaking(old, new) == ["operation_removed"]
    assert nonbreaking(old, new) == ["operation_added"]


def test_removed_success_status_is_breaking_removed_error_status_is_not():
    op_old = get_op(PET)
    op_old["responses"]["404"] = {"description": "missing"}
    old = spec({"/pets/{id}": {"get": op_old}})
    op_new = get_op(PET, status="202")
    new = spec({"/pets/{id}": {"get": op_new}})
    out = changes(old, new)
    removed = {c.status: c.severity for c in out if c.rule == "response_status_removed"}
    assert removed == {"200": od.BREAKING, "404": od.NON_BREAKING}
    assert "response_status_added" in nonbreaking(old, new)


def test_removed_response_media_type_is_breaking():
    old = one_get(PET)
    new = copy.deepcopy(old)
    content = new["paths"]["/pets/{id}"]["get"]["responses"]["200"]["content"]
    content["application/xml"] = content.pop("application/json")
    assert "response_media_type_removed" in breaking(old, new)
    assert "response_media_type_added" in nonbreaking(old, new)


def test_operation_deprecated_and_operation_id_changed_are_non_breaking_on_the_wire():
    old = one_get(PET)
    new = one_get(PET, op_id="fetchPet", deprecated=True)
    assert breaking(old, new) == []
    assert nonbreaking(old, new) == ["operation_deprecated", "operation_id_changed"]


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

def test_removed_response_property_is_breaking():
    new = copy.deepcopy(PET)
    del new["properties"]["tag"]
    assert breaking(one_get(PET), one_get(new)) == ["response_property_removed"]


def test_added_response_property_is_non_breaking():
    new = copy.deepcopy(PET)
    new["properties"]["age"] = {"type": "integer"}
    assert breaking(one_get(PET), one_get(new)) == []
    assert nonbreaking(one_get(PET), one_get(new)) == ["response_property_added"]


def test_response_property_no_longer_required_is_breaking():
    new = copy.deepcopy(PET)
    new["required"] = ["id"]
    assert breaking(one_get(PET), one_get(new)) == ["response_property_became_optional"]


def test_response_property_becoming_required_is_non_breaking():
    new = copy.deepcopy(PET)
    new["required"] = ["id", "name", "tag"]
    assert nonbreaking(one_get(PET), one_get(new)) == ["response_property_became_required"]


def test_nested_response_property_removal_has_a_location():
    old = one_get({"type": "object", "properties": {"owner": PET}})
    new_pet = copy.deepcopy(PET)
    del new_pet["properties"]["name"]
    new = one_get({"type": "object", "properties": {"owner": new_pet}})
    (c,) = [c for c in changes(old, new) if c.severity == od.BREAKING]
    assert c.location == "responses/200/application/json.owner.name"
    assert c.status == "200"


def test_shared_component_change_is_reported_at_every_status_using_it():
    op_old = get_op({"$ref": "#/components/schemas/Pet"})
    op_old["responses"]["201"] = {"description": "x", "content": {"application/json": {
        "schema": {"$ref": "#/components/schemas/Pet"}}}}
    new_pet = copy.deepcopy(PET)
    del new_pet["properties"]["name"]
    old = spec({"/pets/{id}": {"get": op_old}}, {"Pet": PET})
    new = spec({"/pets/{id}": {"get": copy.deepcopy(op_old)}}, {"Pet": new_pet})
    assert sorted(c.status for c in changes(old, new)) == ["200", "201"]


def test_array_items_are_compared():
    new_pet = copy.deepcopy(PET)
    del new_pet["properties"]["id"]
    old = one_get({"type": "array", "items": PET})
    new = one_get({"type": "array", "items": new_pet})
    assert breaking(old, new) == ["response_property_removed"]


def test_response_type_change_is_breaking():
    old = one_get({"type": "object", "properties": {"id": {"type": "integer"}}})
    new = one_get({"type": "object", "properties": {"id": {"type": "string"}}})
    assert breaking(old, new) == ["type_changed"]


def test_response_integer_to_number_is_widening_and_breaking():
    old = one_get({"type": "integer"})
    new = one_get({"type": "number"})
    assert breaking(old, new) == ["type_widened"]


def test_response_number_to_integer_is_narrowing_and_safe():
    assert breaking(one_get({"type": "number"}), one_get({"type": "integer"})) == []
    assert nonbreaking(one_get({"type": "number"}), one_get({"type": "integer"})) == ["type_narrowed"]


def test_response_becoming_nullable_is_breaking():
    old = one_get({"type": "string"})
    assert breaking(old, one_get({"type": "string", "nullable": True})) == ["became_nullable"]
    new31 = spec({"/pets/{id}": {"get": get_op({"type": ["string", "null"]})}}, version="3.1.0")
    assert breaking(old, new31) == ["became_nullable"]


def test_response_nullable_to_non_nullable_is_safe():
    old = one_get({"type": "string", "nullable": True})
    assert breaking(old, one_get({"type": "string"})) == []
    assert nonbreaking(old, one_get({"type": "string"})) == ["became_non_nullable"]


def test_response_enum_value_added_is_non_breaking_and_says_why():
    old = one_get({"type": "string", "enum": ["a"]})
    new = one_get({"type": "string", "enum": ["a", "b"]})
    (c,) = changes(old, new)
    assert (c.rule, c.severity) == ("enum_value_added", od.NON_BREAKING)
    assert "closed enum" in c.message


def test_response_enum_value_removed_is_non_breaking():
    old = one_get({"type": "string", "enum": ["a", "b"]})
    new = one_get({"type": "string", "enum": ["a"]})
    assert nonbreaking(old, new) == ["enum_value_removed"]


def test_response_format_removed_is_breaking_added_is_not():
    assert breaking(one_get({"type": "string", "format": "date"}), one_get({"type": "string"})) == ["format_changed"]
    assert breaking(one_get({"type": "string"}), one_get({"type": "string", "format": "date"})) == []


def test_response_int32_to_int64_is_breaking():
    old = one_get({"type": "integer", "format": "int32"})
    new = one_get({"type": "integer", "format": "int64"})
    assert breaking(old, new) == ["format_changed"]


def test_response_oneof_variant_added_is_breaking():
    old = one_get({"oneOf": [{"type": "string"}]})
    new = one_get({"oneOf": [{"type": "string"}, {"type": "integer"}]})
    assert breaking(old, new) == ["variant_added"]


def test_schema_becoming_one_variant_of_a_oneof_reports_only_the_new_variant():
    other = {"type": "object", "properties": {"code": {"type": "string"}}}
    old = one_get(PET)
    new = one_get({"oneOf": [PET, other]})
    assert breaking(old, new) == ["variant_added"]


def test_write_only_property_removed_from_response_is_ignored():
    old_pet = copy.deepcopy(PET)
    old_pet["properties"]["password"] = {"type": "string", "writeOnly": True}
    assert changes(one_get(old_pet), one_get(PET)) == []


def test_swagger2_response_schema_is_compared():
    old = {"swagger": "2.0", "paths": {"/p": {"get": {"responses": {"200": {"schema": PET}}}}}}
    new_pet = copy.deepcopy(PET)
    del new_pet["properties"]["name"]
    new = {"swagger": "2.0", "paths": {"/p": {"get": {"responses": {"200": {"schema": new_pet}}}}}}
    assert breaking(old, new) == ["response_property_removed"]


# ---------------------------------------------------------------------------
# Request bodies and parameters
# ---------------------------------------------------------------------------

def test_new_required_request_property_is_breaking():
    new = copy.deepcopy(PET)
    new["properties"]["owner"] = {"type": "string"}
    new["required"].append("owner")
    assert breaking(one_post(PET), one_post(new)) == ["request_property_required_added"]


def test_new_optional_request_property_is_non_breaking():
    new = copy.deepcopy(PET)
    new["properties"]["owner"] = {"type": "string"}
    assert breaking(one_post(PET), one_post(new)) == []
    assert nonbreaking(one_post(PET), one_post(new)) == ["request_property_added"]


def test_request_property_becoming_required_is_breaking():
    new = copy.deepcopy(PET)
    new["required"].append("tag")
    assert breaking(one_post(PET), one_post(new)) == ["request_property_became_required"]


def test_new_required_read_only_property_does_not_break_requests():
    new = copy.deepcopy(PET)
    new["properties"]["created_at"] = {"type": "string", "readOnly": True}
    new["required"].append("created_at")
    assert breaking(one_post(PET), one_post(new)) == []


def test_removed_request_property_is_non_breaking_unless_closed():
    new = copy.deepcopy(PET)
    del new["properties"]["tag"]
    assert nonbreaking(one_post(PET), one_post(new)) == ["request_property_removed"]
    closed = copy.deepcopy(new)
    closed["additionalProperties"] = False
    assert breaking(one_post(PET), one_post(closed)) == ["additional_properties_disallowed",
                                                        "request_property_removed"]


def test_request_enum_value_removed_is_breaking_added_is_not():
    old = one_post({"type": "string", "enum": ["a", "b"]})
    assert breaking(old, one_post({"type": "string", "enum": ["a"]})) == ["enum_value_removed"]
    assert breaking(old, one_post({"type": "string", "enum": ["a", "b", "c"]})) == []


def test_request_enum_introduced_is_breaking():
    assert breaking(one_post({"type": "string"}), one_post({"type": "string", "enum": ["a"]})) == ["enum_added"]


def test_request_type_narrowed_and_widened():
    assert breaking(one_post({"type": "number"}), one_post({"type": "integer"})) == ["type_narrowed"]
    assert breaking(one_post({"type": "integer"}), one_post({"type": "number"})) == []
    assert breaking(one_post({"type": ["string", "integer"]}), one_post({"type": "string"})) == ["type_narrowed"]


def test_request_nullable_to_non_nullable_is_breaking():
    assert breaking(one_post({"type": "string", "nullable": True}), one_post({"type": "string"})) == ["became_non_nullable"]
    assert breaking(one_post({"type": "string"}), one_post({"type": "string", "nullable": True})) == []


def test_request_format_narrowing_is_breaking():
    assert breaking(one_post({"type": "string"}), one_post({"type": "string", "format": "uuid"})) == ["format_changed"]
    assert breaking(one_post({"type": "integer", "format": "int64"}),
                    one_post({"type": "integer", "format": "int32"})) == ["format_changed"]
    assert breaking(one_post({"type": "integer", "format": "int32"}),
                    one_post({"type": "integer", "format": "int64"})) == []
    assert breaking(one_post({"type": "string", "format": "date"}),
                    one_post({"type": "string", "format": "date-time"})) == ["format_changed"]


def test_request_constraints():
    assert breaking(one_post({"type": "string", "maxLength": 100}),
                    one_post({"type": "string", "maxLength": 50})) == ["constraint_narrowed"]
    assert breaking(one_post({"type": "string", "maxLength": 50}),
                    one_post({"type": "string", "maxLength": 100})) == []
    assert breaking(one_post({"type": "integer"}), one_post({"type": "integer", "minimum": 1})) == ["constraint_narrowed"]
    # A 3.0 boolean exclusiveMinimum is a modifier, not a bound.
    assert breaking(one_post({"type": "integer", "minimum": 1}),
                    one_post({"type": "integer", "minimum": 1, "exclusiveMinimum": False})) == []


def test_request_oneof_variant_removed_is_breaking():
    old = one_post({"oneOf": [{"type": "string"}, {"type": "integer"}]})
    new = one_post({"oneOf": [{"type": "string"}]})
    assert breaking(old, new) == ["variant_removed"]


def test_request_body_rules():
    no_body = spec({"/pets": {"post": {"operationId": "createPet", "responses": {"201": {"description": "c"}}}}})
    assert breaking(no_body, one_post(PET, required=True)) == ["request_body_added_required"]
    assert nonbreaking(no_body, one_post(PET, required=False)) == ["request_body_added_optional"]
    assert breaking(one_post(PET, required=False), one_post(PET, required=True)) == ["request_body_became_required"]
    assert nonbreaking(one_post(PET), no_body) == ["request_body_removed"]


def test_request_media_type_removed_is_breaking():
    old = one_post(PET)
    new = copy.deepcopy(old)
    c = new["paths"]["/pets"]["post"]["requestBody"]["content"]
    c["application/x-www-form-urlencoded"] = c.pop("application/json")
    assert breaking(old, new) == ["request_media_type_removed"]


def test_new_required_parameter_is_breaking_optional_is_not():
    q = lambda req: {"name": "limit", "in": "query", "required": req, "schema": {"type": "integer"}}  # noqa: E731
    assert breaking(one_get(PET), one_get(PET, params=[q(True)])) == ["required_parameter_added"]
    assert nonbreaking(one_get(PET), one_get(PET, params=[q(False)])) == ["optional_parameter_added"]
    assert breaking(one_get(PET, params=[q(False)]), one_get(PET, params=[q(True)])) == ["parameter_became_required"]
    assert nonbreaking(one_get(PET, params=[q(False)]), one_get(PET)) == ["parameter_removed"]


def test_parameter_schema_is_compared_as_request():
    q = lambda s: {"name": "kind", "in": "query", "schema": s}  # noqa: E731
    assert breaking(one_get(PET, params=[q({"type": "string", "enum": ["a", "b"]})]),
                    one_get(PET, params=[q({"type": "string", "enum": ["a"]})])) == ["enum_value_removed"]


def test_path_level_parameters_are_merged_and_overridden():
    q = {"name": "v", "in": "query", "required": False, "schema": {"type": "string"}}
    q_req = {**q, "required": True}
    old = spec({"/pets/{id}": {"parameters": [q], "get": get_op(PET)}})
    new = spec({"/pets/{id}": {"parameters": [q], "get": get_op(PET, params=[q_req])}})
    assert breaking(old, new) == ["parameter_became_required"]


def test_parameter_refs_are_resolved():
    p = {"name": "limit", "in": "query", "schema": {"type": "integer"}}
    old = spec({"/pets/{id}": {"get": get_op(PET, params=[p])}})
    new = spec({"/pets/{id}": {"get": get_op(PET, params=[{"$ref": "#/components/parameters/Limit"}])}},
               components={"parameters": {"Limit": p}})
    assert changes(old, new) == []


def test_swagger2_body_and_query_parameters():
    def sw(body, query):
        return {"swagger": "2.0", "paths": {"/p": {"post": {
            "parameters": [{"name": "body", "in": "body", "required": True, "schema": body}, query],
            "responses": {"200": {"description": "ok"}}}}}}
    q = {"name": "mode", "in": "query", "type": "string", "enum": ["a", "b"]}
    new_body = copy.deepcopy(PET)
    new_body["required"].append("tag")
    assert breaking(sw(PET, q), sw(new_body, {**q, "enum": ["a"]})) == [
        "enum_value_removed", "request_property_became_required"]


# ---------------------------------------------------------------------------
# Ordering, bounds, ids
# ---------------------------------------------------------------------------

def test_breaking_changes_come_first_and_ids_are_stable():
    new = copy.deepcopy(PET)
    del new["properties"]["tag"]
    new["properties"]["age"] = {"type": "integer"}
    out = od.diff_specs(one_get(PET), one_get(new))
    assert [c.severity for c in out["changes"]] == [od.BREAKING, od.NON_BREAKING]
    again = od.diff_specs(one_get(PET), one_get(new))
    assert [c.id for c in out["changes"]] == [c.id for c in again["changes"]]
    assert out["breaking_operations"] == [{"method": "GET", "path": "/pets/{id}"}]


def test_huge_specs_are_bounded(monkeypatch):
    monkeypatch.setattr(od, "MAX_CHANGES", 5)
    old = spec({f"/p{i}": {"get": get_op(PET, op_id=f"o{i}")} for i in range(20)})
    out = od.diff_specs(old, spec({}))
    assert out["truncated"] is True and len(out["changes"]) == 5


def test_garbage_input_does_not_raise():
    assert od.diff_specs({}, {"paths": {"/x": "nope", "/y": {"get": "nope"}}})["changes"] == []
    assert od.diff_specs(None, None)["changes"] == []


# ---------------------------------------------------------------------------
# Linking to tests
# ---------------------------------------------------------------------------

def _case(cid, ref, status=None, name=None):
    es = {"openapi": ref}
    if status:
        es["status"] = status
    return {"id": cid, "name": name or f"case {cid}", "test_suite_id": 1,
            "steps": [{"type": "http-request", "params": {"expect_schema": es}}]}


def test_breaking_changes_name_the_tests_that_reference_the_operation():
    new = copy.deepcopy(PET)
    del new["properties"]["name"]
    old_s, new_s = one_get(PET), one_get(new)
    cases = [_case(1, {"operationId": "getPet"}), _case(2, {"method": "get", "path": "/pets/{petId}"}),
             _case(3, {"operationId": "other"}), {"id": 4, "name": "plain", "steps": [{"type": "goto"}]}]
    out = od.link_cases(od.diff_specs(old_s, new_s), old_s, new_s, cases)
    assert [t["case_id"] for t in out["affected_tests"]] == [1, 2]
    (c,) = out["changes"]
    assert [a["case_id"] for a in c.affected_cases] == [1, 2]


def test_tests_pinned_to_another_status_are_not_affected():
    op_old = get_op(PET)
    op_old["responses"]["201"] = {"description": "x", "content": {"application/json": {"schema": copy.deepcopy(PET)}}}
    op_new = copy.deepcopy(op_old)
    del op_new["responses"]["201"]["content"]["application/json"]["schema"]["properties"]["tag"]
    old_s, new_s = spec({"/pets/{id}": {"get": op_old}}), spec({"/pets/{id}": {"get": op_new}})
    out = od.link_cases(od.diff_specs(old_s, new_s), old_s, new_s,
                        [_case(1, {"operationId": "getPet"}, status="200"),
                         _case(2, {"operationId": "getPet"}, status="201")])
    assert [t["case_id"] for t in out["affected_tests"]] == [2]


def test_removed_error_status_pinned_by_a_test_is_promoted_to_breaking():
    op_old = get_op(PET)
    op_old["responses"]["404"] = {"description": "missing"}
    old_s, new_s = spec({"/pets/{id}": {"get": op_old}}), one_get(PET)
    base = od.diff_specs(old_s, new_s)
    assert base["breaking_count"] == 0
    out = od.link_cases(base, old_s, new_s, [_case(9, {"operationId": "getPet"}, status="404")])
    assert out["breaking_count"] == 1
    (c,) = out["changes"]
    assert c.promoted_reason and c.affected_cases[0]["case_id"] == 9


def test_operation_id_rename_breaks_tests_that_use_the_old_id():
    old_s, new_s = one_get(PET), one_get(PET, op_id="fetchPet")
    out = od.link_cases(od.diff_specs(old_s, new_s), old_s, new_s,
                        [_case(1, {"operationId": "getPet"}), _case(2, {"method": "GET", "path": "/pets/{id}"})])
    (c,) = out["changes"]
    assert c.severity == od.BREAKING and [a["case_id"] for a in c.affected_cases] == [1]


def test_removed_operation_links_by_old_spec():
    old_s = one_get(PET)
    out = od.link_cases(od.diff_specs(old_s, spec({})), old_s, spec({}), [_case(5, {"operationId": "getPet"})])
    assert out["affected_tests"][0]["operations"] == ["GET /pets/{id}"]


def test_case_references_ignores_malformed_steps():
    assert od.case_references([None, "x", {"params": None}, {"params": {"expect_schema": {"schema": {}}}}]) == []


# ---------------------------------------------------------------------------
# SARIF
# ---------------------------------------------------------------------------

def test_sarif_export_has_one_result_per_change_and_levels():
    new = copy.deepcopy(PET)
    del new["properties"]["tag"]
    new["properties"]["age"] = {"type": "integer"}
    old_s, new_s = one_get(PET), one_get(new)
    out = od.link_cases(od.diff_specs(old_s, new_s), old_s, new_s, [_case(1, {"operationId": "getPet"})])
    log = build_spec_diff_sarif(od.to_json(out), artifact_uri="openapi.json", from_version=1, to_version=2)
    assert log["version"] == "2.1.0"
    run = log["runs"][0]
    assert [r["level"] for r in run["results"]] == ["error", "note"]
    rule_ids = {r["id"] for r in run["tool"]["driver"]["rules"]}
    assert rule_ids == {"openapi/response_property_removed", "openapi/response_property_added"}
    first = run["results"][0]
    assert first["locations"][0]["logicalLocations"][0]["name"] == "GET /pets/{id}"
    assert first["properties"]["affected_case_ids"] == [1]
    assert first["partialFingerprints"]
    assert run["automationDetails"]["id"] == "openapi-diff/v1..v2"


@pytest.mark.parametrize("rule", sorted(od.RULES))
def test_every_rule_has_an_explanation(rule):
    assert od.RULES[rule].endswith(".")

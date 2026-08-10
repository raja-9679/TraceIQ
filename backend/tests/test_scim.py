"""SCIM 2.0 protocol mapping — workstream F2.

Why this exists: TraceIQ had no deprovisioning path of any kind. Someone removed
from Okta or Entra kept their account, their sessions and their refresh token
indefinitely. That is the single item most likely to fail an IT security review
outright, and no amount of SSO polish substitutes for it.

The wire format is the hard part, not the database work. Real IdPs disagree with
each other and with the RFC:

  * Okta sends `{"op": "replace", "value": {"active": false}}` — no `path`.
  * Entra sends `{"op": "Replace", "path": "active", "value": "False"}` —
    capitalised op, and the boolean is a *string*.
  * Both send `filter=userName eq "someone@corp.example.com"` for lookup, which
    is the only filter either of them needs.

Treating `"False"` as truthy would leave a deprovisioned user active — a silent
failure in exactly the direction that matters. So the parsing is pure, and
tested against the shapes real providers actually send rather than the shape the
spec suggests.

The DB-touching half (does deactivation revoke sessions? do Groups map to
teams?) is in tests/integration/test_scim_db.py, against a real Postgres.
"""
import pytest

from app.services.scim import (
    ScimError,
    group_to_scim,
    list_response,
    parse_filter,
    parse_patch,
    user_to_scim,
)


class _User:
    """Minimal stand-in: the mapper must not need a live ORM row."""

    def __init__(self, **kw):
        self.id = kw.get("id", 7)
        self.email = kw.get("email", "dana@corp.example.com")
        self.full_name = kw.get("full_name", "Dana Federated")
        self.is_active = kw.get("is_active", True)
        self.scim_external_id = kw.get("scim_external_id", "okta-0001")
        self.created_at = None


# --- User serialisation ------------------------------------------------------

def test_user_resource_carries_the_scim_user_schema():
    doc = user_to_scim(_User())
    assert doc["schemas"] == ["urn:ietf:params:scim:schemas:core:2.0:User"]


def test_user_name_is_the_email():
    # Okta and Entra both key on userName, and email is the only identifier
    # TraceIQ guarantees is unique.
    assert user_to_scim(_User())["userName"] == "dana@corp.example.com"


def test_id_is_a_string():
    # SCIM ids are strings; a client that round-trips an integer will send it
    # back as one and break path matching.
    assert user_to_scim(_User(id=42))["id"] == "42"


def test_external_id_is_echoed_back():
    # Okta reconciles on externalId. Dropping it makes every sync look like a
    # new user.
    assert user_to_scim(_User(scim_external_id="okta-0001"))["externalId"] == "okta-0001"


def test_external_id_is_absent_when_unknown():
    assert "externalId" not in user_to_scim(_User(scim_external_id=None))


def test_active_reflects_is_active():
    assert user_to_scim(_User(is_active=False))["active"] is False


def test_name_is_split_into_given_and_family():
    doc = user_to_scim(_User(full_name="Dana Federated"))
    assert doc["name"]["givenName"] == "Dana"
    assert doc["name"]["familyName"] == "Federated"
    assert doc["name"]["formatted"] == "Dana Federated"


def test_single_word_name_has_no_family_name():
    doc = user_to_scim(_User(full_name="Prince"))
    assert doc["name"]["givenName"] == "Prince"
    assert doc["name"].get("familyName", "") == ""


def test_email_is_primary_and_work():
    assert user_to_scim(_User())["emails"] == [
        {"value": "dana@corp.example.com", "type": "work", "primary": True}]


def test_meta_declares_the_resource_type():
    assert user_to_scim(_User())["meta"]["resourceType"] == "User"


def test_group_resource_shape():
    doc = group_to_scim(team_id=3, display_name="QA Team",
                        member_ids=[7, 9], external_id=None)
    assert doc["schemas"] == ["urn:ietf:params:scim:schemas:core:2.0:Group"]
    assert doc["id"] == "3"
    assert doc["displayName"] == "QA Team"
    assert doc["members"] == [{"value": "7"}, {"value": "9"}]


# --- List envelope -----------------------------------------------------------

def test_list_response_envelope():
    env = list_response([{"id": "1"}], total=1, start_index=1, count=100)
    assert env["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:ListResponse"]
    assert env["totalResults"] == 1
    assert env["startIndex"] == 1
    assert env["itemsPerPage"] == 1
    assert env["Resources"] == [{"id": "1"}]


def test_empty_list_response_is_not_an_error():
    # A lookup miss is how an IdP asks "does this user exist?" — 200 with zero
    # results, never 404.
    env = list_response([], total=0, start_index=1, count=100)
    assert env["totalResults"] == 0 and env["Resources"] == []


# --- Filters -----------------------------------------------------------------

def test_parse_userName_filter():
    assert parse_filter('userName eq "dana@corp.example.com"') == (
        "userName", "dana@corp.example.com")


def test_parse_filter_is_case_insensitive_on_attribute_and_operator():
    assert parse_filter('USERNAME EQ "dana@corp.example.com"') == (
        "userName", "dana@corp.example.com")


def test_parse_externalId_filter():
    assert parse_filter('externalId eq "okta-1"') == ("externalId", "okta-1")


def test_parse_displayName_filter():
    assert parse_filter('displayName eq "QA Team"') == ("displayName", "QA Team")


def test_absent_filter_is_none():
    assert parse_filter(None) is None and parse_filter("") is None


def test_unsupported_filter_is_rejected():
    # Silently ignoring a filter we don't understand would return the whole
    # directory to a client that asked for one user, and the client would then
    # act on the first row.
    with pytest.raises(ScimError) as raised:
        parse_filter('userName co "dana"')
    assert raised.value.status == 400


def test_unsupported_attribute_is_rejected():
    with pytest.raises(ScimError):
        parse_filter('nickName eq "dana"')


# --- PATCH: the shapes real IdPs send ----------------------------------------

def test_okta_style_deactivation_without_a_path():
    ops = parse_patch({"schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                       "Operations": [{"op": "replace", "value": {"active": False}}]})
    assert ops.active is False


def test_entra_style_deactivation_with_a_stringified_boolean():
    # The bug this guards: bool("False") is True, so a naive implementation
    # leaves a deprovisioned user active.
    ops = parse_patch({"Operations": [
        {"op": "Replace", "path": "active", "value": "False"}]})
    assert ops.active is False


def test_stringified_true_reactivates():
    ops = parse_patch({"Operations": [
        {"op": "Replace", "path": "active", "value": "True"}]})
    assert ops.active is True


def test_native_boolean_path_form():
    ops = parse_patch({"Operations": [
        {"op": "replace", "path": "active", "value": True}]})
    assert ops.active is True


def test_patch_without_an_active_op_leaves_it_unset():
    ops = parse_patch({"Operations": [
        {"op": "replace", "path": "name.givenName", "value": "Dana"}]})
    assert ops.active is None


def test_patch_updates_display_name():
    ops = parse_patch({"Operations": [
        {"op": "replace", "path": "displayName", "value": "Dana F"}]})
    assert ops.display_name == "Dana F"


def test_patch_reads_name_from_a_valueless_path():
    ops = parse_patch({"Operations": [
        {"op": "replace", "value": {"name": {"formatted": "Dana Fed"}}}]})
    assert ops.display_name == "Dana Fed"


def test_group_member_add_and_remove():
    ops = parse_patch({"Operations": [
        {"op": "add", "path": "members", "value": [{"value": "7"}, {"value": "9"}]},
        {"op": "remove", "path": "members", "value": [{"value": "4"}]}]})
    assert ops.add_members == ["7", "9"]
    assert ops.remove_members == ["4"]


def test_group_member_remove_via_filtered_path():
    # Entra removes one member with path='members[value eq "7"]' and no value.
    ops = parse_patch({"Operations": [
        {"op": "remove", "path": 'members[value eq "7"]'}]})
    assert ops.remove_members == ["7"]


def test_replacing_members_wholesale_is_captured_as_a_set():
    ops = parse_patch({"Operations": [
        {"op": "replace", "path": "members", "value": [{"value": "7"}]}]})
    assert ops.replace_members == ["7"]


def test_patch_with_no_operations_is_rejected():
    with pytest.raises(ScimError) as raised:
        parse_patch({"schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"]})
    assert raised.value.status == 400


def test_unknown_op_verb_is_rejected():
    with pytest.raises(ScimError):
        parse_patch({"Operations": [{"op": "frobnicate", "path": "active"}]})


# --- Error envelope ----------------------------------------------------------

def test_error_body_shape():
    body = ScimError(404, "not found").to_dict()
    assert body["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
    assert body["status"] == "404"
    assert body["detail"] == "not found"


def test_conflict_carries_the_uniqueness_scim_type():
    # 409 without scimType=uniqueness makes Okta retry the create forever.
    assert ScimError(409, "exists", scim_type="uniqueness").to_dict()["scimType"] == \
        "uniqueness"

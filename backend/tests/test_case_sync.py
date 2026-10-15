"""Git sync classification and hashing (WP11)."""
from types import SimpleNamespace

from app.services import case_sync as cs


def _case(**kw):
    base = dict(id=1, test_suite_id=10, name="login", steps=[{"id": "s1", "type": "goto", "value": "/"}],
                tags=["smoke"], priority=None, code_paths=[])
    base.update(kw)
    return SimpleNamespace(**base)


def _local(server, **changes):
    doc = {k: server[k] for k in ("id", "suite_id", "name", "steps", "tags", "priority", "code_paths")}
    doc.update(base_hash=server["hash"], base_suite_id=server["suite_id"])
    doc.update(changes)
    return doc


def test_hash_is_stable_across_key_order_and_defaults():
    a = cs.content_hash({"name": "x", "steps": [{"b": 1, "a": 2}], "tags": None})
    b = cs.content_hash({"steps": [{"a": 2, "b": 1}], "name": "x", "tags": [], "code_paths": []})
    assert a == b and a.startswith("sha256:")
    assert cs.content_hash({"name": "y"}) != a


def test_untouched_doc_is_unchanged():
    server = cs.case_doc(_case())
    assert cs.classify(server, _local(server))["kind"] == "unchanged"


def test_local_edit_on_unchanged_server_is_an_update_with_only_changed_fields():
    server = cs.case_doc(_case())
    v = cs.classify(server, _local(server, name="login v2"))
    assert v["kind"] == "update" and v["changes"] == {"name": "login v2"} and v["move_to"] is None
    assert v["base_hash"] == server["hash"]


def test_server_edit_since_pull_is_a_conflict():
    pulled = cs.case_doc(_case())
    server_now = cs.case_doc(_case(name="renamed in UI"))
    v = cs.classify(server_now, _local(pulled, steps=[]))
    assert v["kind"] == "conflict" and "changed on the server" in v["reason"]


def test_same_edit_on_both_sides_is_not_a_conflict():
    pulled = cs.case_doc(_case())
    server_now = cs.case_doc(_case(name="same"))
    assert cs.classify(server_now, _local(pulled, name="same"))["kind"] == "unchanged"


def test_move_is_proposed_and_server_move_conflicts():
    server = cs.case_doc(_case())
    v = cs.classify(server, _local(server, suite_id=11))
    assert v["kind"] == "update" and v["move_to"] == 11 and v["changes"] == {}
    moved_on_server = cs.case_doc(_case(test_suite_id=12))
    v = cs.classify(moved_on_server, {**_local(server, suite_id=11)})
    assert v["kind"] == "conflict" and "moved" in v["reason"]


def test_missing_base_hash_or_server_case_is_a_conflict():
    server = cs.case_doc(_case())
    doc = _local(server, name="x")
    doc.pop("base_hash")
    assert cs.classify(server, doc)["kind"] == "conflict"
    assert cs.classify(None, doc)["kind"] == "conflict"


def test_validate_doc():
    assert cs.validate_doc({"name": "a", "steps": []}) is None
    assert "name" in cs.validate_doc({"steps": []})
    assert "steps" in cs.validate_doc({"name": "a", "steps": ["x"]})
    assert "tags" in cs.validate_doc({"name": "a", "tags": "smoke"})
    assert "id" in cs.validate_doc({"name": "a", "id": "3"})
    assert "suite_id" in cs.validate_doc({"name": "a", "suite_id": True})

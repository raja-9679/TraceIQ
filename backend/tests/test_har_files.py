"""HAR uploads for har-replay (WP15d)."""
import json

import pytest

from app.services import har_files


def _har(entries=None):
    return {"log": {"version": "1.2", "entries": entries if entries is not None else [{
        "request": {"method": "GET", "url": "https://api.example/v1/me",
                    "headers": [{"name": "Authorization", "value": "Bearer tok123"}],
                    "cookies": [{"name": "sid", "value": "sess-1"}]},
        "response": {"status": 200, "headers": [], "cookies": [],
                     "content": {"mimeType": "application/json", "text": '{"api_key":"k-9","n":1}'}},
    }]}}


def test_prepare_upload_redacts_before_storing():
    body, summary = har_files.prepare_upload(json.dumps(_har()).encode(), None)
    text = body.decode()
    for secret in ("tok123", "sess-1", "k-9"):
        assert secret not in text
    assert summary["entry_count"] == 1
    assert summary["hosts"] == ["api.example"]
    assert summary["size_bytes"] == len(body)
    assert len(summary["sha256"]) == 64


def test_project_policy_extends_redaction():
    har = _har()
    har["log"]["entries"][0]["response"]["content"]["text"] = '{"nickname":"zed"}'
    body, _ = har_files.prepare_upload(json.dumps(har).encode(), {"redact_body_fields": ["nickname"]})
    assert "zed" not in body.decode()


@pytest.mark.parametrize("raw,msg", [
    (b"\x89PNG....", "not HAR JSON"),
    (b"[1,2]", "log.entries"),
    (json.dumps({"log": {"entries": []}}).encode(), "no entries"),
    (json.dumps(_har([{"request": {"url": "x"}, "response": {"status": 200}}])).encode(), "entry 0"),
    (json.dumps(_har([{"request": {"url": "x", "method": "GET"}, "response": {"status": True}}])).encode(), "entry 0"),
])
def test_sniffing_refuses_non_har(raw, msg):
    with pytest.raises(har_files.HarError, match=msg):
        har_files.parse_har(raw)


def test_size_cap():
    with pytest.raises(har_files.HarError, match="limited"):
        har_files.parse_har(b" " * (har_files.MAX_HAR_BYTES + 1))


def test_bom_is_accepted():
    assert har_files.parse_har(b"\xef\xbb\xbf" + json.dumps(_har()).encode())["log"]["version"] == "1.2"


def test_keys_are_derived_from_the_project():
    assert har_files.object_key(12, "abc") == "har/12/abc.har"
    assert har_files.project_prefix(12) == "har/12/"


def test_resolve_steps_only_accepts_this_projects_hars():
    steps = [{"type": "goto", "value": "/"},
             {"type": "har-replay", "params": {"har_id": 5}},
             {"type": "har-replay", "params": {"har_id": "9"}},
             {"type": "har-replay", "params": {}},
             {"type": "har-replay", "params": {"har_id": 5, "har": {"id": 99}, "har_error": "forged"}}]
    seen = {}

    def lookup(pid, ids):
        seen["args"] = (pid, sorted(ids))
        return {5: ("login.har", "f" * 64)}

    out = har_files.resolve_steps(steps, 3, lookup)
    assert seen["args"] == (3, [5, 5, 9])
    assert out[0] == steps[0]
    assert out[1]["params"]["har"] == {"id": 5, "name": "login.har", "sha256": "f" * 64}
    assert "not an uploaded HAR of this project" in out[2]["params"]["har_error"]
    assert "needs params.har_id" in out[3]["params"]["har_error"]
    # Caller-written har/har_error never survive: they are recomputed.
    assert out[4]["params"]["har"]["id"] == 5 and "har_error" not in out[4]["params"]


def test_resolve_steps_fails_the_step_when_lookup_breaks():
    def boom(pid, ids):
        raise RuntimeError("db down")
    out = har_files.resolve_steps([{"type": "har-replay", "params": {"har_id": 1}}], 3, boom)
    assert "har_error" in out[0]["params"]
    assert har_files.resolve_steps([{"type": "click"}], 3, boom) == [{"type": "click"}]

"""Minimal Playwright trace → action list."""
import json
import zipfile

from app.ai.trace_parser import TraceParser


def _trace(tmp_path, lines):
    path = tmp_path / "trace.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("trace.trace", "\n".join(l if isinstance(l, str) else json.dumps(l) for l in lines))
        z.writestr("trace.network", "")
    return str(path)


def test_actions_are_paired_and_ordered(tmp_path):
    path = _trace(tmp_path, [
        {"type": "before", "callId": "c2", "class": "Frame", "method": "click",
         "params": {"selector": "#buy"}, "startTime": 200},
        {"type": "before", "callId": "c1", "apiName": "page.goto", "params": {"url": "https://x"}, "startTime": 100},
        {"type": "after", "callId": "c1", "endTime": 150.5},
        "not json",
        {"type": "after", "callId": "c2", "endTime": 30200, "error": {"message": "Timeout 30000ms exceeded"}},
        {"type": "after", "callId": "orphan", "endTime": 1},
    ])
    actions = TraceParser().parse_actions(path)
    assert [a["api"] for a in actions] == ["page.goto", "Frame.click"]
    assert actions[0]["duration_ms"] == 50.5 and actions[0]["error"] is None
    assert actions[1]["selector"] == "#buy"
    assert actions[1]["error"] == "Timeout 30000ms exceeded"


def test_missing_or_corrupt_trace_yields_nothing(tmp_path):
    assert TraceParser().parse_actions(str(tmp_path / "nope.zip")) == []
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    assert TraceParser().parse_actions(str(bad)) == []

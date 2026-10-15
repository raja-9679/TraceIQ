from traceiq_mcp.tools.runs import typed_reports


def test_v2_report_fields_are_typed():
    analysis = {"schema_version": 1, "reports": [{
        "schema_version": 2, "result_id": 5, "root_cause_category": "app_bug", "summary": "s",
        "observed": [{"text": "Step 2 failed.", "evidence_refs": ["s1"]}],
        "hypotheses": [{"category": "app_bug", "fix_target": "app", "summary": "500",
                        "confidence": 0.7, "evidence_refs": ["s1.r0"], "source": "heuristic"}],
        "evidence_items": [{"id": "s1.r0", "kind": "request", "label": "POST /api → 500", "step_index": 1}],
        "history": {"runs_considered": 3, "failing_streak": 2, "last_pass_commit": "abc"},
        "changed_since_last_pass": {"from_commit": "abc", "to_commit": "def", "last_pass_run_id": 9},
        "some_future_field": 1,
    }]}
    [r] = typed_reports(analysis)
    assert r.hypotheses[0].evidence_refs == ["s1.r0"]
    assert r.evidence_items[0].kind == "request"
    assert r.history.last_pass_commit == "abc"
    assert r.changed_since_last_pass.to_commit == "def"


def test_v1_and_legacy_analyses_still_work():
    [r] = typed_reports({"reports": [{"schema_version": 1, "root_cause_category": "test_bug",
                                      "summary": "s", "confidence": 0.5}]})
    assert r.hypotheses == [] and r.history is None
    assert typed_reports("freeform text") == []
    assert typed_reports({"reports": ["junk", {"changed_since_last_pass": {"bad": 1}}]}) == []

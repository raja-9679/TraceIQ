"""Failure investigation v2: step-evidence ingestion, observations, ranked
hypotheses, history, LLM citation validation and v1 compatibility."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.schemas.failure_report import FailureReport, HistoryEntry
from app.services import failure_analysis
from app.services.failure_investigation import (
    build_evidence_index, summarise_history, validate_citations,
)
from app.tasks.result_aggregator import _evidence_payload, normalise_step_evidence


@pytest.fixture
def null_provider(monkeypatch):
    monkeypatch.setattr(failure_analysis, "get_provider", lambda pid=None: SimpleNamespace(name="null"))


def _evidence():
    """goto ok → click ok (fetch 500 + page error) → fill fails → expect skipped."""
    return [
        {"id": "s0", "index": 0, "type": "goto", "status": "passed", "duration_ms": 800},
        {"id": "s1", "index": 1, "type": "click", "selector": "#buy", "status": "passed", "duration_ms": 120,
         "requests": [{"id": "s1.r0", "method": "POST", "url": "https://shop.test/api/cart?id=…",
                       "status": 500, "resource_type": "fetch"}],
         "console": [{"id": "s1.c0", "type": "pageerror", "text": "TypeError: cart is undefined"}]},
        {"id": "s2", "index": 2, "type": "fill", "selector": "#qty", "status": "failed", "duration_ms": 30000,
         "error": "Timeout 30000ms exceeded waiting for locator('#qty')",
         "dom": "<div><p>Something went wrong</p></div>", "dom_scope": "page",
         "screenshot": "runs/9/screenshots/j-failure.png"},
        {"id": "s3", "index": 3, "type": "expect-visible", "status": "skipped"},
    ]


# ── Ingestion ────────────────────────────────────────────────────────────────

def test_ingestion_keeps_the_evidence_and_rederives_ids():
    raw = _evidence()
    raw[1]["console"][0]["id"] = "forged"          # ids come from position, not the sender
    out = normalise_step_evidence(raw, run_id=9)
    assert [e["id"] for e in out] == ["s0", "s1", "s2", "s3"]
    assert out[1]["console"][0]["id"] == "s1.c0"
    assert out[1]["requests"][0] == {"id": "s1.r0", "method": "POST", "url": "https://shop.test/api/cart?id=…",
                                     "status": 500, "resource_type": "fetch"}
    assert out[2]["screenshot"] == "runs/9/screenshots/j-failure.png"
    assert out[2]["dom_scope"] == "page"


def test_ingestion_refuses_another_runs_screenshot_and_drops_garbage():
    raw = [
        {"index": 0, "type": "click", "status": "failed", "screenshot": "runs/1/screenshots/x.png"},
        {"index": 0, "type": "dup"},                      # duplicate index
        {"index": -1}, {"index": True}, "junk", None,
        {"index": 1, "type": "x" * 500, "status": "weird", "duration_ms": "slow",
         "console": "not a list", "requests": [{"url": 3}, {"url": "u", "status": True}],
         "dom": "d" * 9000},
    ]
    out = normalise_step_evidence(raw, run_id=9)
    assert [e["index"] for e in out] == [0, 1]
    assert "screenshot" not in out[0]
    assert out[1]["status"] == "passed"
    assert len(out[1]["type"]) == 60
    assert "duration_ms" not in out[1] and "console" not in out[1]
    assert out[1]["requests"] == [{"id": "s1.r0", "method": "GET", "url": "u"}]
    assert len(out[1]["dom"]) == 4000
    assert normalise_step_evidence("nope") == []


def test_ingestion_caps_steps_and_items():
    raw = [{"index": i, "type": "click", "status": "passed",
            "console": [{"type": "error", "text": "e"}] * 30} for i in range(300)]
    out = normalise_step_evidence(raw, run_id=1)
    assert len(out) == 200
    assert len(out[0]["console"]) == 10
    assert sum(len(e.get("console", [])) for e in out) == 200   # per-result budget


def test_evidence_payload_carries_step_evidence_and_old_workers_send_none():
    assert "step_evidence" in _evidence_payload({"step_evidence": _evidence()}, run_id=9)
    assert _evidence_payload({"status": "failed"}) == {}


# ── Report v2 (no LLM) ───────────────────────────────────────────────────────

def _analyze(**kw):
    defaults = dict(test_name="buy", error_message="Timeout 30000ms exceeded waiting for locator('#qty')",
                    steps=[{"type": "goto"}, {"type": "click", "selector": "#buy"},
                           {"type": "fill", "selector": "#qty"}, {"type": "expect-visible"}])
    defaults.update(kw)
    return failure_analysis.analyze_case_failure(**defaults)


def test_v2_report_ranks_a_server_error_above_the_timeout(null_provider):
    r = _analyze(step_evidence=_evidence(), result_id=42)
    assert r.schema_version == 2
    assert r.result_id == 42
    top = r.hypotheses[0]
    assert top.category == "app_bug"
    assert "500" in top.summary and "s1.r0" in top.evidence_refs
    # v1 headline mirrors the best hypothesis.
    assert r.root_cause_category == top.category and r.summary == top.summary
    categories = [h.category for h in r.hypotheses]
    assert "test_bug" in categories                    # the timeout/selector reading still competes
    assert r.hypotheses == sorted(r.hypotheses, key=lambda h: -h.confidence)
    # Per-step evidence pins the failing step even though the error names no selector match.
    assert r.evidence.failing_step_index == 2


def test_v2_observed_facts_cite_real_evidence(null_provider):
    r = _analyze(step_evidence=_evidence())
    texts = " | ".join(o.text for o in r.observed)
    assert "Step 3 (fill `#qty`) failed after 30000 ms." in texts
    assert "returned 500 during step 2" in texts
    assert "Uncaught page error during step 2" in texts
    assert "did not resolve" in texts
    known = {i.id for i in r.evidence_items}
    for fact in r.observed:
        assert set(fact.evidence_refs) <= known
    for h in r.hypotheses:
        assert set(h.evidence_refs) <= known
    assert {"s2", "s1.r0", "s1.c0", "s2.dom", "s2.shot"} <= known


def test_selector_gone_from_the_page_is_a_test_bug(null_provider):
    ev = _evidence()
    ev[1].pop("requests"); ev[1].pop("console")
    r = _analyze(step_evidence=ev)
    assert r.hypotheses[0].category == "test_bug"
    assert "s2.dom" in r.hypotheses[0].evidence_refs


def test_target_present_but_not_actionable(null_provider):
    ev = _evidence()
    ev[1].pop("requests"); ev[1].pop("console")
    ev[2]["dom_scope"] = "target"
    r = _analyze(step_evidence=ev)
    assert any("not actionable" in h.summary for h in r.hypotheses)


def test_network_level_failure_points_at_the_environment(null_provider):
    ev = [{"id": "s0", "index": 0, "type": "goto", "status": "failed",
           "error": "page.goto: net::ERR_NAME_NOT_RESOLVED",
           "requests": [{"id": "s0.r0", "method": "GET", "url": "https://shop.test/",
                         "failure": "net::ERR_NAME_NOT_RESOLVED"}]}]
    r = _analyze(error_message="page.goto: net::ERR_NAME_NOT_RESOLVED", steps=[{"type": "goto"}], step_evidence=ev)
    assert r.hypotheses[0].category == "environment"
    assert r.fix_target == "infra"


def test_fail_on_console_errors_message_is_an_app_bug(null_provider):
    r = _analyze(error_message="JavaScript error on page (fail_on_console_errors): Uncaught page error: TypeError",
                 steps=[])
    assert r.root_cause_category == "app_bug"


def test_old_worker_results_still_get_observations_and_hypotheses(null_provider):
    r = _analyze(error_message="Expected status 200 but got 503", steps=[],
                 network_failures=[{"method": "GET", "url": "https://api.test/x", "status": 503, "resourceType": "xhr"}],
                 console_errors=[{"type": "error", "text": "Failed to load resource"}])
    ids = {i.id for i in r.evidence_items}
    assert {"net.0", "console.0"} <= ids
    assert r.hypotheses and r.hypotheses[0].category == "app_bug"
    # v1 evidence block unchanged.
    assert r.evidence.network_failures[0]["status"] == 503


def test_v1_reports_still_parse_and_v1_fields_survive():
    v1 = {"schema_version": 1, "root_cause_category": "test_bug", "summary": "s", "details": "d",
          "fix_target": "test", "confidence": 0.5, "evidence": {"error_message": "x"},
          "analyzed_by": "heuristic"}
    r = FailureReport.model_validate(v1)
    assert r.schema_version == 1
    assert r.hypotheses == [] and r.observed == [] and r.history is None
    dumped = json.loads(r.model_dump_json())
    for key in v1:
        assert key in dumped


# ── History ──────────────────────────────────────────────────────────────────

def _h(run_id, status, commit=None, same=False):
    return HistoryEntry(run_id=run_id, result_id=run_id * 10, status=status, git_commit=commit,
                        same_signature=same)


def test_history_finds_the_first_failing_commit_and_the_change_range():
    prior = [_h(9, "failed", "c9", True), _h(8, "failed", "c8", True), _h(7, "passed", "c7"), _h(6, "passed", "c6")]
    history, change = summarise_history(prior, "c10", 10, "https://github.com/acme/shop")
    assert history.failing_streak == 3
    assert history.first_failing_run_id == 8 and history.first_failing_commit == "c8"
    assert history.last_pass_run_id == 7 and history.last_pass_commit == "c7"
    assert history.prior_failures == 2
    assert change.from_commit == "c7" and change.to_commit == "c10"
    assert change.compare_url == "https://github.com/acme/shop/compare/c7...c10"


def test_history_without_git_info_has_no_change_range():
    history, change = summarise_history([_h(4, "passed")], None, 5)
    assert history.failing_streak == 1 and history.first_failing_run_id == 5
    assert change is None


def test_history_flips_raise_a_flake_hypothesis_and_regressions_boost_app_bugs(null_provider):
    flappy, _ = summarise_history(
        [_h(9, "passed"), _h(8, "failed"), _h(7, "passed"), _h(6, "failed")], None, 10)
    assert flappy.flip_count == 4
    r = _analyze(step_evidence=_evidence(), history=flappy)
    assert any(h.category == "flake" for h in r.hypotheses)

    fresh, change = summarise_history([_h(9, "passed", "c9")], "c10", 10)
    base = _analyze(step_evidence=_evidence())
    boosted = _analyze(step_evidence=_evidence(), history=fresh, changed_since_last_pass=change)
    assert boosted.hypotheses[0].confidence > base.hypotheses[0].confidence
    assert "run:9" in boosted.hypotheses[0].evidence_refs
    assert any("Code changed since the last pass" in o.text for o in boosted.observed)


# ── LLM layer ────────────────────────────────────────────────────────────────

class _FakeLLM:
    name = "fake"

    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def complete(self, prompt, system=None, max_tokens=None):
        self.prompts.append(prompt)
        return self.reply


def test_validate_citations():
    assert validate_citations(["a", "zz", "a", 3], {"a", "b"}) == (["a"], 3)
    assert validate_citations(None, {"a"}) == ([], 0)
    assert validate_citations("a", {"a"}) == ([], 1)


def test_llm_citations_are_validated_and_ungrounded_hypotheses_dropped(monkeypatch):
    reply = json.dumps({
        "root_cause_category": "app_bug", "summary": "Cart API broke", "details": "d",
        "suggested_fix": "fix the cart endpoint", "fix_target": "app", "confidence": 0.8,
        "hypotheses": [
            {"category": "app_bug", "fix_target": "app", "summary": "Cart API 500",
             "confidence": 0.9, "evidence_refs": ["s1.r0", "s99.r7", "s2"]},
            {"category": "test_bug", "fix_target": "test", "summary": "Invented",
             "confidence": 0.95, "evidence_refs": ["made-up"]},
            {"category": "nonsense", "fix_target": "???", "summary": "Bad enums",
             "confidence": "high", "evidence_refs": ["s1.c0"]},
        ],
    })
    llm = _FakeLLM(reply)
    contexts = []
    real_ctx = failure_analysis.llm_call_context

    def spy(**kw):
        contexts.append(kw)
        return real_ctx(**kw)

    monkeypatch.setattr(failure_analysis, "get_provider", lambda pid=None: llm)
    monkeypatch.setattr(failure_analysis, "llm_call_context", spy)
    r = _analyze(step_evidence=_evidence())

    assert contexts == [{"feature": "failure_analysis"}]          # metering kept
    assert "s1.r0 [request]" in llm.prompts[0]                       # LLM sees the ids
    assert r.analyzed_by == "llm:fake"
    assert r.dropped_citations == 2                                  # s99.r7 + made-up
    summaries = [h.summary for h in r.hypotheses]
    assert "Invented" not in summaries
    top = r.hypotheses[0]
    assert top.summary == "Cart API 500" and top.source == "llm:fake"
    assert top.evidence_refs == ["s1.r0", "s2"]
    bad = next(h for h in r.hypotheses if h.summary == "Bad enums")
    assert bad.category == "unknown" and bad.fix_target == "none" and bad.confidence == 0.5
    # Heuristic hypotheses stay in the ranking next to the LLM's.
    assert any(h.source == "heuristic" for h in r.hypotheses)
    known = {i.id for i in r.evidence_items}
    for h in r.hypotheses:
        assert set(h.evidence_refs) <= known


def test_malformed_llm_output_falls_back_to_the_heuristic_v2_report(monkeypatch):
    monkeypatch.setattr(failure_analysis, "get_provider", lambda pid=None: _FakeLLM("not json"))
    r = _analyze(step_evidence=_evidence())
    assert r.analyzed_by == "heuristic"
    assert r.hypotheses and r.observed


def test_evidence_index_ignores_entries_without_an_index():
    idx = build_evidence_index(step_evidence=[{"type": "x"}, "junk"], error_message="")
    assert not idx.has_steps and idx.items == {}

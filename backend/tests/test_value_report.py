"""WP16d value report maths (app/services/value_report.py), no database."""
from datetime import date, datetime, timedelta

from app.services import value_report as vr

T0 = datetime(2026, 10, 1, 9, 0, 0)


def h(n):
    return T0 + timedelta(hours=n)


# --- helpers -------------------------------------------------------------------

def test_percentile_is_nearest_rank():
    assert vr.percentile([], 50) is None
    assert vr.percentile([5], 90) == 5
    assert vr.percentile([1, 2, 3, 4], 50) == 2
    assert vr.percentile(list(range(1, 11)), 90) == 9
    assert vr.percentile([10, 1, 7], 90) == 10


def test_window_bounds_cover_the_whole_end_day():
    s, e = vr.window_bounds(date(2026, 10, 1), date(2026, 10, 31))
    assert s == datetime(2026, 10, 1) and e == datetime(2026, 11, 1)


def test_hypothesis_key_tracks_the_claim_not_the_confidence():
    a = {"category": "app_bug", "summary": "500 from /api/cart", "source": "heuristic", "confidence": 0.4}
    b = dict(a, confidence=0.9, evidence_refs=["s1"])
    c = dict(a, summary="timeout on /api/cart")
    assert vr.hypothesis_key(a) == vr.hypothesis_key(b)
    assert vr.hypothesis_key(a) != vr.hypothesis_key(c)
    assert len(vr.hypothesis_key(a)) == 32


# --- 1. time to diagnosis ------------------------------------------------------

def test_time_to_diagnosis_takes_the_earliest_signal_after_the_first_failure():
    items = [
        vr.DiagnosisItem(1, 1, h(0), {"triage": h(5), "ticket": h(2), "quarantine": None, "fixed": None}),
        vr.DiagnosisItem(2, 1, h(0), {"triage": None, "ticket": None, "quarantine": None, "fixed": h(10)}),
        # A quarantine from BEFORE the failure is not a diagnosis of it.
        vr.DiagnosisItem(3, 1, h(0), {"quarantine": h(-1), "triage": h(4)}),
        vr.DiagnosisItem(4, 1, h(0), {"triage": None}),
    ]
    m = vr.time_to_diagnosis(items)
    assert m["kind"] == vr.MEASURED and m["missing"] is None
    v = m["value"]
    assert v["diagnosed"] == 3 and v["undiagnosed"] == 1
    assert v["median_hours"] == 4.0 and v["p90_hours"] == 10.0
    assert v["by_signal"] == {"triage": 1, "ticket": 1, "quarantine": 0, "fixed": 1}
    assert m["sample_size"] == 3 and m["clusters_considered"] == 4


def test_time_to_diagnosis_missing_is_text_never_zero():
    m = vr.time_to_diagnosis([])
    assert m["value"] is None and "No failure cluster" in m["missing"]
    m = vr.time_to_diagnosis([vr.DiagnosisItem(1, 1, h(0), {})])
    assert m["value"] is None and "1 failure cluster" in m["missing"] and m["sample_size"] == 0


def test_a_fix_needs_a_different_commit():
    passes = [(h(1), "abc"), (h(2), None), (h(3), "def")]
    assert vr.first_fix_after(h(0), "abc", passes) == h(3)
    assert vr.first_fix_after(h(0), None, passes) is None          # unknown failing commit
    assert vr.first_fix_after(h(5), "abc", passes) is None          # passes before the failure


# --- 2. accuracy -----------------------------------------------------------------

def _votes(pattern):
    """pattern: list of (result_id, verdicts...) for the top hypothesis."""
    out = []
    for rid, *verdicts in pattern:
        for v in verdicts:
            out.append(vr.Vote(rid, f"k{rid}", 0, v, "llm:anthropic" if rid % 2 else "heuristic"))
    return out


def test_accuracy_is_withheld_below_the_minimum_sample():
    m = vr.analysis_accuracy(_votes([(1, "correct"), (2, "correct")]))
    assert m["value"] is None and "Not enough ratings: 2 rated" in m["missing"]
    assert m["min_sample"] == vr.MIN_RATED and m["sample_size"] == 2
    assert vr.analysis_accuracy([])["missing"].startswith("No top hypothesis")


def test_accuracy_uses_the_majority_per_hypothesis_and_ignores_lower_ranks():
    votes = _votes([(1, "correct"), (2, "correct", "incorrect", "correct"), (3, "incorrect"),
                    (4, "correct"), (5, "correct"), (6, "correct", "incorrect")])
    votes.append(vr.Vote(7, "k7", 1, "incorrect"))      # not the top hypothesis
    m = vr.analysis_accuracy(votes)
    v = m["value"]
    assert (v["correct"], v["incorrect"], v["split"]) == (4, 1, 1)
    assert v["percent"] == 80.0 and m["sample_size"] == 5
    assert "reviewed sample" in m["sample_label"]
    assert v["by_source"]["llm"]["correct"] + v["by_source"]["heuristic"]["correct"] == 4


# --- 3. targeted savings -------------------------------------------------------

def run(i, at, mode, total, cases, dur, browser="chromium", user=1, commit="c1", fin=None):
    return vr.RunRow(id=i, created_at=at, selection_mode=mode, selection_total=total, browser=browser,
                     duration_ms=dur, finalized_at=fin, user_id=user, git_commit=commit,
                     case_ids=frozenset(cases))


def test_runs_made_by_one_request_are_one_batch():
    rows = [run(1, h(0), "full", 4, [1], 1000), run(2, h(0) + timedelta(seconds=30), "full", 4, [2], 1000),
            run(3, h(0) + timedelta(seconds=200), "full", 4, [3], 1000),          # gap too long
            run(4, h(0) + timedelta(seconds=10), "full", 4, [4], 1000, user=2)]   # someone else
    batches = vr.group_batches(rows)
    assert sorted(len(b.runs) for b in batches) == [1, 1, 2]


def test_saving_uses_the_newest_comparable_full_run():
    full_old = vr.group_batches([run(i, h(0), "full", 4, [i], 60000) for i in (1, 2, 3, 4)])
    full_new = vr.group_batches([run(10 + i, h(10), "full", 4, [i], 120000,
                                     fin=h(10) + timedelta(minutes=6)) for i in (1, 2, 3, 4)])
    # A full run of a different suite size never counts as the baseline.
    other = vr.group_batches([run(20, h(15), "full", 9, [1, 2, 3, 4], 999999)])
    targeted = vr.group_batches([run(30, h(20), "targeted", 4, [1], 30000)])
    m = vr.targeted_savings(targeted, full_old + full_new + other)
    assert m["kind"] == vr.ESTIMATED and "1 − cases_ran / cases_total" in m["formula"]
    v = m["value"]
    # baseline = 4 × 2 min = 8 browser minutes; 3 of 4 cases skipped → 6
    assert v["saved_browser_minutes"] == 6.0
    assert v["saved_wall_minutes"] == 4.5          # 6 minutes wall × 0.75
    assert v["cases_skipped"] == 3 and v["cases_ran"] == 1
    assert m["runs"][0]["baseline_first_run_id"] == 11
    assert m["measured_inputs"] == {"targeted_runs": 1, "estimated": 1, "no_baseline": 0, "not_estimable": 0}


def test_no_baseline_when_cases_browsers_or_age_do_not_match():
    full = vr.group_batches([run(1, h(0), "full", 4, [1, 2], 1000)])
    stale = vr.group_batches([run(2, h(0) - timedelta(days=40), "full", 4, [1, 2, 3, 4], 1000)])
    firefox = vr.group_batches([run(3, h(1), "full", 4, [1, 2, 3, 4], 1000, browser="firefox")])
    targeted = vr.group_batches([run(9, h(2), "targeted", 4, [3], 500)])
    m = vr.targeted_savings(targeted, full + stale + firefox)
    assert m["value"] is None and "none has a comparable full run" in m["missing"]
    assert m["measured_inputs"]["no_baseline"] == 1
    assert vr.targeted_savings([], full)["missing"] == "No targeted run in this window."


# --- 4. proposals ----------------------------------------------------------------

def rev(n, at, source, steps):
    return vr.Revision(number=n, created_at=at, source=source, steps=steps)


def test_revert_is_a_later_revision_restoring_the_earlier_steps():
    a, b = [{"type": "click", "selector": "#a"}], [{"type": "click", "selector": "#b"}]
    revs = [rev(1, h(0), "create", a), rev(2, h(1), "heal", b), rev(3, h(5), "restore", a)]
    assert vr.revert_state(h(1) + timedelta(seconds=1), ("heal",), revs) == "reverted"
    assert vr.revert_state(h(1), ("heal",), revs[:2]) == "kept"
    # No revision near the decision, or no earlier revision: not measurable.
    assert vr.revert_state(h(3), ("heal",), revs) == "unknown"
    assert vr.revert_state(h(1), ("heal",), [rev(2, h(1), "heal", b)]) == "unknown"
    assert vr.revert_state(None, ("heal",), revs) == "unknown"
    # A change that touched no steps cannot be reverted in the steps sense.
    assert vr.revert_state(h(1), ("proposal",), [rev(1, h(0), "create", a),
                                                rev(2, h(1), "proposal", a)]) == "unknown"


def test_proposal_outcomes_rates_and_repair_validation():
    case_rows = [("accepted", False), ("accepted", True), ("rejected", False), ("pending", False)]
    heal_rows = [("accepted", False), ("auto_applied", True), ("rejected", False), ("rejected", False)]
    events = [("validation_passed", True, None), ("validation_passed", False, None),
              ("validation_failed", None, None), ("accepted", None, True), ("auto_applied", None, False)]
    m = vr.proposal_outcomes(case_rows, heal_rows, ["reverted", "kept", "kept", "unknown"], events)
    v = m["value"]
    assert v["case"]["decided"] == 3 and v["case"]["auto_apply_rate"] == 33.3
    assert v["heal"]["reject_rate"] == 50.0 and v["heal"]["auto_applied"] == 1
    assert v["revert"] == {"reverted": 1, "kept": 2, "not_measurable": 1, "revert_rate": 33.3}
    rv = v["repair_validation"]
    assert rv["validation_runs"] == 3 and rv["success_rate"] == 33.3
    assert rv["validated_share"] == 50.0
    assert m["sample_size"] == 7


def test_proposal_outcomes_missing_and_rates_with_no_decisions():
    m = vr.proposal_outcomes([], [], [], [])
    assert m["value"] is None and m["missing"] == "No proposal was created in this window."
    m = vr.proposal_outcomes([("pending", False)], [], [], [])
    assert m["value"]["case"]["accept_rate"] is None          # nothing decided: no rate, not 0
    assert m["value"]["revert"]["revert_rate"] is None


def test_every_metric_says_what_it_is():
    report = vr.assemble(vr.Inputs(), date(2026, 10, 1), date(2026, 10, 31), {"kind": "project"})
    assert [m["key"] for m in report["metrics"]] == [
        "time_to_diagnosis", "analysis_accuracy", "targeted_time_saved", "proposal_outcomes"]
    for m in report["metrics"]:
        assert m["kind"] in (vr.MEASURED, vr.ESTIMATED)
        assert m["definition"] and m["data_source"] and m["sample_label"]
        assert m["value"] is None and m["missing"]      # empty inputs: text, never 0
    assert {l["path"] for l in report["links"]} >= {"/reports/flake-trends", "/incidents"}

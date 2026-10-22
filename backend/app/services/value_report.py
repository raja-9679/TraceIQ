"""Pilot value report (WP16d, decision V6).

Four numbers a pilot sponsor asks about, each returned as a `metric` dict that
says whether it is MEASURED or ESTIMATED, how it is defined, how big the
sample is and where the data came from. A metric with no inputs carries a
`missing` sentence and `value: None` — never 0, because "nothing measured"
and "measured zero" mean opposite things to the reader.

1. Time to diagnosis (measured). Unit = failure cluster (one root-cause
   signature in one project) whose first failure falls in the window. First
   failure = the earliest COUNTED run carrying one of its results (falls back
   to `first_seen_at` once retention has swept the results). Diagnosed = the
   earliest of these signals at or after the first failure:
     triage     the cluster first moved to investigating/resolved
                (`first_triaged_at`; a legacy resolved cluster uses resolved_at)
     ticket     an issue ticket filed for the cluster or one of its results
                (anything but status=error)
     quarantine one of the cluster's cases was quarantined
     fixed      one of its cases passed again in a counted run on a different
                git commit from the first failing run (needs both commits)
   Median and p90 (nearest rank) over diagnosed clusters; clusters with no
   signal yet are reported as `undiagnosed`, not as an infinite time.
2. AI failure-analysis accuracy (measured, reviewed sample). Share of rated
   TOP hypotheses whose reviewers' majority verdict is "correct" (ties are
   `split`, excluded). Below MIN_RATED rated hypotheses the share is withheld.
3. CI time saved by targeted runs (ESTIMATE). Runs are created one per case
   (or per CONTINUOUS suite) without a request id, so runs are grouped into
   batches: same mode, selection_total, user, API key and commit, created
   within BATCH_GAP of the previous one. For each targeted batch the baseline
   is the newest earlier full batch (within BASELINE_LOOKBACK) with the same
   selection_total and browsers that ran every case the targeted batch ran.
       saved = baseline browser time × (1 − cases ran / cases in suite)
   Browser time = summed run durations (what the cost report bills); a wall
   clock version uses first created → last finalized of the baseline.
4. Proposal outcomes (measured). Case + heal proposals created in the window
   by status; auto-applied = accepted by the policy (no human decider).
   Revert (UPDATE and heal proposals only): the revision written when the
   proposal was applied is found by time (same case, source proposal|heal,
   within REVISION_MATCH of decided_at); it is reverted if any LATER revision's
   steps equal the steps from before it. No matching revision, or no earlier
   revision to compare with → "not measurable" for that proposal. Repair
   validation (WP16c RepairOutcome): passed under the failing run's conditions
   / all validation runs.

Validation runs (WP16c) never count: every run query goes through
`run_scope.counted()`. The pure functions below take plain rows so the maths
is testable without a database; the loaders at the bottom build those rows.
"""
from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

MEASURED = "measured"
ESTIMATED = "estimated"

MIN_RATED = 5
BATCH_GAP = timedelta(seconds=120)
BASELINE_LOOKBACK = timedelta(days=30)
REVISION_MATCH = timedelta(seconds=120)

# Row caps per report: a report must stay bounded whatever the project's size.
MAX_CLUSTERS = 5000
MAX_RUNS = 20000
MAX_PROPOSALS = 5000

SIGNALS = ("triage", "ticket", "quarantine", "fixed")
VERDICTS = ("correct", "incorrect")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def percentile(values: Sequence[float], p: float) -> Optional[float]:
    """Nearest-rank percentile (an observed value, never interpolated)."""
    if not values:
        return None
    ordered = sorted(values)
    k = max(1, math.ceil(p / 100.0 * len(ordered)))
    return ordered[k - 1]


def _pct(part: int, whole: int) -> Optional[float]:
    return round(part * 100.0 / whole, 1) if whole else None


def metric(key: str, title: str, kind: str, definition: str, data_source: str,
           sample_size: int, sample_label: str, value: Optional[Dict[str, Any]],
           missing: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    out = {"key": key, "title": title, "kind": kind, "definition": definition,
           "data_source": data_source, "sample_size": sample_size,
           "sample_label": sample_label, "value": value,
           "missing": missing if value is None else None}
    out.update(extra)
    return out


def hypothesis_key(h: Dict[str, Any]) -> str:
    """Stable identity of a hypothesis (they have no id): a re-analysis that
    changes what is claimed gets a new key and never inherits votes."""
    raw = "\x1f".join([str(h.get("category") or "unknown"), str(h.get("summary") or ""),
                       str(h.get("source") or "heuristic")])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def window_bounds(start: date, end: date) -> Tuple[datetime, datetime]:
    """Inclusive dates → [start 00:00, day after end 00:00) naive UTC."""
    return datetime.combine(start, time.min), datetime.combine(end + timedelta(days=1), time.min)


# ---------------------------------------------------------------------------
# 1. Time to diagnosis
# ---------------------------------------------------------------------------

@dataclass
class DiagnosisItem:
    cluster_id: int
    project_id: int
    first_failure_at: datetime
    signals: Dict[str, Optional[datetime]] = field(default_factory=dict)


def first_fix_after(first_failure_at: datetime, first_commit: Optional[str],
                    passes: Iterable[Tuple[datetime, Optional[str]]]) -> Optional[datetime]:
    """Earliest pass after the first failure on a DIFFERENT commit. Without
    the failing commit there is no way to tell a fix from a flake."""
    if not first_commit:
        return None
    for at, commit in sorted(passes, key=lambda x: x[0]):
        if at > first_failure_at and commit and commit != first_commit:
            return at
    return None


def time_to_diagnosis(items: Sequence[DiagnosisItem]) -> Dict[str, Any]:
    hours: List[float] = []
    by_signal: Counter = Counter()
    undiagnosed = 0
    for it in items:
        cands = [(t, name) for name, t in it.signals.items()
                 if t is not None and t >= it.first_failure_at]
        if not cands:
            undiagnosed += 1
            continue
        t, name = min(cands)
        hours.append((t - it.first_failure_at).total_seconds() / 3600.0)
        by_signal[name] += 1
    definition = ("For each failure cluster whose first failure is in the window: time from that "
                  "first failure (earliest counted run) to the first diagnosed signal — the cluster "
                  "moved to investigating or resolved, a ticket filed, one of its cases quarantined, "
                  "or one of its cases passing again on a different git commit.")
    source = "Failure clusters, test results and runs (validation runs excluded), issue tickets, flake quarantines."
    if not items:
        value, missing = None, "No failure cluster started in this window."
    elif not hours:
        value, missing = None, (f"None of the {undiagnosed} failure cluster(s) that started in this "
                                "window has a diagnosed signal yet.")
    else:
        value, missing = {
            "median_hours": round(percentile(hours, 50), 2),
            "p90_hours": round(percentile(hours, 90), 2),
            "diagnosed": len(hours), "undiagnosed": undiagnosed,
            "by_signal": {s: by_signal.get(s, 0) for s in SIGNALS},
        }, None
    return metric("time_to_diagnosis", "Time to diagnosis", MEASURED, definition, source,
                  len(hours), "diagnosed failure clusters", value, missing,
                  clusters_considered=len(items))


# ---------------------------------------------------------------------------
# 2. AI failure-analysis accuracy
# ---------------------------------------------------------------------------

@dataclass
class Vote:
    result_id: int
    hypothesis_key: str
    hypothesis_index: int
    verdict: str
    source: str = "heuristic"


def analysis_accuracy(votes: Sequence[Vote], min_rated: int = MIN_RATED) -> Dict[str, Any]:
    tally: Dict[Tuple[int, str], Counter] = defaultdict(Counter)
    source_of: Dict[Tuple[int, str], str] = {}
    for v in votes:
        if v.hypothesis_index != 0 or v.verdict not in VERDICTS:
            continue
        k = (v.result_id, v.hypothesis_key)
        tally[k][v.verdict] += 1
        source_of[k] = v.source
    correct = incorrect = split = 0
    by_source: Dict[str, Dict[str, int]] = defaultdict(lambda: {"correct": 0, "incorrect": 0})
    for k, c in tally.items():
        kind = "llm" if source_of[k].startswith("llm") else "heuristic"
        if c["correct"] > c["incorrect"]:
            correct += 1
            by_source[kind]["correct"] += 1
        elif c["incorrect"] > c["correct"]:
            incorrect += 1
            by_source[kind]["incorrect"] += 1
        else:
            split += 1
    rated = correct + incorrect
    definition = ("Share of rated top-ranked hypotheses that reviewers marked correct (majority of "
                  "votes per hypothesis; ties are counted as split and left out). A reviewed "
                  "sample, not all runs: only failures someone rated are included.")
    source = "Thumbs up / down on the run page's failure analysis (validation runs excluded)."
    if rated == 0:
        value, missing = None, "No top hypothesis has been rated in this window."
    elif rated < min_rated:
        value, missing = None, f"Not enough ratings: {rated} rated, at least {min_rated} needed."
    else:
        value, missing = {"percent": _pct(correct, rated), "correct": correct,
                          "incorrect": incorrect, "split": split,
                          "votes": sum(sum(c.values()) for c in tally.values()),
                          "by_source": dict(by_source)}, None
    return metric("analysis_accuracy", "AI failure-analysis accuracy", MEASURED, definition, source,
                  rated, "rated top hypotheses (reviewed sample, not all runs)", value, missing,
                  min_sample=min_rated, rated=rated, split=split)


# ---------------------------------------------------------------------------
# 3. CI time saved by targeted runs
# ---------------------------------------------------------------------------

@dataclass
class RunRow:
    id: int
    created_at: datetime
    selection_mode: str
    selection_total: Optional[int]
    browser: str
    duration_ms: Optional[float]
    finalized_at: Optional[datetime] = None
    user_id: Optional[int] = None
    api_key_id: Optional[int] = None
    git_commit: Optional[str] = None
    case_ids: FrozenSet[int] = frozenset()


@dataclass
class Batch:
    runs: List[RunRow]

    @property
    def created_at(self) -> datetime:
        return self.runs[0].created_at

    @property
    def selection_total(self) -> Optional[int]:
        return self.runs[0].selection_total

    @property
    def case_ids(self) -> FrozenSet[int]:
        return frozenset().union(*(r.case_ids for r in self.runs))

    @property
    def browsers(self) -> FrozenSet[str]:
        return frozenset(r.browser for r in self.runs)

    @property
    def duration_ms(self) -> Optional[float]:
        if any(r.duration_ms is None for r in self.runs):
            return None
        return float(sum(r.duration_ms for r in self.runs))

    @property
    def wall_ms(self) -> Optional[float]:
        ends = [r.finalized_at for r in self.runs]
        if any(e is None for e in ends):
            return None
        return max(0.0, (max(ends) - self.created_at).total_seconds() * 1000.0)


def group_batches(runs: Iterable[RunRow], gap: timedelta = BATCH_GAP) -> List[Batch]:
    """One POST /runs makes many runs and records no request id; rebuild the
    request from what its runs share, chaining runs created within `gap`."""
    open_batches: Dict[tuple, Batch] = {}
    out: List[Batch] = []
    for r in sorted(runs, key=lambda x: (x.created_at, x.id)):
        key = (r.selection_mode, r.selection_total, r.user_id, r.api_key_id, r.git_commit)
        b = open_batches.get(key)
        if b is not None and r.created_at - b.runs[-1].created_at <= gap:
            b.runs.append(r)
            continue
        b = Batch(runs=[r])
        open_batches[key] = b
        out.append(b)
    return out


def targeted_savings(targeted: Sequence[Batch], full: Sequence[Batch],
                     lookback: timedelta = BASELINE_LOOKBACK) -> Dict[str, Any]:
    estimated: List[Dict[str, Any]] = []
    no_baseline = 0
    not_estimable = 0
    full_sorted = sorted(full, key=lambda b: b.created_at)
    for tb in targeted:
        ran, total = len(tb.case_ids), tb.selection_total
        if not total or ran == 0 or ran > total:
            not_estimable += 1
            continue
        base = None
        for fb in full_sorted:
            if fb.created_at >= tb.created_at:
                break
            if (tb.created_at - fb.created_at <= lookback and fb.selection_total == total
                    and fb.browsers == tb.browsers and tb.case_ids <= fb.case_ids
                    and fb.duration_ms is not None):
                base = fb          # keep the newest qualifying one
        if base is None:
            no_baseline += 1
            continue
        share = 1.0 - ran / total
        wall = base.wall_ms
        estimated.append({
            "first_run_id": tb.runs[0].id, "created_at": tb.created_at.isoformat(),
            "cases_ran": ran, "cases_total": total,
            "targeted_browser_minutes": round((tb.duration_ms or 0) / 60000.0, 2)
            if tb.duration_ms is not None else None,
            "baseline_first_run_id": base.runs[0].id,
            "baseline_browser_minutes": round(base.duration_ms / 60000.0, 2),
            "saved_browser_minutes": round(base.duration_ms * share / 60000.0, 2),
            "saved_wall_minutes": round(wall * share / 60000.0, 2) if wall is not None else None,
        })
    definition = ("For each targeted run (one that ran only selected test cases), the newest earlier full run of "
                  "the same suite size and browsers that ran every selected case is the baseline; "
                  "saving = baseline duration × (1 − cases ran ÷ cases in the suite). An estimate: "
                  "the skipped cases are assumed to cost the baseline's average.")
    formula = "saved = baseline duration × (1 − cases ran ÷ cases in the suite)"
    source = ("Targeted and full runs (validation and manual runs excluded): run durations, "
              "how many cases each run selected and which; baseline within the 30 days before.")
    targeted_n = len(targeted)
    if targeted_n == 0:
        value, missing = None, "No targeted run in this window."
    elif not estimated:
        value, missing = None, (f"{targeted_n} targeted run(s), but none has a comparable full run "
                                "in the 30 days before it to estimate against.")
    else:
        walls = [e["saved_wall_minutes"] for e in estimated if e["saved_wall_minutes"] is not None]
        browser = sum(e["saved_browser_minutes"] for e in estimated)
        value, missing = {
            "saved_browser_minutes": round(browser, 2),
            "saved_wall_minutes": round(sum(walls), 2) if walls else None,
            # Seconds as well: a small suite saves well under a minute per
            # run, and "0 min" reads as "saved nothing".
            "saved_browser_seconds": round(browser * 60),
            "saved_wall_seconds": round(sum(walls) * 60) if walls else None,
            "wall_samples": len(walls),
            "cases_skipped": sum(e["cases_total"] - e["cases_ran"] for e in estimated),
            "cases_ran": sum(e["cases_ran"] for e in estimated),
        }, None
    return metric("targeted_time_saved", "CI time saved by targeted runs", ESTIMATED, definition,
                  source, len(estimated), "targeted runs with a comparable full run", value, missing,
                  formula=formula,
                  measured_inputs={"targeted_runs": targeted_n, "estimated": len(estimated),
                                   "no_baseline": no_baseline, "not_estimable": not_estimable},
                  runs=sorted(estimated, key=lambda e: e["created_at"], reverse=True)[:20])


# ---------------------------------------------------------------------------
# 4. Proposal outcomes
# ---------------------------------------------------------------------------

@dataclass
class Revision:
    number: int
    created_at: datetime
    source: str
    steps: Any


def revert_state(decided_at: Optional[datetime], sources: Tuple[str, ...],
                 revisions: Sequence[Revision], match: timedelta = REVISION_MATCH) -> str:
    """reverted | kept | unknown for one applied proposal."""
    if decided_at is None:
        return "unknown"
    cands = [r for r in revisions if r.source in sources
             and abs((r.created_at - decided_at).total_seconds()) <= match.total_seconds()]
    if not cands:
        return "unknown"
    applied = min(cands, key=lambda r: (abs((r.created_at - decided_at).total_seconds()), r.number))
    before = next((r for r in revisions if r.number == applied.number - 1), None)
    if before is None or before.steps == applied.steps:
        return "unknown"     # nothing recorded before it, or it changed no steps
    for later in revisions:
        if later.number > applied.number and later.steps == before.steps:
            return "reverted"
    return "kept"


def _bucket(status: str, auto: bool) -> str:
    if status == "accepted":
        return "auto_applied" if auto else "accepted"
    if status in ("auto_applied", "rejected", "pending"):
        return status
    return "other"


def proposal_outcomes(case_rows: Sequence[Tuple[str, bool]], heal_rows: Sequence[Tuple[str, bool]],
                      reverts: Sequence[str], repair_events: Sequence[Tuple[str, Optional[bool], Optional[bool]]]
                      ) -> Dict[str, Any]:
    """case_rows / heal_rows: (status, auto_applied). reverts: revert_state()
    per applied UPDATE/heal proposal. repair_events: (event, conditions_matched,
    validated) from RepairOutcome."""
    def counts(rows):
        c = Counter(_bucket(s, a) for s, a in rows)
        decided = c["accepted"] + c["auto_applied"] + c["rejected"]
        return {"total": len(rows), "pending": c["pending"], "accepted": c["accepted"],
                "auto_applied": c["auto_applied"], "rejected": c["rejected"], "other": c["other"],
                "decided": decided,
                "accept_rate": _pct(c["accepted"], decided),
                "auto_apply_rate": _pct(c["auto_applied"], decided),
                "reject_rate": _pct(c["rejected"], decided)}

    case_c, heal_c = counts(case_rows), counts(heal_rows)
    rc = Counter(reverts)
    measurable = rc["reverted"] + rc["kept"]
    ev = Counter()
    for event, matched, validated in repair_events:
        if event == "validation_passed":
            ev["passed_matched" if matched else "passed_unmatched"] += 1
        elif event == "validation_failed":
            ev["failed"] += 1
        elif event in ("accepted", "auto_applied"):
            ev["decisions"] += 1
            if validated:
                ev["decisions_validated"] += 1
    validations = ev["passed_matched"] + ev["passed_unmatched"] + ev["failed"]
    decided = case_c["decided"] + heal_c["decided"]
    definition = ("Case and heal proposals created in the window, by outcome. Rates are over decided "
                  "proposals; auto-applied = accepted by the workspace threshold with no human "
                  "reviewer. Reverted = a later revision of the case put back the steps from before "
                  "the accepted change (update and heal proposals only; create/delete/move are not "
                  "measured).")
    source = ("Case proposals, selector-heal proposals, test-case revisions, and the results of "
              "validation runs for proposed fixes.")
    value = None
    missing = None
    if case_c["total"] + heal_c["total"] == 0 and validations == 0:
        missing = "No proposal was created in this window."
    else:
        value = {
            "case": case_c, "heal": heal_c,
            "revert": {"reverted": rc["reverted"], "kept": rc["kept"], "not_measurable": rc["unknown"],
                       "revert_rate": _pct(rc["reverted"], measurable)},
            "repair_validation": {
                "validation_runs": validations, "passed_matched": ev["passed_matched"],
                "passed_unmatched": ev["passed_unmatched"], "failed": ev["failed"],
                "success_rate": _pct(ev["passed_matched"], validations),
                "decisions": ev["decisions"], "decisions_validated": ev["decisions_validated"],
                "validated_share": _pct(ev["decisions_validated"], ev["decisions"]),
            },
        }
    return metric("proposal_outcomes", "Proposal outcomes", MEASURED, definition, source,
                  decided, "decided proposals", value, missing,
                  sub_definitions={
                      "revert": ("Detected from revision history: the revision written within 2 "
                                 "minutes of the decision is the applied change; a later revision "
                                 "with the earlier steps is a revert. Not measurable when no such "
                                 "revision exists or there is no earlier revision to compare."),
                      "repair_validation": ("Success = a validation run that passed under the failing "
                                            "run's browser, device and environment, over all "
                                            "validation runs."),
                  })


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

LINKS = [
    {"label": "Flake trend", "path": "/reports/flake-trends",
     "reason": "Flaky-test rate over time is reported there, not recomputed here."},
    {"label": "Escaped defects", "path": "/incidents",
     "reason": "Incidents that no test caught are tracked on the incidents page."},
    {"label": "Delivery (DORA)", "path": "/reports/delivery",
     "reason": "Lead time, change failure rate and time to restore."},
]


def assemble(inputs: "Inputs", start: date, end: date, scope: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "scope": scope,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "generated_at": datetime.utcnow().isoformat(),
        "metrics": [
            time_to_diagnosis(inputs.diagnosis),
            analysis_accuracy(inputs.votes),
            targeted_savings(inputs.targeted, inputs.full),
            proposal_outcomes(inputs.case_rows, inputs.heal_rows, inputs.reverts, inputs.repair_events),
        ],
        "links": LINKS,
    }


@dataclass
class Inputs:
    diagnosis: List[DiagnosisItem] = field(default_factory=list)
    votes: List[Vote] = field(default_factory=list)
    targeted: List[Batch] = field(default_factory=list)
    full: List[Batch] = field(default_factory=list)
    case_rows: List[Tuple[str, bool]] = field(default_factory=list)
    heal_rows: List[Tuple[str, bool]] = field(default_factory=list)
    reverts: List[str] = field(default_factory=list)
    repair_events: List[Tuple[str, Optional[bool], Optional[bool]]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Loaders (async session)
# ---------------------------------------------------------------------------

async def collect(session, project_ids: Sequence[int], start: date, end: date) -> Inputs:
    """Every input for the given projects. The caller has already checked
    access to each id; nothing here widens the set."""
    inputs = Inputs()
    pids = list(project_ids)
    if not pids:
        return inputs
    s, e = window_bounds(start, end)
    inputs.diagnosis = await _diagnosis_items(session, pids, s, e)
    inputs.votes = await _votes(session, pids, s, e)
    inputs.targeted, inputs.full = await _batches(session, pids, s, e)
    (inputs.case_rows, inputs.heal_rows, inputs.reverts,
     inputs.repair_events) = await _proposals(session, pids, s, e)
    return inputs


async def _diagnosis_items(session, pids, s, e) -> List[DiagnosisItem]:
    from sqlmodel import or_, select
    from app.models import (FailureCluster, FlakeRecord, IssueTicket, TestCaseResult, TestRun,
                            TestStatus)
    from app.services.run_scope import counted

    clusters = (await session.exec(select(FailureCluster).where(
        FailureCluster.project_id.in_(pids), FailureCluster.first_seen_at >= s,
        FailureCluster.first_seen_at < e).order_by(FailureCluster.id).limit(MAX_CLUSTERS))).all()
    if not clusters:
        return []
    cids = [c.id for c in clusters]
    rows = (await session.exec(
        select(TestCaseResult.cluster_id, TestCaseResult.id, TestCaseResult.test_case_id,
               TestRun.created_at, TestRun.git_commit, TestRun.is_validation)
        .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
        .where(TestCaseResult.cluster_id.in_(cids), TestRun.project_id.in_(pids))
        .order_by(TestRun.created_at).limit(MAX_RUNS))).all()     # earliest first survive the cap
    per: Dict[int, List[tuple]] = defaultdict(list)
    for cid, rid, case_id, at, commit, is_val in rows:
        per[cid].append((rid, case_id, at, commit, is_val is True))

    result_ids = [r[0] for v in per.values() for r in v]
    tickets = (await session.exec(select(IssueTicket.cluster_id, IssueTicket.result_id,
                                         IssueTicket.created_at).where(
        IssueTicket.status != "error",
        or_(IssueTicket.cluster_id.in_(cids),
            IssueTicket.result_id.in_(result_ids or [-1]))))).all()
    case_ids = {r[1] for v in per.values() for r in v if r[1] is not None}
    quarantines: Dict[int, datetime] = {}
    passes: Dict[int, List[Tuple[datetime, Optional[str]]]] = defaultdict(list)
    if case_ids:
        for case_id, at in (await session.exec(select(
                FlakeRecord.test_case_id, FlakeRecord.quarantined_at).where(
                FlakeRecord.test_case_id.in_(case_ids),
                FlakeRecord.quarantined_at.is_not(None)))).all():
            quarantines[case_id] = min(at, quarantines.get(case_id, at))
        for case_id, at, commit in (await session.exec(
                select(TestCaseResult.test_case_id, TestRun.created_at, TestRun.git_commit)
                .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
                .where(TestCaseResult.test_case_id.in_(case_ids),
                       TestCaseResult.status == TestStatus.PASSED,
                       TestRun.project_id.in_(pids), TestRun.created_at >= s, counted())
                .limit(MAX_RUNS))).all():
            passes[case_id].append((at, commit))

    ticket_by_cluster: Dict[int, datetime] = {}
    result_to_cluster = {r[0]: cid for cid, v in per.items() for r in v}
    cid_set = set(cids)
    for cid, rid, at in tickets:
        target = cid if cid in cid_set else result_to_cluster.get(rid)
        if target is None:
            continue
        ticket_by_cluster[target] = min(at, ticket_by_cluster.get(target, at))

    items: List[DiagnosisItem] = []
    for c in clusters:
        occ = per.get(c.id, [])
        counted_occ = [o for o in occ if not o[4]]
        if occ and not counted_occ:
            continue      # only ever seen in validation runs
        if counted_occ:
            first = min(counted_occ, key=lambda o: o[2])
            first_at, first_commit = first[2], first[3]
        else:
            first_at, first_commit = c.first_seen_at, None
        cases = {o[1] for o in counted_occ if o[1] is not None}
        triaged = c.first_triaged_at or (c.resolved_at if c.status == "resolved" else None)
        q = [quarantines[k] for k in cases if k in quarantines and quarantines[k] >= first_at]
        fix = first_fix_after(first_at, first_commit,
                              [p for k in cases for p in passes.get(k, [])])
        items.append(DiagnosisItem(
            cluster_id=c.id, project_id=c.project_id, first_failure_at=first_at,
            signals={"triage": triaged, "ticket": ticket_by_cluster.get(c.id),
                     "quarantine": min(q) if q else None, "fixed": fix}))
    return items


async def _votes(session, pids, s, e) -> List[Vote]:
    from sqlmodel import select
    from app.models import HypothesisFeedback, TestRun
    from sqlalchemy import true
    validation_runs = select(TestRun.id).where(TestRun.is_validation == true())
    rows = (await session.exec(select(HypothesisFeedback).where(
        HypothesisFeedback.project_id.in_(pids),
        HypothesisFeedback.run_created_at >= s, HypothesisFeedback.run_created_at < e,
        HypothesisFeedback.run_id.not_in(validation_runs)))).all()
    return [Vote(result_id=r.result_id, hypothesis_key=r.hypothesis_key,
                 hypothesis_index=r.hypothesis_index, verdict=r.verdict, source=r.source)
            for r in rows]


async def _batches(session, pids, s, e) -> Tuple[List[Batch], List[Batch]]:
    from sqlmodel import select
    from app.models import ExecutorType, TestCaseResult, TestRun
    from app.services.run_scope import counted
    from app.services.run_status import VERDICT

    runs = (await session.exec(select(TestRun).where(
        TestRun.project_id.in_(pids), counted(), TestRun.status.in_(VERDICT),
        TestRun.executor != ExecutorType.MANUAL.value,
        TestRun.selection_mode.in_(("targeted", "full")),
        TestRun.created_at >= s - BASELINE_LOOKBACK, TestRun.created_at < e)
        .order_by(TestRun.created_at).limit(MAX_RUNS))).all()
    # CONTINUOUS runs carry no test_case_id: their cases are their results'.
    suite_runs = [r.id for r in runs if r.test_case_id is None]
    from_results: Dict[int, set] = defaultdict(set)
    if suite_runs:
        for run_id, case_id in (await session.exec(select(
                TestCaseResult.test_run_id, TestCaseResult.test_case_id).where(
                TestCaseResult.test_run_id.in_(suite_runs),
                TestCaseResult.test_case_id.is_not(None)))).all():
            from_results[run_id].add(case_id)
    rows_by_project: Dict[int, List[RunRow]] = defaultdict(list)
    for r in runs:
        ids = frozenset([r.test_case_id]) if r.test_case_id is not None else frozenset(from_results[r.id])
        rows_by_project[r.project_id].append(RunRow(
            id=r.id, created_at=r.created_at, selection_mode=r.selection_mode,
            selection_total=r.selection_total, browser=r.browser or "chromium",
            duration_ms=r.duration_ms, finalized_at=r.finalized_at, user_id=r.user_id,
            api_key_id=r.api_key_id, git_commit=r.git_commit, case_ids=ids))
    targeted: List[Batch] = []
    full: List[Batch] = []
    for rows in rows_by_project.values():    # a batch never spans projects
        for b in group_batches(rows):
            if b.runs[0].selection_mode == "targeted":
                if s <= b.created_at < e:
                    targeted.append(b)
            else:
                full.append(b)
    return targeted, full


async def _proposals(session, pids, s, e):
    from sqlmodel import select
    from app.models import (CaseProposal, CaseProposalAction, RepairOutcome, SelectorHealProposal,
                            TestCase, TestCaseRevision, TestSuite)

    cases = (await session.exec(select(CaseProposal).where(
        CaseProposal.project_id.in_(pids), CaseProposal.created_at >= s,
        CaseProposal.created_at < e).limit(MAX_PROPOSALS))).all()
    heals = (await session.exec(
        select(SelectorHealProposal)
        .join(TestCase, TestCase.id == SelectorHealProposal.test_case_id)
        .join(TestSuite, TestSuite.id == TestCase.test_suite_id)
        .where(TestSuite.project_id.in_(pids), SelectorHealProposal.created_at >= s,
               SelectorHealProposal.created_at < e).limit(MAX_PROPOSALS))).all()

    def auto_case(p) -> bool:
        return p.status == "accepted" and p.decided_by_id is None and \
            (p.decision_note or "").startswith("auto-applied")

    case_rows = [(p.status, auto_case(p)) for p in cases]
    heal_rows = [(p.status, p.status == "auto_applied") for p in heals]

    applied: List[Tuple[int, Optional[datetime], Tuple[str, ...]]] = []
    for p in cases:
        if p.status == "accepted" and p.action == CaseProposalAction.UPDATE and p.target_case_id:
            applied.append((p.target_case_id, p.decided_at, ("proposal",)))
    for p in heals:
        if p.status in ("accepted", "auto_applied"):
            applied.append((p.test_case_id, p.decided_at, ("heal",)))
    reverts: List[str] = []
    if applied:
        revs: Dict[int, List[Revision]] = defaultdict(list)
        for r in (await session.exec(select(TestCaseRevision).where(
                TestCaseRevision.test_case_id.in_({a[0] for a in applied}))
                .order_by(TestCaseRevision.revision_number))).all():
            revs[r.test_case_id].append(Revision(number=r.revision_number, created_at=r.created_at,
                                                 source=r.change_source,
                                                 steps=(r.snapshot or {}).get("steps")))
        reverts = [revert_state(at, sources, revs.get(case_id, [])) for case_id, at, sources in applied]

    events = [(o.event, o.conditions_matched, o.validated) for o in (await session.exec(
        select(RepairOutcome).where(RepairOutcome.project_id.in_(pids),
                                    RepairOutcome.created_at >= s,
                                    RepairOutcome.created_at < e))).all()]
    return case_rows, heal_rows, reverts, events

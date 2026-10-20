"""Release readiness: the gate checks that need case-level evidence, and the
one-page go/no-go built on top of the gate (`GET /projects/{id}/release-readiness`).

Three opt-in gate checks live here (policy fields on QualityGatePolicy, all
off by default so an existing policy evaluates exactly as before):

* `flaky_cases` — cases with an ACTIVE quarantine or flake_score >= 0.4.
  Expired quarantines do not count as quarantined (they run again), but their
  score still does.
* `critical_passed` — every case whose priority is "critical" (free-form
  column, compared trimmed + case-insensitively) passed on the evaluated
  commit. Results are matched by TestCaseResult.test_case_id, never by name;
  with a commit, every finished run on that commit counts (not just the runs
  the caller happened to evaluate), without one only the evaluated runs do.
  A case is judged per browser/device: the newest result in each must pass, so
  a green rerun replaces a red one but a pass in chromium does not hide a
  failure in firefox. Fails closed when the project has no critical case —
  whoever switched the check on expects it to protect something.
* `evidence_age` — the newest passing result of each critical case (of every
  case when none is critical, so the check still means something on projects
  that never set priorities) is at most N days old. Any commit counts: this is
  "is our evidence fresh", not "did this commit pass".

* `manual_signoff` (WP12d) — the newest release sign-off of the project (for
  the evaluated commit, when there is one) is an approval. No sign-off is
  missing data; a newer rejection overrides an older approval.

A check that fails only because evidence is missing sets `missing_data`, and
readiness reports such a gate as "insufficient data" instead of "not ready".
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

from sqlalchemy import func, or_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import (
    FlakeRecord, QualityGateCheck, ReadinessCommit, QualityGatePolicy, QualityGateResult,
    ReadinessCriticalCase, ReadinessPerson, ReadinessQuarantine,
    ReadinessRequirement, ReadinessRequirementCase, ReadinessRun,
    ReleaseReadiness, RequirementLink, TestCase, TestCaseResult, TestRun,
    TestStatus, User,
)
from app.services.flake_policy import QUARANTINE_SCORE, active_quarantine_clause
from app.services.run_scope import counted

CRITICAL = "critical"
FINISHED = (TestStatus.PASSED, TestStatus.FAILED, TestStatus.ERROR)
DETAIL_CAP = 10

PASSED, FAILED, NOT_RUN = "passed", "failed", "not_run"


class ResultRow(NamedTuple):
    result_id: int
    case_id: int
    status: TestStatus
    run_id: int
    browser: Optional[str]
    device: Optional[str]


def is_critical(priority: Optional[str]) -> bool:
    return (priority or "").strip().lower() == CRITICAL


def _critical_clause():
    return func.lower(func.trim(TestCase.priority)) == CRITICAL


def name_list(names: Sequence[str], cap: int = DETAIL_CAP) -> str:
    shown = ", ".join(names[:cap])
    return shown + (f" (+{len(names) - cap} more)" if len(names) > cap else "")


def evidence_time():
    """When a result became evidence. finalized_at is when the run finished;
    rows older than that column fall back to creation."""
    return func.coalesce(TestRun.finalized_at, TestRun.created_at)


# ---------------------------------------------------------------------------
# Pure decision logic (unit-tested without a database)
# ---------------------------------------------------------------------------


def classify_case(rows: Iterable[ResultRow]) -> Tuple[str, Optional[int], Optional[str]]:
    """(status, run_id, detail) for one case from its results in scope."""
    newest: Dict[tuple, ResultRow] = {}
    for r in rows:
        key = (r.browser or "", r.device or "")
        if key not in newest or r.result_id > newest[key].result_id:
            newest[key] = r
    if not newest:
        return NOT_RUN, None, None
    bad = [r for r in newest.values() if r.status != TestStatus.PASSED]
    if bad:
        worst = max(bad, key=lambda r: r.result_id)
        where = " / ".join(x for x in (worst.browser, worst.device) if x)
        return FAILED, worst.run_id, f"{worst.status.value} in run {worst.run_id}" + (
            f" ({where})" if where else "")
    return PASSED, max(newest.values(), key=lambda r: r.result_id).run_id, None


def flaky_cases_check(flaky_names: Sequence[str], limit: int) -> QualityGateCheck:
    return QualityGateCheck(
        name="flaky_cases", passed=len(flaky_names) <= limit,
        actual=f"{len(flaky_names)} flaky", threshold=f"<= {limit}",
        detail=name_list(sorted(flaky_names)) or None)


def critical_passed_check(critical: Sequence[Tuple[int, str]], status: Dict[int, str],
                          scope_label: str) -> QualityGateCheck:
    if not critical:
        return QualityGateCheck(
            name="critical_passed", passed=False, actual="no critical cases",
            threshold="all critical passed", missing_data=True,
            detail="no case in this project has priority 'critical' — mark the "
                   "cases a release depends on, or turn this check off")
    failed = [n for cid, n in critical if status.get(cid) == FAILED]
    missing = [n for cid, n in critical if status.get(cid, NOT_RUN) == NOT_RUN]
    ok = len(critical) - len(failed) - len(missing)
    parts = []
    if failed:
        parts.append("failed: " + name_list(failed))
    if missing:
        parts.append(f"no result {scope_label}: " + name_list(missing))
    return QualityGateCheck(
        name="critical_passed", passed=not failed and not missing,
        actual=f"{ok} of {len(critical)} passed", threshold="all critical passed",
        detail="; ".join(parts) or None,
        missing_data=bool(missing) and not failed)


def evidence_age_check(cases: Sequence[Tuple[int, str]], newest_pass: Dict[int, datetime],
                       max_days: int, now: datetime, scope_label: str) -> QualityGateCheck:
    threshold = f"<= {max_days}d ({scope_label})"
    if not cases:
        return QualityGateCheck(
            name="evidence_age", passed=False, actual="no cases", threshold=threshold,
            missing_data=True, detail="the project has no test cases")
    never = [n for cid, n in cases if cid not in newest_pass]
    ages = {cid: (now - newest_pass[cid]).total_seconds() / 86400
            for cid, _ in cases if cid in newest_pass}
    names = dict(cases)
    stale = sorted((cid for cid, age in ages.items() if age > max_days),
                   key=lambda c: -ages[c])
    if ages:
        oldest = max(ages, key=lambda c: ages[c])
        actual = f"oldest {ages[oldest]:.1f}d ({names[oldest]})"
    else:
        actual = "no passing result"
    if never:
        actual += f", {len(never)} never passed"
    parts = []
    if stale:
        parts.append("stale: " + name_list([f"{names[c]} {ages[c]:.0f}d" for c in stale]))
    if never:
        parts.append("never passed: " + name_list(never))
    return QualityGateCheck(
        name="evidence_age", passed=not stale and not never, actual=actual,
        threshold=threshold, detail="; ".join(parts) or None,
        missing_data=bool(never) and not stale)


def signoff_check(latest: Optional[Tuple[str, str, Optional[str]]], scope_label: str) -> QualityGateCheck:
    """latest = (verdict, release_label, signer name) of the newest sign-off."""
    if latest is None:
        return QualityGateCheck(
            name="manual_signoff", passed=False, actual="no sign-off",
            threshold="approved sign-off", missing_data=True,
            detail=f"nobody has signed off a release {scope_label}")
    verdict, label, signer = latest
    return QualityGateCheck(
        name="manual_signoff", passed=verdict == "approve",
        actual=f"{'approved' if verdict == 'approve' else 'rejected'}: {label}",
        threshold="approved sign-off", detail=f"by {signer}" if signer else None)


def requirement_status(case_statuses: Sequence[str]) -> str:
    if any(s == FAILED for s in case_statuses):
        return "failing"
    passed = sum(1 for s in case_statuses if s == PASSED)
    if passed and passed == len(case_statuses):
        return "passing"
    return "partial" if passed else "untested"


def verdict_for(gate: QualityGateResult, has_runs: bool) -> Tuple[str, List[str]]:
    failing = [c for c in gate.checks if not c.passed]
    reasons = [f"{c.name}: {c.actual} (needs {c.threshold})"
               + (f" — {c.detail}" if c.detail else "") for c in failing]
    if not has_runs:
        return "insufficient_data", reasons
    if not failing:
        ok = [f"all {len(gate.checks)} checks passed over "
              f"{len(gate.evaluated_run_ids)} run(s)"]
        partial = [c for c in gate.checks if c.name == "coverage" and c.detail]
        ok += [f"coverage: {c.actual}" for c in partial]
        return "ready", ok
    if all(c.missing_data for c in failing):
        return "insufficient_data", reasons
    return "not_ready", reasons


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


async def flaky_case_names(session: AsyncSession, project_id: int,
                           now: Optional[datetime] = None) -> List[str]:
    rows = (await session.exec(
        select(TestCase.id, TestCase.name).distinct()
        .join(FlakeRecord, FlakeRecord.test_case_id == TestCase.id)
        .where(TestCase.project_id == project_id,
               or_(active_quarantine_clause(now),
                   FlakeRecord.flake_score >= QUARANTINE_SCORE)))).all()
    return [name for _id, name in rows]


async def critical_cases(session: AsyncSession, project_id: int) -> List[Tuple[int, str]]:
    rows = (await session.exec(
        select(TestCase.id, TestCase.name)
        .where(TestCase.project_id == project_id, _critical_clause())
        .order_by(TestCase.name))).all()
    return [(i, n) for i, n in rows]


async def results_in_scope(session: AsyncSession, project_id: int,
                           git_commit: Optional[str], run_ids: Sequence[int],
                           case_ids: Optional[Iterable[int]] = None) -> Dict[int, List[ResultRow]]:
    """Results per case: every finished run on `git_commit` when given, else
    the given runs. Project-filtered either way."""
    stmt = (select(TestCaseResult.id, TestCaseResult.test_case_id, TestCaseResult.status,
                   TestRun.id, TestRun.browser, TestRun.device)
            .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
            .where(TestRun.project_id == project_id, counted(),
                   TestCaseResult.test_case_id.is_not(None)))
    if git_commit:
        stmt = stmt.where(TestRun.git_commit == git_commit, TestRun.status.in_(FINISHED))
    else:
        if not run_ids:
            return {}
        stmt = stmt.where(TestRun.id.in_(list(run_ids)))
    if case_ids is not None:
        ids = list(case_ids)
        if not ids:
            return {}
        stmt = stmt.where(TestCaseResult.test_case_id.in_(ids))
    out: Dict[int, List[ResultRow]] = defaultdict(list)
    for row in (await session.exec(stmt)).all():
        r = ResultRow(*row)
        out[r.case_id].append(r)
    return out


async def newest_passes(session: AsyncSession, project_id: int,
                        case_ids: Sequence[int]) -> Dict[int, datetime]:
    if not case_ids:
        return {}
    rows = (await session.exec(
        select(TestCaseResult.test_case_id, func.max(evidence_time()))
        .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
        .where(TestRun.project_id == project_id, counted(),
               TestCaseResult.status == TestStatus.PASSED,
               TestCaseResult.test_case_id.in_(list(case_ids)))
        .group_by(TestCaseResult.test_case_id))).all()
    return {cid: at for cid, at in rows}


async def readiness_gate_checks(session: AsyncSession, project_id: int,
                                policy: QualityGatePolicy, run_ids: Sequence[int],
                                git_commit: Optional[str],
                                now: Optional[datetime] = None) -> List[QualityGateCheck]:
    """The opt-in checks above, for evaluate_gate_for_runs."""
    now = now or datetime.utcnow()
    checks: List[QualityGateCheck] = []
    if policy.max_flaky_cases is not None:
        checks.append(flaky_cases_check(
            await flaky_case_names(session, project_id, now), policy.max_flaky_cases))

    if getattr(policy, "require_manual_signoff", False):
        from app.models import ReleaseSignoff
        q = (select(ReleaseSignoff.verdict, ReleaseSignoff.release_label, User.full_name, User.email)
             .join(User, User.id == ReleaseSignoff.signed_by_id, isouter=True)
             .where(ReleaseSignoff.project_id == project_id))
        if git_commit:
            q = q.where(ReleaseSignoff.git_commit == git_commit)
        row = (await session.exec(q.order_by(ReleaseSignoff.signed_at.desc(),
                                             ReleaseSignoff.id.desc()).limit(1))).first()
        checks.append(signoff_check(
            (row[0], row[1], row[2] or row[3]) if row else None,
            f"for commit {git_commit[:12]}" if git_commit else "for this project"))

    if not policy.require_critical_passed and not policy.max_evidence_age_days:
        return checks
    critical = await critical_cases(session, project_id)
    scope_label = f"for commit {git_commit[:12]}" if git_commit else "in the evaluated runs"

    if policy.require_critical_passed:
        by_case = await results_in_scope(session, project_id, git_commit, run_ids,
                                         [c for c, _ in critical])
        status = {cid: classify_case(by_case.get(cid, []))[0] for cid, _ in critical}
        checks.append(critical_passed_check(critical, status, scope_label))

    if policy.max_evidence_age_days:
        if critical:
            scope, label = critical, "critical cases"
        else:
            scope = [(i, n) for i, n in (await session.exec(
                select(TestCase.id, TestCase.name)
                .where(TestCase.project_id == project_id)
                .order_by(TestCase.name))).all()]
            label = "all cases — none is critical"
        passes = await newest_passes(session, project_id, [c for c, _ in scope])
        checks.append(evidence_age_check(scope, passes, policy.max_evidence_age_days,
                                         now, label))
    return checks


# ---------------------------------------------------------------------------
# Readiness page
# ---------------------------------------------------------------------------


async def resolve_runs(session: AsyncSession, project_id: int, git_commit: Optional[str],
                       git_branch: Optional[str]) -> Tuple[str, Optional[str], List[TestRun]]:
    """(scope, commit, finished runs). Without a commit, the newest finished
    run (on the branch, if given) picks the commit, so readiness judges a
    whole commit rather than whichever single run happened to finish last."""
    base = select(TestRun).where(TestRun.project_id == project_id,
                                 TestRun.status.in_(FINISHED), counted())
    if git_commit:
        runs = (await session.exec(
            base.where(TestRun.git_commit == git_commit)
            .order_by(TestRun.created_at.desc()))).all()
        return "commit", git_commit, list(runs)
    anchor_q = base
    if git_branch:
        anchor_q = anchor_q.where(TestRun.git_branch == git_branch)
    anchor = (await session.exec(anchor_q.order_by(TestRun.created_at.desc()).limit(1))).first()
    scope = "branch" if git_branch else "latest_run"
    if anchor is None:
        return "none", None, []
    if not anchor.git_commit:
        return scope, None, [anchor]
    runs = (await session.exec(
        base.where(TestRun.git_commit == anchor.git_commit)
        .order_by(TestRun.created_at.desc()))).all()
    return scope, anchor.git_commit, list(runs)


async def _last_results(session: AsyncSession, project_id: int,
                        case_ids: Sequence[int]) -> Dict[int, tuple]:
    """Newest result per case anywhere in the project: (status, at, commit)."""
    if not case_ids:
        return {}
    newest = (select(func.max(TestCaseResult.id))
              .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
              .where(TestRun.project_id == project_id, counted(),
                     TestCaseResult.test_case_id.in_(list(case_ids)))
              .group_by(TestCaseResult.test_case_id))
    rows = (await session.exec(
        select(TestCaseResult.test_case_id, TestCaseResult.status, evidence_time(),
               TestRun.git_commit)
        .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
        .where(TestCaseResult.id.in_(newest)))).all()
    return {cid: (st, at, commit) for cid, st, at, commit in rows}


async def recent_commits(session: AsyncSession, project_id: int,
                         limit: int = 20) -> List[ReadinessCommit]:
    rows = (await session.exec(
        select(TestRun.git_commit, func.max(TestRun.git_branch),
               func.max(TestRun.created_at), func.count())
        .where(TestRun.project_id == project_id, TestRun.git_commit.is_not(None),
               TestRun.status.in_(FINISHED), counted())
        .group_by(TestRun.git_commit)
        .order_by(func.max(TestRun.created_at).desc())
        .limit(limit))).all()
    return [ReadinessCommit(git_commit=c, git_branch=b, last_run_at=at, runs=n)
            for c, b, at, n in rows]


async def build_readiness(session: AsyncSession, project_id: int, scope: str,
                          git_commit: Optional[str], git_branch: Optional[str],
                          runs: List[TestRun], gate: QualityGateResult) -> ReleaseReadiness:
    run_ids = [r.id for r in runs]
    verdict, reasons = verdict_for(gate, bool(runs))
    if not runs:
        if git_commit:
            reasons.insert(0, f"no finished run for commit {git_commit}")
        elif git_branch:
            reasons.insert(0, f"no finished run on branch {git_branch}")
        else:
            reasons.insert(0, "no finished runs in this project")

    # Critical cases: on-commit status + last evidence anywhere.
    critical = await critical_cases(session, project_id)
    links = (await session.exec(
        select(RequirementLink, TestCase.name)
        .join(TestCase, TestCase.id == RequirementLink.test_case_id)
        .where(RequirementLink.project_id == project_id,
               TestCase.project_id == project_id)
        .order_by(RequirementLink.ref))).all()
    wanted = {c for c, _ in critical} | {link.test_case_id for link, _ in links}
    by_case = await results_in_scope(session, project_id, git_commit, run_ids, wanted)
    status = {cid: classify_case(by_case.get(cid, [])) for cid in wanted}

    crit_ids = [c for c, _ in critical]
    last = await _last_results(session, project_id, crit_ids)
    last_pass = await newest_passes(session, project_id, crit_ids)
    critical_rows = []
    for cid, name in critical:
        st, run_id, detail = status[cid]
        if st == NOT_RUN:
            detail = ("no run for this commit" if git_commit
                      else "not in the evaluated runs")
        ls = last.get(cid)
        critical_rows.append(ReadinessCriticalCase(
            case_id=cid, name=name, status=st, run_id=run_id, detail=detail,
            last_status=ls[0].value if ls else None, last_at=ls[1] if ls else None,
            last_commit=ls[2] if ls else None, last_passed_at=last_pass.get(cid)))

    # Active quarantines, with owners.
    flakes = (await session.exec(
        select(FlakeRecord, TestCase.name)
        .join(TestCase, TestCase.id == FlakeRecord.test_case_id)
        .where(TestCase.project_id == project_id, active_quarantine_clause())
        .order_by(FlakeRecord.quarantine_expires_at))).all()
    owner_ids = {f.owner_user_id for f, _ in flakes if f.owner_user_id}
    owners = {u.id: u for u in (await session.exec(
        select(User).where(User.id.in_(owner_ids)))).all()} if owner_ids else {}
    quarantines = []
    for f, name in flakes:
        o = owners.get(f.owner_user_id)
        quarantines.append(ReadinessQuarantine(
            flake_record_id=f.id, case_id=f.test_case_id, case_name=name,
            flake_score=f.flake_score, source=f.quarantine_source or "auto",
            reason=f.quarantine_reason, quarantined_at=f.quarantined_at,
            expires_at=f.quarantine_expires_at,
            owner=ReadinessPerson(id=o.id, email=o.email, full_name=o.full_name) if o else None))

    # Requirements, judged on the same evidence as the critical cases.
    by_ref: Dict[str, dict] = {}
    for link, name in links:
        b = by_ref.setdefault(link.ref, {"link": link, "cases": []})
        b["cases"].append(ReadinessRequirementCase(
            case_id=link.test_case_id, name=name, status=status[link.test_case_id][0]))
    requirements = [
        ReadinessRequirement(
            ref=ref, source=b["link"].source, title=b["link"].title, url=b["link"].url,
            status=requirement_status([c.status for c in b["cases"]]), cases=b["cases"])
        for ref, b in by_ref.items()]
    order = {"failing": 0, "untested": 1, "partial": 2, "passing": 3}
    requirements.sort(key=lambda r: (order[r.status], r.ref))

    return ReleaseReadiness(
        project_id=project_id, verdict=verdict, reasons=reasons, scope=scope,
        git_commit=git_commit, git_branch=git_branch, gate=gate,
        runs=[ReadinessRun(
            id=r.id, status=r.status, created_at=r.created_at, suite_name=r.suite_name,
            browser=r.browser, git_commit=r.git_commit, git_branch=r.git_branch,
            selection_mode=r.selection_mode, total_tests=r.total_tests or 0,
            passed_tests=r.passed_tests or 0, failed_tests=r.failed_tests or 0)
            for r in runs],
        critical_cases=critical_rows, quarantines=quarantines, requirements=requirements,
        recent_commits=await recent_commits(session, project_id))

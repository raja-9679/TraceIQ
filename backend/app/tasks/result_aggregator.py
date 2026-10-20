"""
Result Aggregator - Processes job results and updates TestRun records

This Celery task:
1. Consumes results from Redis stream
2. Creates TestCaseResult records
3. Updates TestRun progress and status
4. Publishes real-time updates via WebSocket

Staleness Detection Strategy
----------------------------
A run is considered "stuck" ONLY if no new job result has arrived within
STALE_RUN_INACTIVITY_MINUTES (default 15 min). Large suites (700+ tests)
may legitimately run for hours if the worker pool is small — they must NOT
be killed purely based on wall-clock age.

Every time a result is processed, we record `last_progress_at` in Redis:
    HSET runs:{id}:progress last_progress_at <iso-timestamp>

check_stale_runs() reads this field to decide whether a run is inactive.
"""
import json
from datetime import datetime
import math
from typing import Dict, Any, List, Optional
import redis
from sqlmodel import Session, create_engine, select
from app.core.celery_app import celery_app
from app.core.config import db_url_for, settings
from app.models import TestRun, TestCaseResult, TestStatus, TestCase
from app.services.run_completion import finalize_on_commit
from app.services.redaction import redact_worker_result
from app.core.secrets import decrypt_json, encrypt_json

# Sync database engine for Celery
sync_db_url = db_url_for(settings.DATABASE_URL, sync=True)
sync_engine = create_engine(sync_db_url, echo=False)

# Sync Redis client
redis_client = redis.from_url(
    settings.CELERY_BROKER_URL, decode_responses=True)

RESULTS_STREAM = 'jobs:results'
CONSUMER_GROUP = 'result-processors'
CONSUMER_NAME = 'aggregator-1'


@celery_app.task(name="app.tasks.result_aggregator.process_job_results")
def process_job_results():
    """
    Process job results from Redis stream.
    Runs periodically via Celery Beat.
    """
    processed_count = 0
    max_messages = 20  # Process batch per invocation

    try:
        # Ensure consumer group exists
        try:
            redis_client.xgroup_create(
                RESULTS_STREAM, CONSUMER_GROUP, id='0', mkstream=True)
        except redis.ResponseError as e:
            if 'BUSYGROUP' not in str(e):
                raise

        # Read messages from stream
        messages = redis_client.xreadgroup(
            CONSUMER_GROUP,
            CONSUMER_NAME,
            {RESULTS_STREAM: '>'},
            count=max_messages,
            block=1000  # Block for 1 second
        )

        if not messages:
            return processed_count

        for stream_name, stream_messages in messages:
            for message_id, fields in stream_messages:
                try:
                    result_json = fields.get('result')
                    if not result_json:
                        print(
                            f"[Aggregator] Invalid message format: {message_id}")
                        redis_client.xack(
                            RESULTS_STREAM, CONSUMER_GROUP, message_id)
                        continue

                    result = json.loads(result_json)

                    # Process the result
                    process_single_result(result)

                    # Acknowledge message
                    redis_client.xack(
                        RESULTS_STREAM, CONSUMER_GROUP, message_id)
                    processed_count += 1

                except Exception as e:
                    print(
                        f"[Aggregator] Error processing message {message_id}: {e}")
                    # Don't ack - will be reprocessed

        if processed_count > 0:
            print(f"[Aggregator] Processed {processed_count} job results")

    except Exception as e:
        print(f"[Aggregator] Error accessing Redis stream: {e}")

    return processed_count


def process_single_result(result: Dict[str, Any]):
    """Process a single job result and update database"""
    # Defence in depth. The worker already scrubbed this at capture time,
    # but the worker image bakes its code at build time and can be older
    # than this backend — in that window it ships raw headers and bodies.
    # Redacting here means an un-upgraded worker cannot write a credential
    # into an unencrypted column. Routing/status fields are untouched.
    result = redact_worker_result(result)
    run_id = result.get('run_id')
    job_id = result.get('job_id')

    # Persist a captured auth session (storageState) if the job ran a
    # passing auth-setup case. Job-level field in both single and
    # continuous results.
    _persist_auth_state(result)

    # Persist reactive selector-heal suggestions as pending proposals.
    _persist_heal_suggestions(result)

    # Check if this is a multi-test continuous job result
    test_results = result.get('test_results')

    if test_results and len(test_results) > 0:
        # Multi-test continuous job - process each test result
        process_continuous_job_result(run_id, job_id, result, test_results)
    else:
        # Single test job - original behavior
        process_single_test_result(run_id, job_id, result)


def _persist_auth_state(result: Dict[str, Any]):
    """Upsert the project's AuthSession from a worker-captured storageState."""
    auth_state = result.get('auth_state')
    if not auth_state:
        return

    case_id = result.get('auth_case_id') or result.get('test_case_id')
    if not case_id:
        return

    from datetime import datetime
    from app.models import TestCase, AuthSession

    try:
        with Session(sync_engine) as session:
            case = session.get(TestCase, case_id)
            if not case or not case.project_id:
                print(f"[Aggregator] auth_state received but case {case_id} "
                      f"has no project — skipped")
                return
            auth = session.exec(
                select(AuthSession).where(
                    AuthSession.project_id == case.project_id)
            ).first()
            # Live session cookies + localStorage for a logged-in account on
            # the app under test. Encrypted at rest so DB read access does
            # not hand over resumable sessions.
            sealed = encrypt_json(auth_state)
            if auth:
                auth.storage_state = sealed
                auth.captured_by_case_id = case.id
                auth.captured_at = datetime.utcnow()
            else:
                auth = AuthSession(
                    project_id=case.project_id,
                    storage_state=sealed,
                    captured_by_case_id=case.id,
                )
            session.add(auth)
            session.commit()
            print(f"[Aggregator] Stored auth session for project "
                  f"{case.project_id} (captured by case {case.id})")
    except Exception as e:
        print(f"[Aggregator] Failed to persist auth session: {e}")


def update_run_from_progress(run: TestRun, run_id: int, progress: Dict[str, str], session: Session):
    """Update run status and progress from Redis progress tracking"""
    if not progress:
        return

    run.total_tests = int(progress.get('total', run.total_tests))
    run.passed_tests = int(progress.get('passed', 0))
    run.failed_tests = int(progress.get('failed', 0))

    # Check if run is complete
    completed = int(progress.get('completed', 0))
    total = int(progress.get('total', 0))

    if completed >= total and total > 0:
        # All jobs complete - finalize run
        if run.failed_tests > 0:
            run.status = TestStatus.FAILED
            # Clear stale timeout error_message; the real failures are in
            # individual TestCaseResult records.
            run.error_message = None
            # Kick off typed failure analysis (heuristic always; LLM when a
            # provider is configured). Fire-and-forget.
            # Queue it only once THIS transaction commits: the run's network
            # events and final results are written in it, and a worker that
            # picked the task up immediately read the run before they existed
            # (failure analysis then saw no network failures).
            try:
                from sqlalchemy import event
                from app.tasks.analysis_tasks import analyze_run_failures
                event.listen(session, "after_commit",
                             lambda _s: analyze_run_failures.delay(run_id), once=True)
            except Exception as e:
                print(f"[Aggregator] Could not queue failure analysis: {e}")
        else:
            run.status = TestStatus.PASSED
            # Clear any stale error_message (e.g. from a previous timeout
            # marking by check_stale_runs that was later resolved by
            # arriving results).
            run.error_message = None

        # Calculate wall-clock duration from when the first worker actually
        # picked up a job (worker_started_at), not from queue dispatch time
        # (created_at). Falls back to created_at if the field is absent.
        from datetime import datetime, timezone
        worker_started_str = progress.get('worker_started_at') if progress else None
        start_ref = None
        if worker_started_str:
            try:
                start_ref = datetime.fromisoformat(worker_started_str)
            except ValueError:
                start_ref = None
        if start_ref is None and run.created_at:
            start_ref = run.created_at
        if start_ref is not None:
            # Handle timezone-aware vs naive datetime
            now = datetime.now(
                timezone.utc) if getattr(start_ref, 'tzinfo', None) else datetime.utcnow()
            run.duration_ms = int(
                (now - start_ref).total_seconds() * 1000)

        # Get results for artifact copying
        results = session.exec(
            select(TestCaseResult).where(
                TestCaseResult.test_run_id == run_id)
        ).all()

        # Copy video/trace/HAR from results to run level
        if len(results) == 1 and results[0]:
            if results[0].video_url and not run.video_url:
                run.video_url = results[0].video_url
            if results[0].trace_url and not run.trace_url:
                run.trace_url = results[0].trace_url
            if results[0].har_url and not run.har_url:
                run.har_url = results[0].har_url
        elif len(results) > 1:
            # For multi-test runs, use the first result's artifacts as run-level
            first_with_video = next(
                (r for r in results if r.video_url), None)
            first_with_trace = next(
                (r for r in results if r.trace_url), None)
            first_with_har = next(
                (r for r in results if r.har_url), None)
            if first_with_video and not run.video_url:
                run.video_url = first_with_video.video_url
            if first_with_trace and not run.trace_url:
                run.trace_url = first_with_trace.trace_url
            if first_with_har and not run.har_url:
                run.har_url = first_with_har.har_url

        print(
            f"[Aggregator] Run {run_id} completed: {run.passed_tests} passed, {run.failed_tests} failed, duration={run.duration_ms}ms")

        # Update per-case flake scores now that the run is finalized.
        # Never allowed to break aggregation — fully guarded internally.
        _update_flake_scores(run, run_id, session)

        # Notifications, outbound webhooks, monitor check, paging, scans,
        # triage — once per run, queued after this transaction commits.
        # This used to happen only via the legacy engine's /finalize call.
        finalize_on_commit(session, run_id)
    else:
        run.status = TestStatus.RUNNING


def _update_flake_scores(run: TestRun, run_id: int, session: Session):
    """Post-process results of a finished run: link, validate, flake-score.

    Besides flake scoring, this stamps TestCaseResult.test_case_id on every
    result it can resolve, and TestCase.last_validated_commit/_at when a case
    PASSED on a run that carries git_commit.

    For each TestCaseResult of the run, resolve its TestCase (run.test_case_id
    when the run targeted a single case, otherwise TestCase looked up by name)
    and score the last 20 completed results for that test name:

        flake_score = pass<->fail alternations / (window - 1)

    A perfectly stable test (all pass or all fail) scores 0.0; a test that
    flips on every run scores 1.0. The score is upserted into the case-level
    FlakeRecord (step_id=None). Auto-quarantine at score >= 0.4 with at least
    6 samples; auto-release when the score drops below 0.15 — automatic
    quarantines only (rules in app/services/flake_policy.py).

    Wrapped in try/except so scoring can never break result aggregation.
    """
    FLAKE_WINDOW = 20

    try:
        from app.models import FlakeRecord
        from app.services import flake_policy

        results = session.exec(
            select(TestCaseResult).where(TestCaseResult.test_run_id == run_id)
        ).all()

        scored_case_ids = set()
        case_of_result: Dict[int, int] = {}
        score_of_case: Dict[int, float] = {}
        for res in results:
            # Resolve the TestCase behind this result. Prefer an id already
            # stamped on the result (worker payload / earlier pass), then the
            # run's single-case target, then name matching.
            test_case = None
            if res.test_case_id:
                test_case = session.get(TestCase, res.test_case_id)
            if test_case is None and run.test_case_id:
                test_case = session.get(TestCase, run.test_case_id)
            if test_case is None and run.test_suite_id:
                # Name match, but accept ONLY an unambiguous single match. The
                # old code took .first() of a project-wide match, so a run of a
                # parent suite could stamp an arbitrary same-named case from a
                # sibling suite — and impact-analysis v2 then trusts that wrong
                # id (plus validation/flake state) permanently.
                matches = session.exec(select(TestCase).where(
                    TestCase.name == res.test_name,
                    TestCase.test_suite_id == run.test_suite_id)).all()
                if len(matches) == 1:
                    test_case = matches[0]
            if test_case is None and run.project_id:
                # Project-scoped fallback (covers nested suites) — again only if
                # the name is unique across the project. If it's duplicated we
                # leave the result unresolved rather than guess.
                matches = session.exec(select(TestCase).where(
                    TestCase.name == res.test_name,
                    TestCase.project_id == run.project_id)).all()
                if len(matches) == 1:
                    test_case = matches[0]
            if test_case is None:
                # Unresolved or ambiguous — do NOT guess a case to stamp.
                continue

            # Stamp the resolved link on the result (impact-analysis v2 /
            # run-history rely on this id instead of name matching).
            if res.test_case_id is None:
                res.test_case_id = test_case.id
                session.add(res)

            # A pass on a run that carries git context validates the case at
            # that commit — the "does this case still match the code?" stamp.
            # Don't let it move BACKWARDS: runs finish out of order (retries,
            # bisect/backfill of old commits), so only advance the stamp when
            # this run is at least as new as whatever last validated the case.
            if res.status == TestStatus.PASSED and run.git_commit:
                prior = test_case.last_validated_at
                if prior is None or (run.created_at and prior <= run.created_at):
                    test_case.last_validated_commit = run.git_commit
                    test_case.last_validated_at = datetime.utcnow()
                    session.add(test_case)

            case_of_result[res.id] = test_case.id
            if test_case.id in scored_case_ids:
                continue
            scored_case_ids.add(test_case.id)

            # Last N completed results for this test name across runs
            history_stmt = (
                select(TestCaseResult)
                .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
                .where(
                    TestCaseResult.test_name == res.test_name,
                    TestCaseResult.status.in_(
                        [TestStatus.PASSED, TestStatus.FAILED, TestStatus.ERROR]),
                )
                .order_by(TestCaseResult.id.desc())
                .limit(FLAKE_WINDOW)
            )
            if run.project_id:
                history_stmt = history_stmt.where(
                    TestRun.project_id == run.project_id)
            history = session.exec(history_stmt).all()

            sample_count = len(history)
            if sample_count >= 2:
                outcomes = [h.status == TestStatus.PASSED for h in history]
                alternations = sum(
                    1 for a, b in zip(outcomes, outcomes[1:]) if a != b)
                flake_score = alternations / (sample_count - 1)
            else:
                flake_score = 0.0

            last_failure = next(
                (h.error_message for h in history
                 if h.status != TestStatus.PASSED and h.error_message),
                None)

            record = session.exec(
                select(FlakeRecord).where(
                    FlakeRecord.test_case_id == test_case.id,
                    FlakeRecord.step_id.is_(None))
            ).first()
            if record is None:
                record = FlakeRecord(test_case_id=test_case.id, step_id=None)

            score_of_case[test_case.id] = flake_score
            record.flake_score = round(flake_score, 4)
            record.sample_count = sample_count
            if last_failure:
                record.last_failure_message = last_failure[:2000]
            record.last_observed_at = datetime.utcnow()

            transition = flake_policy.apply_score(record, flake_score, sample_count)
            if transition:
                print(f"[Aggregator] Case {test_case.id} ('{res.test_name}') "
                      f"{transition} automatically — flake_score={flake_score:.2f} "
                      f"over {sample_count} samples")

            session.add(record)

        for res in results:
            score = score_of_case.get(case_of_result.get(res.id))
            is_flaky, confidence = flake_verdict(
                res.status, res.retry_count or 0, score)
            if res.is_flaky != is_flaky or res.confidence != confidence:
                res.is_flaky = is_flaky
                res.confidence = confidence
                session.add(res)

        if scored_case_ids:
            print(f"[Aggregator] Updated flake scores for "
                  f"{len(scored_case_ids)} case(s) after run {run_id}")
    except Exception as e:
        print(f"[Aggregator] Flake scoring failed for run {run_id}: {e}")


def _evidence_payload(res: Dict[str, Any], run_id: Optional[int] = None) -> Dict[str, Any]:
    """Failure-analysis evidence a worker attached to one test's result.

    `console_errors` feed failure analysis; `step_dom` (step id -> compact
    DOM, sent only at capture level `full`) feeds proactive selector heal;
    `attempts` shows each try of a retried test in the run UI. Older workers
    send none of them, so all are optional.
    """
    out: Dict[str, Any] = {}
    if res.get('console_errors'):
        out['console_errors'] = res['console_errors'][:20]
    if isinstance(res.get('step_dom'), dict) and res['step_dom']:
        out['step_dom'] = res['step_dom']
    attempts = _attempts(res.get('attempts'))
    if attempts:
        out['attempts'] = attempts
    step_evidence = normalise_step_evidence(res.get('step_evidence'), run_id)
    if step_evidence:
        out['step_evidence'] = step_evidence
    # Suite setup/teardown + cleanup outcomes -> `hooks`, `cleanup` and
    # `hook_warnings` (teardown/cleanup failures never change the status).
    from app.services.suite_hooks import hook_payload
    out.update(hook_payload(res.get('hook_report')))
    return out


MAX_EVIDENCE_STEPS = 200
_MAX_EVIDENCE_ITEMS = 10
# Console + request items across one result (the worker caps at 150).
MAX_EVIDENCE_ITEMS_PER_RESULT = 200
_EVIDENCE_STATUSES = {'passed', 'failed', 'skipped', 'healed'}


def _clip(value: Any, limit: int) -> Optional[str]:
    return value[:limit] if isinstance(value, str) else None


def normalise_step_evidence(raw: Any, run_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Per-step evidence (worker `step_evidence`), rebuilt field by field.

    Like `attempts` it can come from a local worker, i.e. from any API-key
    holder, so nothing is stored as sent: known keys only, types coerced,
    strings and lists capped, malformed entries dropped (never raised — an
    exception here would leave the job's result un-acked). Ids are re-derived
    from the index, because failure investigation cites them and must be able
    to trust that `s3.c0` means "first console item of step 3". A screenshot
    key is kept only under THIS run's prefix: the UI presigns it, and a key
    naming another run would be a pointer into someone else's artifacts.
    """
    if not isinstance(raw, list):
        return []
    shot_prefix = f"runs/{run_id}/screenshots/" if run_id is not None else None
    out: List[Dict[str, Any]] = []
    seen = set()
    budget = MAX_EVIDENCE_ITEMS_PER_RESULT
    for item in raw[:MAX_EVIDENCE_STEPS]:
        if not isinstance(item, dict):
            continue
        index = item.get('index')
        if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index in seen:
            continue
        seen.add(index)
        sid = f"s{index}"
        status = item.get('status') if item.get('status') in _EVIDENCE_STATUSES else 'passed'
        entry: Dict[str, Any] = {
            'id': sid, 'index': index,
            'type': _clip(item.get('type'), 60) or 'unknown',
            'status': status,
        }
        for key, limit in (('step_id', 100), ('selector', 300), ('error', 2000)):
            v = _clip(item.get(key), limit)
            if v:
                entry[key] = v
        if isinstance(item.get('duration_ms'), (int, float)) and not isinstance(item.get('duration_ms'), bool):
            entry['duration_ms'] = max(0, int(item['duration_ms']))
        console = []
        for c in (item.get('console') or [])[:min(_MAX_EVIDENCE_ITEMS, budget)] if isinstance(item.get('console'), list) else []:
            if isinstance(c, dict) and isinstance(c.get('text'), str):
                console.append({'id': f"{sid}.c{len(console)}",
                                'type': _clip(c.get('type'), 20) or 'error',
                                'text': c['text'][:500]})
        if console:
            entry['console'] = console
        budget -= len(console)
        requests = []
        for r in (item.get('requests') or [])[:min(_MAX_EVIDENCE_ITEMS, budget)] if isinstance(item.get('requests'), list) else []:
            if not isinstance(r, dict) or not isinstance(r.get('url'), str):
                continue
            req: Dict[str, Any] = {'id': f"{sid}.r{len(requests)}",
                                   'method': (_clip(r.get('method'), 10) or 'GET').upper(),
                                   'url': r['url'][:500]}
            if isinstance(r.get('status'), int) and not isinstance(r.get('status'), bool):
                req['status'] = r['status']
            for key, limit in (('failure', 200), ('resource_type', 30)):
                v = _clip(r.get(key), limit)
                if v:
                    req[key] = v
            requests.append(req)
        if requests:
            entry['requests'] = requests
        budget -= len(requests)
        shot = item.get('screenshot')
        if isinstance(shot, str) and shot_prefix and shot.startswith(shot_prefix) and '..' not in shot:
            entry['screenshot'] = shot[:300]
        dom = _clip(item.get('dom'), 4000)
        if dom:
            entry['dom'] = dom
            entry['dom_scope'] = 'target' if item.get('dom_scope') == 'target' else 'page'
        out.append(entry)
    out.sort(key=lambda e: e['index'])
    return out


MAX_ATTEMPTS_KEPT = 10


def _attempts(raw: Any) -> List[Dict[str, Any]]:
    """Per-attempt outcomes of a retried test (worker sends them only when a
    retry happened). Normalised and capped: it is shown in the run UI, not
    trusted for anything."""
    if not isinstance(raw, list):
        return []
    # A bad entry is dropped, never raised: an exception here would leave the
    # whole job's result un-acked (local-worker results come from API-key
    # holders, so the shape is not guaranteed).
    out = []
    for item in raw[:MAX_ATTEMPTS_KEPT]:
        if not isinstance(item, dict) or not isinstance(item.get('status'), str):
            continue
        number = item.get('attempt')
        entry: Dict[str, Any] = {
            'attempt': number if isinstance(number, int) and not isinstance(number, bool) and number > 0
            else len(out) + 1,
            'status': item['status'][:20],
        }
        if isinstance(item.get('error'), str):
            entry['error'] = item['error'][:1000]
        duration = item.get('duration_ms')
        if (isinstance(duration, (int, float)) and not isinstance(duration, bool)
                and math.isfinite(duration) and duration >= 0):
            entry['duration_ms'] = int(duration)
        out.append(entry)
    return out


def _case_payload(test_res: Dict[str, Any], run_id: Optional[int] = None) -> Dict[str, Any]:
    """result_payload for one case of a continuous job."""
    out = _evidence_payload(test_res, run_id)
    if test_res.get('web_vitals'):
        out['web_vitals'] = test_res['web_vitals']
    return out


def flake_verdict(status, retry_count: int, flake_score: Optional[float]):
    """(is_flaky, confidence) for one result.

    A result is flaky when it only passed after a retry, or when its case's
    pass/fail history alternates at the quarantine threshold. `confidence` is
    how sure we are a FAILURE is real: the complement of the case's flake
    score, so a failure of a historically stable case is ~1.0 and one from a
    coin-flip case is low. Passes carry no confidence (None). Retries are
    never hidden: a pass-after-retry is still reported as flaky.
    """
    score = flake_score or 0.0
    passed = status == TestStatus.PASSED
    is_flaky = (passed and retry_count > 0) or score >= 0.4
    confidence = None if passed else round(max(0.0, min(1.0, 1.0 - score)), 2)
    return is_flaky, confidence


def _heal_snapshot_policy(session, run_id):
    """(RedactionPolicy, mask selectors, capture level) of the run's project,
    for the backend's own pass over a heal snapshot before it is stored."""
    from app.models import Project
    from app.services.data_policy import resolve_for_project
    from app.services.redaction import RedactionPolicy
    run = session.get(TestRun, run_id) if run_id else None
    project = session.get(Project, run.project_id) if run and run.project_id else None
    if project is None:
        return RedactionPolicy(), [], "standard"
    blob = resolve_for_project(project)
    return (RedactionPolicy.from_data_policy(blob), blob.get("mask_selectors") or [],
            blob.get("capture_level") or "standard")


def _persist_heal_suggestions(result: Dict[str, Any]):
    """Insert worker heal suggestions as pending SelectorHealProposal rows.

    W5: a suggestion carries its ranked candidates (evidence included) and the
    redacted DOM they were scored against. The DOM is redacted again here
    with the project's policy and stored in the object store under the run's
    prefix (not in the row) unless the project captures nothing; accept
    re-checks uniqueness against it. Older workers send neither, and get the
    pre-W5 single-candidate row.

    Dedupes on (test_case_id, step_id, new_selector) against existing
    pending proposals so a repeatedly failing selector doesn't flood the
    queue on every run."""
    suggestions = result.get('heal_suggestions') or []
    if not suggestions:
        return

    from app.models import SelectorHealProposal
    from app.services import heal_candidates
    from app.services.redaction import redact_dom

    try:
        with Session(sync_engine) as session:
            run_id = result.get('run_id')
            policy = None
            created = 0
            for s in suggestions[:10]:
                if not s.get('test_case_id') or not s.get('new_selector'):
                    continue
                existing = session.exec(
                    select(SelectorHealProposal).where(
                        SelectorHealProposal.test_case_id == s['test_case_id'],
                        SelectorHealProposal.step_id == (s.get('step_id') or ''),
                        SelectorHealProposal.new_selector == s['new_selector'],
                        SelectorHealProposal.status == 'pending',
                    )
                ).first()
                if existing:
                    continue
                matches = int(s.get('matches') or 0)
                candidates = heal_candidates.sanitize_candidates(s.get('candidates'))
                chosen = next((c for c in candidates if c['selector'] == s['new_selector']), None)
                snapshot_key = None
                if isinstance(s.get('dom_snapshot'), str) and s['dom_snapshot']:
                    if policy is None:
                        policy = _heal_snapshot_policy(session, run_id)
                    redaction_policy, masks, capture_level = policy
                    if capture_level != 'none':
                        snapshot_key = heal_candidates.store_snapshot(
                            run_id, s['test_case_id'], s.get('step_id') or '',
                            redact_dom(s['dom_snapshot'], redaction_policy, masks))
                if chosen is not None:
                    confidence = chosen['score']
                else:
                    # A pre-W5 worker: one candidate, judged by its match count.
                    confidence = 0.75 if matches == 1 else (0.5 if matches > 1 else 0.25)
                applied = " (applied at run time)" if s.get('applied') else ""
                session.add(SelectorHealProposal(
                    test_case_id=s['test_case_id'],
                    step_id=s.get('step_id') or '',
                    old_selector=s.get('old_selector'),
                    new_selector=s['new_selector'],
                    intent=s.get('intent'),
                    confidence=confidence,
                    rationale=(
                        f"Reactive heal after step failure in run {run_id}{applied}: "
                        f"chosen selector matched {matches} element(s) in the live DOM"
                        + (f"; {len(candidates)} candidate(s) ranked." if candidates else ".")),
                    source_run_id=run_id,
                    status='pending',
                    candidates=candidates or None,
                    chosen_selector=s['new_selector'],
                    dom_snapshot_key=snapshot_key,
                ))
                created += 1
            if created:
                session.commit()
                print(f"[Aggregator] Stored {created} heal proposal(s) "
                      f"from run {run_id}")
    except Exception as e:
        print(f"[Aggregator] Failed to persist heal suggestions: {e}")


def _persist_page_budgets(session, run, test_case_id, test_name, res: Dict[str, Any]):
    """`check-page-budget` outcomes -> PageBudgetSample rows (WP10). Older
    workers send none. Never raises: a bad sample must not leave the job's
    result un-acked."""
    if not res.get('page_budgets'):
        return
    try:
        from app.services.page_budgets import persist_samples
        persist_samples(session, run, test_case_id, test_name, res.get('page_budgets'))
    except Exception as e:  # noqa: BLE001
        print(f"[Aggregator] Failed to store page budgets for run {getattr(run, 'id', None)}: {e}")


def _persist_visual_results(session, run, test_case_id, test_name, res: Dict[str, Any],
                            screenshot_keys):
    """`expect-visual-match` outcomes -> VisualComparison rows (WP14e).
    Older workers send none. Never raises, like the page budgets."""
    if not res.get('visual_results'):
        return
    try:
        from app.services.visual_review import persist_comparisons
        persist_comparisons(session, run, test_case_id, test_name,
                            res.get('visual_results'), screenshot_keys or [])
    except Exception as e:  # noqa: BLE001
        print(f"[Aggregator] Failed to store visual results for run {getattr(run, 'id', None)}: {e}")


def process_continuous_job_result(run_id: int, job_id: str, result: Dict[str, Any], test_results: list):
    """Process a continuous job result with multiple test cases"""
    if not run_id:
        print(f"[Aggregator] Missing run_id in continuous result: {job_id}")
        return

    with Session(sync_engine) as session:
        # Get the test run
        run = session.get(TestRun, run_id)
        if not run:
            print(f"[Aggregator] Run {run_id} not found")
            return

        status_map = {
            'passed': TestStatus.PASSED,
            'failed': TestStatus.FAILED,
            'error': TestStatus.ERROR
        }

        # Job-level artifacts (shared video/trace/HAR for all tests in this job)
        artifacts = result.get('artifacts', {})
        job_video = artifacts.get('video')
        job_trace = artifacts.get('trace')
        job_har = artifacts.get('har')
        network_events = result.get('network_events', [])

        # Process each test result in the job
        for test_res in test_results:
            test_name = test_res.get('test_name')
            status = status_map.get(test_res.get(
                'status', 'error'), TestStatus.ERROR)
            response_data = test_res.get('response_data', {})
            request_data = response_data.get(
                'request', {}) if response_data else {}

            # Check for existing result (idempotency)
            existing = session.exec(
                select(TestCaseResult).where(
                    TestCaseResult.test_run_id == run_id,
                    TestCaseResult.test_name == test_name
                )
            ).first()

            if existing:
                # Update existing result
                existing.status = status
                existing.duration_ms = test_res.get('duration_ms', 0)
                existing.error_message = test_res.get('error')
                if existing.test_case_id is None and test_res.get('test_case_id'):
                    existing.test_case_id = int(test_res['test_case_id'])
                # Use job-level video/trace/HAR for all tests (shared browser)
                existing.video_url = job_video
                existing.trace_url = job_trace
                existing.har_url = job_har
                existing.response_status = response_data.get('status')
                existing.response_headers = response_data.get('headers')
                existing.response_body = response_data.get('body')
                existing.request_headers = request_data.get('headers')
                existing.request_body = request_data.get('body')
                existing.request_url = request_data.get('url')
                existing.request_method = request_data.get('method')
                existing.request_params = request_data.get('params')
                case_payload = _case_payload(test_res, run_id)
                if case_payload:
                    existing.result_payload = {**(existing.result_payload or {}),
                                               **case_payload}
                session.add(existing)
            else:
                # Create new result
                test_result = TestCaseResult(
                    test_run_id=run_id,
                    test_name=test_name,
                    status=status,
                    test_case_id=(int(test_res['test_case_id'])
                                  if test_res.get('test_case_id') else None),
                    duration_ms=test_res.get('duration_ms', 0),
                    error_message=test_res.get('error'),
                    video_url=job_video,
                    trace_url=job_trace,
                    har_url=job_har,
                    screenshots=artifacts.get('screenshots', []),
                    response_status=response_data.get('status'),
                    response_headers=response_data.get('headers'),
                    response_body=response_data.get('body'),
                    request_headers=request_data.get('headers'),
                    request_body=request_data.get('body'),
                    request_url=request_data.get('url'),
                    request_method=request_data.get('method'),
                    request_params=request_data.get('params'),
                    result_payload=_case_payload(test_res, run_id) or None
                )
                session.add(test_result)
            _persist_page_budgets(session, run, test_res.get('test_case_id'), test_name, test_res)
            _persist_visual_results(session, run, test_res.get('test_case_id'), test_name, test_res,
                                    artifacts.get('screenshots'))

        # Update run with network events (tagged by test name already)
        if network_events:
            existing_events = run.network_events or []
            run.network_events = existing_events + network_events

        # Record last-activity timestamp so check_stale_runs() knows this run
        # is still progressing (prevents false timeout on large suites)
        redis_client.hset(
            f'runs:{run_id}:progress',
            'last_progress_at',
            datetime.utcnow().isoformat()
        )

        # Update run progress from Redis
        progress = redis_client.hgetall(f'runs:{run_id}:progress')
        update_run_from_progress(run, run_id, progress, session)

        session.add(run)
        session.commit()

        # Publish real-time update for each test
        for test_res in test_results:
            publish_progress_update(run_id, {
                'run_id': run_id,
                'type': 'progress' if run.status == TestStatus.RUNNING else 'complete',
                'status': run.status.value,
                'passed_tests': run.passed_tests,
                'failed_tests': run.failed_tests,
                'total_tests': run.total_tests,
                'latest_test': test_res.get('test_name'),
                'latest_status': test_res.get('status')
            })


def process_single_test_result(run_id: int, job_id: str, result: Dict[str, Any]):
    """Process a single test job result (original behavior)"""
    test_case_id = result.get('test_case_id')
    test_name = result.get('test_name')

    if not run_id:
        print(f"[Aggregator] Missing run_id in result: {job_id}")
        return

    with Session(sync_engine) as session:
        # Get the test run
        run = session.get(TestRun, run_id)
        if not run:
            print(f"[Aggregator] Run {run_id} not found")
            return

        # Map status
        status_map = {
            'passed': TestStatus.PASSED,
            'failed': TestStatus.FAILED,
            'error': TestStatus.ERROR
        }
        status = status_map.get(result.get(
            'status', 'error'), TestStatus.ERROR)

        # Check for existing result (idempotency)
        existing = session.exec(
            select(TestCaseResult).where(
                TestCaseResult.test_run_id == run_id,
                TestCaseResult.test_name == test_name
            )
        ).first()

        artifacts = result.get('artifacts', {})
        response_data = result.get('response_data', {})
        request_data = response_data.get(
            'request', {}) if response_data else {}
        network_events = result.get('network_events', [])
        web_vitals = result.get('web_vitals')

        # Type-aware payload from non-UI executors (load, security, …); web
        # vitals ride along in the same JSON column for UI runs.
        typed_payload = result.get('result_payload')
        typed_kind = result.get('result_kind')
        merged_payload = None
        evidence = _evidence_payload(result, run_id)
        if typed_payload or web_vitals or evidence:
            merged_payload = {**(typed_payload or {}), **evidence}
            if web_vitals:
                merged_payload['web_vitals'] = web_vitals

        retry_count = int(result.get('retry_count', 0) or 0)

        if existing:
            # Update existing result
            existing.status = status
            existing.duration_ms = result.get('duration_ms', 0)
            existing.error_message = result.get('error')
            if existing.test_case_id is None and test_case_id:
                existing.test_case_id = int(test_case_id)
            existing.video_url = artifacts.get('video')
            existing.trace_url = artifacts.get('trace')
            existing.har_url = artifacts.get('har')
            existing.screenshots = artifacts.get('screenshots', [])
            existing.retry_count = retry_count
            if typed_kind:
                existing.result_kind = typed_kind
            if merged_payload:
                existing.result_payload = {**(existing.result_payload or {}), **merged_payload}
            existing.response_status = response_data.get('status')
            existing.response_headers = response_data.get('headers')
            existing.response_body = response_data.get('body')
            existing.request_headers = request_data.get('headers')
            existing.request_body = request_data.get('body')
            existing.request_url = request_data.get('url')
            existing.request_method = request_data.get('method')
            existing.request_params = request_data.get('params')
            session.add(existing)
        else:
            # Create new result
            test_result = TestCaseResult(
                test_run_id=run_id,
                test_name=test_name,
                status=status,
                test_case_id=int(test_case_id) if test_case_id else None,
                duration_ms=result.get('duration_ms', 0),
                error_message=result.get('error'),
                video_url=artifacts.get('video'),
                trace_url=artifacts.get('trace'),
                har_url=artifacts.get('har'),
                screenshots=artifacts.get('screenshots', []),
                retry_count=retry_count,
                response_status=response_data.get('status'),
                response_headers=response_data.get('headers'),
                response_body=response_data.get('body'),
                request_headers=request_data.get('headers'),
                request_body=request_data.get('body'),
                request_url=request_data.get('url'),
                request_method=request_data.get('method'),
                request_params=request_data.get('params'),
                result_kind=typed_kind,
                result_payload=merged_payload
            )
            session.add(test_result)
        _persist_page_budgets(session, run, test_case_id, test_name, result)
        _persist_visual_results(session, run, test_case_id, test_name, result,
                                artifacts.get('screenshots'))

        # Update run with network events (accumulate from all test results)
        if network_events:
            existing_events = run.network_events or []
            # Add test name to each event for identification
            for event in network_events:
                event['testCaseName'] = test_name
            run.network_events = existing_events + network_events

        # Record last-activity timestamp so check_stale_runs() knows this run
        # is still progressing (prevents false timeout on large suites)
        redis_client.hset(
            f'runs:{run_id}:progress',
            'last_progress_at',
            datetime.utcnow().isoformat()
        )

        # Update run progress from Redis
        progress = redis_client.hgetall(f'runs:{run_id}:progress')
        update_run_from_progress(run, run_id, progress, session)

        session.add(run)
        session.commit()

        # Publish real-time update
        publish_progress_update(run_id, {
            'run_id': run_id,
            'type': 'progress' if run.status == TestStatus.RUNNING else 'complete',
            'status': run.status.value,
            'passed_tests': run.passed_tests,
            'failed_tests': run.failed_tests,
            'total_tests': run.total_tests,
            'latest_test': test_name,
            'latest_status': result.get('status')
        })


def publish_progress_update(run_id: int, payload: Dict[str, Any]):
    """Publish progress update via Redis pub/sub"""
    try:
        redis_client.publish(f'run:{run_id}', json.dumps(payload))
    except Exception as e:
        print(f"[Aggregator] Failed to publish update for run {run_id}: {e}")


@celery_app.task(name="app.tasks.result_aggregator.check_stale_runs")
def check_stale_runs():
    """
    Check for runs that have stale progress (no new results arriving).

    A run is only considered "stuck" if:
      1. No job result has arrived within STALE_RUN_INACTIVITY_MINUTES, OR
      2. The run has been RUNNING longer than MAX_RUN_DURATION_HOURS (hard cap).

    IMPORTANT: A run with 700+ test cases legitimately takes hours. We must NOT
    kill it just because it is old — only kill it if progress has STOPPED.
    """
    from datetime import timedelta

    inactivity_minutes = getattr(settings, 'STALE_RUN_INACTIVITY_MINUTES', 15)
    max_hours = getattr(settings, 'MAX_RUN_DURATION_HOURS', 6)

    inactivity_cutoff = datetime.utcnow() - timedelta(minutes=inactivity_minutes)
    absolute_cutoff = datetime.utcnow() - timedelta(hours=max_hours)

    with Session(sync_engine) as session:
        # Load all currently RUNNING runs — no created_at filter so we can
        # apply nuanced logic per-run based on their Redis progress data.
        running_runs = session.exec(
            select(TestRun).where(TestRun.status == TestStatus.RUNNING)
        ).all()

        killed = 0
        for run in running_runs:
            progress = redis_client.hgetall(f'runs:{run.id}:progress')

            # Case 1: Absolute hard-cap exceeded — kill regardless of activity
            if run.created_at and run.created_at < absolute_cutoff:
                _mark_run_timeout(
                    run, progress, session,
                    reason=f"exceeded maximum run duration ({max_hours}h hard cap)"
                )
                killed += 1
                continue

            # Case 2: No Redis progress data at all
            if not progress:
                # Only kill if the run was created more than inactivity_minutes ago
                # (gives the dispatcher time to write the progress hash after creation)
                if run.created_at and run.created_at < inactivity_cutoff:
                    _mark_run_timeout(
                        run, progress, session,
                        reason="no progress data found in Redis"
                    )
                    killed += 1
                continue

            # Case 3: Has progress data — check last_progress_at timestamp
            last_progress_str = progress.get('last_progress_at')
            completed = int(progress.get('completed', 0))
            total = int(progress.get('total', 0))

            # If all jobs completed but run is still RUNNING, the aggregator
            # update_run_from_progress() will handle finalizing it on the next
            # process_job_results() cycle. Don't interfere here.
            if total > 0 and completed >= total:
                continue

            if last_progress_str:
                try:
                    last_progress_at = datetime.fromisoformat(last_progress_str)
                except ValueError:
                    last_progress_at = None

                if last_progress_at and last_progress_at < inactivity_cutoff:
                    # Progress has stalled — real timeout
                    _mark_run_timeout(
                        run, progress, session,
                        reason=f"no job results received for {inactivity_minutes}+ minutes"
                    )
                    killed += 1
                # else: last progress was recent — run is healthy, skip
            else:
                # last_progress_at not yet recorded (first result not arrived yet).
                # If a worker has already picked up a job (worker_started_at is set),
                # use that as the activity baseline so we don't false-timeout a run
                # that is legitimately waiting in a busy queue.
                # If no worker has touched the run yet, fall back to created_at.
                worker_started_str = progress.get('worker_started_at')
                baseline = None
                if worker_started_str:
                    try:
                        baseline = datetime.fromisoformat(worker_started_str)
                    except ValueError:
                        pass
                if baseline is None:
                    baseline = run.created_at
                if baseline and baseline < inactivity_cutoff:
                    _mark_run_timeout(
                        run, progress, session,
                        reason="no initial results arrived within expected time"
                    )
                    killed += 1

        if killed > 0:
            session.commit()
            print(f"[Aggregator] Marked {killed} stale run(s) as ERROR")
        else:
            print(f"[Aggregator] check_stale_runs: {len(running_runs)} running, all healthy")


def _mark_run_timeout(
    run: TestRun,
    progress: Optional[Dict[str, str]],
    session: Session,
    reason: str
):
    """
    Mark a run as ERROR due to timeout/staleness.
    Includes partial progress counts from Redis if available.
    """
    completed = int(progress.get('completed', 0)) if progress else 0
    total = int(progress.get('total', run.total_tests or 0)) if progress else (run.total_tests or 0)
    passed = int(progress.get('passed', 0)) if progress else run.passed_tests
    failed = int(progress.get('failed', 0)) if progress else run.failed_tests

    run.status = TestStatus.ERROR
    run.error_message = (
        f"Test execution timed out ({reason}). "
        f"{completed}/{total} jobs completed before timeout."
    )
    if total > 0:
        run.total_tests = total
        run.passed_tests = passed
        # Count incomplete jobs as failures
        run.failed_tests = failed + max(0, total - completed)

    session.add(run)
    # A timed-out run is finished as far as anyone watching it is concerned
    # (monitors record DOWN, CI webhooks fire). Results that arrive later
    # still update the run, but its side effects have already been sent.
    finalize_on_commit(session, run.id)
    print(f"[Aggregator] Run {run.id} → ERROR: {reason} ({completed}/{total} completed)")


# NOTE: Beat schedule is registered in app/core/celery_app.py.
# Do NOT add beat_schedule.update() here — it would create duplicate entries
# and the last registration silently overrides the first.

"""Failure investigation v2 — evidence index, observations, ranked hypotheses.

Builds on the per-step evidence workers attach to each result
(`result_payload.step_evidence`, see execution-engine/src/core/evidence.ts)
and on the case's run history. Everything here is deterministic and works
with no LLM; `failure_analysis.py` layers an optional LLM pass on top and uses
`valid_refs` to throw away citations of evidence that does not exist.

Two lists are kept apart on purpose: `observed` holds facts (each tied to the
evidence it came from) and `hypotheses` holds interpretations. A reader who
disagrees with the interpretation still gets the facts.

Results from workers that predate per-step evidence carry only per-test
`console_errors` plus the run's network failures; those become
`console.<n>` / `net.<n>` items so the same machinery still runs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from app.schemas.failure_report import (
    ChangeRange,
    ClusterRef,
    EvidenceItem,
    FailureHistory,
    HistoryEntry,
    Hypothesis,
    ObservedFact,
)

MAX_OBSERVED = 14
MAX_HYPOTHESES = 5
MAX_HISTORY = 20
_SELECTOR_FAILURE = re.compile(
    r"waiting for locator|not found|no element|failed to find element|strict mode violation|"
    r"Timeout \d+ms exceeded|not visible|not enabled|intercepts pointer events", re.I)
_DNS_OR_REFUSED = re.compile(r"ERR_NAME_NOT_RESOLVED|ERR_CONNECTION_REFUSED|ERR_CONNECTION_RESET|"
                             r"ERR_ADDRESS_UNREACHABLE|ERR_INTERNET_DISCONNECTED|ERR_CERT", re.I)
_API_RESOURCE_TYPES = {"fetch", "xhr", "document", None, ""}


def _short(text: Any, limit: int = 160) -> str:
    s = str(text or "").strip().splitlines()[0] if str(text or "").strip() else ""
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _path(url: str) -> str:
    try:
        parts = urlsplit(url)
        return (parts.netloc + parts.path) or url
    except ValueError:
        return url


# ── Evidence index ──────────────────────────────────────────────────────────

@dataclass
class EvidenceIndex:
    items: Dict[str, EvidenceItem] = field(default_factory=dict)
    steps: List[dict] = field(default_factory=list)
    failing: Optional[dict] = None
    # Legacy (pre step-evidence) items.
    legacy_console: List[Tuple[str, dict]] = field(default_factory=list)
    legacy_requests: List[Tuple[str, dict]] = field(default_factory=list)

    def add(self, item: EvidenceItem) -> None:
        self.items[item.id] = item

    @property
    def has_steps(self) -> bool:
        return bool(self.steps)

    def valid_refs(self) -> set:
        return set(self.items)


def _step_label(entry: dict) -> str:
    sel = f" {entry['selector']}" if entry.get("selector") else ""
    return f"Step {entry['index'] + 1} {entry.get('type', '?')}{sel} — {entry.get('status', '?')}"


def build_evidence_index(
    *,
    step_evidence: Optional[List[dict]],
    error_message: str,
    console_errors: Optional[List[dict]] = None,
    network_failures: Optional[List[dict]] = None,
    failing_step_guess: Optional[int] = None,
    step_traces: Optional[Dict[int, dict]] = None,
) -> EvidenceIndex:
    """`step_traces` (WP16a): {step index: {count, first}} of backend ERROR
    spans found under that step's span (trace_backends.error_spans_by_step).
    Only steps the stored evidence lists get a `sN.trace` item, so the
    citation-drop rule covers it like every other id."""
    idx = EvidenceIndex()
    if error_message:
        idx.add(EvidenceItem(id="error", kind="error", label=_short(error_message, 300)))

    entries = [e for e in (step_evidence or []) if isinstance(e, dict) and isinstance(e.get("index"), int)]
    for e in sorted(entries, key=lambda x: x["index"]):
        sid = f"s{e['index']}"
        e = {**e, "id": sid}
        idx.steps.append(e)
        idx.add(EvidenceItem(id=sid, kind="step", label=_step_label(e), step_index=e["index"]))
        for c in e.get("console") or []:
            if isinstance(c, dict) and c.get("id"):
                idx.add(EvidenceItem(id=c["id"], kind="console", step_index=e["index"],
                                     label=f"{c.get('type', 'error')}: {_short(c.get('text'), 200)}"))
        for r in e.get("requests") or []:
            if isinstance(r, dict) and r.get("id"):
                outcome = r.get("status") if r.get("status") is not None else (r.get("failure") or "failed")
                idx.add(EvidenceItem(id=r["id"], kind="request", step_index=e["index"],
                                     label=f"{r.get('method', 'GET')} {_path(r.get('url', ''))} → {outcome}"))
        if e.get("dom"):
            scope = "around the target" if e.get("dom_scope") == "target" else "of the page (target not found)"
            idx.add(EvidenceItem(id=f"{sid}.dom", kind="dom", step_index=e["index"],
                                 label=f"DOM excerpt {scope} at step {e['index'] + 1}"))
        if e.get("screenshot"):
            idx.add(EvidenceItem(id=f"{sid}.shot", kind="screenshot", step_index=e["index"],
                                 label=f"Screenshot at step {e['index'] + 1}"))
        trace = (step_traces or {}).get(e["index"])
        if trace and trace.get("count"):
            first = trace.get("first") or {}
            msg = f": {_short(first.get('status_message'), 160)}" if first.get("status_message") else ""
            idx.add(EvidenceItem(
                id=f"{sid}.trace", kind="trace", step_index=e["index"],
                label=(f"{trace['count']} backend error span(s) at step {e['index'] + 1}; first "
                       f"{first.get('service', '?')} {first.get('operation', '?')}{msg}")))
    failing = [e for e in idx.steps if e.get("status") == "failed"]
    if failing:
        idx.failing = failing[-1]

    if not idx.steps:
        for n, c in enumerate((console_errors or [])[:10]):
            if isinstance(c, dict):
                cid = f"console.{n}"
                idx.legacy_console.append((cid, c))
                idx.add(EvidenceItem(id=cid, kind="console",
                                     label=f"{c.get('type', 'error')}: {_short(c.get('text'), 200)}"))
        for n, r in enumerate((network_failures or [])[:10]):
            if isinstance(r, dict):
                rid = f"net.{n}"
                idx.legacy_requests.append((rid, r))
                idx.add(EvidenceItem(id=rid, kind="request",
                                     label=f"{r.get('method', 'GET')} {_path(str(r.get('url', '')))} → {r.get('status')}"))
        if failing_step_guess is not None:
            idx.failing = {"index": failing_step_guess, "id": None}
    return idx


def _upto_failing(idx: EvidenceIndex) -> List[dict]:
    """Steps that ran up to and including the failing one (skipped excluded)."""
    limit = idx.failing["index"] if idx.failing else None
    return [e for e in idx.steps if e.get("status") != "skipped"
            and (limit is None or e["index"] <= limit)]


def _requests(idx: EvidenceIndex) -> List[Tuple[str, dict, Optional[int]]]:
    """(id, request, step index) for every failed request up to the failure."""
    if idx.has_steps:
        return [(r["id"], r, e["index"]) for e in _upto_failing(idx)
                for r in (e.get("requests") or []) if isinstance(r, dict) and r.get("id")]
    return [(rid, r, None) for rid, r in idx.legacy_requests]


def _console(idx: EvidenceIndex) -> List[Tuple[str, dict, Optional[int]]]:
    if idx.has_steps:
        return [(c["id"], c, e["index"]) for e in _upto_failing(idx)
                for c in (e.get("console") or []) if isinstance(c, dict) and c.get("id")]
    return [(cid, c, None) for cid, c in idx.legacy_console]


# ── History ─────────────────────────────────────────────────────────────────

def summarise_history(
    entries: Sequence[HistoryEntry],
    current_commit: Optional[str],
    current_run_id: int,
    git_repo: Optional[str] = None,
    cluster: Optional[ClusterRef] = None,
) -> Tuple[FailureHistory, Optional[ChangeRange]]:
    """`entries` are PRIOR results of this case, newest first."""
    entries = list(entries)[:MAX_HISTORY]
    failing = {"failed", "error"}
    streak = 1
    first_fail_run, first_fail_commit = current_run_id, current_commit
    last_pass: Optional[HistoryEntry] = None
    for e in entries:
        if e.status in failing:
            if last_pass is None:
                streak += 1
                first_fail_run, first_fail_commit = e.run_id, e.git_commit
        elif e.status == "passed":
            last_pass = e
            break
    # Flips across the window, oldest → newest, ending with this failure.
    seq = [e.status in failing for e in reversed(entries)] + [True]
    flips = sum(1 for a, b in zip(seq, seq[1:]) if a != b)
    history = FailureHistory(
        runs_considered=len(entries),
        prior_failures=sum(1 for e in entries if e.status in failing),
        failing_streak=streak,
        first_failing_run_id=first_fail_run,
        first_failing_commit=first_fail_commit,
        last_pass_run_id=last_pass.run_id if last_pass else None,
        last_pass_commit=last_pass.git_commit if last_pass else None,
        flip_count=flips,
        cluster=cluster,
        entries=entries,
    )
    change = None
    if last_pass and last_pass.git_commit and current_commit and last_pass.git_commit != current_commit:
        compare = None
        repo = (git_repo or "").rstrip("/")
        if repo.startswith("https://github.com/"):
            compare = f"{repo}/compare/{last_pass.git_commit}...{current_commit}"
        change = ChangeRange(from_commit=last_pass.git_commit, to_commit=current_commit,
                             last_pass_run_id=last_pass.run_id, repo=git_repo, compare_url=compare)
    return history, change


def add_history_items(idx: EvidenceIndex, history: Optional[FailureHistory]) -> None:
    if not history:
        return
    for e in history.entries:
        commit = f" @ {e.git_commit[:10]}" if e.git_commit else ""
        idx.add(EvidenceItem(id=f"run:{e.run_id}", kind="history",
                             label=f"Run #{e.run_id} {e.status}{commit}"))


# ── Observations ────────────────────────────────────────────────────────────

def observe(idx: EvidenceIndex, history: Optional[FailureHistory],
            change: Optional[ChangeRange]) -> List[ObservedFact]:
    facts: List[ObservedFact] = []
    f = idx.failing
    if f and f.get("id"):
        took = f" after {f['duration_ms']} ms" if isinstance(f.get("duration_ms"), int) else ""
        sel = f" `{f['selector']}`" if f.get("selector") else ""
        facts.append(ObservedFact(
            text=f"Step {f['index'] + 1} ({f.get('type')}{sel}) failed{took}.",
            evidence_refs=[f["id"]] + (["error"] if "error" in idx.items else [])))
    elif "error" in idx.items:
        facts.append(ObservedFact(text=f"The test failed: {idx.items['error'].label}",
                                  evidence_refs=["error"]))

    for rid, r, step in _requests(idx)[:6]:
        outcome = f"returned {r['status']}" if r.get("status") is not None else f"failed ({r.get('failure') or 'network error'})"
        where = f" during step {step + 1}" if step is not None else ""
        facts.append(ObservedFact(text=f"{r.get('method', 'GET')} {_path(str(r.get('url', '')))} {outcome}{where}.",
                                  evidence_refs=[rid]))
    for cid, c, step in _console(idx)[:6]:
        kind = {"pageerror": "Uncaught page error", "warning": "Console warning"}.get(c.get("type"), "Console error")
        where = f" during step {step + 1}" if step is not None else ""
        facts.append(ObservedFact(text=f"{kind}{where}: {_short(c.get('text'), 200)}", evidence_refs=[cid]))

    if f and f.get("id"):
        if f"{f['id']}.dom" in idx.items:
            facts.append(ObservedFact(
                text=("The step's target element was present in the DOM when it failed."
                      if f.get("dom_scope") == "target" else
                      "The step's selector did not resolve to any element when it failed."),
                evidence_refs=[f"{f['id']}.dom"]))
        if f"{f['id']}.shot" in idx.items:
            facts.append(ObservedFact(text="A screenshot was captured at the failing step.",
                                      evidence_refs=[f"{f['id']}.shot"]))
    for item in [i for i in idx.items.values() if i.kind == "trace"][:3]:
        facts.append(ObservedFact(text=f"The application's tracing recorded {item.label}.",
                                  evidence_refs=[item.id]))

    if history and history.runs_considered:
        fail_refs = [f"run:{e.run_id}" for e in history.entries if e.status in ("failed", "error")][:5]
        facts.append(ObservedFact(
            text=f"This case failed in {history.prior_failures} of its previous {history.runs_considered} run(s).",
            evidence_refs=fail_refs))
        if history.failing_streak > 1 and history.first_failing_run_id:
            commit = f" (commit {history.first_failing_commit[:10]})" if history.first_failing_commit else ""
            facts.append(ObservedFact(
                text=f"It has failed {history.failing_streak} runs in a row, starting with run "
                     f"#{history.first_failing_run_id}{commit}.",
                evidence_refs=[f"run:{history.first_failing_run_id}"]))
        if history.last_pass_run_id:
            commit = f" at commit {history.last_pass_commit[:10]}" if history.last_pass_commit else ""
            facts.append(ObservedFact(text=f"It last passed in run #{history.last_pass_run_id}{commit}.",
                                      evidence_refs=[f"run:{history.last_pass_run_id}"]))
        same = [f"run:{e.run_id}" for e in history.entries if e.same_signature][:5]
        if same:
            facts.append(ObservedFact(
                text=f"The same failure signature appeared in {len(same)} earlier run(s).",
                evidence_refs=same))
    if change:
        facts.append(ObservedFact(
            text=f"Code changed since the last pass: {change.from_commit[:10]}..{change.to_commit[:10]}.",
            evidence_refs=[f"run:{change.last_pass_run_id}"]))
    return facts[:MAX_OBSERVED]


# ── Hypotheses ──────────────────────────────────────────────────────────────

def heuristic_hypotheses(
    idx: EvidenceIndex,
    error_message: str,
    base: Hypothesis,
    history: Optional[FailureHistory],
    change: Optional[ChangeRange],
) -> List[Hypothesis]:
    """Rank candidate causes from the evidence. `base` is the error-pattern
    classification (v1 heuristic) and always competes."""
    f = idx.failing
    fid = f.get("id") if f else None
    in_failing = (lambda step: f is not None and step == f.get("index"))
    anchor = [r for r in (fid, "error") if r and r in idx.items]
    out: List[Hypothesis] = [base.model_copy(update={"evidence_refs": base.evidence_refs or anchor})]

    reqs = _requests(idx)
    server = [(rid, r, s) for rid, r, s in reqs if isinstance(r.get("status"), int) and r["status"] >= 500]
    if server:
        rid, r, s = next(((a, b, c) for a, b, c in server if in_failing(c)), server[0])
        here = "in the failing step" if in_failing(s) else "before the failure"
        out.append(Hypothesis(
            category="app_bug", fix_target="app",
            summary=f"The server returned {r['status']} for {r.get('method', 'GET')} {_path(r.get('url', ''))} {here}.",
            confidence=0.65 if in_failing(s) else 0.5,
            evidence_refs=[x[0] for x in server[:3]] + anchor[:1]))
    auth = [(rid, r, s) for rid, r, s in reqs if r.get("status") in (401, 403)]
    if auth:
        out.append(Hypothesis(
            category="test_bug", fix_target="test",
            summary="Requests were rejected as unauthenticated/forbidden — the stored session or the "
                    "test's credentials may have expired or lack permission.",
            confidence=0.5, evidence_refs=[x[0] for x in auth[:3]]))
    missing = [(rid, r, s) for rid, r, s in reqs
               if r.get("status") == 404 and (r.get("resource_type") or r.get("resourceType")) in _API_RESOURCE_TYPES]
    if missing:
        out.append(Hypothesis(
            category="app_bug", fix_target="app",
            summary=f"{_path(missing[0][1].get('url', ''))} returned 404 — an endpoint or page the flow "
                    f"depends on is missing.",
            confidence=0.45, evidence_refs=[x[0] for x in missing[:3]]))
    neterr = [(rid, r, s) for rid, r, s in reqs if r.get("status") is None and r.get("failure")]
    if neterr:
        hard = any(_DNS_OR_REFUSED.search(str(r.get("failure"))) for _, r, _ in neterr)
        out.append(Hypothesis(
            category="environment", fix_target="infra",
            summary=f"Requests failed at the network level ({neterr[0][1].get('failure')}) — a host was "
                    f"unreachable from the worker.",
            confidence=0.55 if hard else 0.35, evidence_refs=[x[0] for x in neterr[:3]]))

    # Backend error spans under a step's own span (WP16a): the application
    # itself reported failing while serving this step — direct evidence, not
    # a guess from timing.
    traces = [i for i in idx.items.values() if i.kind == "trace"]
    if traces:
        t = next((i for i in traces if in_failing(i.step_index)), traces[0])
        here = "in the failing step" if in_failing(t.step_index) else "before the failure"
        out.append(Hypothesis(
            category="app_bug", fix_target="app",
            summary=f"The application recorded an error while serving a request {here} — {t.label}.",
            confidence=0.7 if in_failing(t.step_index) else 0.5,
            evidence_refs=[i.id for i in traces[:3]] + anchor[:1]))
    backend_failed = bool(server) or bool(traces)

    cons = _console(idx)
    page_errors = [(cid, c, s) for cid, c, s in cons if c.get("type") == "pageerror"]
    errors = [(cid, c, s) for cid, c, s in cons if c.get("type") == "error"]
    js = page_errors or errors
    if js:
        cid, c, s = next(((a, b, d) for a, b, d in js if in_failing(d)), js[0])
        kind = "An uncaught JavaScript exception" if page_errors else "A console error"
        here = " in the failing step" if in_failing(s) else ""
        conf = 0.5 if page_errors else 0.35
        if in_failing(s):
            conf += 0.05
        out.append(Hypothesis(
            category="app_bug", fix_target="app",
            summary=f"{kind} occurred{here}: {_short(c.get('text'), 120)}",
            confidence=conf, evidence_refs=[x[0] for x in js[:3]]))

    if f and fid and _SELECTOR_FAILURE.search(error_message or ""):
        # A server or JS error before a missing element usually IS why the
        # element is missing (error page, half-rendered component), so the
        # "selector is wrong" readings yield to it.
        upstream = 0.15 if (backend_failed or page_errors) else 0.0
        dom_ref = f"{fid}.dom"
        if dom_ref in idx.items and f.get("dom_scope") == "target":
            out.append(Hypothesis(
                category="test_bug", fix_target="test",
                summary="The target element exists but was not actionable in time (hidden, disabled, covered "
                        "or still animating) — wait for its state rather than its presence.",
                confidence=0.45 - upstream, evidence_refs=[fid, dom_ref]))
        elif dom_ref in idx.items:
            out.append(Hypothesis(
                category="test_bug", fix_target="test",
                summary="The selector no longer matches anything on the page — the UI changed or the "
                        "selector is brittle.",
                confidence=0.55 - upstream, evidence_refs=[fid, dom_ref]))

    if history and history.flip_count >= 2:
        refs = [f"run:{e.run_id}" for e in history.entries[:6]]
        out.append(Hypothesis(
            category="flake", fix_target="none",
            summary=f"The case has flipped between pass and fail {history.flip_count} times in its recent "
                    f"runs — likely flaky; retry before investigating.",
            confidence=min(0.6, 0.25 + 0.05 * history.flip_count), evidence_refs=refs))

    # A failure that started right after a code change makes "the app
    # changed" more likely for every app-side explanation.
    if change and history and history.failing_streak == 1:
        for h in out:
            if h.category == "app_bug":
                h.confidence = min(0.95, h.confidence + 0.1)
                ref = f"run:{change.last_pass_run_id}"
                if ref not in h.evidence_refs:
                    h.evidence_refs.append(ref)

    # Same reasoning for the error-pattern reading: "a wait timed out" is a
    # symptom when the server or the page had already failed.
    if (backend_failed or page_errors) and out[0].category == "test_bug":
        out[0].confidence = max(0.1, out[0].confidence - 0.15)

    return rank(out)


def rank(hypotheses: List[Hypothesis]) -> List[Hypothesis]:
    seen = set()
    out = []
    for h in sorted(hypotheses, key=lambda h: -h.confidence):
        key = (h.category, h.summary.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        h.confidence = round(max(0.0, min(1.0, h.confidence)), 2)
        out.append(h)
    return out[:MAX_HYPOTHESES]


def validate_citations(refs: Any, valid: set) -> Tuple[List[str], int]:
    """(kept refs, number dropped). Non-strings and unknown ids are dropped."""
    if not isinstance(refs, list):
        return [], (0 if refs in (None, []) else 1)
    kept, dropped = [], 0
    for r in refs:
        if isinstance(r, str) and r in valid and r not in kept:
            kept.append(r)
        else:
            dropped += 1
    return kept, dropped


def cited_items(idx: EvidenceIndex, observed: List[ObservedFact],
                hypotheses: List[Hypothesis]) -> List[EvidenceItem]:
    """The evidence items the report actually references, in index order,
    so a consumer can resolve every id without the raw result payload."""
    wanted = {r for o in observed for r in o.evidence_refs} | {r for h in hypotheses for r in h.evidence_refs}
    return [item for iid, item in idx.items.items() if iid in wanted]


def evidence_for_prompt(idx: EvidenceIndex, max_chars: int = 6000) -> str:
    """Evidence as `id: label` lines for the LLM, failing step's DOM included."""
    lines = [f"{iid} [{item.kind}]: {item.label}" for iid, item in idx.items.items()]
    f = idx.failing
    if f and f.get("dom"):
        lines.append(f"{f['id']}.dom content: {str(f['dom'])[:1500]}")
    text = "\n".join(lines)
    return text[:max_chars]

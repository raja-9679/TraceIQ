"""Shared twin vectors (WP15e, H8).

The SAME input -> expected-output files are consumed here and by
execution-engine/src/core/twin-vectors.test.ts, so the Python and TypeScript
twins of the redaction and heal-ranking code are held to one answer instead of
two corpora that are only supposed to agree.

Files: tests/vectors/{redaction,heal_ranking}.json at the REPO root (not under
backend/), so both suites read one copy. run-tests.sh / run-tests-live.sh
mount that directory at /tests/vectors, which is where this file resolves it
from inside the container; CI runs from a full checkout. Format and editing
rules: tests/vectors/README.md.

A vector with `known_divergence` pins each twin's current output
(`expected_by_twin.python` here): a recorded disagreement, not an accepted one.

`TWIN_VECTORS_ACTUAL=<file>` writes every actual output to that file.
"""
import json
import os
from pathlib import Path

import pytest

from app.services import heal_candidates as hc
from app.services import redaction as r

VECTOR_DIR = Path(os.environ.get("TRACEIQ_VECTORS_DIR")
                  or Path(__file__).resolve().parents[2] / "tests" / "vectors")


def _resolve(x, fixtures):
    """`{"$concat": [...]}` / `{"$repeat": [s, n]}` build strings the file must
    not hold literally (vendor credential formats that secret scanners flag,
    very long values); `{"$fixture": "NAME"}` is the document's
    `fixtures.NAME`. Twin of `resolve` in twin-vectors.test.ts."""
    if isinstance(x, list):
        return [_resolve(i, fixtures) for i in x]
    if isinstance(x, dict):
        if isinstance(x.get("$fixture"), str):
            assert x["$fixture"] in fixtures, f"unknown fixture {x['$fixture']}"
            return _resolve(fixtures[x["$fixture"]], fixtures)
        if isinstance(x.get("$repeat"), list):
            return str(x["$repeat"][0]) * int(x["$repeat"][1])
        if isinstance(x.get("$concat"), list):
            return "".join(_resolve(i, fixtures) for i in x["$concat"])
        return {k: _resolve(v, fixtures) for k, v in x.items()}
    return x


def _load(name: str):
    path = VECTOR_DIR / name
    # Fail, never skip: a missing vector file must not read as "all twins agree".
    assert path.is_file(), (
        f"twin vector file missing: {path} — run via ./run-tests.sh (it mounts "
        f"<repo>/tests/vectors) or set TRACEIQ_VECTORS_DIR")
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["format"] == "traceiq-twin-vectors"
    return _resolve(doc["vectors"], doc.get("fixtures") or {})


REDACTION = {
    "luhn_valid": lambda a, p: r.luhn_valid(a["digits"]),
    "verhoeff_valid": lambda a, p: r.verhoeff_valid(a["digits"]),
    "redact_text": lambda a, p: r.redact_text(a["text"], p),
    "redact_message_text": lambda a, p: r.redact_message_text(a["text"], p),
    "redact_headers": lambda a, p: r.redact_headers(a["headers"], p),
    "redact_body": lambda a, p: r.redact_body(a["body"], a.get("content_type"), p),
    "redact_network_events": lambda a, p: r.redact_network_events(a["events"], p),
    "query_names_only": lambda a, p: r._query_names_only(a["raw"]),
    "redact_hook_report": lambda a, p: r.redact_hook_report(a["report"], p),
    "redact_page_budgets": lambda a, p: r.redact_page_budgets(a["items"], p),
    "redact_a11y_target": lambda a, p: r.redact_a11y_target(a["selector"], p),
    "a11y_help_url": lambda a, p: r.a11y_help_url(a["raw"]),
    "redact_accessibility_results": lambda a, p: r.redact_accessibility_results(a["items"], p),
    "redact_crawl_report": lambda a, p: r.redact_crawl_report(a["report"], p),
    "redact_step_evidence": lambda a, p: r.redact_step_evidence(a["entries"], p),
    "redact_access_observation": lambda a, p: r.redact_access_observation(a["obs"], p),
    "redact_har": lambda a, p: r.redact_har(a["har"], p),
    "redact_job_result": lambda a, p: r.redact_worker_result(a["result"], p),
    "redact_dom": lambda a, p: r.redact_dom(
        a["html"], p, a.get("mask_selectors") or (),
        **({"max_chars": a["max_chars"]} if a.get("max_chars") else {})),
}


def _element_by_id(html: str, target_id: str):
    root = hc.parse_html(html)
    for el in hc._elements(root):
        if el.get("id") == target_id:
            return root, el
    raise AssertionError(f"vector html has no element with id={target_id}")


def _deterministic(a):
    root = hc.parse_html(a["html"])
    return [{k: d[k] for k in ("selector", "strategy", "stability", "similarity", "target")}
            for d in hc.deterministic_candidates(root, a["old_selector"], a.get("intent"))]


HEAL = {
    "tokens": lambda a, p: hc.tokens(a["text"]),
    "similarity": lambda a, p: hc.similarity(a["query"], a["features"]),
    "looks_generated": lambda a, p: hc.looks_generated(a["value"]),
    "classify_selector": lambda a, p: hc.classify_selector(a["selector"]),
    "score_candidate": lambda a, p: hc.score_candidate(a["breakdown"]),
    "implicit_role": lambda a, p: hc.implicit_role(_element_by_id(a["html"], a["target_id"])[1]),
    "accessible_name": lambda a, p: hc.accessible_name(
        *reversed(_element_by_id(a["html"], a["target_id"]))),
    "deterministic_candidates": lambda a, p: _deterministic(a),
    # Match counts are computed from the snapshot by the lxml matcher; the
    # vector's `counts` (what the TS twin is fed) must therefore be true of the
    # html, and the expected `matches` pins that.
    "rank_candidates": lambda a, p: hc.rank_candidates(
        a["html"], a["old_selector"], a.get("intent"), a.get("llm_selector"), a.get("llm_confidence")),
}

_ACTUAL: dict = {}


def _expected(v):
    if v.get("known_divergence"):
        assert "expected_by_twin" in v, f"{v['id']}: known_divergence needs expected_by_twin"
        return v["expected_by_twin"]["python"]
    return v["expected"]


def _check(name: str, fns: dict):
    vectors = _load(name)
    assert vectors
    ids = [v["id"] for v in vectors]
    assert len(ids) == len(set(ids)), "duplicate vector ids"
    # Run everything before asserting, so one report lists every disagreement.
    failures = []
    for v in vectors:
        fn = fns.get(v["fn"])
        assert fn, f"{v['id']}: no Python binding for fn {v['fn']}"
        policy = (r.RedactionPolicy.from_data_policy(v["data_policy"])
                  if "data_policy" in v else r.DEFAULT_POLICY)
        # Round-trip through JSON so tuples/lists compare like the TS twin's.
        out = json.loads(json.dumps(fn(v["args"], policy)))
        _ACTUAL[v["id"]] = out
        want = _expected(v)
        try:
            same = (json.loads(out) == json.loads(want)) if v.get("compare") == "json" else out == want
        except (TypeError, ValueError):
            same = False
        if not same:
            failures.append(f"{v['id']}\n  expected: {json.dumps(want)}\n  actual:   {json.dumps(out)}")
    dump = os.environ.get("TWIN_VECTORS_ACTUAL")
    if dump:
        Path(dump).write_text(json.dumps(_ACTUAL, indent=2), encoding="utf-8")
    assert not failures, f"{len(failures)} vector(s) disagree:\n" + "\n".join(failures)


def test_redaction_vectors():
    _check("redaction.json", REDACTION)


def test_heal_ranking_vectors():
    _check("heal_ranking.json", HEAL)


@pytest.mark.parametrize("name", ["redaction.json", "heal_ranking.json"])
def test_every_vector_fn_has_both_bindings(name):
    """The TS test has the same dispatch table; a fn only one side knows
    would make that side fail loudly, so keep the two tables in step."""
    fns = REDACTION if name == "redaction.json" else HEAL
    assert {v["fn"] for v in _load(name)} <= set(fns)

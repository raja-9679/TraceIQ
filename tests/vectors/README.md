# Shared twin vectors

Some logic exists twice — once in the worker (TypeScript, runs at capture
time) and once in the backend (Python, runs at ingestion or for proactive
work). The twins are supposed to give the same answer. These files are the
one answer both are tested against:

| File | TypeScript twin | Python twin |
|------|-----------------|-------------|
| `redaction.json` | `execution-engine/src/core/redact.ts` | `backend/app/services/redaction.py` |
| `heal_ranking.json` | `execution-engine/src/core/heal-candidates.ts` | `backend/app/services/heal_candidates.py` |

Consumers: `execution-engine/src/core/twin-vectors.test.ts` (`npm test`) and
`backend/tests/test_twin_vectors.py` (`./run-tests.sh`, which mounts this
directory at `/tests/vectors`; `TRACEIQ_VECTORS_DIR` overrides). Both FAIL
when a file is missing — a skipped twin test reads as "they agree". The
older per-twin corpora (`redact.test.ts`, `test_redaction.py`,
`heal-candidates.test.ts`, `test_heal_candidates.py`) stay; these are
additional.

## Format

```json
{ "format": "traceiq-twin-vectors", "version": 1, "fixtures": {"PAGE": "<html>…"},
  "vectors": [ { "id": "pan.spaces", "fn": "redact_text",
                 "args": {"text": "card 4111 1111 1111 1111"},
                 "data_policy": {"redact_patterns": ["pan"]},
                 "expected": "card [REDACTED:pan]" } ] }
```

- `fn` names a binding in BOTH test files' dispatch tables (snake_case; the
  TS table maps it to the camelCase function). `args` is that binding's input.
- `data_policy` (optional) is a `Project.data_policy` blob, turned into a
  policy by `policyFromDataPolicy` / `RedactionPolicy.from_data_policy`.
  Absent = the built-in default policy.
- `compare: "json"` compares a JSON-string result by its parsed value.
- `note` is for humans.
- Strings that must not appear literally use `{"$concat": [...]}` (vendor
  credential formats — secret scanners and push protection flag a contiguous
  token) or `{"$repeat": ["x", 300]}`; `{"$fixture": "NAME"}` is
  `fixtures.NAME`. Both loaders resolve these before running anything.
- `rank_candidates`: `counts` (matches per selector) and `same_element`
  (groups of selectors resolving to one element) are what the TS twin is fed
  — at run time the live page answers them. The Python twin computes both
  from `html` with its lxml matcher, so they must be TRUE of the html; the
  expected `matches` / `llm` fields catch it when they are not.

## Disagreements

A vector the twins disagree on is a bug in one of them. When the intended
behaviour is clear (CLAUDE.md, the code's own comments), fix the wrong twin.
When it is not, record it instead of picking a side:

```json
"known_divergence": "<why, and what deciding it would take>",
"expected_by_twin": {"python": "...", "typescript": "..."}
```

Each twin then asserts its own current output, so a change on either side is
noticed and the entry gets revisited. It is a recorded disagreement, not an
accepted one.

## Adding a vector

1. Add the input with a guessed `expected`.
2. Run both suites with `TWIN_VECTORS_ACTUAL=<file>` set — `npm test` in the
   engine, and `pytest tests/test_twin_vectors.py` in a 3.11 venv (or add
   `-e TWIN_VECTORS_ACTUAL=…` plus a writable mount to the `docker run` in
   `run-tests.sh`); each writes every actual output to that file. A failure
   lists every disagreeing vector, not just the first.
3. Check the outputs by hand — never paste one twin's output in as the truth
   unread. If both twins agree on something wrong, fix both.

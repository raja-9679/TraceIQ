const test = require('node:test');
const assert = require('node:assert/strict');
const { decideSelection } = require('./selection');

const c = (id, extra = {}) => ({ id, name: `case ${id}`, ...extra });

test('suite mode always runs the suite', () => {
    const d = decideSelection({ mode: 'suite', isPullRequest: true, impact: null, impactError: null });
    assert.equal(d.kind, 'suite');
});

test('targeted runs exactly the matched cases when every file is covered', () => {
    const d = decideSelection({ mode: 'targeted', isPullRequest: true, impactError: null,
        impact: { matched_cases: [c(1), c(2)], unmatched_files: [] } });
    assert.deepEqual(d, { kind: 'targeted', caseIds: [1, 2], skippedQuarantined: [], reason: null });
});

test('fallback when any changed file maps to no test', () => {
    const d = decideSelection({ mode: 'targeted', isPullRequest: true, impactError: null,
        impact: { matched_cases: [c(1)], unmatched_files: ['src/billing.ts'] } });
    assert.equal(d.kind, 'fallback');
    assert.match(d.reason, /src\/billing\.ts.*no mapped test/);
});

test('fallback when nothing matched', () => {
    const d = decideSelection({ mode: 'targeted', isPullRequest: true, impactError: null,
        impact: { matched_cases: [], unmatched_files: [] } });
    assert.equal(d.kind, 'fallback');
    assert.match(d.reason, /no test case matched/);
});

test('fallback when impact analysis failed', () => {
    const d = decideSelection({ mode: 'targeted', isPullRequest: true, impact: null,
        impactError: new Error('HTTP 502') });
    assert.equal(d.kind, 'fallback');
    assert.match(d.reason, /impact analysis failed.*502/);
});

test('fallback when not a pull request', () => {
    const d = decideSelection({ mode: 'targeted', isPullRequest: false, impact: null, impactError: null });
    assert.equal(d.kind, 'fallback');
    assert.match(d.reason, /not a pull request/);
});

test('quarantined matches are skipped and reported; all-quarantined falls back', () => {
    const q = c(3, { flake: { is_quarantined: true, flake_score: 0.6 } });
    const some = decideSelection({ mode: 'targeted', isPullRequest: true, impactError: null,
        impact: { matched_cases: [c(1), q], unmatched_files: [] } });
    assert.deepEqual(some.caseIds, [1]);
    assert.deepEqual(some.skippedQuarantined.map((x) => x.id), [3]);
    const all = decideSelection({ mode: 'targeted', isPullRequest: true, impactError: null,
        impact: { matched_cases: [q], unmatched_files: [] } });
    assert.equal(all.kind, 'fallback');
    assert.match(all.reason, /quarantined/);
});

test('review notes list only cases that actually ran (never a skipped quarantined one)', () => {
    const { reviewNotes } = require('./selection');
    const q = c(3, { suggested_action: 'review', reasons: ['case is quarantined as flaky'], flake: { is_quarantined: true } });
    const r = c(1, { suggested_action: 'review', reasons: ['last result failed'] });
    const d = decideSelection({ mode: 'targeted', isPullRequest: true, impactError: null,
        impact: { matched_cases: [r, q], unmatched_files: [] } });
    assert.deepEqual(reviewNotes(d, [r, q]).map((x) => x.id), [1]);
});

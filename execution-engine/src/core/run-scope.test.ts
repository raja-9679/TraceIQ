import { test } from 'node:test';
import assert from 'node:assert/strict';

import { RunScope } from './run-scope';
import { generateFake, resolveTemplates } from './interpolate';

const JOB = { run_id: 42, settings: { run: { id: 42, short: 'k3x9q2ab', started_at: '2026-10-15T10:00:00Z' } } };

test('run variables resolve from the dispatched run context', () => {
    const scope = RunScope.forCase(JOB, { id: 7, row_index: 1 }, 'worker-ab12');
    const out = resolveTemplates(
        '{{run.id}}|{{run.short}}|{{run.worker}}|{{run.case}}|{{run.row}}|{{run.started_at}}', { scope });
    assert.equal(out, '42|k3x9q2ab|worker-ab12|7|2|2026-10-15T10:00:00Z');
});

test('run.row is 0 for a case without a dataset; unknown run keys stay visible', () => {
    const scope = RunScope.forCase(JOB, { id: 7 }, 'w');
    assert.equal(resolveTemplates('{{run.row}} {{run.nope}}', { scope }), '0 {{run.nope}}');
});

test('unique values are stable within a case and differ across case, row, run and attempt', () => {
    const a = RunScope.forCase(JOB, { id: 7 }, 'w1');
    const first = resolveTemplates('{{unique.email}}', { scope: a });
    assert.match(first, /^[a-z][a-z0-9]{11}$/);
    assert.equal(resolveTemplates('{{ unique.email }}', { scope: a }), first);
    assert.notEqual(a.unique('username'), first);
    // Same case on another worker: still the same value (derived, not random).
    assert.equal(RunScope.forCase(JOB, { id: 7 }, 'w2').unique('email'), first);
    const others = [
        RunScope.forCase(JOB, { id: 8 }, 'w1'),
        RunScope.forCase(JOB, { id: 7, row_index: 0 }, 'w1'),
        RunScope.forCase({ ...JOB, run_id: 43, settings: { run: { id: 43, short: 'zz' } } }, { id: 7 }, 'w1'),
        RunScope.forCase(JOB, { id: 7 }, 'w1', 2),
    ].map(s => s.unique('email'));
    assert.equal(new Set([first, ...others]).size, 5);
});

test('unique tokens are left alone when no scope is available (older callers)', () => {
    assert.equal(resolveTemplates('{{unique.email}} {{run.id}}', {}), '{{unique.email}} {{run.id}}');
});

test('seeded fakes: same seed + case + row reproduce the same values', () => {
    const seeded = { ...JOB, settings: { run: { ...JOB.settings.run, data_seed: 'replay-1' } } };
    const tpl = '{{fake.email}} {{fake.name}} {{fake.uuid}} {{fake.phone}} {{fake.date}} {{fake.number}}';
    const one = resolveTemplates(tpl, { scope: RunScope.forCase(seeded, { id: 7, row_index: 0 }) });
    const two = resolveTemplates(tpl, { scope: RunScope.forCase(seeded, { id: 7, row_index: 0 }) });
    assert.equal(one, two);
    // A different run with the same seed replays the same data…
    const replay = { run_id: 99, settings: { run: { id: 99, short: 'other', data_seed: 'replay-1' } } };
    assert.equal(resolveTemplates(tpl, { scope: RunScope.forCase(replay, { id: 7, row_index: 0 }) }), one);
    // …while another row, case or seed does not.
    assert.notEqual(resolveTemplates(tpl, { scope: RunScope.forCase(seeded, { id: 7, row_index: 1 }) }), one);
    assert.notEqual(resolveTemplates(tpl, { scope: RunScope.forCase(seeded, { id: 8, row_index: 0 }) }), one);
    const reseeded = { ...JOB, settings: { run: { ...JOB.settings.run, data_seed: 'replay-2' } } };
    assert.notEqual(resolveTemplates(tpl, { scope: RunScope.forCase(reseeded, { id: 7, row_index: 0 }) }), one);
});

test('seeded fakes still differ between occurrences within a case', () => {
    const seeded = { ...JOB, settings: { run: { ...JOB.settings.run, data_seed: 's' } } };
    const scope = RunScope.forCase(seeded, { id: 7 });
    assert.notEqual(resolveTemplates('{{fake.uuid}}', { scope }), resolveTemplates('{{fake.uuid}}', { scope }));
});

test('without a seed fakes stay random (behaviour unchanged)', () => {
    const scope = RunScope.forCase(JOB, { id: 7 });
    assert.equal(scope.fakeRng, undefined);
    const a = resolveTemplates('{{fake.uuid}}', { scope });
    const b = resolveTemplates('{{fake.uuid}}', { scope });
    assert.notEqual(a, b);
    assert.match(generateFake('email'), /@example\.com$/);
    assert.equal(generateFake('nope'), '{{fake.nope}}');
});

test('setup exports resolve as {{setup.X}}; missing ones stay visible', () => {
    assert.equal(resolveTemplates('Bearer {{setup.token}} {{setup.missing}}', { setup: { token: 'abc' } }),
        'Bearer abc {{setup.missing}}');
});

import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
    abortedJobResult, cancelKey, CANCELLED_MESSAGE, isJobAbort, JobAbort, RunControl, timeoutMessage,
} from './run-control';
import { withinDeadline } from './suite-hooks';

const job = { job_id: 'j1', run_id: 42 };

test('cancel key matches the one the backend sets', () => {
    // backend: app/services/run_cancel.py CANCEL_KEY
    assert.equal(cancelKey(42), 'run:cancelled:42');
});

test('check passes while nothing is set', async () => {
    const ctl = new RunControl(async () => null);
    await ctl.check(job);
});

test('a cancelled run stops the job with status cancelled', async () => {
    const ctl = new RunControl(async (k) => (k === 'run:cancelled:42' ? '1' : null));
    await assert.rejects(ctl.check(job), (e: any) => {
        assert.ok(isJobAbort(e));
        assert.equal(e.status, 'cancelled');
        assert.equal(e.errorKind, 'cancelled');
        assert.equal(e.message, CANCELLED_MESSAGE);
        return true;
    });
});

test('a timed-out job stops at its next step, and release forgets it', async () => {
    const ctl = new RunControl(async () => null);
    ctl.markTimedOut('j1');
    await assert.rejects(ctl.check(job), (e: any) => e.status === 'timed_out' && e.errorKind === 'timeout');
    await ctl.check({ job_id: 'other', run_id: 42 });   // other jobs unaffected
    ctl.release('j1');
    await ctl.check(job);
});

test('cancel lookups are cached briefly, and a cancel is sticky', async () => {
    let calls = 0;
    let value: string | null = null;
    let now = 0;
    const ctl = new RunControl(async () => { calls++; return value; }, () => now);
    assert.equal(await ctl.isRunCancelled(42), false);
    assert.equal(await ctl.isRunCancelled(42), false);
    assert.equal(calls, 1);                       // cached
    value = '1';
    now = 2000;                                   // cache expired
    assert.equal(await ctl.isRunCancelled(42), true);
    value = null;
    now = 10_000_000;
    assert.equal(await ctl.isRunCancelled(42), true);   // never un-cancelled
    assert.equal(calls, 2);
});

test('a Redis error never cancels a healthy run', async () => {
    const ctl = new RunControl(async () => { throw new Error('ECONNRESET'); });
    assert.equal(await ctl.isRunCancelled(42), false);
    await ctl.check(job);
});

test('aborted result for a single-test job', () => {
    const r = abortedJobResult({ job_id: 'j', run_id: 1, test_case_id: 7, test_case: { id: 7, name: 'login' } },
        'timed_out', timeoutMessage(600000), 600000);
    assert.equal(r.status, 'timed_out');
    assert.equal(r.error_kind, 'timeout');
    assert.equal(r.test_case_id, 7);
    assert.equal(r.test_name, 'login');
    assert.equal(r.duration_ms, 600000);
    assert.match(r.error, /platform's time limit/);
    assert.equal(r.test_results, undefined);
});

test('aborted result for a continuous job reports every case, so progress completes', () => {
    const r = abortedJobResult({ job_id: 'j', run_id: 1, test_cases: [{ id: 1, name: 'a' }, { id: 2, name: 'b [row 1]' }] },
        'cancelled', CANCELLED_MESSAGE);
    assert.equal(r.test_results.length, 2);
    assert.deepEqual(r.test_results.map((t: any) => [t.test_case_id, t.test_name, t.status, t.error_kind]),
        [[1, 'a', 'cancelled', 'cancelled'], [2, 'b [row 1]', 'cancelled', 'cancelled']]);
});

test('the case deadline (suite hooks) is a timed_out abort, not a test failure', async () => {
    await assert.rejects(withinDeadline(Date.now() - 1, async () => 'x'),
        (e: any) => e instanceof JobAbort && e.status === 'timed_out' && /time budget is exhausted/.test(e.message));
    await assert.rejects(withinDeadline(Date.now() + 20, () => new Promise(r => setTimeout(r, 300))),
        (e: any) => e instanceof JobAbort && e.status === 'timed_out' && /timed out/.test(e.message));
    assert.equal(await withinDeadline(undefined, async () => 'x'), 'x');
});

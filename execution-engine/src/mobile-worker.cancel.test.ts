import { test } from 'node:test';
import assert from 'node:assert/strict';
import { MobileWorker } from './mobile-worker';
import { RunControl } from './core/run-control';

// Built without the constructor (it connects to Redis / Appium); only what
// runCase touches is provided.
function worker(cancelAfterSteps: number, executed: string[]) {
    const w: any = Object.create(MobileWorker.prototype);
    let steps = 0;
    w.runControl = new RunControl(async () => (steps >= cancelAfterSteps ? '1' : null), () => steps * 10_000);
    w.executeStep = async (_s: string, step: any) => { executed.push(step.id); steps++; };
    w.tryHeal = async () => { throw new Error('heal must not be tried for an abort'); };
    w.driver = { takeScreenshot: async () => '' };
    return w;
}

const job: any = { job_id: 'm1', run_id: 7, settings: {} };
const testCase: any = { id: 3, name: 'login', steps: [{ id: 'a', type: 'mobile-tap' }, { id: 'b', type: 'mobile-tap' }, { id: 'c', type: 'mobile-tap' }] };

test('a cancelled run stops a mobile case before its next step', async () => {
    const executed: string[] = [];
    const r = await worker(2, executed).runCase('s', testCase, {}, job, [], []);
    assert.deepEqual(executed, ['a', 'b']);
    assert.equal(r.status, 'cancelled');
    assert.equal(r.error_kind, 'cancelled');
});

test('without a cancel every step runs and the case passes', async () => {
    const executed: string[] = [];
    const r = await worker(99, executed).runCase('s', testCase, {}, job, [], []);
    assert.deepEqual(executed, ['a', 'b', 'c']);
    assert.equal(r.status, 'passed');
});

test('an ordinary step failure is a test failure', async () => {
    const w = worker(99, []);
    w.executeStep = async () => { throw new Error('element not found'); };
    w.tryHeal = async () => false;
    const r = await w.runCase('s', testCase, {}, job, [], []);
    assert.equal(r.status, 'failed');
    assert.equal(r.error_kind, 'test');
});

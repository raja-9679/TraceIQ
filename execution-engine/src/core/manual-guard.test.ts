import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isManualCase, manualJobResult, splitManualCases, MANUAL_CASE_ERROR } from './manual-guard';
import { TestExecutor } from './test-executor';

const manual: any = { id: 7, name: 'invoice pdf', executor: 'manual',
    steps: [{ id: 's1', type: 'manual-step', value: 'Open the PDF', params: { expected: 'VAT shown' } }] };
const auto: any = { id: 8, name: 'login', executor: 'ui_playwright', steps: [{ id: 's1', type: 'goto', value: '/' }] };

test('a case is manual by executor or by its steps', () => {
    assert.ok(isManualCase(manual));
    assert.ok(isManualCase({ ...manual, executor: undefined }));
    assert.ok(!isManualCase(auto));
    assert.ok(!isManualCase(null));
});

test('a manual single-case job is reported as not executed, never thrown', () => {
    const r = manualJobResult({ job_id: 'j', run_id: 1, test_case_id: 7, test_case: manual } as any);
    assert.equal(r.status, 'error');
    assert.equal(r.duration_ms, 0);
    assert.equal(r.error, MANUAL_CASE_ERROR);
    assert.deepEqual(r.artifacts.screenshots, []);
});

test('continuous jobs keep their automated cases and report the manual ones', () => {
    const { automated, manual: skipped } = splitManualCases([auto, manual]);
    assert.deepEqual(automated.map((c) => c.id), [8]);
    assert.equal(skipped.length, 1);
    assert.equal(skipped[0].test_case_id, 7);
    assert.equal(skipped[0].status, 'error');
});

test('manual-step is not something the step interpreter claims to run', () => {
    assert.ok(!TestExecutor.SUPPORTED_STEP_TYPES.includes('manual-step'));
});

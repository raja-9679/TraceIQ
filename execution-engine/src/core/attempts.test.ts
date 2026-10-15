import { test } from 'node:test';
import assert from 'node:assert/strict';

import { summariseAttempt } from './evidence';

test('attempt summary keeps status, duration and a truncated error', () => {
    const s = summariseAttempt(1, { status: 'failed', error: 'e'.repeat(3000), duration_ms: 42 });
    assert.equal(s.attempt, 1);
    assert.equal(s.status, 'failed');
    assert.equal(s.error?.length, 1000);
    assert.equal(s.duration_ms, 42);
});

test('attempt summary omits an empty error', () => {
    const s = summariseAttempt(2, { status: 'passed', error: '' });
    assert.deepEqual(s, { attempt: 2, status: 'passed' });
});

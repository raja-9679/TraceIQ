import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isValidationJob } from './validation-run';

test('only an explicit settings.validation_run marks a validation job', () => {
    assert.equal(isValidationJob({ settings: { validation_run: true } }), true);
    assert.equal(isValidationJob({ settings: { validation_run: 'true' } }), false);
    assert.equal(isValidationJob({ settings: {} }), false);
    assert.equal(isValidationJob({}), false);
    assert.equal(isValidationJob(null), false);
});

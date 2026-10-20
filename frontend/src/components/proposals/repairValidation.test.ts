import { describe, expect, it } from 'vitest';
import { acceptBlock, anyValidating, caseValidatable, conditionsText, detailText, validationLabel } from './repairValidation';

const base = { status: 'pending', validation_required: true };

describe('acceptBlock', () => {
    it('never blocks when validation is not required or the proposal is decided', () => {
        expect(acceptBlock({ status: 'pending', validation_required: false })).toBeNull();
        expect(acceptBlock({ status: 'accepted', validation_required: true })).toBeNull();
    });
    it('explains each missing state', () => {
        expect(acceptBlock(base)).toMatch(/must pass a validation run/);
        expect(acceptBlock({ ...base, validation_status: 'validating' })).toMatch(/not finished/);
        expect(acceptBlock({ ...base, validation_status: 'validation_failed' })).toMatch(/did not pass/);
        expect(acceptBlock({ ...base, protected_suite_id: 4 })).toMatch(/Suite #4 is protected/);
    });
    it('a validated heal counts only for the selector it ran with', () => {
        const p = { ...base, validation_status: 'validated' as const, validation_conditions: { selector: '#a' } };
        expect(acceptBlock(p, '#a')).toBeNull();
        expect(acceptBlock(p, '#b')).toMatch(/used #a/);
        expect(acceptBlock({ ...base, validation_status: 'validated' })).toBeNull();
    });
});

describe('helpers', () => {
    it('labels states', () => {
        expect(validationLabel('validated').tone).toBe('mint');
        expect(validationLabel(null).label).toBe('Not validated');
    });
    it('describes conditions', () => {
        expect(conditionsText({ browser: 'firefox', device: 'Pixel 7', environment_id: 2 }))
            .toBe('firefox · Pixel 7 · environment #2');
        expect(conditionsText({})).toBe('chromium · default environment');
        expect(conditionsText(null)).toBe('');
    });
    it('knows which case proposals can be validated', () => {
        expect(caseValidatable({ action: 'update', target_case_id: 1, payload: { steps: [] } })).toBe(true);
        expect(caseValidatable({ action: 'update', target_case_id: 1, payload: { tags: [] } })).toBe(false);
        expect(caseValidatable({ action: 'create', target_case_id: null, payload: { steps: [] } })).toBe(false);
    });
    it('reads string and object error details', () => {
        expect(detailText('nope')).toBe('nope');
        expect(detailText({ error: 'validation_required', message: 'Run it first' })).toBe('Run it first');
        expect(detailText(undefined, 'x')).toBe('x');
    });
    it('detects running validations', () => {
        expect(anyValidating([{ validation_status: 'validating' }])).toBe(true);
        expect(anyValidating([])).toBe(false);
    });
});

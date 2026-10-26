import { describe, expect, it } from 'vitest';
import { calledCaseId, isValidKey, normaliseKey, overridesToRows, rowsToOverrides, tokensIn } from './caseCalls';
import { segmentByOrigin, type StepEvidence } from '../evidence/evidenceHelpers';

describe('run-test form helpers', () => {
    it('reads the called case id', () => {
        expect(calledCaseId({ case_id: 42 })).toBe(42);
        expect(calledCaseId({ case_id: '7' })).toBe(7);
        expect(calledCaseId({ case_id: 0 })).toBeNull();
        expect(calledCaseId(undefined)).toBeNull();
    });

    it('round-trips overrides, dropping blank keys and braces', () => {
        const rows = overridesToRows({ 'env.USER': 'a@b.c' });
        expect(rows).toEqual([{ key: 'env.USER', value: 'a@b.c' }]);
        expect(rowsToOverrides([...rows, { key: '{{ secret.PASS }}', value: '{{secret.SUB}}' }, { key: ' ', value: 'x' }]))
            .toEqual({ 'env.USER': 'a@b.c', 'secret.PASS': '{{secret.SUB}}' });
        expect(rowsToOverrides([])).toBeUndefined();
        expect(normaliseKey('{{env.X}}')).toBe('env.X');
    });

    it('accepts only the keys the backend accepts', () => {
        expect(isValidKey('env.LOGIN_USER')).toBe(true);
        expect(isValidKey('token')).toBe(true);
        expect(isValidKey('fake.email')).toBe(false);
        expect(isValidKey('env.a b')).toBe(false);
    });

    it('suggests the tokens a case uses', () => {
        expect(tokensIn([{ value: '{{env.A}}/{{ secret.B }}', params: { body: { x: '{{data.C}}', y: '{{name}}' } } }]))
            .toEqual(['data.C', 'env.A', 'secret.B']);
    });
});

describe('segmentByOrigin', () => {
    const step = (index: number, from?: StepEvidence['from']): StepEvidence =>
        ({ id: `s${index}`, index, type: 'click', status: 'passed', from });
    const login = { case_id: 42, case_name: 'Login', call_id: 'c1', depth: 1 };

    it('groups consecutive steps of one call and keeps own steps apart', () => {
        const segs = segmentByOrigin([step(0), step(1, login), step(2, login),
            step(3, { ...login, call_id: 'c2' }), step(4)]);
        expect(segs.map(s => [s.origin?.call_id ?? null, s.steps.map(x => x.index)])).toEqual([
            [null, [0]], ['c1', [1, 2]], ['c2', [3]], [null, [4]],
        ]);
    });
});

import { describe, it, expect } from 'vitest';
import {
    isInvestigation, refChipText, runIdOfRef, stepEvidenceOf, stepIndexOfRef,
} from './evidenceHelpers';

describe('evidence refs', () => {
    it('maps step refs to their step index', () => {
        expect(stepIndexOfRef('s3')).toBe(3);
        expect(stepIndexOfRef('s3.c0')).toBe(3);
        expect(stepIndexOfRef('s12.dom')).toBe(12);
        expect(stepIndexOfRef('error')).toBeNull();
        expect(stepIndexOfRef('run:4')).toBeNull();
        expect(stepIndexOfRef('console.0')).toBeNull();
        expect(stepIndexOfRef('s3x')).toBeNull();
    });

    it('recognises prior-run refs', () => {
        expect(runIdOfRef('run:42')).toBe(42);
        expect(runIdOfRef('s1')).toBeNull();
    });

    it('labels chips by kind', () => {
        expect(refChipText('s0')).toBe('step 1');
        expect(refChipText('s2.r1')).toBe('step 3 · request');
        expect(refChipText('s2.c0')).toBe('step 3 · console');
        expect(refChipText('s2.dom')).toBe('step 3 · DOM');
        expect(refChipText('s2.shot')).toBe('step 3 · screenshot');
        expect(refChipText('run:7')).toBe('run #7');
        expect(refChipText('error')).toBe('error');
    });
});

describe('stepEvidenceOf', () => {
    it('reads result_payload.step_evidence and tolerates old results', () => {
        expect(stepEvidenceOf({ result_payload: { step_evidence: [{ index: 0, id: 's0', type: 'goto', status: 'passed' }, 'junk'] } }))
            .toHaveLength(1);
        expect(stepEvidenceOf({ result_payload: null })).toEqual([]);
        expect(stepEvidenceOf({})).toEqual([]);
        expect(stepEvidenceOf(null)).toEqual([]);
    });
});

describe('isInvestigation', () => {
    it('is false for v1 reports', () => {
        expect(isInvestigation({ schema_version: 1, summary: 's' })).toBe(false);
        expect(isInvestigation({ schema_version: 2, observed: [{ text: 'x', evidence_refs: [] }] })).toBe(true);
    });
});

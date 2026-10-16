import { describe, expect, it } from 'vitest';
import { draftError, draftsFromSteps, outcomeMeta, stepsFromDrafts, summaryLine } from './manualSteps';

describe('manual steps', () => {
    it('round-trips the stored step shape', () => {
        const stored = [{ id: 'a', type: 'manual-step', value: 'Open the PDF', params: { expected: 'VAT shown' } }];
        const drafts = draftsFromSteps(stored);
        expect(drafts).toEqual([{ id: 'a', instruction: 'Open the PDF', expected: 'VAT shown' }]);
        expect(stepsFromDrafts(drafts)).toEqual(stored);
    });

    it('drops blank rows and ignores non-manual steps', () => {
        expect(stepsFromDrafts([{ id: 'x', instruction: ' ', expected: '' }])).toEqual([]);
        expect(draftsFromSteps([{ type: 'goto', value: '/' }])).toEqual([]);
    });

    it('explains what is missing', () => {
        expect(draftError('', [])).toMatch(/name/);
        expect(draftError('t', [])).toMatch(/at least one/);
        expect(draftError('t', [{ id: '1', instruction: '', expected: 'x' }])).toMatch(/Step 1/);
        expect(draftError('t', [{ id: '1', instruction: 'do', expected: '' }])).toBeNull();
    });

    it('labels outcomes and summaries', () => {
        expect(outcomeMeta('blocked').label).toBe('Blocked');
        expect(outcomeMeta(null).label).toBe('Not recorded');
        expect(summaryLine({ counts: { passed: 1, failed: 0, blocked: 2, skipped: 0 }, unrecorded: 1, passed: 1, failed: 2, status: 'failed' }))
            .toBe('1 passed · 0 failed · 2 blocked · 0 skipped · 1 to do');
    });
});

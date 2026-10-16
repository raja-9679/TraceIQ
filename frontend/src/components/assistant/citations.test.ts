import { describe, expect, it } from 'vitest';
import type { Citation } from '@/api/assistant';
import { citationHref, citationLabel, formatWhen, isSystemNotice, splitInlineRefs } from './citations';

const c = (kind: Citation['kind'], extra: Partial<Citation> = {}): Citation =>
    ({ ref: `${kind}:5`, kind, id: 5, project_id: 1, label: null, ...extra });

describe('citations', () => {
    it('links each kind to its page', () => {
        expect(citationHref(c('run'))).toBe('/runs/5');
        expect(citationHref(c('result', { run_id: 12 }))).toBe('/runs/12');
        expect(citationHref(c('case', { suite_id: 3 }))).toBe('/suites/3/cases/5/edit');
        expect(citationHref(c('case'))).toBe('/suites');
        expect(citationHref(c('cluster'))).toBe('/triage');
        expect(citationHref(c('finding'))).toBe('/security');
        expect(citationHref(c('readiness'))).toBe('/release-readiness');
        expect(citationHref(c('project'))).toBe('/quality');
    });
    it('labels fall back to kind and id', () => {
        expect(citationLabel(c('run'))).toBe('Run #5');
        expect(citationLabel(c('run', { label: 'Run #5 · Checkout' }))).toBe('Run #5 · Checkout');
    });
    it('splits inline refs and leaves other brackets alone', () => {
        expect(splitInlineRefs('Failed [run:10] and [unverified] [note]')).toEqual([
            { text: 'Failed ' }, { text: '[run:10]', ref: 'run:10' }, { text: ' and [unverified] [note]' },
        ]);
    });
    it('system notices are not model answers', () => {
        expect(isSystemNotice('answered')).toBe(false);
        expect(isSystemNotice('not_configured')).toBe(true);
        expect(isSystemNotice(null)).toBe(false);
    });
    it('treats naive datetimes as UTC', () => {
        expect(formatWhen('2026-10-16T10:00:00')).toBe(new Date('2026-10-16T10:00:00Z').toLocaleString());
        expect(formatWhen('2026-10-16T10:00:00Z')).toBe(new Date('2026-10-16T10:00:00Z').toLocaleString());
        expect(formatWhen('nonsense')).toBe('nonsense');
    });
});

import { describe, it, expect } from 'vitest';
import type { MatrixRow } from '@/api/accessMatrix';
import {
    cycleCell, errorDetail, failureKind, hostAllowed, nextExpect, parseBody, prerequisites,
    runIsActive, setCellStatus, summaryParts,
} from './grid';
import { cellIndex, cleanDraft, observedText, VERDICT_TONE } from './model';

const row = (cells: MatrixRow['cells'] = {}, extra: Partial<MatrixRow> = {}): MatrixRow =>
    ({ key: 'r1', label: 'orders', kind: 'request', method: 'GET', url: '/api/orders', cells, ...extra });

describe('cell cycling', () => {
    it('cycles skip → allow → deny → skip', () => {
        expect(nextExpect(undefined)).toBe('allow');
        expect(nextExpect('skip')).toBe('allow');
        expect(nextExpect('allow')).toBe('deny');
        expect(nextExpect('deny')).toBe('skip');
    });

    it('removes the cell on skip and drops a status on every change', () => {
        let r = cycleCell(row(), '7');
        expect(r.cells['7']).toEqual({ expect: 'allow' });
        r = setCellStatus(r, '7', '201');
        expect(r.cells['7'].status).toBe(201);
        r = cycleCell(r, '7');
        expect(r.cells['7']).toEqual({ expect: 'deny' });
        r = cycleCell(r, '7');
        expect('7' in r.cells).toBe(false);
    });

    it('does not mutate the input row', () => {
        const r = row({ anonymous: { expect: 'deny' } });
        cycleCell(r, 'anonymous');
        expect(r.cells.anonymous.expect).toBe('deny');
    });

    it('ignores out-of-range statuses and status on a skipped cell', () => {
        const r = cycleCell(row(), '7');
        expect(setCellStatus(r, '7', '99').cells['7'].status).toBeNull();
        expect(setCellStatus(r, '7', '').cells['7'].status).toBeNull();
        expect(setCellStatus(row(), '7', '403').cells['7']).toBeUndefined();
    });

    it('cleanDraft drops cells for removed columns', () => {
        const d = cleanDraft({
            name: 'm', description: '', base_url: '', persona_ids: [1], include_anonymous: false,
            deny_statuses: null, rows: [row({ '1': { expect: 'allow' }, anonymous: { expect: 'deny' } })],
        });
        expect(Object.keys(d.rows[0].cells)).toEqual(['1']);
        expect(d.base_url).toBeNull();
    });
});

describe('verdict rendering', () => {
    it('has a tone for every verdict', () => {
        for (const v of ['pass', 'fail', 'inconclusive', 'pending'] as const) expect(VERDICT_TONE[v]).toBeTruthy();
    });

    it('names the two failure kinds apart', () => {
        expect(failureKind('deny', 'fail')).toBe('access granted');
        expect(failureKind('allow', 'fail')).toBe('allow regression');
        expect(failureKind('deny', 'inconclusive')).toBeNull();
    });

    it('describes observations', () => {
        const base = { id: 1, row_key: 'r1', persona_key: '7', expect: 'deny' as const, expected_status: null,
            reason: null, test_run_id: null, completed_at: null };
        expect(observedText({ ...base, verdict: 'pending', observed: null })).toBe('waiting…');
        expect(observedText({ ...base, verdict: 'fail', observed: { status: 200, element_present: true } }))
            .toBe('200 · element visible');
        expect(observedText({ ...base, verdict: 'inconclusive', observed: { error_kind: 'timeout' } })).toBe('timeout');
        expect(cellIndex([{ ...base, verdict: 'pass', observed: null }]).get('r1|7')?.id).toBe(1);
    });

    it('summarises non-zero verdicts only', () => {
        expect(summaryParts({ pass: 3, fail: 1, inconclusive: 0, pending: 0, total: 4, access_granted: 1, allow_regressions: 0 }))
            .toEqual([{ verdict: 'pass', count: 3 }, { verdict: 'fail', count: 1 }]);
        expect(summaryParts(null)).toEqual([]);
    });

    it('polls only while a run is in flight', () => {
        expect(runIsActive({ status: 'pending' })).toBe(true);
        expect(runIsActive({ status: 'running' })).toBe(true);
        expect(runIsActive({ status: 'completed' })).toBe(false);
        expect(runIsActive(undefined)).toBe(false);
    });
});

describe('prerequisites', () => {
    const sec = { enabled: true, allowed_domains: ['example.com'], allow_active_scan: false };

    it('matches hosts like the backend', () => {
        expect(hostAllowed('https://app.example.com/x', ['example.com'])).toBe(true);
        expect(hostAllowed('https://badexample.com/x', ['example.com'])).toBe(false);
        expect(hostAllowed('not a url', ['example.com'])).toBe(false);
    });

    it('is empty when only case rows execute', () => {
        expect(prerequisites([row({ '1': { expect: 'allow' } }, { kind: 'case', test_case_id: 3 })], null, undefined, ['1'])).toEqual([]);
    });

    it('flags disabled testing, off-list hosts and mutating rows', () => {
        const rows = [row({ '1': { expect: 'deny' } }, { method: 'DELETE', url: 'https://evil.test/a' })];
        const p = prerequisites(rows, null, { ...sec, enabled: false }, ['1']);
        expect(p.map(x => x.ok)).toEqual([false, false, false]);
        expect(p[1].text).toContain('evil.test');
    });

    it('passes a GET to an allowlisted host via the base url', () => {
        const p = prerequisites([row({ '1': { expect: 'deny' } })], 'https://app.example.com', sec, ['1']);
        expect(p.every(x => x.ok)).toBe(true);
    });

    it('ignores rows whose every cell is skip', () => {
        expect(prerequisites([row({}, { url: 'https://evil.test' })], null, sec, ['1'])).toEqual([]);
    });
});

describe('misc', () => {
    it('parses bodies as JSON when possible', () => {
        expect(parseBody('{"a":1}')).toEqual({ a: 1 });
        expect(parseBody('a=1')).toBe('a=1');
        expect(parseBody('  ')).toBeUndefined();
    });

    it('surfaces backend detail verbatim', () => {
        expect(errorDetail({ response: { data: { detail: 'Security testing is disabled' } } })).toBe('Security testing is disabled');
        expect(errorDetail({ response: { data: { detail: [{ msg: 'a' }, { msg: 'b' }] } } })).toBe('a; b');
        expect(errorDetail(new Error('boom'))).toBe('boom');
    });
});

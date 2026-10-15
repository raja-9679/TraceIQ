// @vitest-environment jsdom
import { useState } from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { MatrixRow, MatrixRun } from '@/api/accessMatrix';
import ExpectCell from './ExpectCell';
import ResultsGrid from './ResultsGrid';
import { cycleCell } from './grid';

afterEach(cleanup);

function Harness() {
    const [row, setRow] = useState<MatrixRow>({ key: 'r1', label: 'orders', kind: 'request', url: '/a', cells: {} });
    return <ExpectCell cell={row.cells['7']} rowLabel="orders" columnLabel="Bob"
        onCycle={() => setRow(cycleCell(row, '7'))} onStatus={() => undefined} />;
}

describe('ExpectCell', () => {
    it('cycles through allow, deny and skip with a labelled control', async () => {
        render(<Harness />);
        const btn = () => screen.getByRole('button', { name: /Expectation for orders as Bob/ });
        expect(btn().textContent).toBe('Skip');
        await userEvent.click(btn());
        expect(btn().textContent).toBe('Allow');
        expect(screen.getByLabelText('Exact status for orders as Bob')).toBeTruthy();
        await userEvent.click(btn());
        expect(btn().textContent).toBe('Deny');
        expect(screen.getByLabelText('Extra deny status for orders as Bob')).toBeTruthy();
        await userEvent.click(btn());
        expect(btn().textContent).toBe('Skip');
    });
});

describe('ResultsGrid', () => {
    const run: MatrixRun = {
        id: 4, matrix_id: 1, status: 'completed', matrix_group: 'g', authorized: false, error: null,
        created_at: '2026-10-15T00:00:00Z', finished_at: null,
        summary: { pass: 1, fail: 1, inconclusive: 0, pending: 0, total: 2, access_granted: 1, allow_regressions: 0 },
        snapshot: {
            name: 'm', base_url: null, columns: ['7', 'anonymous'], persona_names: { '7': 'Bob' }, deny_statuses: [401, 403, 404],
            rows: [{ key: 'r1', label: 'orders', kind: 'request', method: 'GET', url: '/a', cells: {} }],
        },
        cells: [
            { id: 10, row_key: 'r1', persona_key: '7', expect: 'allow', expected_status: null, verdict: 'pass',
              reason: null, observed: { status: 200 }, test_run_id: null, completed_at: null },
            { id: 11, row_key: 'r1', persona_key: 'anonymous', expect: 'deny', expected_status: null, verdict: 'fail',
              reason: 'status 200', observed: { status: 200 }, test_run_id: null, completed_at: null },
        ],
    };

    it('renders verdicts, failure kinds and summary, and selects a cell', async () => {
        const onSelect = vi.fn();
        render(<ResultsGrid run={run} selectedCellId={null} onSelect={onSelect} />);
        expect(screen.getByText('Fail — access granted')).toBeTruthy();
        expect(screen.getByText('Pass: 1')).toBeTruthy();
        expect(screen.getByText(/1 access granted that should be denied/)).toBeTruthy();
        await userEvent.click(screen.getByRole('button', { name: /orders as Anonymous: Fail/ }));
        expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 11 }));
    });
});

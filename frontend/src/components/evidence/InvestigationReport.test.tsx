// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { InvestigationReport } from './InvestigationReport';
import type { FailureReportV2 } from './evidenceHelpers';

afterEach(cleanup);

const report: FailureReportV2 = {
    schema_version: 2,
    result_id: 5,
    observed: [{ text: 'Step 3 (fill) failed after 30000 ms.', evidence_refs: ['s2', 'error'] }],
    hypotheses: [
        { category: 'app_bug', fix_target: 'app', summary: 'The server returned 500.', confidence: 0.65,
          evidence_refs: ['s1.r0', 'run:9'], source: 'heuristic' },
    ],
    evidence_items: [
        { id: 's2', kind: 'step', label: 'Step 3 fill #qty — failed', step_index: 2 },
        { id: 's1.r0', kind: 'request', label: 'POST shop.test/api/cart → 500', step_index: 1 },
    ],
    history: { runs_considered: 3, prior_failures: 1, failing_streak: 2, first_failing_commit: 'abcdef123456',
               flip_count: 1 },
    changed_since_last_pass: { from_commit: 'aaaaaaaa11', to_commit: 'bbbbbbbb22', last_pass_run_id: 8,
                               compare_url: 'https://github.com/acme/shop/compare/a...b' },
};

describe('InvestigationReport', () => {
    it('keeps observed facts apart from the likely cause', () => {
        render(<MemoryRouter><InvestigationReport report={report} onSelectEvidence={vi.fn()} /></MemoryRouter>);
        expect(screen.getByText('Observed')).toBeTruthy();
        expect(screen.getByText('Likely cause')).toBeTruthy();
        expect(screen.getByText(/Step 3 \(fill\) failed/)).toBeTruthy();
        expect(screen.getByText(/The server returned 500/)).toBeTruthy();
        expect(screen.getByText('65%')).toBeTruthy();
        expect(screen.getByText(/2 in a row/)).toBeTruthy();
        expect(screen.getByRole('link', { name: /aaaaaaaa…bbbbbbbb/ }).getAttribute('href'))
            .toBe('https://github.com/acme/shop/compare/a...b');
    });

    it('step refs call back with the ref; run refs link to the run', async () => {
        const onSelect = vi.fn();
        render(<MemoryRouter><InvestigationReport report={report} onSelectEvidence={onSelect} /></MemoryRouter>);
        await userEvent.click(screen.getByRole('button', { name: 'step 2 · request' }));
        expect(onSelect).toHaveBeenCalledWith('s1.r0');
        expect(screen.getByRole('button', { name: 'step 3' }).getAttribute('title')).toBe('Step 3 fill #qty — failed');
        expect(screen.getByRole('link', { name: 'run #9' }).getAttribute('href')).toBe('/runs/9');
    });
});

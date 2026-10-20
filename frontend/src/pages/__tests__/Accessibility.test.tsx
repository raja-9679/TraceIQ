// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Accessibility from '../Accessibility';

const createWaiver = vi.fn(async () => ({}));
vi.mock('@/api/accessibility', async (orig) => ({
    ...(await orig<typeof import('@/api/accessibility')>()),
    accessibilityApi: {
        trend: vi.fn(async () => ({
            project_id: 1, days: 30, checks: 2, runs: 2,
            series: [{ date: '2026-10-19', critical: 1, serious: 0, moderate: 0, minor: 0, new: 1, fixed: 0, waived: 0 }],
            open: [{ id: 5, test_run_id: 2, test_case_id: 3, test_name: 'cart', step_id: 's1',
                page_key: 'https://shop.example/cart', rule_id: 'button-name', impact: 'critical',
                target: 'button.x', fingerprint: 'f', node_count: 2, help_url: null,
                new: true, waived: false, waiver_id: null }],
            totals: { critical: 1, serious: 0, moderate: 0, minor: 0, waived: 0, new: 1 },
            rules: [],
        })),
        waivers: vi.fn(async () => [{
            id: 9, project_id: 1, rule_id: 'color-contrast', selector_pattern: '#footer *', reason: 'vendor',
            owner: null, created_by: null, created_at: '', updated_at: '',
            expires_at: '2026-01-01T00:00:00Z', state: 'expired',
        }]),
        createWaiver: (...a: unknown[]) => createWaiver(...(a as [])),
        renewWaiver: vi.fn(), deleteWaiver: vi.fn(),
    },
}));
vi.mock('@/api/flakes', () => ({ flakesApi: { ownerCandidates: vi.fn(async () => []) } }));

beforeEach(() => localStorage.setItem('activeProjectId', '1'));
afterEach(() => { cleanup(); localStorage.clear(); });

describe('Accessibility page', () => {
    it('shows open violations and waivers, and drafts a waiver from a row', async () => {
        render(<QueryClientProvider client={new QueryClient()}><Accessibility /></QueryClientProvider>);
        expect(await screen.findByText('button-name')).toBeTruthy();
        expect(screen.getByText('Expired — counts again')).toBeTruthy();
        fireEvent.click(screen.getByRole('button', { name: 'Waive' }));
        const form = screen.getByRole('form', { name: 'New waiver' });
        expect((screen.getByDisplayValue('button.x') as HTMLInputElement).value).toBe('button.x');
        fireEvent.change(screen.getByPlaceholderText('Vendor widget, fix scheduled'), { target: { value: 'third-party' } });
        fireEvent.submit(form);
        await waitFor(() => expect(createWaiver).toHaveBeenCalledWith(1, expect.objectContaining({
            rule_id: 'button-name', selector_pattern: 'button.x', reason: 'third-party', days: 30,
        })));
    });
});

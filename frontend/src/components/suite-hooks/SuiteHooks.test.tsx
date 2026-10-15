// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { SuiteHooksCard } from './SuiteHooksCard';
import { HookOutcomes, RunDataSummary } from './HookOutcomes';
import { hookOutcomesOf, resultsWithHookWarnings, withHooks, moveItem } from './suiteHooks';
import { CleanupField } from '@/components/test-builder/CleanupField';
import { cleanupFor, cleanupMode } from '@/components/test-builder/cleanupSpec';

afterEach(cleanup);

function wrap(node: ReactNode) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, queryFn: () => new Promise(() => {}) } } });
    return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

describe('suite hook helpers', () => {
    it('drops empty hook lists and a blank seed, keeps other settings', () => {
        const next = withHooks({ headers: { A: '1' }, setup_steps: [{ id: 'x', type: 'goto' }], data_seed: 'old' },
            [], [{ id: 't', type: 'http-request' }], '  ');
        expect(next).toEqual({ headers: { A: '1' }, teardown_steps: [{ id: 't', type: 'http-request' }] });
        expect(withHooks({}, [], [], ' s1 ').data_seed).toBe('s1');
    });

    it('reads outcomes and counts results with warnings', () => {
        const payload = { cleanup: [{ method: 'DELETE', url: '/x', status: 'failed', origin: 'case' }], hook_warnings: ['w'] };
        expect(hookOutcomesOf(payload)?.cleanup).toHaveLength(1);
        expect(hookOutcomesOf({ web_vitals: {} })).toBeNull();
        expect(resultsWithHookWarnings([{ result_payload: payload }, { result_payload: null }, {}])).toBe(1);
        expect(moveItem([1, 2, 3], 0, 'up')).toEqual([1, 2, 3]);
        expect(moveItem([1, 2, 3], 0, 'down')).toEqual([2, 1, 3]);
    });

    it('maps cleanup modes to params.cleanup', () => {
        expect(cleanupMode(true)).toBe('auto');
        expect(cleanupMode({ url: '/a' })).toBe('custom');
        expect(cleanupMode(undefined)).toBe('off');
        expect(cleanupFor('custom', { url: '/a', method: 'POST' })).toEqual({ url: '/a', method: 'POST' });
        expect(cleanupFor('off', true)).toBeUndefined();
    });
});

describe('SuiteHooksCard', () => {
    it('adds a setup step, sets a seed and saves both into the settings blob', () => {
        const onSave = vi.fn();
        wrap(<SuiteHooksCard settings={{ headers: { A: '1' } }} inherit onSave={onSave} />);
        const save = screen.getByRole('button', { name: 'Save setup & teardown' });
        expect((save as HTMLButtonElement).disabled).toBe(true);
        fireEvent.click(screen.getByRole('button', { name: /Add setup step/ }));
        fireEvent.change(screen.getByLabelText(/Data seed/), { target: { value: 'repro-1' } });
        fireEvent.click(save);
        const [next, message] = onSave.mock.calls[0];
        expect(message).toBe('Setup & teardown saved');
        expect(next.headers).toEqual({ A: '1' });
        expect(next.data_seed).toBe('repro-1');
        expect(next.setup_steps).toHaveLength(1);
        expect(next.setup_steps[0].type).toBe('http-request');
        expect(next.teardown_steps).toBeUndefined();
    });

    it('explains when parent hooks are not inherited', () => {
        wrap(<SuiteHooksCard settings={{}} inherit={false} onSave={vi.fn()} />);
        expect(screen.getByText(/does not inherit settings/)).toBeTruthy();
    });
});

describe('HookOutcomes', () => {
    it('shows phases, cleanup results and warnings', () => {
        render(<HookOutcomes outcomes={{
            setup: [{ suite_id: 1, suite_name: 'Checkout', phase: 'setup', status: 'passed', steps: [] }],
            teardown: [{ suite_id: 1, suite_name: 'Checkout', phase: 'teardown', status: 'failed', error: 'step 1: 500',
                steps: [{ index: 0, type: 'http-request', status: 'failed', error: '500' }] }],
            cleanup: [{ method: 'DELETE', url: '/items/{{id}}', status: 'ok', http_status: 204, origin: 'case' }],
            warnings: ['Teardown failed (Checkout): step 1: 500'],
        }} />);
        expect(screen.getByRole('region', { name: 'Setup, teardown and cleanup' })).toBeTruthy();
        expect(screen.getByText('Teardown failed (Checkout): step 1: 500')).toBeTruthy();
        expect(screen.getByText('DELETE /items/{{id}}')).toBeTruthy();
        expect(screen.getByText('HTTP 204')).toBeTruthy();
    });

    it('run summary offers a replay with the recorded seed', () => {
        const onReplay = vi.fn();
        render(<RunDataSummary seed="s-9" warningResults={2} onReplay={onReplay} />);
        expect(screen.getByText('2 results had teardown or cleanup warnings')).toBeTruthy();
        fireEvent.click(screen.getByRole('button', { name: 'Re-run with this seed' }));
        expect(onReplay).toHaveBeenCalled();
        cleanup();
        const { container } = render(<RunDataSummary seed={null} warningResults={0} />);
        expect(container.innerHTML).toBe('');
    });
});

describe('CleanupField', () => {
    it('switches between no cleanup, auto and a custom request', () => {
        const onChange = vi.fn();
        const { rerender } = render(<CleanupField stepType="http-request" spec={undefined} onChange={onChange} />);
        fireEvent.change(screen.getByLabelText(/Clean up afterwards/), { target: { value: 'auto' } });
        expect(onChange).toHaveBeenLastCalledWith(true);
        rerender(<CleanupField stepType="http-request" spec={{ method: 'DELETE', url: '' }} onChange={onChange} />);
        fireEvent.change(screen.getByLabelText('Cleanup URL'), { target: { value: '/items/{{id}}' } });
        expect(onChange).toHaveBeenLastCalledWith({ method: 'DELETE', url: '/items/{{id}}' });
    });
});

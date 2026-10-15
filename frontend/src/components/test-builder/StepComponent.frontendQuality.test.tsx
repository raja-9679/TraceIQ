// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StepComponent, type TestStep } from './StepComponent';

afterEach(cleanup);

function renderStep(type: TestStep['type'], params: TestStep['params'] = {}) {
    const updateStep = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, queryFn: () => new Promise(() => {}) } } });
    render(
        <QueryClientProvider client={client}>
            <StepComponent step={{ id: 's1', type, params }} index={0} updateStep={updateStep}
                removeStep={vi.fn()} moveStep={vi.fn()} insertStep={vi.fn()} isFirst isLast />
        </QueryClientProvider>,
    );
    return updateStep;
}

describe('frontend-quality step editors', () => {
    it('page budget thresholds land in params.budgets as numbers', () => {
        const updateStep = renderStep('check-page-budget', { budgets: { cls: 0.1 } });
        fireEvent.change(screen.getByLabelText('LCP max (ms)'), { target: { value: '2500' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'params', { budgets: { cls: 0.1, lcp_ms: 2500 } });
        fireEvent.change(screen.getByLabelText('CLS max'), { target: { value: '' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'params', { budgets: undefined });
        fireEvent.change(screen.getByLabelText('SEO checks'), { target: { value: 'fail' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'params', { budgets: { cls: 0.1 }, seo: 'fail' });
    });

    it('overflow step edits scope selector, tolerance and ignore list', () => {
        const updateStep = renderStep('expect-no-horizontal-overflow');
        fireEvent.change(screen.getByLabelText('Only check inside (selector, optional)'), { target: { value: 'main' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'selector', 'main');
        fireEvent.change(screen.getByLabelText('Tolerance (px)'), { target: { value: '3' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'params', { tolerance_px: 3 });
        fireEvent.blur(screen.getByLabelText('Ignore selectors (comma separated)'), { target: { value: '.carousel, .ticker' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'params', { ignore: ['.carousel', '.ticker'] });
    });

    it('rtl step can expect left-to-right instead', () => {
        const updateStep = renderStep('expect-rtl-layout');
        fireEvent.change(screen.getByLabelText('Expected direction'), { target: { value: 'ltr' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'params', { expect_dir: 'ltr' });
    });
});

// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StepComponent, type TestStep } from './StepComponent';
import { textToSequence, withMode, harIdParam } from './networkMockSteps';

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

describe('network mock helpers', () => {
    it('parses a sequence list and rejects anything else', () => {
        expect(textToSequence('[{"status":500},{"status":200}]').sequence).toHaveLength(2);
        expect(textToSequence('[]').error).toMatch(/at least one/);
        expect(textToSequence('[1]').error).toMatch(/object/);
        expect(textToSequence('{').error).toMatch(/JSON/);
    });
    it('switches modes without leaving stale params', () => {
        expect(withMode({ status: 404 }, 'sequence')).toEqual({ status: 404, sequence: [{ status: 404 }] });
        expect(withMode({ sequence: [{}], after_sequence: 'fallthrough', status: 1 }, 'single')).toEqual({ status: 1 });
        expect(harIdParam(' 12 ')).toBe(12);
        expect(harIdParam('x')).toBeUndefined();
    });
});

describe('network mock step editors', () => {
    it('mock-response edits pattern and status, and turns into a sequence', () => {
        const updateStep = renderStep('mock-response');
        fireEvent.change(screen.getByLabelText('URL pattern to mock'), { target: { value: '**/api/me' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'selector', '**/api/me');
        fireEvent.change(screen.getByLabelText('Status code'), { target: { value: '503' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'params', { status: 503 });
        fireEvent.change(screen.getByLabelText('Mock mode'), { target: { value: 'sequence' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'params', { sequence: [{ status: 200 }] });
    });

    it('sequence editor stores valid JSON only, and the after-sequence choice', () => {
        const updateStep = renderStep('mock-response', { sequence: [{ status: 500 }] });
        const box = screen.getByLabelText('Response sequence (JSON list)');
        fireEvent.change(box, { target: { value: '[{"status": 500}, {"status' } });
        expect(screen.getByText('Not valid JSON yet.')).toBeTruthy();
        expect(updateStep).not.toHaveBeenCalled();
        fireEvent.change(box, { target: { value: '[{"status": 500}, {"status": 200}]' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'params', { sequence: [{ status: 500 }, { status: 200 }] });
        fireEvent.change(screen.getByLabelText('After the sequence'), { target: { value: 'fallthrough' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'params', { sequence: [{ status: 500 }], after_sequence: 'fallthrough' });
    });

    it('har-replay takes an id, a URL filter and the not-found behaviour', () => {
        const updateStep = renderStep('har-replay');
        fireEvent.change(screen.getByLabelText('HAR file id'), { target: { value: '7' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'params', { har_id: 7 });
        fireEvent.change(screen.getByLabelText('Only URLs matching (glob, optional)'), { target: { value: '**/api/**' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'selector', '**/api/**');
        fireEvent.change(screen.getByLabelText('When the HAR has no match'), { target: { value: 'fallback' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'params', { not_found: 'fallback' });
    });
});

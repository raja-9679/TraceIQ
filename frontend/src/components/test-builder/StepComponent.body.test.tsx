// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StepComponent, type TestStep } from './StepComponent';

afterEach(cleanup);

function renderStep(step: TestStep) {
    const updateStep = vi.fn();
    // The step-type catalogue query never resolves here; the API step's
    // fields are built in, so the body editor renders without it.
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, queryFn: () => new Promise(() => {}) } } });
    render(
        <QueryClientProvider client={client}>
            <StepComponent
                step={step} index={0} updateStep={updateStep} removeStep={vi.fn()}
                moveStep={vi.fn()} insertStep={vi.fn()} isFirst isLast
            />
        </QueryClientProvider>,
    );
    return { updateStep, body: screen.getByPlaceholderText('Request Body (JSON/Text)') as HTMLTextAreaElement };
}

const apiStep = (body: NonNullable<TestStep['params']>['body']): TestStep => ({
    id: 's1', type: 'http-request', value: 'https://api.example.com/orders',
    params: { method: 'POST', body },
});

describe('API step request body', () => {
    it('shows an object body as JSON instead of [object Object]', () => {
        const { body } = renderStep(apiStep({ sku: 'A1', qty: 2 }));
        expect(body.value).toBe('{\n  "sku": "A1",\n  "qty": 2\n}');
    });

    it('saves a valid JSON edit as an object', () => {
        const { body, updateStep } = renderStep(apiStep({ sku: 'A1' }));
        fireEvent.change(body, { target: { value: '{"sku":"B2"}' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'params', { method: 'POST', body: { sku: 'B2' } });
    });

    it('does not save half-typed JSON over the object body', () => {
        const { body, updateStep } = renderStep(apiStep({ sku: 'A1' }));
        fireEvent.change(body, { target: { value: '{"sku": "B' } });
        expect(updateStep).not.toHaveBeenCalled();
        expect(screen.getByText(/Not valid JSON yet/)).toBeTruthy();
        expect(body.value).toBe('{"sku": "B');
    });

    it('keeps a string body a string', () => {
        const { body, updateStep } = renderStep(apiStep('a=1'));
        expect(body.value).toBe('a=1');
        fireEvent.change(body, { target: { value: 'a=2' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'params', { method: 'POST', body: 'a=2' });
    });
});

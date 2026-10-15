// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StepComponent, type TestStep } from './StepComponent';
import { emailVariable, webhookVariable, whereError } from './hookSteps';

afterEach(cleanup);

function renderStep(type: TestStep['type'], params: TestStep['params']) {
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

describe('wait-for-email step editor', () => {
    it('edits filters and extraction into params', () => {
        const updateStep = renderStep('wait-for-email', {});
        fireEvent.change(screen.getByLabelText('Subject contains'), { target: { value: 'Reset' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'params', { subject: 'Reset' });
        fireEvent.change(screen.getByLabelText('Pattern'), { target: { value: '/reset' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'params', { pattern: '/reset' });
        fireEvent.change(screen.getByLabelText('Inbox address'), { target: { value: '{{inbox.address.signup}}' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'params', { to: '{{inbox.address.signup}}' });
    });

    it('tells the user which variable the value lands in', () => {
        renderStep('wait-for-email', { extract: 'code' });
        expect(screen.getByText(/\{\{email_code\}\} in a later step/)).toBeTruthy();
    });
});

describe('wait-for-webhook step editor', () => {
    it('stores conditions and extraction', () => {
        const updateStep = renderStep('wait-for-webhook', {});
        fireEvent.change(screen.getByLabelText('Method'), { target: { value: 'post' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'params', { method: 'POST' });
        fireEvent.change(screen.getByLabelText('Extract JSON path'), { target: { value: 'data.id' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'params', { extract: 'data.id' });
    });

    it('only saves valid JSON for the body condition', () => {
        const updateStep = renderStep('wait-for-webhook', {});
        const where = screen.getByLabelText('Body must match (JSON)');
        fireEvent.change(where, { target: { value: '{"type": ' } });
        expect(updateStep).not.toHaveBeenCalled();
        expect(screen.getByText('Not valid JSON')).toBeTruthy();
        fireEvent.change(where, { target: { value: '{"type": "order.paid"}' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'params', { where: '{"type": "order.paid"}' });
    });
});

describe('hook step helpers', () => {
    it('defaults variable names like the worker does', () => {
        expect(emailVariable({})).toBe('email_link');
        expect(emailVariable({ extract: 'body' })).toBe('email_body');
        expect(emailVariable({ extract: 'none' })).toBeNull();
        expect(emailVariable({ extract: 'code', variableName: 'otp' })).toBe('otp');
        expect(webhookVariable({})).toBe('webhook_body');
        expect(webhookVariable({ extract: 'a.b' })).toBe('webhook_value');
    });

    it('validates the where object', () => {
        expect(whereError('')).toBeNull();
        expect(whereError('{"a": 1}')).toBeNull();
        expect(whereError('[1]')).toMatch(/JSON object/);
        expect(whereError('{')).toBe('Not valid JSON');
    });
});

// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StepComponent, type TestStep } from './StepComponent';

afterEach(cleanup);

function renderStep(params: TestStep['params']) {
    const updateStep = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, queryFn: () => new Promise(() => {}) } } });
    render(
        <QueryClientProvider client={client}>
            <StepComponent step={{ id: 's1', type: 'totp', params }} index={0} updateStep={updateStep}
                removeStep={vi.fn()} moveStep={vi.fn()} insertStep={vi.fn()} isFirst isLast />
        </QueryClientProvider>,
    );
    return updateStep;
}

describe('2FA code step editor', () => {
    it('edits the secret and the variable name into params', () => {
        const updateStep = renderStep({});
        fireEvent.change(screen.getByLabelText('Authenticator secret'), { target: { value: '{{secret.MFA_SEED}}' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'params', { secret: '{{secret.MFA_SEED}}' });
        fireEvent.change(screen.getByLabelText('Save code as'), { target: { value: 'code' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'params', { variableName: 'code' });
    });

    it('tells the user how to use the code when the secret is a reference', () => {
        renderStep({ secret: '{{secret.MFA_SEED}}', variableName: 'code' });
        expect(screen.getByText(/\{\{code\}\}/)).toBeTruthy();
    });

    it('warns when a raw key is pasted instead of a project secret', () => {
        renderStep({ secret: 'JBSWY3DPEHPK3PXP' });
        expect(screen.getByText(/saved in plain text/)).toBeTruthy();
    });
});

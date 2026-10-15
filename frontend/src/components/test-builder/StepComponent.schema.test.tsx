// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StepComponent, type TestStep } from './StepComponent';
import { buildOpenApiSchema, openApiRefError, parseInlineSchema, schemaMode } from './expectSchema';

afterEach(cleanup);

function renderStep(params: TestStep['params']) {
    const updateStep = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, queryFn: () => new Promise(() => {}) } } });
    render(
        <QueryClientProvider client={client}>
            <StepComponent step={{ id: 's1', type: 'http-request', value: 'https://api.example.com/pets/1', params }}
                index={0} updateStep={updateStep} removeStep={vi.fn()} moveStep={vi.fn()} insertStep={vi.fn()} isFirst isLast />
        </QueryClientProvider>,
    );
    return updateStep;
}

describe('http-request expect_schema editor', () => {
    it('stores an inline schema only once it parses', () => {
        const updateStep = renderStep({ method: 'GET' });
        fireEvent.click(screen.getByRole('button', { name: 'Inline JSON Schema' }));
        const box = screen.getByLabelText('JSON Schema');
        fireEvent.change(box, { target: { value: '{"type": ' } });
        expect(screen.getByText('Not valid JSON')).toBeTruthy();
        expect(updateStep).not.toHaveBeenCalled();
        fireEvent.change(box, { target: { value: '{"type": "object"}' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'params', { method: 'GET', expect_schema: { schema: { type: 'object' } } });
    });

    it('references an OpenAPI operation by id with an optional status', () => {
        const updateStep = renderStep({ method: 'GET' });
        fireEvent.click(screen.getByRole('button', { name: 'OpenAPI operation' }));
        fireEvent.change(screen.getByLabelText('Operation ID'), { target: { value: 'getPet' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'params', { method: 'GET', expect_schema: { openapi: { operationId: 'getPet' } } });
        fireEvent.change(screen.getByLabelText('Response status'), { target: { value: '200' } });
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'params',
            { method: 'GET', expect_schema: { openapi: { operationId: 'getPet' }, status: '200' } });
    });

    it('loads an existing reference and can switch the check off', () => {
        const updateStep = renderStep({ expect_schema: { openapi: { method: 'GET', path: '/pets/{id}' } } });
        expect((screen.getByLabelText('Spec path') as HTMLInputElement).value).toBe('/pets/{id}');
        fireEvent.click(screen.getByRole('button', { name: 'No schema check' }));
        expect(updateStep).toHaveBeenLastCalledWith('s1', 'params', { expect_schema: undefined });
    });
});

describe('expect_schema helpers', () => {
    it('classifies, parses and builds', () => {
        expect(schemaMode(undefined)).toBe('none');
        expect(schemaMode({ schema: {} })).toBe('inline');
        expect(schemaMode({ openapi: { operationId: 'x' } })).toBe('openapi');
        expect(parseInlineSchema('[]').error).toMatch(/object/);
        expect(buildOpenApiSchema({ operationId: '', method: 'get', path: '/a', status: '' }))
            .toEqual({ openapi: { method: 'GET', path: '/a' } });
        expect(openApiRefError({ openapi: { method: 'GET' } })).toMatch(/method and path/);
    });
});

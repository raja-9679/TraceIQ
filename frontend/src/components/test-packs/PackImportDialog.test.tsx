// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { PackSummary } from '@/api/solution';

const importPack = vi.fn();
vi.mock('@/lib/api', () => ({
    getProjects: async () => [{ id: 3, name: 'Shop', workspace_id: 1 }, { id: 4, name: 'Admin', workspace_id: 1 }],
}));
vi.mock('@/api/solution', async (orig) => ({
    ...(await orig<typeof import('@/api/solution')>()),
    packsApi: {
        environments: async () => [{ id: 9, name: 'staging', is_default: true }],
        importPack: (...a: unknown[]) => importPack(...a),
    },
}));

import PackImportDialog from './PackImportDialog';

const PACK: PackSummary = {
    id: 'scim-2', version: '1.0.0', title: 'SCIM 2.0 provisioning', category: 'identity',
    description: 'd', suite_name: 'SCIM 2.0 provisioning pack v1.0.0', case_count: 5,
    variables: [
        { name: 'SCIM_BASE_URL', description: 'Base URL', required: true, secret: false, default: null },
        { name: 'SCIM_TOKEN', description: 'Token', required: true, secret: true, default: null },
        { name: 'SCIM_TEST_DOMAIN', description: 'Domain', required: false, secret: false, default: 'example.com' },
    ],
    suites: [],
};

afterEach(cleanup);
beforeEach(() => {
    importPack.mockReset().mockResolvedValue({
        suite_id: 77, suite_name: PACK.suite_name, case_count: 5, pack: { id: 'scim-2', version: '1.0.0' },
        environment: { id: 9, name: 'staging' }, variables: { set: [], defaults_used: [], kept_existing: [] },
        secrets: { stored: ['SCIM_TOKEN'], already_set: [] }, missing: [],
    });
});

function open() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(
        <QueryClientProvider client={client}>
            <MemoryRouter>
                <PackImportDialog pack={PACK} open onOpenChange={vi.fn()} defaultProjectId={4} />
            </MemoryRouter>
        </QueryClientProvider>,
    );
}

describe('PackImportDialog', () => {
    it('masks secret variables and shows defaults as placeholders', async () => {
        open();
        expect((screen.getByLabelText(/SCIM_TOKEN/) as HTMLInputElement).type).toBe('password');
        const domain = screen.getByLabelText(/SCIM_TEST_DOMAIN/) as HTMLInputElement;
        expect(domain.value).toBe('');
        expect(domain.placeholder).toBe('Default: example.com');
    });

    it('imports into the chosen project with the filled values and links to the suite', async () => {
        open();
        const user = userEvent.setup();
        await user.type(screen.getByLabelText(/SCIM_BASE_URL/), 'https://scim.example.com/v2');
        await user.type(screen.getByLabelText(/SCIM_TOKEN/), 's3cret');
        await user.click(screen.getByRole('button', { name: /Import pack/ }));
        expect(importPack).toHaveBeenCalledWith(4, 'scim-2', {
            variables: { SCIM_BASE_URL: 'https://scim.example.com/v2', SCIM_TOKEN: 's3cret' },
            environment_id: null,
        });
        const link = await screen.findByRole('link', { name: /Open the suite/ });
        expect(link.getAttribute('href')).toBe('/suites/77');
    });
});

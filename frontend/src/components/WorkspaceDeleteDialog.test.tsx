// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const preview = vi.fn();
const remove = vi.fn();
vi.mock('@/lib/api', () => ({
    previewWorkspaceDeletion: (id: number) => preview(id),
    deleteWorkspace: (id: number, confirm: string) => remove(id, confirm),
}));

import WorkspaceDeleteDialog from './WorkspaceDeleteDialog';

afterEach(cleanup);
beforeEach(() => {
    preview.mockReset().mockResolvedValue({
        status: 'dry-run', workspace_name: 'Acme QA', total_rows_deleted: 57,
        rows_deleted: { project: 2, testcase: 30, testrun: 12, testcaseresult: 13 },
        retained: { auditlog: 'legal obligation' },
    });
    remove.mockReset().mockResolvedValue({ status: 'success' });
});

function open(onDeleted = vi.fn()) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(
        <QueryClientProvider client={client}>
            <WorkspaceDeleteDialog workspace={{ id: 7, name: 'Acme QA' }} open onOpenChange={vi.fn()} onDeleted={onDeleted} />
        </QueryClientProvider>,
    );
    return onDeleted;
}

describe('WorkspaceDeleteDialog', () => {
    it('shows what will be removed, from a dry run', async () => {
        open();
        expect(preview).toHaveBeenCalledWith(7);
        expect(await screen.findByText(/2 projects/)).toBeTruthy();
        expect(screen.getByText(/30 test cases/)).toBeTruthy();
        expect(screen.getByText(/12 runs/)).toBeTruthy();
        expect(screen.getByText(/audit history is kept/i)).toBeTruthy();
    });

    it('only deletes once the exact workspace name is typed, and sends it as confirm', async () => {
        const user = userEvent.setup();
        const onDeleted = open();
        const button = await screen.findByRole('button', { name: /delete workspace/i });
        expect((button as HTMLButtonElement).disabled).toBe(true);

        await user.type(screen.getByLabelText(/type the workspace name/i), 'acme qa');
        expect((button as HTMLButtonElement).disabled).toBe(true);

        await user.clear(screen.getByLabelText(/type the workspace name/i));
        await user.type(screen.getByLabelText(/type the workspace name/i), 'Acme QA');
        expect((button as HTMLButtonElement).disabled).toBe(false);
        await user.click(button);

        expect(remove).toHaveBeenCalledWith(7, 'Acme QA');
        await vi.waitFor(() => expect(onDeleted).toHaveBeenCalled());
    });

    it("shows the server's reason when the delete is refused", async () => {
        remove.mockRejectedValue({ response: { data: { detail: 'Permission denied: workspace:delete_workspace' } } });
        const user = userEvent.setup();
        open();
        await user.type(await screen.findByLabelText(/type the workspace name/i), 'Acme QA');
        await user.click(screen.getByRole('button', { name: /delete workspace/i }));
        expect(await screen.findByText(/Permission denied/)).toBeTruthy();
    });
});

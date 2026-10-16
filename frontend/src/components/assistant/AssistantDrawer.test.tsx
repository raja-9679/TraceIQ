// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AssistantStatus, ConversationDetail } from '@/api/assistant';

const api = vi.hoisted(() => ({
    status: vi.fn(), list: vi.fn(), get: vi.fn(), create: vi.fn(), remove: vi.fn(), send: vi.fn(),
}));
vi.mock('@/api/assistant', () => ({ assistantApi: api }));
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

import AssistantDrawer from './AssistantDrawer';

const ON: AssistantStatus = {
    enabled: true, reason: null, message: null, disclaimer: 'AI can be wrong; check the cited records.',
    max_tool_steps: 4, retention_days: 30,
};
const THREAD: ConversationDetail = {
    id: 7, workspace_id: 1, project_id: 3, title: 'why did checkout fail?',
    created_at: '2026-10-16T10:00:00', updated_at: '2026-10-16T10:00:00',
    disclaimer: ON.disclaimer,
    messages: [
        { id: 1, role: 'user', status: null, content: 'why did checkout fail?', hidden: false, citations: [],
          uncited: false, dropped_citations: 0, tool_calls: [], created_at: '2026-10-16T10:00:00' },
        { id: 2, role: 'assistant', status: 'answered', content: 'A timeout [result:44], see [unverified].',
          hidden: false, uncited: false, dropped_citations: 1,
          citations: [{ ref: 'result:44', kind: 'result', id: 44, project_id: 8, label: 'checkout pays · failed (run #12)', run_id: 12 }],
          tool_calls: [{ tool: 'search_results', items: 1 }], created_at: '2026-10-16T10:00:01' },
        { id: 3, role: 'assistant', status: 'answered', content: 'Probably flaky.', hidden: false, uncited: true,
          dropped_citations: 0, citations: [], tool_calls: [], created_at: '2026-10-16T10:00:02' },
    ],
};

function Where() {
    return <span data-testid="where">{useLocation().pathname}</span>;
}

const onSelectProject = vi.fn();
const onOpenChange = vi.fn();

function open(projectId = 3) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(
        <QueryClientProvider client={client}>
            <MemoryRouter initialEntries={['/']}>
                <AssistantDrawer open onOpenChange={onOpenChange} workspaceId={1} projectId={projectId}
                    onSelectProject={onSelectProject} />
                <Routes><Route path="*" element={<Where />} /></Routes>
            </MemoryRouter>
        </QueryClientProvider>,
    );
}

afterEach(cleanup);
beforeEach(() => {
    Object.values(api).forEach((f) => f.mockReset());
    onSelectProject.mockReset();
    onOpenChange.mockReset();
    api.status.mockResolvedValue(ON);
    api.list.mockResolvedValue([{ id: 7, workspace_id: 1, project_id: 3, title: THREAD.title,
        created_at: THREAD.created_at, updated_at: THREAD.updated_at }]);
    api.get.mockResolvedValue(THREAD);
});

describe('AssistantDrawer', () => {
    it('shows the disclaimer and labelled controls', async () => {
        open();
        expect(await screen.findByText('AI can be wrong; check the cited records.')).toBeTruthy();
        expect(screen.getByLabelText('Your question')).toBeTruthy();
        expect(screen.getByRole('button', { name: /New conversation/ })).toBeTruthy();
        expect(screen.getByRole('button', { name: 'Close assistant' })).toBeTruthy();
    });

    it('opens a past conversation with citation chips, flags and removed refs', async () => {
        const user = userEvent.setup();
        open();
        await user.click(await screen.findByRole('button', { name: /History \(1\)/ }));
        await user.click(screen.getByRole('button', { name: /^why did checkout fail\?/ }));
        expect(await screen.findByText('Sources')).toBeTruthy();
        expect(screen.getByText('1 reference removed (not in the data)')).toBeTruthy();
        expect(screen.getByText('No sources cited: treat as unverified')).toBeTruthy();
        expect(screen.getByText(/Looked up: search results \(1\)/)).toBeTruthy();

        // The chip switches to the record's project, then navigates to it.
        await user.click(screen.getByRole('button', { name: /Open Result: checkout pays/ }));
        expect(onSelectProject).toHaveBeenCalledWith(8);
        expect(onOpenChange).toHaveBeenCalledWith(false);
        expect(screen.getByTestId('where').textContent).toBe('/runs/12');
    });

    it('creates a conversation on the first question and sends it', async () => {
        const user = userEvent.setup();
        api.create.mockResolvedValue({ id: 9 });
        api.send.mockResolvedValue({ conversation: { id: 9 }, disclaimer: '', message: THREAD.messages[1] });
        open();
        await user.type(await screen.findByLabelText('Your question'), 'is the release ready?');
        await user.click(screen.getByRole('button', { name: /^Ask$/ }));
        await waitFor(() => expect(api.send).toHaveBeenCalledWith(9, 1, 'is the release ready?', 3));
        expect(api.create).toHaveBeenCalledWith(1, 3);
    });

    it('explains when AI is not configured and disables the input', async () => {
        api.status.mockResolvedValue({ ...ON, enabled: false, reason: 'not_configured',
            message: 'AI is not configured on this TraceIQ instance.' });
        open();
        expect(await screen.findByText('AI is not configured on this TraceIQ instance.')).toBeTruthy();
        expect((screen.getByLabelText('Your question') as HTMLTextAreaElement).disabled).toBe(true);
    });
});

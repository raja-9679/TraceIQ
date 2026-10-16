import { api } from '@/lib/api';

// Mirrors backend/app/api/assistant.py (WP14a, "Ask TraceIQ").

export type CitationKind = 'project' | 'run' | 'result' | 'cluster' | 'finding' | 'case' | 'readiness';

export interface Citation {
    ref: string;
    kind: CitationKind;
    id: number;
    project_id: number | null;
    label: string | null;
    /** result citations: the run the result belongs to. */
    run_id?: number;
    /** case citations: the suite the case lives in. */
    suite_id?: number | null;
}

export type AnswerStatus =
    | 'answered' | 'step_limit' | 'invalid' | 'not_configured' | 'disabled' | 'quota' | 'no_response';

export interface AssistantMessage {
    id: number;
    role: 'user' | 'assistant';
    status: AnswerStatus | null;
    content: string;
    /** The answer used a project the reader can no longer access. */
    hidden: boolean;
    citations: Citation[];
    /** Nothing it says is backed by a record. */
    uncited: boolean;
    /** References the model gave that were not in the data and were removed. */
    dropped_citations: number;
    tool_calls: { tool: string; items: number; error?: string | null }[];
    created_at: string;
}

export interface Conversation {
    id: number;
    workspace_id: number;
    project_id: number | null;
    title: string;
    created_at: string;
    updated_at: string;
}

export interface ConversationDetail extends Conversation {
    disclaimer: string;
    messages: AssistantMessage[];
}

export interface AssistantStatus {
    enabled: boolean;
    reason: 'disabled' | 'not_configured' | null;
    message: string | null;
    disclaimer: string;
    max_tool_steps: number;
    retention_days: number;
}

export interface SendResult {
    conversation: Conversation;
    disclaimer: string;
    message: AssistantMessage;
}

export const assistantApi = {
    status: (workspaceId: number) =>
        api.get<AssistantStatus>('/assistant/status', { params: { workspace_id: workspaceId } })
            .then((r) => r.data),
    list: (workspaceId: number) =>
        api.get<Conversation[]>('/assistant/conversations', { params: { workspace_id: workspaceId } })
            .then((r) => r.data),
    get: (id: number) =>
        api.get<ConversationDetail>(`/assistant/conversations/${id}`).then((r) => r.data),
    create: (workspaceId: number, projectId?: number | null) =>
        api.post<Conversation>('/assistant/conversations', {
            workspace_id: workspaceId,
            ...(projectId ? { project_id: projectId } : {}),
        }).then((r) => r.data),
    remove: (id: number) => api.delete(`/assistant/conversations/${id}`).then((r) => r.data),
    // Answers take several LLM round trips; the default 30 s timeout is too tight.
    send: (id: number, workspaceId: number, text: string, projectId?: number | null) =>
        api.post<SendResult>(`/assistant/conversations/${id}/messages`, {
            text,
            workspace_id: workspaceId,
            ...(projectId ? { project_id: projectId } : {}),
        }, { timeout: 180000 }).then((r) => r.data),
};

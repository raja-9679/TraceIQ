import { api } from '@/lib/api';

// ---------------------------------------------------------------------------
// Types — mirror backend/app/models.py WorkspaceWebhookRead and the
// WebhookCreateResponse / WebhookPatch schemas in api/workspace_webhooks.py
// ---------------------------------------------------------------------------

export interface WorkspaceWebhook {
    id: number;
    workspace_id: number;
    project_id: number | null;
    name: string;
    url: string;
    // Comma-separated event names (run.completed / run.failed / run.passed);
    // null or empty matches all events.
    event_filter: string | null;
    is_active: boolean;
    created_at: string;
    last_delivery_at: string | null;
    last_delivery_status: number | null;
    failure_count: number;
}

// The signing secret is returned exactly once, at creation time.
export interface WebhookCreateResponse extends WorkspaceWebhook {
    secret: string;
}

export interface WebhookCreatePayload {
    name: string;
    url: string;
    workspace_id: number;
    project_id?: number | null;
    event_filter?: string | null;
}

export interface WebhookPatchPayload {
    is_active?: boolean;
    event_filter?: string;
    name?: string;
}

export const WEBHOOK_EVENTS = ['run.completed', 'run.failed', 'run.passed'] as const;

export const webhooksApi = {
    list: async (workspaceId: number): Promise<WorkspaceWebhook[]> => {
        const response = await api.get(`/workspaces/${workspaceId}/webhooks`);
        return response.data;
    },

    create: async (workspaceId: number, body: WebhookCreatePayload): Promise<WebhookCreateResponse> => {
        const response = await api.post(`/workspaces/${workspaceId}/webhooks`, body);
        return response.data;
    },

    patch: async (workspaceId: number, webhookId: number, body: WebhookPatchPayload): Promise<WorkspaceWebhook> => {
        const response = await api.patch(`/workspaces/${workspaceId}/webhooks/${webhookId}`, body);
        return response.data;
    },

    remove: async (workspaceId: number, webhookId: number): Promise<{ status: string; id: number }> => {
        const response = await api.delete(`/workspaces/${workspaceId}/webhooks/${webhookId}`);
        return response.data;
    },
};

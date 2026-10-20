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
    // Comma-separated event names (see WEBHOOK_EVENTS); null or empty
    // matches all events. "run.completed" also receives cancelled and
    // timed-out runs (they were "completed" before WP15f).
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

export const WEBHOOK_EVENTS = ['run.passed', 'run.failed', 'run.cancelled', 'run.timed_out', 'run.completed'] as const;

// One delivery ATTEMPT (backend WebhookDelivery, metadata only — the payload
// and the URL are never returned).
export interface WebhookDelivery {
    id: number;
    webhook_id: number;
    run_id: number | null;
    event: string;
    event_id: string;
    // Stable across retries and redelivery (sent as X-TraceIQ-Delivery).
    delivery_id: string;
    attempt: number;
    manual: boolean;
    status: 'pending' | 'ok' | 'failed' | 'skipped';
    status_code: number | null;
    duration_ms: number | null;
    error: string | null;
    next_retry_at: string | null;
    created_at: string | null;
    completed_at: string | null;
}

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

    deliveries: async (workspaceId: number, webhookId: number, status?: 'ok' | 'failed'):
        Promise<{ webhook_id: number; retention_days: number; deliveries: WebhookDelivery[] }> => {
        const qs = status ? `?status=${status}` : '';
        const response = await api.get(`/workspaces/${workspaceId}/webhooks/${webhookId}/deliveries${qs}`);
        return response.data;
    },

    redeliver: async (workspaceId: number, webhookId: number, deliveryRowId: number):
        Promise<{ status: string; delivery_id: string }> => {
        const response = await api.post(
            `/workspaces/${workspaceId}/webhooks/${webhookId}/deliveries/${deliveryRowId}/redeliver`);
        return response.data;
    },
};

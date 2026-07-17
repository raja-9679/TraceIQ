import { api } from '@/lib/api';

// ---------------------------------------------------------------------------
// Types — mirror backend/app/models.py ApiKeyRead / ApiKeyCreateResponse
// ---------------------------------------------------------------------------

export interface ApiKey {
    id: number;
    workspace_id: number;
    project_id: number | null;
    name: string;
    prefix: string;
    created_at: string;
    last_used_at: string | null;
    expires_at: string | null;
    revoked_at: string | null;
}

// Plaintext secret is returned exactly once, at creation time.
export interface ApiKeyCreateResponse extends ApiKey {
    secret: string;
}

export interface ApiKeyCreatePayload {
    name: string;
    workspace_id: number;
    project_id?: number | null;
    role_id?: number | null;
    expires_in_days?: number | null;
}

export const apiKeysApi = {
    list: async (workspaceId: number): Promise<ApiKey[]> => {
        const response = await api.get(`/workspaces/${workspaceId}/api-keys`);
        return response.data;
    },

    create: async (workspaceId: number, body: ApiKeyCreatePayload): Promise<ApiKeyCreateResponse> => {
        const response = await api.post(`/workspaces/${workspaceId}/api-keys`, body);
        return response.data;
    },

    revoke: async (workspaceId: number, keyId: number): Promise<{ status: string; id?: number }> => {
        const response = await api.delete(`/workspaces/${workspaceId}/api-keys/${keyId}`);
        return response.data;
    },
};

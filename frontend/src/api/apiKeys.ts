import { api } from '@/lib/api';
import type { Out, Schema } from './schemaTypes';

// Types generated from the backend's OpenAPI document (ApiKeyRead,
// ApiKeyCreateResponse, ApiKeyCreate). `allowed_cidrs`: source-IP allowlist
// (WP8); null/empty = any address.
export type ApiKey = Out<'ApiKeyRead'>;
/** The plaintext secret is returned exactly once, at creation time. */
export type ApiKeyCreateResponse = Out<'ApiKeyCreateResponse'>;
export type ApiKeyCreatePayload = Schema<'ApiKeyCreate'>;

export const apiKeysApi = {
    list: async (workspaceId: number): Promise<ApiKey[]> => {
        const response = await api.get(`/workspaces/${workspaceId}/api-keys`);
        return response.data;
    },

    create: async (workspaceId: number, body: ApiKeyCreatePayload): Promise<ApiKeyCreateResponse> => {
        const response = await api.post(`/workspaces/${workspaceId}/api-keys`, body);
        return response.data;
    },

    setAllowedCidrs: async (workspaceId: number, keyId: number, allowedCidrs: string[] | null): Promise<ApiKey> => {
        const response = await api.put(`/workspaces/${workspaceId}/api-keys/${keyId}/allowed-cidrs`,
            { allowed_cidrs: allowedCidrs });
        return response.data;
    },

    revoke: async (workspaceId: number, keyId: number): Promise<{ status: string; id?: number }> => {
        const response = await api.delete(`/workspaces/${workspaceId}/api-keys/${keyId}`);
        return response.data;
    },
};

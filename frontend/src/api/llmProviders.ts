import { api } from '@/lib/api';

// Mirrors backend/app/api/llm_providers.py
export interface LLMProviderRead {
    id: number;
    name: string;
    provider_type: string;
    model: string;
    base_url: string | null;
    api_key_set: boolean;
    is_active: boolean;
    is_default: boolean;
    updated_at: string;
}

export interface LLMProviderPublic {
    id: number;
    name: string;
    provider_type: string;
    model: string;
    is_default: boolean;
}

export interface LLMProviderCreate {
    name: string;
    provider_type: string;
    model: string;
    base_url?: string | null;
    api_key?: string | null;
    is_active?: boolean;
    is_default?: boolean;
}

export interface LLMProviderUpdate extends Partial<LLMProviderCreate> {
    clear_api_key?: boolean;
}

export const PROVIDER_TYPES = [
    'anthropic',
    'openai',
    'gemini',
    'ollama',
    'openai-compatible',
] as const;

export const llmProvidersApi = {
    // Admin (instance admin only)
    list: async (): Promise<LLMProviderRead[]> =>
        (await api.get('/admin/llm-providers')).data,
    create: async (body: LLMProviderCreate): Promise<LLMProviderRead> =>
        (await api.post('/admin/llm-providers', body)).data,
    update: async (id: number, body: LLMProviderUpdate): Promise<LLMProviderRead> =>
        (await api.patch(`/admin/llm-providers/${id}`, body)).data,
    remove: async (id: number): Promise<void> => {
        await api.delete(`/admin/llm-providers/${id}`);
    },
    test: async (id: number): Promise<{ ok: boolean; provider: string; model: string; reply: string }> =>
        (await api.post(`/admin/llm-providers/${id}/test`)).data,

    // Any authenticated user — powers the per-analysis picker
    listActive: async (): Promise<LLMProviderPublic[]> =>
        (await api.get('/llm-providers/active')).data,
};

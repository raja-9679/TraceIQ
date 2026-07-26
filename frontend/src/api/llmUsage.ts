import { api } from '@/lib/api';

// Mirrors backend/app/api/llm_usage.py

export interface LLMProviderStats {
    provider: string;
    model: string;
    calls: number;
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
    avg_latency_ms: number;
    success_rate: number;
}

export interface LLMFeatureStats {
    feature: string;
    calls: number;
    total_tokens: number;
}

export interface LLMDailyStats {
    date: string;
    calls: number;
    total_tokens: number;
}

export interface LLMUsageSummary {
    workspace_id: number;
    window_days: number;
    totals: {
        calls: number;
        input_tokens: number;
        output_tokens: number;
        total_tokens: number;
        avg_latency_ms: number;
    };
    by_provider: LLMProviderStats[];
    by_feature: LLMFeatureStats[];
    daily: LLMDailyStats[];
    period: string;
    period_tokens_used: number;
    period_tokens_limit: number; // 0 = unlimited
}

export const llmUsageApi = {
    summary: async (workspaceId: number, days = 30): Promise<LLMUsageSummary> => {
        const r = await api.get(`/workspaces/${workspaceId}/llm-usage?days=${days}`);
        return r.data;
    },
};

import { api } from '@/lib/api';

// Mirrors the effectiveness endpoints in backend/app/api/analytics.py

export interface TestEffectiveness {
    test_name: string;
    runs: number;
    failures: number;
    failure_rate: number;
    clusters_surfaced: number;
    avg_duration_ms: number | null;
}

export interface EffectivenessSummary {
    project_id: number;
    window_days: number;
    total_finished_runs: number;
    pass_rate: number;
    run_counts: Record<string, number>;
    cluster_counts: Record<string, number>;
    open_clusters: number;
    mttr_hours: number | null;
    top_failing_tests: { test_name: string; runs: number; failures: number; avg_duration_ms: number | null }[];
    slowest_tests: { test_name: string; runs: number; failures: number; avg_duration_ms: number | null }[];
    flakiest_tests: { test_name: string; flake_score: number }[];
}

export const analyticsApi = {
    effectiveness: async (projectId: number, days = 30, limit = 100): Promise<TestEffectiveness[]> => {
        const r = await api.get(`/analytics/projects/${projectId}/test-effectiveness?days=${days}&limit=${limit}`);
        return r.data;
    },
    summary: async (projectId: number, days = 30): Promise<EffectivenessSummary> => {
        const r = await api.get(`/analytics/projects/${projectId}/effectiveness-summary?days=${days}`);
        return r.data;
    },
};

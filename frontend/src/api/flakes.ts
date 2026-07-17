import { api } from '@/lib/api';

// ---------------------------------------------------------------------------
// Types — mirror backend/app/api/analytics.py project_flakiness entries and
// backend/app/models.py FlakeRecord (returned raw by GET /api/flakes)
// ---------------------------------------------------------------------------

export interface FlakinessEntry {
    test_case_id: number;
    name: string;
    flake_score: number; // 0.0 = stable, 1.0 = pure flake
    is_quarantined: boolean;
    sample_count: number;
    last_failure_message: string | null;
    recent_failures: number;
}

export interface FlakeRecord {
    id: number;
    test_case_id: number;
    step_id: string | null;
    flake_score: number;
    is_quarantined: boolean;
    first_observed_at: string;
    last_observed_at: string;
    sample_count: number;
    last_failure_message: string | null;
}

export const flakesApi = {
    projectFlakiness: async (projectId: number, days = 30): Promise<FlakinessEntry[]> => {
        const response = await api.get(`/analytics/projects/${projectId}/flakiness?days=${days}`);
        return response.data;
    },

    list: async (testCaseId?: number, quarantinedOnly = false): Promise<FlakeRecord[]> => {
        const params = new URLSearchParams();
        if (testCaseId) params.append('test_case_id', testCaseId.toString());
        if (quarantinedOnly) params.append('quarantined_only', 'true');
        const response = await api.get(`/flakes?${params.toString()}`);
        return response.data;
    },

    quarantine: async (flakeId: number): Promise<{ status: string; id: number }> => {
        const response = await api.post(`/flakes/${flakeId}/quarantine`);
        return response.data;
    },

    release: async (flakeId: number): Promise<{ status: string; id: number }> => {
        const response = await api.post(`/flakes/${flakeId}/release`);
        return response.data;
    },
};

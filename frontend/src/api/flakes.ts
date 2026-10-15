import { api } from '@/lib/api';

// ---------------------------------------------------------------------------
// Types — mirror backend/app/api/analytics.py project_flakiness entries and
// backend/app/models.py FlakeRecord (returned raw by GET /api/flakes)
// ---------------------------------------------------------------------------

// 'expired' = past its expiry: the case runs again, but someone should decide
// whether to release it or quarantine it again.
export type QuarantineState = 'none' | 'active' | 'expired';

export interface FlakePerson {
    id: number;
    email: string;
    full_name: string;
}

export interface FlakinessEntry {
    test_case_id: number;
    name: string;
    flake_score: number; // 0.0 = stable, 1.0 = pure flake
    is_quarantined: boolean;
    quarantine_state: QuarantineState;
    sample_count: number;
    last_failure_message: string | null;
    recent_failures: number;
    flake_id: number | null; // case-level record the actions below act on
    owner: FlakePerson | null;
    quarantine_reason: string | null;
    quarantine_source: 'auto' | 'manual' | null;
    quarantine_expires_at: string | null;
}

export interface FlakeRecord {
    id: number;
    test_case_id: number;
    step_id: string | null;
    flake_score: number;
    is_quarantined: boolean;
    quarantine_state: QuarantineState;
    first_observed_at: string;
    last_observed_at: string;
    sample_count: number;
    last_failure_message: string | null;
    owner: FlakePerson | null;
    quarantined_by: FlakePerson | null;
    quarantine_reason: string | null;
    quarantine_source: 'auto' | 'manual' | null;
    quarantined_at: string | null;
    quarantine_expires_at: string | null;
}

export interface QuarantineInput {
    reason: string;
    days: number;
    owner_user_id?: number | null;
}

export const QUARANTINE_DEFAULT_DAYS = 14;
export const QUARANTINE_MAX_DAYS = 90;

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

    quarantine: async (flakeId: number, input: QuarantineInput): Promise<FlakeRecord> => {
        const response = await api.post(`/flakes/${flakeId}/quarantine`, input);
        return response.data;
    },

    release: async (flakeId: number): Promise<FlakeRecord> => {
        const response = await api.post(`/flakes/${flakeId}/release`);
        return response.data;
    },

    setOwner: async (flakeId: number, ownerUserId: number | null): Promise<FlakeRecord> => {
        const response = await api.put(`/flakes/${flakeId}/owner`, { owner_user_id: ownerUserId });
        return response.data;
    },

    ownerCandidates: async (projectId: number): Promise<FlakePerson[]> => {
        const response = await api.get(`/flakes/owner-candidates?project_id=${projectId}`);
        return response.data;
    },
};

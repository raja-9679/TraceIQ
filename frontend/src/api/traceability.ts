import { api } from '@/lib/api';

// Mirrors backend/app/api/traceability.py

export interface RequirementCoverage {
    ref: string;
    source: string;
    title?: string | null;
    url?: string | null;
    test_count: number;
    status: 'passing' | 'failing' | 'mixed' | 'unknown';
    passing: number;
    failing: number;
    untested: number;
    test_names: string[];
}

export interface TraceabilityGaps {
    project_id: number;
    total_cases: number;
    traced_cases: number;
    untraced_cases: { id: number; name: string }[];
}

export interface RequirementLink {
    id: number;
    test_case_id: number;
    ref: string;
    source: string;
    title?: string | null;
    url?: string | null;
    created_at: string;
}

export const traceApi = {
    requirements: async (projectId: number): Promise<RequirementCoverage[]> =>
        (await api.get(`/projects/${projectId}/requirements`)).data,
    gaps: async (projectId: number): Promise<TraceabilityGaps> =>
        (await api.get(`/projects/${projectId}/traceability/gaps`)).data,
    caseLinks: async (caseId: number): Promise<RequirementLink[]> =>
        (await api.get(`/cases/${caseId}/requirements`)).data,
    addLink: async (caseId: number, body: { ref: string; source?: string; title?: string; url?: string }): Promise<RequirementLink> =>
        (await api.post(`/cases/${caseId}/requirements`, body)).data,
    removeLink: async (caseId: number, linkId: number): Promise<void> => {
        await api.delete(`/cases/${caseId}/requirements/${linkId}`);
    },
};

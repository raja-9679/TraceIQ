import { api } from '@/lib/api';

// Mirrors backend/app/api/requirements.py

export type CoverageStatus = 'covered' | 'failing' | 'untested' | 'not_covered';

export interface RequirementCoverageDetail {
    status: CoverageStatus;
    cases: { id: number; name: string; latest_status: string | null }[];
    passing: number;
    failing: number;
    untested: number;
}

export interface AcceptanceCriterion {
    index: number;
    text: string;
    kind: 'gherkin' | 'list' | 'checklist' | 'text' | 'title';
    ambiguous: boolean;
    reasons: string[];
    proposal_id?: number | null;
    proposal_status?: string;
    needs_authoring?: boolean;
}

export interface ImportedRequirement {
    id: number;
    project_id: number;
    tracker_config_id: number | null;
    source: 'jira' | 'linear';
    external_key: string;
    title: string;
    url: string | null;
    external_status: string | null;
    external_updated_at: string | null;
    last_synced_at: string;
    sync_error: string | null;
    created_at: string;
    criteria_count: number;
    ambiguous_count: number;
    proposed_count: number;
    coverage: RequirementCoverageDetail;
}

export interface RequirementDetail extends ImportedRequirement {
    description: string | null;
    acceptance_criteria: AcceptanceCriterion[];
}

export interface ExternalIssueSummary {
    key: string;
    title: string;
    url: string | null;
    status: string | null;
    updated_at: string | null;
    labels: string[];
    already_imported: boolean;
}

export interface SearchBody {
    config_id: number;
    jql?: string;
    team_key?: string;
    query?: string;
    label?: string;
    state?: string;
    max_results?: number;
}

export interface GenerateResult {
    generated: { index: number; proposal_id: number; status: string; needs_authoring: boolean; needs_authoring_reason: string | null }[];
    ambiguous: { index: number; text: string; reasons: string[] }[];
    skipped: { index: number; reason: string }[];
}

export const requirementsApi = {
    list: async (projectId: number): Promise<{ requirements: ImportedRequirement[]; totals: Record<CoverageStatus, number> }> =>
        (await api.get(`/projects/${projectId}/requirements/imported`)).data,
    get: async (id: number): Promise<RequirementDetail> => (await api.get(`/requirements/${id}`)).data,
    search: async (projectId: number, body: SearchBody): Promise<{ provider: string; issues: ExternalIssueSummary[] }> =>
        (await api.post(`/projects/${projectId}/requirements/search`, body)).data,
    import: async (projectId: number, configId: number, keys: string[]): Promise<{ created: number; updated: number; not_found: string[] }> =>
        (await api.post(`/projects/${projectId}/requirements/import`, { config_id: configId, keys })).data,
    sync: async (id: number): Promise<RequirementDetail> => (await api.post(`/requirements/${id}/sync`)).data,
    syncAll: async (projectId: number): Promise<{ synced: number; errors: Record<string, string> }> =>
        (await api.post(`/projects/${projectId}/requirements/sync`)).data,
    generate: async (id: number, body: { test_suite_id: number; target_url?: string; criteria?: number[]; include_ambiguous?: boolean; regenerate?: boolean }): Promise<GenerateResult> =>
        (await api.post(`/requirements/${id}/generate`, body)).data,
    remove: async (id: number): Promise<void> => { await api.delete(`/requirements/${id}`); },
};

export const COVERAGE_LABEL: Record<CoverageStatus, string> = {
    covered: 'Covered',
    failing: 'Failing',
    untested: 'Not run yet',
    not_covered: 'Not covered',
};

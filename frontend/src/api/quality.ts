import { api } from '@/lib/api';
import type { Out } from './schemaTypes';

// Request/response shapes come from the backend's OpenAPI document
// (generated/schema.ts). Fields the backend types as plain `str` but documents
// as a fixed set are narrowed here (verdict, scope, case outcome).

export type QualityTrendPoint = Out<'QualityTrendPoint'>;
export type QualitySnapshot = Out<'QualitySnapshot'>;
/** Includes the WP16b API-contract fields (api_baseline, api_baseline_version,
 *  require_no_breaking_api_changes) the hand-written interface had lost. */
export type QualityGatePolicy = Out<'QualityGatePolicy'>;
export interface ExternalTestReport {
    id: number;
    project_id: number;
    source: string;
    suite_name: string | null;
    git_commit: string | null;
    git_branch: string | null;
    tests: number;
    failures: number;
    errors: number;
    skipped: number;
    time_seconds: number;
    failed_cases: { name: string; classname: string; message: string }[] | null;
    uploaded_by: string | null;
    created_at: string;
}

export type QualityGateCheck = Out<'QualityGateCheck'>;
export type QualityGateResult = Out<'QualityGateResult'>;

export interface GatePolicyVersion {
    version: number;
    sha256: string;
    created_at: string | null;
    created_by_id: number | null;
    policy?: Record<string, unknown>;
}

export type ReadinessVerdict = 'ready' | 'not_ready' | 'insufficient_data';
export type CaseOutcome = 'passed' | 'failed' | 'not_run';

export type ReadinessRun = Out<'ReadinessRun'>;
export type ReadinessCriticalCase = Omit<Out<'ReadinessCriticalCase'>, 'status'> & { status: CaseOutcome };
export type ReadinessQuarantine = Out<'ReadinessQuarantine'>;
export type ReadinessRequirement = Omit<Out<'ReadinessRequirement'>, 'status' | 'cases'> & {
    status: 'passing' | 'failing' | 'partial' | 'untested';
    cases: (Omit<Out<'ReadinessRequirementCase'>, 'status'> & { status: CaseOutcome })[];
};
export type ReleaseReadiness = Omit<Out<'ReleaseReadiness'>, 'verdict' | 'scope' | 'critical_cases' | 'requirements'> & {
    verdict: ReadinessVerdict;
    scope: 'commit' | 'branch' | 'latest_run' | 'none';
    critical_cases: ReadinessCriticalCase[];
    requirements: ReadinessRequirement[];
};

export type CiSettings = Out<'CiSettings'>;

export const qualityApi = {
    dashboard: async (projectId: number, days = 7): Promise<QualitySnapshot> => {
        const r = await api.get(`/projects/${projectId}/quality?days=${days}`);
        return r.data;
    },
    gate: async (projectId: number, gitCommit?: string, gitBranch?: string): Promise<QualityGateResult> => {
        const params = new URLSearchParams();
        if (gitCommit) params.set('git_commit', gitCommit);
        if (gitBranch) params.set('git_branch', gitBranch);
        const qs = params.toString();
        const r = await api.get(`/projects/${projectId}/quality-gate${qs ? `?${qs}` : ''}`);
        return r.data;
    },
    readiness: async (projectId: number, gitCommit?: string, gitBranch?: string): Promise<ReleaseReadiness> => {
        const params = new URLSearchParams();
        if (gitCommit) params.set('git_commit', gitCommit);
        if (gitBranch) params.set('git_branch', gitBranch);
        const qs = params.toString();
        const r = await api.get(`/projects/${projectId}/release-readiness${qs ? `?${qs}` : ''}`);
        return r.data;
    },
    // Re-judge an earlier verdict with the policy and runs it reported.
    gateReplay: async (projectId: number, policyVersion: number, runIds: number[]): Promise<QualityGateResult> => {
        const params = new URLSearchParams({ policy_version: String(policyVersion) });
        runIds.forEach((id) => params.append('run_ids', String(id)));
        const r = await api.get(`/projects/${projectId}/quality-gate?${params.toString()}`);
        return r.data;
    },
    policyVersions: async (projectId: number): Promise<{ project_id: number; versions: GatePolicyVersion[] }> => {
        const r = await api.get(`/projects/${projectId}/quality-gate/policy/versions`);
        return r.data;
    },
    policyVersion: async (projectId: number, version: number): Promise<GatePolicyVersion> => {
        const r = await api.get(`/projects/${projectId}/quality-gate/policy/versions/${version}`);
        return r.data;
    },
    getPolicy: async (projectId: number): Promise<QualityGatePolicy> => {
        const r = await api.get(`/projects/${projectId}/quality-gate/policy`);
        return r.data;
    },
    setPolicy: async (projectId: number, policy: QualityGatePolicy): Promise<QualityGatePolicy> => {
        const r = await api.put(`/projects/${projectId}/quality-gate/policy`, policy);
        return r.data;
    },
    externalReports: async (projectId: number, limit = 5): Promise<ExternalTestReport[]> => {
        const r = await api.get(`/projects/${projectId}/external-results?limit=${limit}`);
        return r.data;
    },
    getCiSettings: async (projectId: number): Promise<CiSettings> => {
        const r = await api.get(`/projects/${projectId}/ci-settings`);
        return r.data;
    },
    setCiSettings: async (projectId: number, ci: CiSettings): Promise<CiSettings> => {
        const r = await api.put(`/projects/${projectId}/ci-settings`, ci);
        return r.data;
    },
    runReport: async (runId: number): Promise<any> => {
        const r = await api.get(`/runs/${runId}/report`);
        return r.data;
    },
};

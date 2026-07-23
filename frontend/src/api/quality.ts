import { api } from '@/lib/api';

// Mirrors backend/app/api/endpoints/quality.py

export interface QualityTrendPoint {
    date: string;
    runs: number;
    passed_runs: number;
    pass_rate: number;
}

export interface QualitySnapshot {
    project_id: number;
    window_days: number;
    total_runs: number;
    finished_runs: number;
    passed_runs: number;
    failed_runs: number;
    pass_rate: number;
    trend: QualityTrendPoint[];
    flaky_tests: number;
    quarantined_tests: number;
    monitors_total: number;
    monitors_up: number;
    monitors_down: number;
    down_monitor_names: string[];
    security_findings: Record<string, number>;
}

export interface QualityGatePolicy {
    min_pass_rate: number;
    max_high_severity_findings: number;
    max_medium_severity_findings: number | null;
    require_monitors_up: boolean;
    // Performance budgets (0 = not enforced)
    max_lcp_ms: number;
    max_cls: number;
    max_ttfb_ms: number;
}

export interface QualityGateCheck {
    name: string;
    passed: boolean;
    actual: string;
    threshold: string;
    detail?: string | null;
}

export interface QualityGateResult {
    project_id: number;
    passed: boolean;
    git_commit?: string | null;
    git_branch?: string | null;
    evaluated_run_ids: number[];
    checks: QualityGateCheck[];
}

export interface CiSettings {
    enabled: boolean;
    enforce_gate: boolean;
    post_pr_comment: boolean;
}

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
    getPolicy: async (projectId: number): Promise<QualityGatePolicy> => {
        const r = await api.get(`/projects/${projectId}/quality-gate/policy`);
        return r.data;
    },
    setPolicy: async (projectId: number, policy: QualityGatePolicy): Promise<QualityGatePolicy> => {
        const r = await api.put(`/projects/${projectId}/quality-gate/policy`, policy);
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

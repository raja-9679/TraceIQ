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
    require_external_tests_pass: boolean;
    // Release readiness (null / false = off)
    max_flaky_cases: number | null;
    require_critical_passed: boolean;
    max_evidence_age_days: number | null;
}

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

export interface QualityGateCheck {
    name: string;
    passed: boolean;
    actual: string;
    threshold: string;
    detail?: string | null;
    // Failed because evidence is absent, not because something failed.
    missing_data?: boolean;
}

export interface QualityGateResult {
    project_id: number;
    passed: boolean;
    git_commit?: string | null;
    git_branch?: string | null;
    evaluated_run_ids: number[];
    checks: QualityGateCheck[];
}

export type ReadinessVerdict = 'ready' | 'not_ready' | 'insufficient_data';
export type CaseOutcome = 'passed' | 'failed' | 'not_run';

export interface ReadinessRun {
    id: number;
    status: string;
    created_at: string;
    suite_name: string | null;
    browser: string | null;
    git_commit: string | null;
    git_branch: string | null;
    selection_mode: string | null;
    total_tests: number;
    passed_tests: number;
    failed_tests: number;
}

export interface ReadinessCriticalCase {
    case_id: number;
    name: string;
    status: CaseOutcome;
    run_id: number | null;
    detail: string | null;
    last_status: string | null;
    last_at: string | null;
    last_commit: string | null;
    last_passed_at: string | null;
}

export interface ReadinessQuarantine {
    flake_record_id: number;
    case_id: number;
    case_name: string;
    flake_score: number;
    source: string | null;
    reason: string | null;
    quarantined_at: string | null;
    expires_at: string | null;
    owner: { id: number; email: string; full_name: string | null } | null;
}

export interface ReadinessRequirement {
    ref: string;
    source: string;
    title: string | null;
    url: string | null;
    status: 'passing' | 'failing' | 'partial' | 'untested';
    cases: { case_id: number; name: string; status: CaseOutcome }[];
}

export interface ReleaseReadiness {
    project_id: number;
    verdict: ReadinessVerdict;
    reasons: string[];
    scope: 'commit' | 'branch' | 'latest_run' | 'none';
    git_commit: string | null;
    git_branch: string | null;
    gate: QualityGateResult;
    runs: ReadinessRun[];
    critical_cases: ReadinessCriticalCase[];
    quarantines: ReadinessQuarantine[];
    requirements: ReadinessRequirement[];
    recent_commits: { git_commit: string; git_branch: string | null; last_run_at: string; runs: number }[];
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
    readiness: async (projectId: number, gitCommit?: string, gitBranch?: string): Promise<ReleaseReadiness> => {
        const params = new URLSearchParams();
        if (gitCommit) params.set('git_commit', gitCommit);
        if (gitBranch) params.set('git_branch', gitBranch);
        const qs = params.toString();
        const r = await api.get(`/projects/${projectId}/release-readiness${qs ? `?${qs}` : ''}`);
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

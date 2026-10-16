import { api } from '@/lib/api';

// ---------------------------------------------------------------------------
// Types — mirror backend/app/api/dora.py, backend/app/api/reliability_reports.py
// and the services they call (dora.py, slo.py, flake_patterns.py, vendor_health.py)
// ---------------------------------------------------------------------------

export type Band = 'elite' | 'high' | 'medium' | 'low';

export interface DoraDeployment {
    id: number;
    deployed_at: string;
    environment_id: number | null;
    environment: string | null;
    git_commit: string | null;
    version: string | null;
    status: 'success' | 'failed';
    failure_causes: ('deployment_failed' | 'incident' | 'failed_run')[];
    lead_time_hours: number | null;
    lead_time_source: 'commit_at' | 'first_run' | null;
}

export interface DoraReport {
    project_id: number;
    days: number;
    window_start: string;
    window_end: string;
    environment_id: number | null;
    environment: string | null;
    inputs: { deployments: number; incidents: number; failed_runs: number; commits_with_test_runs: number; notes: string[] };
    deployment_frequency: { deployments: number; per_day: number; per_week: number; band: Band | null; missing: string | null };
    lead_time: { median_hours: number | null; samples: number; sources: { commit_at: number; first_run: number }; band: Band | null; missing: string | null };
    change_failure_rate: {
        percent: number | null; failed_deployments: number; deployments: number;
        causes: { deployment_failed: number; incident: number; failed_run: number };
        linked_incidents: number; linked_failed_runs: number; band: Band | null; missing: string | null;
    };
    time_to_restore: { median_hours: number | null; resolved_incidents: number; unresolved_incidents: number; band: Band | null; missing: string | null };
    deployments: DoraDeployment[];
    thresholds: Record<string, Record<string, string>>;
}

export interface Deployment {
    id: number;
    project_id: number;
    environment_id: number | null;
    environment: string | null;
    git_commit: string | null;
    git_branch: string | null;
    version: string | null;
    status: string;
    deployed_at: string;
    source: string;
    external_id: string | null;
    url: string | null;
}

export interface ProjectEnvironmentLite {
    id: number;
    name: string;
}

export type SloStatus = 'meeting' | 'at_risk' | 'breached' | 'no_data';

export interface SloSummary {
    checks: number;
    good: number;
    bad: number;
    compliance_pct: number | null;
    error_budget: { allowed_failures: number; remaining_pct: number | null; burn_rate: number | null; spent_failures: number };
    burn_rate_24h: number | null;
    status: SloStatus;
    latency: { p95_ms: number | null; target_ms: number | null; checks_with_latency: number; met: boolean | null; missing: string | null };
}

export interface SloReport extends SloSummary {
    schedule_id: number;
    name: string;
    project_id: number;
    is_active: boolean;
    monitor_state: string | null;
    window_days: number;
    window_start: string;
    target_pct: number;
    target_is_default: boolean;
    missing: string | null;
    regions: (SloSummary & { region: string })[];
    daily: { date: string; checks: number; good: number }[];
    configured: { availability_target: number | null; latency_p95_ms: number | null; window_days: number | null };
}

export interface ProjectSloResponse {
    project_id: number;
    monitors: SloReport[];
    missing: string | null;
}

export interface SloTargetsBody {
    availability_target: number | null;
    latency_p95_ms: number | null;
    window_days: number | null;
}

export interface FlakeTrendResponse {
    project_id: number;
    days: number;
    daily: {
        date: string; flaky_cases: number; quarantined_cases: number; avg_flake_score: number | null;
        results_total: number; results_flaky: number; results_failed: number; flaky_result_pct: number | null;
    }[];
    cases: { test_case_id: number; name: string; flake_score: number; history: { date: string; flake_score: number; is_quarantined: boolean }[] }[];
    flaky_score_threshold: number;
    missing: string | null;
}

export interface RateBucket {
    key: string | number;
    label: string;
    total: number;
    flaky: number;
    failed: number;
    flaky_pct: number | null;
    failed_pct: number | null;
}

export interface FlakePatternsResponse {
    project_id: number;
    days: number;
    results: number;
    flaky_results: number;
    by_browser: RateBucket[];
    by_hour_utc: RateBucket[];
    by_environment: RateBucket[];
    by_failing_step_type: { step_type: string; flaky: number; failed: number; total: number }[];
    missing: string | null;
}

export interface VendorHealth {
    vendor: string;
    name: string;
    requests: number;
    errors: number;
    error_rate: number | null;
    latency_p50_ms: number | null;
    latency_p95_ms: number | null;
    hosts: string[];
    runs: number;
    runs_with_errors: number;
    daily: { date: string; requests: number; errors: number; error_rate: number | null; p95_ms: number | null }[];
    correlation: {
        failed_results: number;
        failed_with_vendor_errors_pct: number | null;
        passed_results: number;
        passed_with_vendor_errors_pct: number | null;
        lift: number | null;
    };
}

export interface DependencyHealthResponse {
    project_id: number;
    days: number;
    since: string;
    computed_at: string | null;
    vendors: VendorHealth[];
    latency_note: string;
    missing: string | null;
}

// ---------------------------------------------------------------------------
// Clients
// ---------------------------------------------------------------------------

export const reliabilityApi = {
    dora: async (projectId: number, days: number, environmentId?: number | null): Promise<DoraReport> => {
        const params: Record<string, number> = { days };
        if (environmentId != null) params.environment_id = environmentId;
        return (await api.get(`/projects/${projectId}/dora`, { params })).data;
    },
    deployments: async (projectId: number, limit = 50): Promise<Deployment[]> =>
        (await api.get(`/projects/${projectId}/deployments`, { params: { limit } })).data,
    deleteDeployment: async (id: number): Promise<void> => {
        await api.delete(`/deployments/${id}`);
    },
    environments: async (projectId: number): Promise<ProjectEnvironmentLite[]> =>
        (await api.get(`/projects/${projectId}/environments`)).data,
    projectSlo: async (projectId: number, windowDays?: number | null): Promise<ProjectSloResponse> =>
        (await api.get(`/projects/${projectId}/slo`, { params: windowDays ? { window_days: windowDays } : {} })).data,
    monitorSlo: async (scheduleId: number, windowDays?: number | null): Promise<SloReport> =>
        (await api.get(`/schedules/${scheduleId}/slo`, { params: windowDays ? { window_days: windowDays } : {} })).data,
    setMonitorSlo: async (scheduleId: number, body: SloTargetsBody): Promise<SloReport> =>
        (await api.put(`/schedules/${scheduleId}/slo`, body)).data,
    flakeTrend: async (projectId: number, days: number): Promise<FlakeTrendResponse> =>
        (await api.get(`/analytics/projects/${projectId}/flake-trend`, { params: { days } })).data,
    flakePatterns: async (projectId: number, days: number): Promise<FlakePatternsResponse> =>
        (await api.get(`/analytics/projects/${projectId}/flake-patterns`, { params: { days } })).data,
    dependencyHealth: async (projectId: number, days: number): Promise<DependencyHealthResponse> =>
        (await api.get(`/projects/${projectId}/dependency-health`, { params: { days } })).data,
};

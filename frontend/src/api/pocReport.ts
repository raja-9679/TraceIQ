import { api } from '@/lib/api';

// Mirrors backend/app/api/poc_reports.py and app/services/poc_report.py

export interface PocAssumptions {
    minutes_per_execution: number;
    hourly_rate: number | null;
    currency: string | null;
}

export interface PocEstimate {
    automated_executions: number;
    minutes_per_execution: number;
    hours_saved: number;
    label: string;
    basis: string;
    money: { amount: number; currency: string; hourly_rate: number; label: string } | null;
}

export interface PocInventory {
    projects: string[];
    suites: number;
    cases: number;
    cases_by_executor: Record<string, number>;
    environments: string[];
    monitors: string[];
    integrations: {
        issue_trackers: string[];
        outbound_webhooks: number;
        incident_webhooks: number;
        paging_channels: number;
        status_pages: number;
        api_keys: number;
    };
}

export interface PocActivity {
    runs: number;
    passed_runs: number;
    pass_rate: number | null;
    case_executions: number;
    manual_case_executions: number;
    browsers: string[];
    devices: string[];
}

export interface PocValue {
    failures_caught: number;
    failure_clusters: number;
    security_findings: Record<'critical' | 'high' | 'medium' | 'low' | 'info', number | null>;
    security_findings_total: number;
    flaky_tests: number;
    incidents: number;
    incidents_linked: number;
}

/** What the public share view carries (and the common part of the full report). */
export interface PublicPocReport {
    kind: 'poc_report';
    workspace_name: string;
    start: string;
    end: string;
    days: number;
    generated_at: string;
    inventory: PocInventory;
    activity: PocActivity;
    value: PocValue;
    estimate: PocEstimate;
}

export interface PocReport extends Omit<PublicPocReport, 'kind'> {
    workspace_id: number;
    project_ids: number[];
    assumptions: PocAssumptions;
    available_projects: { id: number; name: string }[];
}

export interface PocQuery {
    start: string;
    end: string;
    projectIds: number[];
    minutes: number;
    hourlyRate: number | null;
    currency: string;
}

export const pocApi = {
    get: async (workspaceId: number, q: PocQuery): Promise<PocReport> => {
        const params: Record<string, string | number> = {
            start: q.start, end: q.end, minutes_per_execution: q.minutes,
        };
        if (q.projectIds.length) params.project_ids = q.projectIds.join(',');
        if (q.hourlyRate != null) params.hourly_rate = q.hourlyRate;
        if (q.currency.trim()) params.currency = q.currency.trim();
        return (await api.get(`/workspaces/${workspaceId}/poc-report`, { params })).data;
    },
};

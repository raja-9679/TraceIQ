import { api } from '@/lib/api';
import type { Schema } from './schemaTypes';

// Mirrors backend/app/api/incidents.py

export interface Incident {
    id: number;
    project_id: number;
    source: 'manual' | 'generic' | 'pagerduty' | 'opsgenie';
    external_id: string | null;
    title: string;
    description: string | null;
    severity: 'critical' | 'high' | 'medium' | 'low' | 'unknown';
    status: 'open' | 'acknowledged' | 'resolved';
    service: string | null;
    url: string | null;
    affected_url: string | null;
    started_at: string;
    resolved_at: string | null;
    git_commit: string | null;
    git_branch: string | null;
    release: string | null;
    recurrence_count: number;
    previous_incident_id: number | null;
    matched_cluster_id: number | null;
    matched_cluster_title: string | null;
    matched_case_id: number | null;
    matched_case_name: string | null;
    match_reason: string | null;
    regression_proposal_id: number | null;
    regression_proposal_status: string | null;
    regression_status: string | null;
    regression_case_id: number | null;
    regression_case_name: string | null;
    escaped_defect: boolean;
    escaped_case_id: number | null;
    escaped_case_name: string | null;
    escaped_no_test: boolean;
    escaped_note: string | null;
    created_at: string;
}

/** Generated (IncidentCreate). Adds `started_at` / `resolved_at`, which the
 *  backend accepts for back-dating a manually recorded incident. */
export type IncidentCreate = Schema<'IncidentCreate'>;

export interface WebhookConfig {
    configured: boolean;
    enabled: boolean;
    default_suite_id: number | null;
    created_at: string | null;
    rotated_at: string | null;
    last_received_at: string | null;
    path: string;
    signature_headers: string[];
    secret?: string;
}

export interface EscapedReport {
    project_id: number;
    days: number;
    period: 'month' | 'week';
    periods: { period: string; escaped: number; missed_by_existing_test: number; no_test_existed: number; test_added_after: number; pct_test_added_after: number }[];
    totals: { escaped: number; missed_by_existing_test: number; no_test_existed: number; test_added_after: number; pct_test_added_after: number | null };
}

export const incidentsApi = {
    list: async (projectId: number, params: { status?: string; escaped?: boolean } = {}): Promise<Incident[]> =>
        (await api.get(`/projects/${projectId}/incidents`, { params })).data,
    create: async (projectId: number, body: IncidentCreate): Promise<Incident & { regression: { status: string; proposal_id?: number } | null }> =>
        (await api.post(`/projects/${projectId}/incidents`, body)).data,
    update: async (id: number, body: Partial<{ status: string; severity: string; title: string }>): Promise<Incident> =>
        (await api.patch(`/incidents/${id}`, body)).data,
    markEscaped: async (id: number, body: { escaped: boolean; case_id?: number; no_test_existed?: boolean; note?: string }): Promise<Incident> =>
        (await api.post(`/incidents/${id}/escaped`, body)).data,
    propose: async (id: number, testSuiteId?: number) =>
        (await api.post(`/incidents/${id}/propose-regression`, { test_suite_id: testSuiteId })).data,
    remove: async (id: number): Promise<void> => { await api.delete(`/incidents/${id}`); },
    report: async (projectId: number, days = 180, period: 'month' | 'week' = 'month'): Promise<EscapedReport> =>
        (await api.get(`/projects/${projectId}/incidents/escaped-report`, { params: { days, period } })).data,
    webhook: async (projectId: number): Promise<WebhookConfig> =>
        (await api.get(`/projects/${projectId}/incidents/webhook-config`)).data,
    updateWebhook: async (projectId: number, body: { default_suite_id?: number | null; enabled?: boolean }): Promise<WebhookConfig> =>
        (await api.put(`/projects/${projectId}/incidents/webhook-config`, body)).data,
    rotateSecret: async (projectId: number): Promise<WebhookConfig> =>
        (await api.post(`/projects/${projectId}/incidents/webhook-config/secret`)).data,
    deleteWebhook: async (projectId: number): Promise<void> => { await api.delete(`/projects/${projectId}/incidents/webhook-config`); },
};

export const SEVERITY_TONE: Record<string, string> = {
    critical: 'bg-rose-50 text-rose-700 border-rose-200',
    high: 'bg-orange-50 text-orange-700 border-orange-200',
    medium: 'bg-amber-50 text-amber-700 border-amber-200',
    low: 'bg-slate-50 text-slate-600 border-slate-200',
    unknown: 'bg-slate-50 text-slate-400 border-slate-200',
};

/** Full URL a paging tool should POST to. API_BASE_URL may be relative. */
export const webhookUrl = (path: string): string => {
    const origin = typeof window !== 'undefined' ? window.location.origin : '';
    return `${origin}${path}`;
};

/** Case picker for "which test should have caught this" (first 200 by id). */
export const listProjectCases = async (projectId: number): Promise<{ id: number; name: string }[]> =>
    (await api.get('/cases', { params: { project_id: projectId, limit: 200 } })).data.items;

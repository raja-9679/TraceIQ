import { api, API_BASE_URL } from '@/lib/api';

// Mirrors backend/app/api/reports.py and backend/app/api/share_links.py

export type Channel = 'email' | 'slack' | 'teams';
export type Verdict = 'ready' | 'not_ready' | 'insufficient_data';

export interface DigestResult {
    at?: string;
    channels_sent?: string[];
    emailed?: number;
    skipped?: Record<string, number>;
    errors?: string[];
}

export interface ReportSchedule {
    id: number;
    project_id: number;
    name: string;
    cron_expression: string;
    window_days: number;
    channels?: Channel[] | null;
    /** Extra plain addresses (lists, people outside TraceIQ). */
    recipients?: string[] | null;
    /** Picked project members; re-checked for access when the digest is sent. */
    recipient_user_ids?: number[] | null;
    is_active: boolean;
    next_run_at?: string | null;
    last_run_at?: string | null;
    last_result?: DigestResult | null;
}

export interface ReportScheduleInput {
    name: string;
    cron_expression: string;
    window_days: number;
    channels: Channel[];
    recipients: string[];
    recipient_user_ids: number[];
    is_active?: boolean;
}

export interface WindowStats {
    runs: number;
    passed: number;
    failed: number;
    pass_rate: number | null;
    failures: number;
}

export interface DailyPoint { date: string; runs: number; pass_rate: number | null }
export interface TopFailing { test_name: string; failures: number }

export interface ClusterRow {
    id: number;
    project_id: number;
    title: string;
    category: string;
    status: string;
    occurrences: number;
    last_seen_at: string | null;
}

export interface SummaryProjectRow {
    project_id?: number;
    project_name?: string;
    name?: string;
    pass_rate: number | null;
    runs: number;
    failures: number;
    trend_pass_rate: number | null;
    verdict: Verdict | null;
    flaky_count: number;
}

/** GET /projects/{id}/executive-summary and /workspaces/{id}/executive-summary. */
export interface ExecutiveSummary {
    scope: 'project' | 'workspace';
    project_id?: number;
    project_name?: string;
    workspace_id: number;
    workspace_name?: string;
    window_days: number;
    generated_at: string;
    headline: string;
    current: WindowStats;
    previous: WindowStats;
    trend: { pass_rate: number | null; runs: number; failures: number };
    daily: DailyPoint[];
    top_failing: TopFailing[];
    open_clusters: number;
    clusters?: ClusterRow[];
    flaky_count: number;
    readiness: { verdict: Verdict; reasons?: string[] } | null;
    verdicts?: Record<Verdict, number>;
    incidents: { escaped: number; open: number; open_critical: number };
    projects?: SummaryProjectRow[];
}

export interface PortfolioRow {
    project_id: number;
    project_name: string;
    pass_rate_7d: number | null;
    runs_7d: number;
    pass_rate_30d: number | null;
    runs_30d: number;
    failures_7d: number;
    last_run: { id: number; status: string; created_at: string | null } | null;
    verdict: Verdict | null;
    flaky_count: number;
    open_findings: number;
}

export interface Portfolio {
    workspace_id: number;
    workspace_name: string;
    generated_at: string;
    projects: PortfolioRow[];
}

export type ShareKind = 'run_report' | 'project_summary' | 'workspace_summary';

export interface ShareLink {
    id: number;
    kind: ShareKind;
    project_id: number | null;
    workspace_id: number;
    target_id: number | null;
    window_days: number;
    label: string | null;
    created_at: string;
    expires_at: string;
    revoked_at: string | null;
    last_viewed_at: string | null;
    view_count: number;
    state: 'active' | 'revoked' | 'expired';
    created_by: { id: number; full_name: string | null } | null;
}

/** Only the create response carries the token, once. */
export interface CreatedShareLink extends ShareLink {
    token: string;
    path: string;
}

export interface ShareLinkInput {
    kind: ShareKind;
    run_id?: number;
    expires_in_days: number;
    window_days?: number;
    label?: string;
}

/** GET /api/share/{token}: the reduced public view. */
export interface PublicRunReport {
    kind: 'run_report';
    project_name: string | null;
    run: {
        id: number; suite_name: string | null; status: string; browser: string | null;
        total_tests: number; passed_tests: number; failed_tests: number; pass_rate: number | null;
        duration_ms: number | null; created_at: string | null; finalized_at: string | null;
    };
    results: { test_name: string; status: string; duration_ms: number | null }[];
    expires_at: string;
    label: string | null;
}

/** The public summary: the same shape as ExecutiveSummary minus everything private. */
export interface PublicSummary extends Omit<ExecutiveSummary, 'scope' | 'workspace_id' | 'clusters'> {
    kind: 'project_summary' | 'workspace_summary';
    name: string | null;
    expires_at: string;
    label: string | null;
}

export type PublicShare = PublicRunReport | PublicSummary;

export const reportsApi = {
    list: async (projectId: number): Promise<ReportSchedule[]> => {
        const r = await api.get(`/projects/${projectId}/report-schedules`);
        return r.data;
    },
    create: async (projectId: number, body: ReportScheduleInput): Promise<ReportSchedule> => {
        const r = await api.post(`/projects/${projectId}/report-schedules`, body);
        return r.data;
    },
    update: async (id: number, body: Partial<ReportScheduleInput>): Promise<ReportSchedule> => {
        const r = await api.patch(`/report-schedules/${id}`, body);
        return r.data;
    },
    remove: async (id: number): Promise<void> => {
        await api.delete(`/report-schedules/${id}`);
    },
    sendNow: async (id: number): Promise<void> => {
        await api.post(`/report-schedules/${id}/send-now`);
    },
    projectSummary: async (projectId: number, days: number): Promise<ExecutiveSummary> => {
        const r = await api.get(`/projects/${projectId}/executive-summary`, { params: { days } });
        return r.data;
    },
    workspaceSummary: async (workspaceId: number, days: number): Promise<ExecutiveSummary> => {
        const r = await api.get(`/workspaces/${workspaceId}/executive-summary`, { params: { days } });
        return r.data;
    },
    portfolio: async (workspaceId: number): Promise<Portfolio> => {
        const r = await api.get(`/workspaces/${workspaceId}/portfolio`);
        return r.data;
    },
};

export const shareLinksApi = {
    listProject: async (projectId: number): Promise<ShareLink[]> =>
        (await api.get(`/projects/${projectId}/share-links`)).data,
    listWorkspace: async (workspaceId: number): Promise<ShareLink[]> =>
        (await api.get(`/workspaces/${workspaceId}/share-links`)).data,
    createProject: async (projectId: number, body: ShareLinkInput): Promise<CreatedShareLink> =>
        (await api.post(`/projects/${projectId}/share-links`, body)).data,
    createWorkspace: async (workspaceId: number, body: ShareLinkInput): Promise<CreatedShareLink> =>
        (await api.post(`/workspaces/${workspaceId}/share-links`, body)).data,
    revoke: async (id: number): Promise<ShareLink> => (await api.delete(`/share-links/${id}`)).data,
};

/** Public fetch: plain fetch with no auth header, so a signed-in viewer's
 *  token never rides along to a page anyone can open. */
export async function fetchPublicShare(token: string): Promise<PublicShare> {
    const r = await fetch(`${API_BASE_URL}/share/${encodeURIComponent(token)}`);
    if (!r.ok) throw new Error(String(r.status));
    return r.json();
}

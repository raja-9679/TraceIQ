import { api } from '@/lib/api';

// Mirrors backend/app/api/estates.py (WP9 — passive estate checks + script inventory).

export interface EstateInput {
    name: string;
    dkim_selectors?: string[] | null;
    discover_subdomains: boolean;
    cron_expression: string;
    schedule_enabled: boolean;
    cert_alert_days?: number[] | null;
    alert_emails?: string[] | null;
}

export interface EstateScan {
    id: number;
    estate_id: number;
    status: 'pending' | 'running' | 'completed' | 'error';
    trigger: string;
    summary?: {
        domains_scanned?: number;
        average_score?: number | null;
        skipped_not_allowlisted?: string[];
        discovered?: string[];
        notes?: string[];
        findings?: Record<string, number>;
    } | null;
    error?: string | null;
    created_at: string;
    finished_at?: string | null;
}

export interface EstateDomain {
    id: number;
    estate_id: number;
    hostname: string;
    source: 'user' | 'ct';
    is_apex: boolean | null;
    score: number | null;
    cert_expires_at: string | null;
    last_scanned_at: string | null;
    trend: number[];
    allowlisted?: boolean;
}

export interface Estate extends EstateInput {
    id: number;
    project_id: number;
    next_scan_at: string | null;
    last_scan_at: string | null;
    domain_count?: number;
    average_score?: number | null;
    worst_score?: number | null;
    next_cert_expiry?: string | null;
    domains?: EstateDomain[];
    latest_scan?: EstateScan | null;
}

export interface CheckResult {
    id: string;
    family: string;
    title: string;
    status: 'pass' | 'warn' | 'fail' | 'info' | 'skipped' | 'error';
    summary: string;
    explanation: string;
    evidence: string | null;
    rubric: string | null;
    points: number;
}

export interface Deduction {
    check: string;
    points: number;
    reason: string;
    detail: string;
}

export interface DomainDetail {
    domain: EstateDomain;
    estate: { id: number; name: string };
    latest: null | {
        scan_id: number;
        score: number;
        checks: CheckResult[];
        deductions: Deduction[];
        created_at: string;
        arithmetic: { start: number; deducted: number; score: number };
    };
    history: { scan_id: number; score: number; created_at: string }[];
}

export interface CertEntry {
    domain_id: number;
    hostname: string;
    cert_expires_at: string;
    days_left: number;
    estate_id: number;
    estate_name: string;
    project_id: number;
    project_name: string;
}

export interface ElementCheck {
    selector: string;
    must_contain?: string | null;
    must_not_contain?: string[];
}

export interface MonitoredPageInput {
    name: string;
    url: string;
    track_scripts: boolean;
    track_content: boolean;
    cron_expression: string;
    schedule_enabled: boolean;
    similarity_threshold: number;
    forbidden_keywords?: string[] | null;
    element_checks?: ElementCheck[] | null;
    alert_emails?: string[] | null;
}

export interface MonitoredPage extends MonitoredPageInput {
    id: number;
    project_id: number;
    next_check_at: string | null;
    last_checked_at: string | null;
    last_status: 'ok' | 'changed' | 'alert' | 'error' | null;
    last_error: string | null;
    headers_baseline: Record<string, string>;
    has_content_baseline: boolean;
    content_baseline_at: string | null;
    last_similarity: number | null;
    script_count?: number;
    unapproved_count?: number;
}

export interface PageScript {
    id: number;
    page_id: number;
    script_key: string;
    kind: 'external' | 'inline';
    src: string | null;
    sha384: string | null;
    integrity: string | null;
    third_party: boolean;
    present: boolean;
    first_seen_at: string;
    last_seen_at: string;
    removed_at: string | null;
    authorized: boolean;
    justification: string | null;
    authorized_by_id: number | null;
    authorized_at: string | null;
}

export interface PageChange {
    id: number;
    kind: string;
    summary: string;
    detail: Record<string, unknown> | null;
    created_at: string;
}

export const estatesApi = {
    list: async (projectId: number): Promise<Estate[]> =>
        (await api.get(`/projects/${projectId}/estates`)).data,
    create: async (projectId: number, body: EstateInput): Promise<Estate> =>
        (await api.post(`/projects/${projectId}/estates`, body)).data,
    get: async (id: number): Promise<Estate> => (await api.get(`/estates/${id}`)).data,
    update: async (id: number, body: EstateInput): Promise<Estate> =>
        (await api.put(`/estates/${id}`, body)).data,
    remove: async (id: number): Promise<void> => { await api.delete(`/estates/${id}`); },
    importDomains: async (id: number, text: string): Promise<{ added: string[]; rejected: { value: string; reason: string }[] }> =>
        (await api.post(`/estates/${id}/domains`, { text })).data,
    removeDomain: async (id: number, domainId: number): Promise<void> => {
        await api.delete(`/estates/${id}/domains/${domainId}`);
    },
    scan: async (id: number): Promise<EstateScan> => (await api.post(`/estates/${id}/scan`)).data,
    scans: async (id: number): Promise<EstateScan[]> => (await api.get(`/estates/${id}/scans`)).data,
    domain: async (domainId: number): Promise<DomainDetail> =>
        (await api.get(`/estate-domains/${domainId}`)).data,
    certificates: async (workspaceId: number): Promise<CertEntry[]> =>
        (await api.get(`/workspaces/${workspaceId}/estate-certificates`)).data,
};

export const pagesApi = {
    list: async (projectId: number): Promise<MonitoredPage[]> =>
        (await api.get(`/projects/${projectId}/monitored-pages`)).data,
    create: async (projectId: number, body: MonitoredPageInput): Promise<MonitoredPage> =>
        (await api.post(`/projects/${projectId}/monitored-pages`, body)).data,
    update: async (id: number, body: MonitoredPageInput): Promise<MonitoredPage> =>
        (await api.put(`/monitored-pages/${id}`, body)).data,
    remove: async (id: number): Promise<void> => { await api.delete(`/monitored-pages/${id}`); },
    check: async (id: number): Promise<void> => { await api.post(`/monitored-pages/${id}/check`); },
    resetBaseline: async (id: number): Promise<MonitoredPage> =>
        (await api.post(`/monitored-pages/${id}/baseline/reset`)).data,
    scripts: async (id: number, includeRemoved = false): Promise<PageScript[]> =>
        (await api.get(`/monitored-pages/${id}/scripts`, { params: { include_removed: includeRemoved } })).data,
    approve: async (id: number, scriptId: number, justification: string): Promise<PageScript> =>
        (await api.post(`/monitored-pages/${id}/scripts/${scriptId}/approve`, { justification })).data,
    revoke: async (id: number, scriptId: number): Promise<PageScript> =>
        (await api.post(`/monitored-pages/${id}/scripts/${scriptId}/revoke`)).data,
    changes: async (id: number): Promise<PageChange[]> =>
        (await api.get(`/monitored-pages/${id}/changes`)).data,
};

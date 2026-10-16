import { api, API_BASE_URL } from '@/lib/api';
import type { CreatedShareLink, ShareLink } from '@/api/reports';

// Mirrors backend/app/api/trust_center.py

export type IncidentStatus = 'investigating' | 'identified' | 'monitoring' | 'resolved';
export type PostureState = 'good' | 'attention' | 'info';

export interface PosturePreview {
    key: string;
    label: string;
    hint: string;
    published: boolean;
    available: boolean;
    value: string | null;
    state: PostureState | null;
}

export interface TrustCenterConfig {
    configured: boolean;
    workspace_id: number;
    enabled: boolean;
    slug: string | null;
    path: string | null;
    title: string;
    intro: string;
    contact_email: string | null;
    status_page_ids: number[];
    posture_items: string[];
    allow_access_requests: boolean;
    audit_verified_at: string | null;
    audit_verified_ok: boolean | null;
    view_count: number;
    last_viewed_at: string | null;
    available_status_pages: { id: number; title: string; project_name: string; enabled: boolean }[];
    posture_preview: PosturePreview[];
}

export interface TrustCenterInput {
    enabled?: boolean;
    title?: string;
    intro?: string;
    contact_email?: string;
    status_page_ids?: number[];
    posture_items?: string[];
    allow_access_requests?: boolean;
    regenerate_slug?: boolean;
}

export interface IncidentUpdate { at: string; status: IncidentStatus; body: string }

export interface IncidentPost {
    id: number;
    workspace_id: number;
    title: string;
    status: IncidentStatus;
    components: string[];
    updates: IncidentUpdate[];
    published: boolean;
    published_at: string | null;
    resolved_at: string | null;
    created_at: string;
    updated_at: string;
}

export interface TrustDocument {
    id: number;
    workspace_id: number;
    title: string;
    description: string | null;
    filename: string;
    content_type: string;
    size_bytes: number;
    sha256: string;
    listed: boolean;
    created_at: string;
}

/** GET /api/trust/{slug}: the reduced public page. */
export interface PublicTrustPage {
    title: string;
    intro: string;
    contact_email: string | null;
    status: { overall: 'operational' | 'degraded' | 'down' | 'unknown'; monitors: {
        name: string; state: 'up' | 'down' | 'unknown'; uptime_24h: number | null; uptime_7d: number | null;
    }[] } | null;
    incidents: {
        id: number; title: string; status: IncidentStatus; components: string[];
        started_at: string | null; resolved_at: string | null;
        /** Rendered server-side, escape-first: only p/br/ul/li/strong/em/code. */
        updates: { at: string; status: IncidentStatus; body_html: string }[];
    }[];
    posture: { key: string; label: string; value: string; state: PostureState }[];
    documents: { id: number; title: string; description: string | null }[];
    access_requests: boolean;
    generated_at: string;
}

export interface DocumentDownload {
    title: string;
    filename: string;
    content_type: string;
    size_bytes: number;
    sha256: string;
    url: string;
    url_expires_in: number;
    link_expires_at: string;
}

export const trustApi = {
    get: async (ws: number): Promise<TrustCenterConfig> => (await api.get(`/workspaces/${ws}/trust-center`)).data,
    save: async (ws: number, body: TrustCenterInput): Promise<TrustCenterConfig> =>
        (await api.put(`/workspaces/${ws}/trust-center`, body)).data,
    verifyAudit: async (ws: number): Promise<{ verified: boolean; rows_checked: number }> =>
        (await api.post(`/workspaces/${ws}/trust-center/verify-audit`)).data,
    incidents: async (ws: number): Promise<IncidentPost[]> => (await api.get(`/workspaces/${ws}/trust-center/incidents`)).data,
    createIncident: async (ws: number, body: { title: string; status: IncidentStatus; components: string[]; body?: string; published: boolean }): Promise<IncidentPost> =>
        (await api.post(`/workspaces/${ws}/trust-center/incidents`, body)).data,
    updateIncident: async (id: number, body: { title?: string; components?: string[]; published?: boolean }): Promise<IncidentPost> =>
        (await api.patch(`/trust-center/incidents/${id}`, body)).data,
    addUpdate: async (id: number, body: { status: IncidentStatus; body: string }): Promise<IncidentPost> =>
        (await api.post(`/trust-center/incidents/${id}/updates`, body)).data,
    deleteIncident: async (id: number): Promise<void> => { await api.delete(`/trust-center/incidents/${id}`); },
    documents: async (ws: number): Promise<TrustDocument[]> => (await api.get(`/workspaces/${ws}/trust-center/documents`)).data,
    upload: async (ws: number, file: File, title: string, description: string, listed: boolean): Promise<TrustDocument> => {
        const fd = new FormData();
        fd.append('file', file);
        fd.append('title', title);
        if (description) fd.append('description', description);
        fd.append('listed', String(listed));
        return (await api.post(`/workspaces/${ws}/trust-center/documents`, fd)).data;
    },
    deleteDocument: async (id: number): Promise<void> => { await api.delete(`/trust-center/documents/${id}`); },
    links: async (docId: number): Promise<ShareLink[]> => (await api.get(`/trust-center/documents/${docId}/links`)).data,
    createLink: async (docId: number, recipient: string, expires_in_days: number): Promise<CreatedShareLink> =>
        (await api.post(`/trust-center/documents/${docId}/links`, { recipient, expires_in_days })).data,
};

// Public calls: plain fetch with no auth header, so a signed-in viewer's
// token never rides along to a page anyone can open.

async function publicJson<T>(path: string, init?: RequestInit): Promise<T> {
    const r = await fetch(`${API_BASE_URL}${path}`, init);
    if (!r.ok) throw new Error(String(r.status));
    return r.json();
}

export const fetchPublicTrust = (slug: string) =>
    publicJson<PublicTrustPage>(`/trust/${encodeURIComponent(slug)}`);

/** The token travels in the body, never a URL (URLs land in access logs). */
export const fetchTrustDocument = (slug: string, token: string) =>
    publicJson<DocumentDownload>(`/trust/${encodeURIComponent(slug)}/document`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token }),
    });

export const requestTrustAccess = (slug: string, body: { name: string; email: string; company?: string; message?: string; document_ids?: number[] }) =>
    publicJson<{ received: boolean }>(`/trust/${encodeURIComponent(slug)}/request-access`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });

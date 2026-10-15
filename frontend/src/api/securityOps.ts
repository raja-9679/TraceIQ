import { api } from '@/lib/api';

// Mirrors backend/app/api/access_reviews.py and app/api/siem.py (WP7).

export interface ProjectGrant { project_id: number; project: string; role: string; via: string }
export interface WorkspaceAccess { workspace_id: number; workspace: string; role: string | null; teams: string[]; projects: ProjectGrant[] }

export interface UserAccessSnapshot {
    user_id: number;
    email: string;
    full_name: string;
    active: boolean;
    instance_admin: boolean;
    mfa_enabled: boolean;
    scim_managed: boolean;
    last_login_at: string | null;
    auth_method: string;
    workspaces: WorkspaceAccess[];
}

export interface ApiKeySnapshot {
    api_key_id: number;
    name: string;
    prefix: string;
    workspace: string | null;
    scope: string;
    role: string | null;
    created_by: string | null;
    created_at: string;
    last_used_at: string | null;
    expires_at: string | null;
    expired: boolean;
}

export interface ReviewItem {
    id: number;
    subject_type: 'user' | 'api_key';
    subject_id: number;
    label: string;
    snapshot: UserAccessSnapshot | ApiKeySnapshot;
    decision: 'keep' | 'revoke' | null;
    note: string | null;
    decided_by: string | null;
    decided_at: string | null;
    applied_at: string | null;
    apply_result: string | null;
}

export interface ReviewCampaign {
    id: number;
    workspace_id: number | null;
    scope: 'workspace' | 'instance';
    name: string;
    status: 'open' | 'signed_off';
    created_at: string;
    created_by: string | null;
    signed_off_at: string | null;
    signed_off_by: string | null;
    sign_off_note: string | null;
    revocations_applied_at: string | null;
    revocations_applied_by: string | null;
    counts: { total: number; keep: number; revoke: number; undecided: number; applied: number };
    items?: ReviewItem[];
}

export interface RevocationPlan {
    confirmed: boolean;
    would_revoke?: { item_id: number; label: string; subject_type: string; note: string | null }[];
    results?: { item_id: number; label: string; result: string }[];
}

// workspaceId null = the instance-wide scope (instance admins only).
const base = (workspaceId: number | null) =>
    workspaceId === null ? '/admin/access-reviews' : `/workspaces/${workspaceId}/access-reviews`;

export const accessReviewApi = {
    reportPath: (workspaceId: number | null, format: 'csv' | 'html') =>
        workspaceId === null
            ? `/admin/access-review/report?format=${format}`
            : `/workspaces/${workspaceId}/access-review/report?format=${format}`,
    list: async (workspaceId: number | null): Promise<ReviewCampaign[]> => (await api.get(base(workspaceId))).data,
    start: async (workspaceId: number | null, name?: string): Promise<ReviewCampaign> =>
        (await api.post(base(workspaceId), { name: name || null })).data,
    get: async (id: number): Promise<ReviewCampaign> => (await api.get(`/access-reviews/${id}`)).data,
    decide: async (id: number, itemId: number, decision: 'keep' | 'revoke', note?: string): Promise<ReviewItem> =>
        (await api.patch(`/access-reviews/${id}/items/${itemId}`, { decision, note: note || null })).data,
    signOff: async (id: number, note?: string): Promise<ReviewCampaign> =>
        (await api.post(`/access-reviews/${id}/sign-off`, { note: note || null })).data,
    applyRevocations: async (id: number, confirm: boolean): Promise<RevocationPlan> =>
        (await api.post(`/access-reviews/${id}/apply-revocations`, { confirm })).data,
    exportPath: (id: number, format: 'csv' | 'html') => `/access-reviews/${id}/export?format=${format}`,
};

export interface SiemStatus {
    enabled: boolean;
    sink_type: string | null;
    config_error: string | null;
    pending: number;
    oldest_pending_at: string | null;
    last_error: string | null;
    retrying: boolean;
}

export const siemApi = {
    status: async (): Promise<SiemStatus> => (await api.get('/admin/siem/status')).data,
    test: async (): Promise<{ ok: boolean; sink_type: string }> => (await api.post('/admin/siem/test')).data,
};

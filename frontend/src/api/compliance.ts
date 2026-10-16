import { api } from '@/lib/api';

// Mirrors backend/app/api/compliance.py and app/api/audit.py (WP13a).

export type ControlStatus = 'passing' | 'failing' | 'stale' | 'excepted' | 'not_assessed';
export type SourceStatus = 'pass' | 'fail' | 'unknown';

export interface EvidenceSourceDef {
    type: string;
    label: string;
    description: string;
    defaults: Record<string, unknown>;
}

export interface EvidenceSource { type: string; params: Record<string, unknown> }

export interface SourceResult {
    type: string;
    label: string;
    status: SourceStatus;
    reason: string;
    observed_at: string | null;
}

export interface ControlException {
    id: number;
    control_id: number;
    reason: string;
    compensating_control: string | null;
    status: 'pending' | 'approved' | 'rejected' | 'revoked' | 'expired';
    requested_by: string | null;
    requested_by_id: number;
    requested_at: string;
    approved_by: string | null;
    approved_at: string | null;
    decided_note: string | null;
    expires_at: string;
}

export interface ManualEvidence {
    id: number;
    control_id: number;
    outcome: 'pass' | 'fail';
    note: string | null;
    file_name: string | null;
    content_type: string | null;
    size_bytes: number | null;
    sha256: string | null;
    has_file: boolean;
    created_by: string | null;
    created_at: string;
}

export interface ControlHistoryEntry {
    id: number;
    at: string;
    action: string;
    by: string | null;
    changes: Record<string, unknown> | null;
}

export interface Control {
    id: number;
    workspace_id: number;
    framework: string;
    reference: string;
    title: string;
    description: string | null;
    owner_id: number | null;
    owner: string | null;
    evidence_sources: EvidenceSource[];
    freshness_days: number;
    seeded: boolean;
    created_at: string;
    updated_at: string;
    status: ControlStatus;
    status_reason: string;
    newest_pass_at: string | null;
    results: SourceResult[];
    manual_evidence_count: number;
    active_exception: ControlException | null;
    pending_exceptions: number;
}

export interface ControlDetail extends Control {
    evidence: ManualEvidence[];
    exceptions: ControlException[];
    history: ControlHistoryEntry[];
    can_edit: boolean;
    can_contribute: boolean;
    me: number;
}

export interface ControlList {
    workspace_id: number;
    can_edit: boolean;
    frameworks: string[];
    counts: Record<ControlStatus, number>;
    controls: Control[];
}

export interface ControlInput {
    framework: string;
    reference: string;
    title: string;
    description?: string | null;
    owner_id?: number | null;
    evidence_sources: EvidenceSource[];
    freshness_days: number;
}

export interface Member { id: number; email: string; full_name: string }

export interface PackFile { path: string; sha256: string; size: number }

export interface EvidencePack {
    id: number;
    workspace_id: number;
    status: 'queued' | 'building' | 'ready' | 'failed';
    period_start: string;
    period_end: string;
    requested_by: string | null;
    created_at: string;
    completed_at: string | null;
    size_bytes: number | null;
    sha256: string | null;
    file_count: number | null;
    error: string | null;
    manifest_signature: string | null;
    files: PackFile[];
}

export interface VerifyReport {
    verified: boolean;
    signature_valid: boolean;
    key?: 'current' | 'previous' | null;
    signed_key_id?: string | null;
    files_checked?: number;
    mismatched?: string[];
    missing?: string[];
    unexpected?: string[];
    errors: string[];
    pack_id?: number;
    workspace_id?: number;
    workspace_name?: string;
    period?: { start: string; end: string };
    generated_at?: string;
    generated_by?: { user_id: number; email: string | null };
}

export interface DrillSummary {
    id: number;
    ran_at: string;
    status: 'pass' | 'fail';
    duration_seconds: number | null;
    checks: { check: string; status: 'PASS' | 'WARN' | 'FAIL' }[];
}

export interface AuditRow {
    id: number;
    entity_type: string;
    entity_id: number;
    action: string;
    user_id: number | null;
    timestamp: string;
    changes: Record<string, unknown> | null;
    actor_type: string | null;
    actor_label: string | null;
    ip_address: string | null;
}

export interface AuditFilters {
    entity_type?: string;
    action?: string;
    user_id?: number;
    since?: string;
    until?: string;
}

export interface ChainVerdict {
    verified: boolean;
    rows_checked: number;
    detail: string;
    first_bad_row_id?: number;
    first_bad_row_timestamp?: string;
    in_this_workspace?: boolean;
}

/** Drop empty filters so the query string only carries what was chosen. */
export function auditParams(f: object): Record<string, string | number> {
    const out: Record<string, string | number> = {};
    for (const [k, v] of Object.entries(f)) {
        if (v !== undefined && v !== null && v !== '') out[k] = v as string | number;
    }
    return out;
}

export function auditQuery(f: AuditFilters): string {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(auditParams(f))) p.set(k, String(v));
    const s = p.toString();
    return s ? `?${s}` : '';
}

export const complianceApi = {
    sources: () => api.get<EvidenceSourceDef[]>('/compliance/evidence-sources').then((r) => r.data),
    list: (ws: number, filters: { framework?: string; status?: string; owner_id?: number } = {}) =>
        api.get<ControlList>(`/workspaces/${ws}/compliance/controls`, { params: auditParams(filters) }).then((r) => r.data),
    members: (ws: number) => api.get<Member[]>(`/workspaces/${ws}/compliance/members`).then((r) => r.data),
    create: (ws: number, body: ControlInput) =>
        api.post<ControlDetail>(`/workspaces/${ws}/compliance/controls`, body).then((r) => r.data),
    loadSoc2: (ws: number) =>
        api.post<{ created: number; already_present: number; total: number }>(`/workspaces/${ws}/compliance/controls/load-soc2`).then((r) => r.data),
    get: (id: number) => api.get<ControlDetail>(`/compliance/controls/${id}`).then((r) => r.data),
    update: (id: number, body: Partial<ControlInput>) =>
        api.patch<ControlDetail>(`/compliance/controls/${id}`, body).then((r) => r.data),
    remove: (id: number) => api.delete(`/compliance/controls/${id}`).then((r) => r.data),
    addEvidence: (id: number, input: { note?: string; outcome: 'pass' | 'fail'; file?: File | null }) => {
        const form = new FormData();
        if (input.note) form.append('note', input.note);
        form.append('outcome', input.outcome);
        if (input.file) form.append('file', input.file);
        return api.post<ManualEvidence>(`/compliance/controls/${id}/evidence`, form).then((r) => r.data);
    },
    evidenceFilePath: (evidenceId: number) => `/compliance/evidence/${evidenceId}/file`,
    removeEvidence: (evidenceId: number) => api.delete(`/compliance/evidence/${evidenceId}`).then((r) => r.data),
    requestException: (id: number, body: { reason: string; compensating_control?: string; expires_at: string }) =>
        api.post<ControlException>(`/compliance/controls/${id}/exceptions`, body).then((r) => r.data),
    decideException: (exceptionId: number, action: 'approve' | 'reject' | 'revoke', note?: string) =>
        api.post<ControlException>(`/compliance/exceptions/${exceptionId}/${action}`, note ? { note } : undefined).then((r) => r.data),
    drills: (ws: number) => api.get<DrillSummary[]>(`/workspaces/${ws}/restore-drills`).then((r) => r.data),
};

export const evidencePackApi = {
    list: (ws: number) => api.get<EvidencePack[]>(`/workspaces/${ws}/evidence-packs`).then((r) => r.data),
    create: (ws: number, period_start: string, period_end: string) =>
        api.post<EvidencePack>(`/workspaces/${ws}/evidence-packs`, { period_start, period_end }).then((r) => r.data),
    download: (id: number) =>
        api.post<{ url: string; expires_in: number; filename: string }>(`/evidence-packs/${id}/download`).then((r) => r.data),
    verify: (zip: File, manifest?: File | null) => {
        const form = new FormData();
        form.append('file', zip);
        if (manifest) form.append('manifest', manifest);
        // Large packs take a while to upload and hash.
        return api.post<VerifyReport>('/evidence-packs/verify', form, { timeout: 300_000 }).then((r) => r.data);
    },
};

export const auditApi = {
    list: (ws: number, filters: AuditFilters, limit: number, offset: number) =>
        api.get<AuditRow[]>(`/workspaces/${ws}/audit`, { params: { ...auditParams(filters), limit, offset } }).then((r) => r.data),
    exportPath: (ws: number, filters: AuditFilters) => `/workspaces/${ws}/audit/export${auditQuery(filters)}`,
    verify: (ws: number) => api.get<ChainVerdict>(`/workspaces/${ws}/audit/verify`).then((r) => r.data),
};

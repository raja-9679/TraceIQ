import { api } from '@/lib/api';
import type { Out, Schema } from './schemaTypes';

// Mirrors backend/app/api/security.py + the passive endpoints on test_runs.py.
// Shapes with a response_model come from the generated OpenAPI types.

/** SecurityFindingRead plus what the untyped register/PATCH endpoints add
 *  (`_finding_out` → overdue; PATCH → retest). */
export type SecurityFinding = Omit<Out<'SecurityFindingRead'>, 'severity' | 'status' | 'location'> & {
    severity: 'high' | 'medium' | 'low' | 'info';
    status: FindingStatus;
    location: { run_id?: number; result_id?: number; case_id?: number; url?: string; field?: string } | null;
    overdue?: boolean;
    retest?: { queued: boolean; scan_id?: number; reused?: boolean; reason?: string };
};

export type FindingStatus =
    | 'open' | 'in_progress' | 'fixed_pending_verification' | 'verified_fixed'
    | 'accepted_risk' | 'false_positive';

export type FindingUpdate = Omit<Schema<'FindingUpdate'>, 'status'> & { status?: FindingStatus };

export interface FindingsRegister {
    project_id: number;
    total: number;
    by_status: Record<string, number>;
    actionable: number;
    overdue: number;
    findings: SecurityFinding[];
}

export interface FindingSla {
    workspace_id: number;
    sla_days: Record<string, number>;
    default_sla_days: Record<string, number>;
    can_edit: boolean;
}

export interface OwnerCandidate { id: number; email: string; full_name: string }

export interface ScanDiff {
    scan_id: number;
    previous_scan_id: number | null;
    baseline_available: boolean;
    new: { id: number; severity: string; title: string; target_url: string | null; status: string }[];
    fixed: { id: number; severity: string; title: string; target_url: string | null; status: string }[];
    persisting_count: number;
}

export type SecurityScanResult = Omit<Out<'SecurityScanResult'>, 'findings'> & { findings: SecurityFinding[] };

export type SecurityScan = Omit<Out<'SecurityScanRead'>, 'status' | 'findings'> & {
    status: 'pending' | 'running' | 'completed' | 'error';
    findings: SecurityFinding[];   // auth_header_name only — the secret value is never returned
};

export type SecuritySettings = Out<'SecuritySettings'>;

export const securityApi = {
    // Passive (per-run) findings — item 2.
    runFindings: async (runId: number): Promise<SecurityScanResult> => {
        const r = await api.get(`/runs/${runId}/security-findings`);
        return r.data;
    },
    scanRun: async (runId: number): Promise<SecurityScanResult> => {
        const r = await api.post(`/runs/${runId}/security-scan`);
        return r.data;
    },
    // Active/authenticated scans — item 6.
    getSettings: async (projectId: number): Promise<SecuritySettings> => {
        const r = await api.get(`/projects/${projectId}/security-settings`);
        return r.data;
    },
    setSettings: async (projectId: number, s: SecuritySettings): Promise<SecuritySettings> => {
        const r = await api.put(`/projects/${projectId}/security-settings`, s);
        return r.data;
    },
    listScans: async (projectId: number): Promise<SecurityScan[]> => {
        const r = await api.get(`/projects/${projectId}/security-scans`);
        return r.data;
    },
    getScan: async (scanId: number): Promise<SecurityScan> => {
        const r = await api.get(`/security-scans/${scanId}`);
        return r.data;
    },
    createScan: async (projectId: number, body: {
        target_url: string; scan_type: string; authenticated: boolean; authorized: boolean;
        openapi_url?: string | null;
        auth_header_name?: string | null;
        auth_header_value?: string | null;
    }): Promise<SecurityScan> => {
        const r = await api.post(`/projects/${projectId}/security-scan`, body);
        return r.data;
    },
    updateFinding: async (findingId: number, body: FindingUpdate): Promise<SecurityFinding> => {
        const r = await api.patch(`/security-findings/${findingId}`, body);
        return r.data;
    },
    // Findings register + workflow (WP7).
    listFindings: async (projectId: number, params: { status?: string; overdue?: boolean; scan_type?: string; owner_id?: number } = {}): Promise<FindingsRegister> => {
        const r = await api.get(`/projects/${projectId}/security-findings`, { params });
        return r.data;
    },
    ownerCandidates: async (projectId: number): Promise<OwnerCandidate[]> => {
        const r = await api.get(`/projects/${projectId}/security-findings/owner-candidates`);
        return r.data;
    },
    getSla: async (workspaceId: number): Promise<FindingSla> => {
        const r = await api.get(`/workspaces/${workspaceId}/finding-sla`);
        return r.data;
    },
    setSla: async (workspaceId: number, sla: Record<string, number>): Promise<FindingSla> => {
        const r = await api.put(`/workspaces/${workspaceId}/finding-sla`, { sla_days: sla });
        return r.data;
    },
    projectSarifPath: (projectId: number) => `/projects/${projectId}/security-findings/sarif`,
    scanSarifPath: (scanId: number) => `/security-scans/${scanId}/sarif`,
    scanDiff: async (scanId: number): Promise<ScanDiff> => {
        const r = await api.get(`/security-scans/${scanId}/diff`);
        return r.data;
    },
    getWorkspaceSecurity: async (workspaceId: number): Promise<WorkspaceSecurity> => {
        const r = await api.get(`/workspaces/${workspaceId}/security`);
        return r.data;
    },
    setWorkspaceActiveScan: async (workspaceId: number, enabled: boolean): Promise<WorkspaceSecurity> => {
        const r = await api.put(`/workspaces/${workspaceId}/security`, { active_scan_enabled: enabled });
        return r.data;
    },
};

export interface WorkspaceSecurity {
    workspace_id: number;
    active_scan_enabled: boolean;   // effective (env flag OR workspace toggle)
    workspace_toggle: boolean;      // the stored workspace setting
    forced_by_deployment: boolean;  // env flag forces it on
    can_edit: boolean;              // caller is workspace admin/owner
}

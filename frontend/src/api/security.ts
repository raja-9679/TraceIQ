import { api } from '@/lib/api';

// Mirrors backend/app/api/security.py + the passive endpoints on test_runs.py

export interface SecurityFinding {
    id: number;
    run_id?: number | null;
    scan_id?: number | null;
    scan_type: string;
    category: string;
    severity: 'high' | 'medium' | 'low' | 'info';
    title: string;
    description?: string | null;
    evidence?: string | null;
    target_url?: string | null;
    created_at: string;
    status?: 'open' | 'acknowledged' | 'false_positive' | 'resolved';
    assignee_id?: number | null;
    resolved_at?: string | null;
    fingerprint?: string | null;
}

export interface ScanDiff {
    scan_id: number;
    previous_scan_id: number | null;
    baseline_available: boolean;
    new: { id: number; severity: string; title: string; target_url: string | null; status: string }[];
    fixed: { id: number; severity: string; title: string; target_url: string | null; status: string }[];
    persisting_count: number;
}

export interface SecurityScanResult {
    run_id: number;
    scan_type: string;
    counts: Record<string, number>;
    findings: SecurityFinding[];
}

export interface SecurityScan {
    id: number;
    project_id: number;
    target_url: string;
    scan_type: string;
    authenticated: boolean;
    status: 'pending' | 'running' | 'completed' | 'error';
    created_at: string;
    started_at?: string | null;
    finished_at?: string | null;
    counts?: Record<string, number> | null;
    error?: string | null;
    findings: SecurityFinding[];
}

export interface SecuritySettings {
    enabled: boolean;
    allowed_domains: string[];
    allow_active_scan: boolean;
}

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
    }): Promise<SecurityScan> => {
        const r = await api.post(`/projects/${projectId}/security-scan`, body);
        return r.data;
    },
    updateFinding: async (findingId: number, body: { status?: string; assignee_id?: number | null }): Promise<SecurityFinding> => {
        const r = await api.patch(`/security-findings/${findingId}`, body);
        return r.data;
    },
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

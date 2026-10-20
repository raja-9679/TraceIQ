import { api } from '@/lib/api';

// Mirrors backend/app/services/rls.py:status (WP15a).
export interface RlsTableState {
    table: string;
    enabled: boolean;
    forced: boolean;
    policy: boolean;
}

export interface RlsStatus {
    mode: 'off' | 'log' | 'enforce';
    configured: string;
    sample_percent: number;
    mechanism: 'role' | 'force' | 'unavailable' | 'not_installed';
    mechanism_detail: string;
    role: string | null;
    tables: RlsTableState[];
    policies_installed: boolean;
    enforcing: boolean;
    violations: {
        source: 'instance' | 'process';
        by_table: Record<string, number>;
        total: number;
        since: number | null;
    };
}

export const rlsApi = {
    status: async (): Promise<RlsStatus> => (await api.get('/admin/rls/status')).data,
    resetViolations: async (): Promise<RlsStatus['violations']> =>
        (await api.post('/admin/rls/violations/reset')).data,
};

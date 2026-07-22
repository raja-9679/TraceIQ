import { api } from '@/lib/api';

// Mirrors backend/app/api/triage.py

export interface FailureCluster {
    id: number;
    project_id: number;
    signature: string;
    title: string;
    category: string;
    status: 'open' | 'investigating' | 'resolved' | 'ignored';
    occurrence_count: number;
    first_seen_at: string;
    last_seen_at: string;
    last_run_id?: number | null;
    sample_error?: string | null;
    assignee_id?: number | null;
    resolution_note?: string | null;
}

export interface FailureOccurrence {
    result_id: number;
    run_id: number;
    test_name: string;
    status: string;
    created_at: string;
}

export interface FailureClusterDetail extends FailureCluster {
    occurrences: FailureOccurrence[];
}

export const triageApi = {
    list: async (projectId: number, status?: string, category?: string): Promise<FailureCluster[]> => {
        const p = new URLSearchParams();
        if (status) p.set('status', status);
        if (category) p.set('category', category);
        const qs = p.toString();
        const r = await api.get(`/projects/${projectId}/failure-clusters${qs ? `?${qs}` : ''}`);
        return r.data;
    },
    get: async (clusterId: number): Promise<FailureClusterDetail> => {
        const r = await api.get(`/failure-clusters/${clusterId}`);
        return r.data;
    },
    update: async (clusterId: number, body: Partial<Pick<FailureCluster, 'status' | 'assignee_id' | 'resolution_note'>>): Promise<FailureCluster> => {
        const r = await api.patch(`/failure-clusters/${clusterId}`, body);
        return r.data;
    },
    createTicket: async (clusterId: number, body: { config_id: number; summary?: string }): Promise<{ ticket_id: number; status: string }> => {
        const r = await api.post(`/failure-clusters/${clusterId}/ticket`, body);
        return r.data;
    },
};

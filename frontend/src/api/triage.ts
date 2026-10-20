import { api } from '@/lib/api';
import type { Out } from './schemaTypes';

// Mirrors backend/app/api/triage.py; shapes generated from its response_models.
type ClusterStatus = 'open' | 'investigating' | 'resolved' | 'ignored';

export type FailureCluster = Omit<Out<'FailureClusterRead'>, 'status'> & { status: ClusterStatus };
export type FailureOccurrence = Out<'FailureOccurrenceRead'>;
export type FailureClusterDetail = Omit<Out<'FailureClusterDetail'>, 'status'> & { status: ClusterStatus };

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

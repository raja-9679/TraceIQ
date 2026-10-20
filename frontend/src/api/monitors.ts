import { api } from '@/lib/api';
import type { Out } from './schemaTypes';

// Mirrors the monitor endpoints in backend/app/api/endpoints/schedules.py;
// shapes generated from their response_models. `state` is a plain str there.
type MonitorState = 'up' | 'down' | 'unknown';

export type MonitorCheck = Out<'MonitorCheckRead'>;
export type MonitorRegionStatus = Omit<Out<'MonitorRegionStatus'>, 'state'> & { state: MonitorState };
/** `regions`: multi-region monitors (WP8); empty for unlabelled monitors. */
export type MonitorStatus = Omit<Out<'MonitorStatusRead'>, 'state' | 'regions'> & {
    state: MonitorState;
    regions: MonitorRegionStatus[];
};

export const monitorsApi = {
    list: async (projectId?: number): Promise<MonitorStatus[]> => {
        const qs = projectId ? `?project_id=${projectId}` : '';
        const r = await api.get(`/schedules/monitors${qs}`);
        return r.data;
    },
    status: async (scheduleId: number): Promise<MonitorStatus> => {
        const r = await api.get(`/schedules/${scheduleId}/monitor`);
        return r.data;
    },
    // Toggle monitor config on an existing schedule (reuses schedule update).
    updateSchedule: async (scheduleId: number, data: Record<string, any>): Promise<any> => {
        const r = await api.put(`/schedules/${scheduleId}`, data);
        return r.data;
    },
    getStatusPage: async (projectId: number): Promise<StatusPageConfig | null> => {
        try {
            const r = await api.get(`/projects/${projectId}/status-page`);
            return r.data;
        } catch (e: any) {
            if (e?.response?.status === 404) return null;
            throw e;
        }
    },
    upsertStatusPage: async (projectId: number, body: Record<string, any>): Promise<StatusPageConfig> => {
        const r = await api.put(`/projects/${projectId}/status-page`, body);
        return r.data;
    },
};

export interface StatusPageConfig {
    id: number;
    project_id: number;
    slug: string;
    title: string;
    enabled: boolean;
}

import { api } from '@/lib/api';

// Mirrors the monitor endpoints in backend/app/api/endpoints/schedules.py

export interface MonitorCheck {
    id: number;
    run_id: number | null;
    status: string;
    is_up: boolean;
    checked_at: string;
}

export interface MonitorStatus {
    schedule_id: number;
    name: string;
    is_active: boolean;
    state: 'up' | 'down' | 'unknown';
    consecutive_failures: number;
    total_checks: number;
    uptime_24h: number | null;
    uptime_7d: number | null;
    last_checked_at: string | null;
    recent_checks: MonitorCheck[];
}

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

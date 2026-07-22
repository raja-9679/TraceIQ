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
};

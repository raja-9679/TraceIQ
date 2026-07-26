import { api } from '@/lib/api';

// Mirrors backend/app/api/reports.py

export interface ReportSchedule {
    id: number;
    project_id: number;
    name: string;
    cron_expression: string;
    window_days: number;
    channels?: string[] | null;
    recipients?: string[] | null;
    is_active: boolean;
    next_run_at?: string | null;
    last_run_at?: string | null;
}

export const reportsApi = {
    list: async (projectId: number): Promise<ReportSchedule[]> => {
        const r = await api.get(`/projects/${projectId}/report-schedules`);
        return r.data;
    },
    create: async (projectId: number, body: {
        name: string; cron_expression: string; window_days: number;
        channels?: string[]; recipients?: string[]; is_active?: boolean;
    }): Promise<ReportSchedule> => {
        const r = await api.post(`/projects/${projectId}/report-schedules`, body);
        return r.data;
    },
    update: async (id: number, body: Partial<ReportSchedule>): Promise<ReportSchedule> => {
        const r = await api.patch(`/report-schedules/${id}`, body);
        return r.data;
    },
    remove: async (id: number): Promise<void> => {
        await api.delete(`/report-schedules/${id}`);
    },
    sendNow: async (id: number): Promise<void> => {
        await api.post(`/report-schedules/${id}/send-now`);
    },
};

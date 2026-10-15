import { api } from '@/lib/api';

// Mirrors backend/app/api/paging.py (WP8): paging channels, maintenance
// windows, paging events and the project egress policy.

export type PagingKind = 'pagerduty' | 'opsgenie';
export type Severity = 'critical' | 'error' | 'warning' | 'info';

export interface PagingChannel {
    id: number;
    project_id: number;
    name: string;
    kind: PagingKind;
    api_region: string | null;
    severity: Severity;
    on_monitor: boolean;
    on_run_failure: boolean;
    enabled: boolean;
    last_sent_at: string | null;
    last_error: string | null;
    created_at: string;
    secret_set: boolean;
}

export interface PagingChannelInput {
    name: string;
    kind: PagingKind;
    secret?: string | null;
    api_region?: string | null;
    severity: Severity;
    on_monitor: boolean;
    on_run_failure: boolean;
    enabled: boolean;
}

export interface MaintenanceWindow {
    id: number;
    project_id: number;
    name: string;
    starts_at: string | null;
    ends_at: string | null;
    weekdays: number[] | null;
    start_time: string | null;
    end_time: string | null;
    timezone: string;
    enabled: boolean;
    active_now: boolean;
}

export interface MaintenanceWindowInput {
    name: string;
    starts_at?: string | null;
    ends_at?: string | null;
    weekdays?: number[] | null;
    start_time?: string | null;
    end_time?: string | null;
    timezone: string;
    enabled: boolean;
}

export interface PagingEvent {
    id: number;
    channel_id: number | null;
    source: 'monitor' | 'run';
    action: 'trigger' | 'resolve';
    dedup_key: string;
    outcome: 'sent' | 'suppressed' | 'failed';
    detail: string | null;
    run_id: number | null;
    schedule_id: number | null;
    created_at: string;
}

export interface EgressPolicy {
    enforce: boolean;
    allowed_hosts: string[];
    allow_private: boolean;
}

export interface NetworkPolicyView {
    stored: EgressPolicy | null;
    effective: EgressPolicy | null;
}

const base = (projectId: number) => `/projects/${projectId}`;

export const oncallApi = {
    channels: async (projectId: number): Promise<PagingChannel[]> =>
        (await api.get(`${base(projectId)}/paging-channels`)).data,
    createChannel: async (projectId: number, body: PagingChannelInput): Promise<PagingChannel> =>
        (await api.post(`${base(projectId)}/paging-channels`, body)).data,
    updateChannel: async (projectId: number, id: number, body: PagingChannelInput): Promise<PagingChannel> =>
        (await api.put(`${base(projectId)}/paging-channels/${id}`, body)).data,
    deleteChannel: async (projectId: number, id: number): Promise<void> => {
        await api.delete(`${base(projectId)}/paging-channels/${id}`);
    },
    testChannel: async (projectId: number, id: number): Promise<{ ok: boolean; detail?: string; dedup_key?: string }> =>
        (await api.post(`${base(projectId)}/paging-channels/${id}/test`)).data,

    windows: async (projectId: number): Promise<MaintenanceWindow[]> =>
        (await api.get(`${base(projectId)}/maintenance-windows`)).data,
    createWindow: async (projectId: number, body: MaintenanceWindowInput): Promise<MaintenanceWindow> =>
        (await api.post(`${base(projectId)}/maintenance-windows`, body)).data,
    updateWindow: async (projectId: number, id: number, body: MaintenanceWindowInput): Promise<MaintenanceWindow> =>
        (await api.put(`${base(projectId)}/maintenance-windows/${id}`, body)).data,
    deleteWindow: async (projectId: number, id: number): Promise<void> => {
        await api.delete(`${base(projectId)}/maintenance-windows/${id}`);
    },

    events: async (projectId: number, limit = 50): Promise<PagingEvent[]> =>
        (await api.get(`${base(projectId)}/paging-events?limit=${limit}`)).data,

    networkPolicy: async (projectId: number): Promise<NetworkPolicyView> =>
        (await api.get(`${base(projectId)}/network-policy`)).data,
    setNetworkPolicy: async (projectId: number, body: Partial<EgressPolicy>): Promise<NetworkPolicyView> =>
        (await api.put(`${base(projectId)}/network-policy`, body)).data,
};

import { api } from '@/lib/api';

// Mirrors backend/app/api/issue_trackers.py

export interface IssueTrackerConfig {
    id: number;
    workspace_id: number;
    provider: 'jira' | 'itop' | 'github';
    name: string;
    base_url: string;
    auth_user?: string | null;
    settings?: Record<string, any> | null;
    enabled: boolean;
    created_at: string;
}

export interface IssueTrackerConfigCreate {
    provider: string;
    name: string;
    base_url: string;
    auth_user?: string;
    auth_secret: string;
    settings?: Record<string, any>;
    enabled?: boolean;
}

export interface IssueTicket {
    id: number;
    config_id: number;
    run_id?: number | null;
    provider: string;
    external_key?: string | null;
    url?: string | null;
    summary: string;
    status: 'pending' | 'created' | 'error';
    attachments_uploaded: number;
    attachments_total: number;
    error?: string | null;
    created_at: string;
}

export const ticketsApi = {
    listConfigs: async (workspaceId: number): Promise<IssueTrackerConfig[]> => {
        const r = await api.get(`/workspaces/${workspaceId}/issue-trackers`);
        return r.data;
    },
    createConfig: async (workspaceId: number, body: IssueTrackerConfigCreate): Promise<IssueTrackerConfig> => {
        const r = await api.post(`/workspaces/${workspaceId}/issue-trackers`, body);
        return r.data;
    },
    updateConfig: async (workspaceId: number, id: number, body: Partial<IssueTrackerConfigCreate>): Promise<IssueTrackerConfig> => {
        const r = await api.patch(`/workspaces/${workspaceId}/issue-trackers/${id}`, body);
        return r.data;
    },
    deleteConfig: async (workspaceId: number, id: number): Promise<void> => {
        await api.delete(`/workspaces/${workspaceId}/issue-trackers/${id}`);
    },
    createTicket: async (runId: number, body: {
        config_id: number; summary?: string; description?: string; priority?: string;
        attach_trace: boolean; attach_video: boolean; attach_screenshots: boolean;
    }): Promise<IssueTicket> => {
        const r = await api.post(`/runs/${runId}/tickets`, body);
        return r.data;
    },
    listTickets: async (runId: number): Promise<IssueTicket[]> => {
        const r = await api.get(`/runs/${runId}/tickets`);
        return r.data;
    },
    // Workspace configs, but any workspace the user belongs to may hold a tracker
    // used by a run's project — the create-ticket dialog fetches per workspace.
};

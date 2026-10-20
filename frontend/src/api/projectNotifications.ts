import { api } from '@/lib/api';

// Mirrors backend/app/api/project_notifications.py

export interface ProjectNotificationSettings {
    email_enabled: boolean;
    recipient_user_ids: number[];
    extra_emails: string[];
    failures_only: boolean;
    slack_enabled: boolean;
    teams_enabled: boolean;
    /** Email a test's owner on its first failure after a pass (WP16b). */
    notify_case_owners?: boolean;
    /** Webhook URLs never come back from the server: only whether one is set and a short hint. */
    slack_webhook_set: boolean;
    slack_webhook_hint: string | null;
    teams_webhook_set: boolean;
    teams_webhook_hint: string | null;
}

/** A partial update. A webhook is sent as a URL to set it, '' to clear it, or left out to keep it. */
export type ProjectNotificationPatch = Partial<Pick<ProjectNotificationSettings,
    'email_enabled' | 'recipient_user_ids' | 'extra_emails' | 'failures_only' | 'slack_enabled' | 'teams_enabled'
    | 'notify_case_owners'>> & {
    slack_webhook?: string;
    teams_webhook?: string;
};

export interface NotificationRecipient {
    id: number;
    email: string;
    full_name: string | null;
}

/** A person's choice for one project; 'default' follows their own Settings. */
export type ProjectNotifyMode = 'default' | 'all' | 'failures' | 'off';

export interface MyProjectNotification {
    project_id: number;
    project_name: string;
    workspace_id: number;
    mode: ProjectNotifyMode;
}

export const getProjectNotifications = (projectId: number) =>
    api.get<ProjectNotificationSettings>(`/projects/${projectId}/notification-settings`).then((r) => r.data);

export const updateProjectNotifications = (projectId: number, patch: ProjectNotificationPatch) =>
    api.put<ProjectNotificationSettings>(`/projects/${projectId}/notification-settings`, patch).then((r) => r.data);

export const getNotificationRecipients = (projectId: number) =>
    api.get<NotificationRecipient[]>(`/projects/${projectId}/notification-recipients`).then((r) => r.data);

export const getMyProjectNotification = (projectId: number) =>
    api.get<{ project_id: number; mode: ProjectNotifyMode }>(`/projects/${projectId}/my-notifications`).then((r) => r.data);

export const setMyProjectNotification = (projectId: number, mode: ProjectNotifyMode) =>
    api.put<{ project_id: number; mode: ProjectNotifyMode }>(`/projects/${projectId}/my-notifications`, { mode }).then((r) => r.data);

export const listMyProjectNotifications = () =>
    api.get<MyProjectNotification[]>('/me/project-notifications').then((r) => r.data);

export const NOTIFY_MODE_LABELS: Record<ProjectNotifyMode, string> = {
    default: 'My default',
    all: 'All runs',
    failures: 'Failed runs only',
    off: 'Nothing',
};

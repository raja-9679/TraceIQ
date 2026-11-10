// Comments, assignment and notifications on a run's results
// (backend: app/api/collaboration.py).
import { api } from '@/lib/api';

export interface Person { id: number; name: string }

export interface DiscussionComment {
    id: number; body: string; created_at: string; author: Person | null; can_delete: boolean;
}

export interface Assignment {
    assignee: Person; assigned_by: Person | null; status: 'open' | 'done';
    created_at: string; done_at: string | null;
    /** Where it was assigned; another run's result when `carried`. */
    run_id: number; result_id: number; carried: boolean;
}

export interface Discussion { comments: DiscussionComment[]; assignment: Assignment | null; can_write: boolean }

export interface ResultSummary { comments: number; assignee: Person | null; status: string | null; carried: boolean }

export type NotificationKind = 'assigned' | 'comment' | 'assignment_done';

export interface NotificationItem {
    id: number; kind: NotificationKind | string; created_at: string; read: boolean; actor: Person | null;
    project_id: number; project_name: string; run_id: number; result_id: number; test_name: string;
}

export interface MyAssignment {
    project_id: number; project_name: string; run_id: number; result_id: number; test_name: string;
    status: string; assigned_by: Person | null; created_at: string;
}

const base = (runId: number, resultId: number) => `/runs/${runId}/results/${resultId}`;

export const collabApi = {
    discussion: (runId: number, resultId: number) =>
        api.get<Discussion>(`${base(runId, resultId)}/discussion`).then((r) => r.data),
    addComment: (runId: number, resultId: number, body: string) =>
        api.post<DiscussionComment>(`${base(runId, resultId)}/comments`, { body }).then((r) => r.data),
    deleteComment: (runId: number, resultId: number, commentId: number) =>
        api.delete(`${base(runId, resultId)}/comments/${commentId}`).then(() => undefined),
    assign: (runId: number, resultId: number, userId: number) =>
        api.put<Assignment>(`${base(runId, resultId)}/assignment`, { assignee_user_id: userId }).then((r) => r.data),
    unassign: (runId: number, resultId: number) =>
        api.delete(`${base(runId, resultId)}/assignment`).then(() => undefined),
    setDone: (runId: number, resultId: number, done: boolean) =>
        api.post<Assignment>(`${base(runId, resultId)}/assignment/done`, { done }).then((r) => r.data),
    runSummary: (runId: number) =>
        api.get<{ results: Record<string, ResultSummary> }>(`/runs/${runId}/discussion`).then((r) => r.data.results),
    candidates: (projectId: number) =>
        api.get<Person[]>(`/projects/${projectId}/assignee-candidates`).then((r) => r.data),
    notifications: () =>
        api.get<{ items: NotificationItem[]; unread: number }>('/notifications').then((r) => r.data),
    unreadCount: () => api.get<{ unread: number }>('/notifications/unread-count').then((r) => r.data.unread),
    markRead: (body: { ids?: number[]; all?: boolean }) =>
        api.post<{ marked: number }>('/notifications/read', body).then((r) => r.data),
    myAssignments: () => api.get<MyAssignment[]>('/assignments/mine').then((r) => r.data),
};

// Test cycles: a round of testing for a release (backend: app/api/cycles.py).
import { api } from '@/lib/api';

export interface Person { id: number; name: string }

export type CycleState = 'passed' | 'failed' | 'blocked' | 'skipped' | 'not_run';

export interface Progress {
    total: number;
    executed: number;
    counts: Record<CycleState, number>;
    percent_executed: number | null;
    percent_passed: number | null;
}

export interface CycleSignoff {
    id: number; verdict: 'approve' | 'reject'; note: string | null;
    signed_by: Person | null; signed_at: string;
}

export interface Cycle {
    id: number;
    project_id: number;
    name: string;
    release_label: string | null;
    git_commit: string | null;
    description: string | null;
    status: 'open' | 'closed';
    start_date: string | null;
    due_date: string | null;
    overdue: boolean;
    created_by: Person | null;
    created_at: string;
    closed_by: Person | null;
    closed_at: string | null;
    progress: Progress;
    manual_cases: number;
    automated_cases: number;
    signoff: CycleSignoff | null;
}

export interface CycleItem {
    case_id: number;
    name: string;
    kind: 'manual' | 'automated';
    priority: string | null;
    suite_id: number | null;
    suite_name: string | null;
    root_suite_id: number | null;
    assignee: Person | null;
    status: CycleState;
    run_id: number | null;
    result_id: number | null;
    tested_at: string | null;
    tested_by: Person | null;
    configurations: number;
}

export interface CycleRun {
    id: number; kind: 'manual' | 'automated'; status: string; live: boolean;
    suite_name: string | null; browser: string | null; device: string | null;
    total_tests: number | null; passed_tests: number | null; failed_tests: number | null;
    created_at: string | null; started_by: Person | null;
}

export interface AssigneeProgress extends Progress { assignee: Person | null }

export interface CycleDetail extends Cycle {
    items: CycleItem[];
    assignees: AssigneeProgress[];
    runs: CycleRun[];
    runs_total: number;
    can_edit: boolean;
}

export interface CycleInput {
    name: string;
    release_label?: string | null;
    git_commit?: string | null;
    description?: string | null;
    start_date?: string | null;
    due_date?: string | null;
}

export interface ManualStarted {
    runs: { id: number; suite_name: string | null; cases: number }[];
    already_open: number;
    open_run_ids: number[];
}

export interface MyCycle {
    cycle_id: number; cycle_name: string; project_id: number; project_name: string;
    due_date: string | null; assigned: number; remaining: number;
}

export const cyclesApi = {
    list: (projectId: number) =>
        api.get<Cycle[]>(`/projects/${projectId}/test-cycles`).then((r) => r.data),
    create: (projectId: number, body: CycleInput & { suite_id?: number; case_ids?: number[] }) =>
        api.post<CycleDetail>(`/projects/${projectId}/test-cycles`, body).then((r) => r.data),
    get: (id: number) => api.get<CycleDetail>(`/test-cycles/${id}`).then((r) => r.data),
    update: (id: number, body: Partial<CycleInput>) =>
        api.patch<CycleDetail>(`/test-cycles/${id}`, body).then((r) => r.data),
    close: (id: number) => api.post<CycleDetail>(`/test-cycles/${id}/close`).then((r) => r.data),
    reopen: (id: number) => api.post<CycleDetail>(`/test-cycles/${id}/reopen`).then((r) => r.data),
    remove: (id: number) => api.delete(`/test-cycles/${id}`).then(() => undefined),
    addItems: (id: number, body: { suite_id?: number; case_ids?: number[] }) =>
        api.post<CycleDetail>(`/test-cycles/${id}/items`, body).then((r) => r.data),
    removeItems: (id: number, caseIds: number[]) =>
        api.post<CycleDetail>(`/test-cycles/${id}/items/remove`, { case_ids: caseIds }).then((r) => r.data),
    assign: (id: number, caseIds: number[], assigneeUserId: number | null) =>
        api.put<CycleDetail>(`/test-cycles/${id}/assignments`,
            { case_ids: caseIds, assignee_user_id: assigneeUserId }).then((r) => r.data),
    startManual: (id: number, body: { scope: 'remaining' | 'all'; mine_only: boolean }) =>
        api.post<ManualStarted>(`/test-cycles/${id}/manual-runs`, body).then((r) => r.data),
    mine: () => api.get<MyCycle[]>('/test-cycles/assigned-to-me').then((r) => r.data),
    searchCases: (projectId: number, q: string) =>
        api.get<{ items: { id: number; name: string; executor: string }[] }>(
            '/cases', { params: { project_id: projectId, q, limit: 20 } }).then((r) => r.data.items),
};

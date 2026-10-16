import { api } from '@/lib/api';

// Mirrors backend/app/api/manual_testing.py

export type ManualOutcome = 'passed' | 'failed' | 'blocked' | 'skipped';
export type SignoffVerdict = 'approve' | 'reject';

export interface ManualStepResult {
    index: number;
    outcome: ManualOutcome | null;
    note: string | null;
}

export interface ManualAttachment {
    key: string;
    name: string;
    content_type: string;
    size: number;
}

export interface ManualResult {
    id: number;
    test_case_id: number | null;
    test_name: string;
    status: string;
    outcome: ManualOutcome | null;
    notes: string | null;
    steps: { instruction: string; expected: string }[];
    step_results: ManualStepResult[];
    tester_id: number | null;
    tester_name: string | null;
    recorded_at: string | null;
    duration_ms: number;
    attachments: ManualAttachment[];
}

export interface ManualRunSummary {
    counts: Record<ManualOutcome, number>;
    unrecorded: number;
    passed: number;
    failed: number;
    status: 'passed' | 'failed';
}

export interface ManualRun {
    id: number;
    project_id: number;
    test_suite_id: number;
    suite_name: string | null;
    status: string;
    executor: 'manual';
    user_id: number | null;
    user_name: string | null;
    created_at: string;
    finalized_at: string | null;
    git_commit: string | null;
    git_branch: string | null;
    release_label: string | null;
    summary: ManualRunSummary;
    results: ManualResult[];
}

export interface ManualRunListItem {
    id: number;
    suite_name: string | null;
    status: string;
    created_at: string;
    finalized_at: string | null;
    user_id: number | null;
    total_tests: number;
    passed_tests: number;
    failed_tests: number;
    git_commit: string | null;
    release_label: string | null;
}

export interface ReleaseSignoff {
    id: number;
    project_id: number;
    release_label: string;
    git_commit: string | null;
    run_ids: number[];
    case_ids: number[] | null;
    verdict: SignoffVerdict;
    note: string | null;
    signed_by_id: number;
    signed_by_name: string | null;
    signed_at: string;
}

export const manualTestingApi = {
    start: async (body: { suite_id: number; case_ids?: number[]; release_label?: string; git_commit?: string }): Promise<ManualRun> =>
        (await api.post('/manual-runs', body)).data,
    get: async (runId: number): Promise<ManualRun> => (await api.get(`/manual-runs/${runId}`)).data,
    record: async (runId: number, resultId: number, body: {
        outcome: ManualOutcome; notes?: string | null;
        step_results?: { outcome: ManualOutcome | null; note?: string | null }[]; duration_ms?: number;
    }): Promise<ManualResult> => (await api.put(`/manual-runs/${runId}/results/${resultId}`, body)).data,
    attach: async (runId: number, resultId: number, file: File): Promise<ManualResult> => {
        const form = new FormData();
        form.append('file', file);
        return (await api.post(`/manual-runs/${runId}/results/${resultId}/attachments`, form)).data;
    },
    complete: async (runId: number, skipUnrecorded = false): Promise<ManualRun> =>
        (await api.post(`/manual-runs/${runId}/complete`, { skip_unrecorded: skipUnrecorded })).data,
    list: async (projectId: number): Promise<ManualRunListItem[]> =>
        (await api.get(`/projects/${projectId}/manual-runs`)).data,
    signoffs: async (projectId: number): Promise<ReleaseSignoff[]> =>
        (await api.get(`/projects/${projectId}/signoffs`)).data,
    signOff: async (projectId: number, body: {
        release_label: string; run_ids: number[]; verdict: SignoffVerdict; note?: string; git_commit?: string;
    }): Promise<ReleaseSignoff> => (await api.post(`/projects/${projectId}/signoffs`, body)).data,
    artifactUrl: async (key: string): Promise<string> => (await api.get(`/artifacts/${key}`)).data.url,
};

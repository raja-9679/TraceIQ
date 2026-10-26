// 'Run another test' steps (run-test): finding a test to run, and which tests
// run a given one. Backend: app/api/endpoints/test_cases.py, rules in
// app/services/case_calls.py.
import { api } from '@/lib/api';
import type { Out } from './schemaTypes';

export type CaseSummary = Out<'TestCaseSummary'>;
export type CaseUsedBy = Out<'CaseUsedByResponse'>;

export const caseCallsApi = {
    search: async (projectId: number, q: string, limit = 50): Promise<CaseSummary[]> =>
        (await api.get<Out<'TestCaseListResponse'>>('/cases', {
            params: { project_id: projectId, q: q.trim() || undefined, limit },
        })).data.items,
    usedBy: async (caseId: number): Promise<CaseUsedBy> =>
        (await api.get<CaseUsedBy>(`/cases/${caseId}/used-by`)).data,
};

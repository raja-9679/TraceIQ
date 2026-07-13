import { api } from '@/lib/api';

// ---------------------------------------------------------------------------
// Types — mirror backend/app/models.py CaseProposalRead / SelectorHealProposalRead
// ---------------------------------------------------------------------------

export interface ProposedStep {
    id?: string;
    type: string;
    selector?: string | null;
    value?: string | null;
    intent?: string | null;
    params?: Record<string, unknown> | null;
}

export type CaseProposalAction = 'create' | 'update' | 'delete' | 'move';
export type ProposalStatus = 'pending' | 'accepted' | 'rejected';

export interface CaseProposalPayload {
    name?: string;
    steps?: ProposedStep[];
    code_paths?: string[];
    intent?: string;
    reason?: string;
    new_test_suite_id?: number;
    [key: string]: unknown;
}

export interface CaseProposal {
    id: number;
    project_id: number;
    test_suite_id: number | null;
    target_case_id: number | null;
    action: CaseProposalAction;
    payload: CaseProposalPayload | null;
    rationale: string | null;
    ai_confidence: number;
    agent_id: string | null;
    source_run_id: number | null;
    status: ProposalStatus;
    created_at: string;
    decided_at: string | null;
}

export type HealProposalStatus = 'pending' | 'accepted' | 'rejected' | 'auto_applied';

export interface HealProposal {
    id: number;
    test_case_id: number;
    step_id: string;
    old_selector: string | null;
    new_selector: string;
    intent: string | null;
    confidence: number;
    rationale: string | null;
    source_run_id: number | null;
    status: HealProposalStatus;
    created_at: string;
}

// Shape of GET /api/cases/{id} (TestCaseRead) — only fields the review UI needs.
export interface TestCaseInfo {
    id: number;
    name: string;
    project_id: number | null;
    test_suite_id: number | null;
    steps: ProposedStep[];
    code_paths?: string[] | null;
    is_ai_authored?: boolean;
}

// ---------------------------------------------------------------------------
// Case proposals (agent-proposed create/update/delete/move of test cases)
// ---------------------------------------------------------------------------

export const caseProposalsApi = {
    list: async (projectId?: number, status?: string): Promise<CaseProposal[]> => {
        const params = new URLSearchParams();
        if (projectId) params.append('project_id', projectId.toString());
        if (status) params.append('status', status);
        const response = await api.get(`/case-proposals?${params.toString()}`);
        return response.data;
    },

    accept: async (id: number, note?: string): Promise<CaseProposal> => {
        const response = await api.post(`/case-proposals/${id}/accept`, { note: note || null });
        return response.data;
    },

    reject: async (id: number, note?: string): Promise<CaseProposal> => {
        const response = await api.post(`/case-proposals/${id}/reject`, { note: note || null });
        return response.data;
    },
};

// ---------------------------------------------------------------------------
// Selector heal proposals
// ---------------------------------------------------------------------------

export const healProposalsApi = {
    list: async (status?: string, testCaseId?: number): Promise<HealProposal[]> => {
        const params = new URLSearchParams();
        if (status) params.append('status', status);
        if (testCaseId) params.append('test_case_id', testCaseId.toString());
        const response = await api.get(`/heal-proposals?${params.toString()}`);
        return response.data;
    },

    accept: async (id: number): Promise<{ status: string; applied: boolean }> => {
        const response = await api.post(`/heal-proposals/${id}/accept`);
        return response.data;
    },

    reject: async (id: number): Promise<{ status: string }> => {
        const response = await api.post(`/heal-proposals/${id}/reject`);
        return response.data;
    },
};

export const getTestCaseInfo = async (caseId: number): Promise<TestCaseInfo> => {
    const response = await api.get(`/cases/${caseId}`);
    return response.data;
};

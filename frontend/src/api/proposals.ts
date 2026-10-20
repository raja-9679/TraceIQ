import { api } from '@/lib/api';
import type { ValidationConditions, ValidationStatus } from '@/components/proposals/repairValidation';

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
    // WP16c — validation run state (see components/proposals/repairValidation.ts)
    validation_status?: ValidationStatus;
    validation_run_id?: number | null;
    validation_conditions?: ValidationConditions | null;
    validation_detail?: string | null;
    validated_at?: string | null;
    validation_required?: boolean | null;
    protected_suite_id?: number | null;
}

export type HealProposalStatus = 'pending' | 'accepted' | 'rejected' | 'auto_applied';

export type HealStrategy = 'test-id' | 'id' | 'role' | 'label' | 'text' | 'css-path' | 'llm';

/** One ranked replacement selector with its evidence (W5). */
export interface HealCandidate {
    selector: string;
    strategy: HealStrategy;
    source: 'deterministic' | 'llm' | 'both';
    matches: number;
    applicable: boolean;
    score: number;
    breakdown: { stability: number; similarity: number; llm: number; uniqueness: number };
}

export type HealRecheckStatus = 'unique' | 'not_unique' | 'no_match' | 'unsupported' | 'unavailable';

export interface HealRecheck {
    status: HealRecheckStatus;
    matches: number | null;
    selector: string;
    detail: string;
}

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
    candidates?: HealCandidate[];
    chosen_selector?: string | null;
    has_dom_snapshot?: boolean;
    recheck?: HealRecheck | null;
    // WP16c — validation run state (see components/proposals/repairValidation.ts)
    validation_status?: ValidationStatus;
    validation_run_id?: number | null;
    validation_conditions?: ValidationConditions | null;
    validation_detail?: string | null;
    validated_at?: string | null;
    validation_required?: boolean | null;
    protected_suite_id?: number | null;
}

export interface ValidationStarted {
    run_id: number;
    validation_status: ValidationStatus;
    conditions: ValidationConditions | null;
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

    /** WP16c: run the target case once with this change applied to that run only. */
    validate: async (id: number): Promise<ValidationStarted> => {
        const response = await api.post(`/case-proposals/${id}/validate`);
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

    /** Apply a heal; `selector` picks one of the proposal's candidates. */
    accept: async (id: number, selector?: string): Promise<{ status: string; applied: boolean; selector: string; recheck: HealRecheck }> => {
        const response = await api.post(`/heal-proposals/${id}/accept`, selector ? { selector } : undefined);
        return response.data;
    },

    /** Dry run of the accept-time uniqueness check against the stored DOM snapshot. */
    recheck: async (id: number, selector?: string): Promise<HealRecheck> => {
        const response = await api.post(`/heal-proposals/${id}/recheck`, selector ? { selector } : undefined);
        return response.data;
    },

    /** WP16c: run the case once with this selector applied to that run only. */
    validate: async (id: number, selector?: string): Promise<ValidationStarted> => {
        const response = await api.post(`/heal-proposals/${id}/validate`, selector ? { selector } : undefined);
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

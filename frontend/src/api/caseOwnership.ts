import { api } from '@/lib/api';

// Mirrors backend/app/api/case_ownership.py (test ownership + review, WP16b).

export type OwnerState = 'ok' | 'no_access';
export type ReviewStatus = 'never' | 'overdue' | 'ok' | 'not_required';

export interface OwnershipPerson {
    id: number;
    name: string | null;
    email: string | null;
    state: OwnerState;
}

export interface CaseOwnership {
    case_id: number;
    name: string;
    test_suite_id: number | null;
    project_id: number;
    priority: string | null;
    owner: OwnershipPerson | null;
    backup_owner: OwnershipPerson | null;
    last_reviewed_at: string | null;
    last_reviewed_by: OwnershipPerson | null;
    review_interval_days: number;
    review_status: ReviewStatus;
    review_due_at: string | null;
    suite_name?: string | null;
}

export interface TestReviewList {
    project_id: number;
    counts: Record<'never' | 'overdue' | 'ok' | 'not_required' | 'owner_no_access' | 'unowned', number>;
    total: number;
    truncated: boolean;
    items: CaseOwnership[];
    default_interval_days: number;
}

export type ReviewOwnerFilter = 'me' | 'none' | 'no_access' | `${number}` | undefined;

export const getCaseOwnership = async (caseId: number): Promise<CaseOwnership> =>
    (await api.get(`/cases/${caseId}/ownership`)).data;

/** Omitted = unchanged; null = clear. */
export const setCaseOwnership = async (
    caseId: number, body: { owner_user_id?: number | null; backup_owner_user_id?: number | null },
): Promise<CaseOwnership> => (await api.put(`/cases/${caseId}/ownership`, body)).data;

export const markCaseReviewed = async (caseId: number): Promise<CaseOwnership> =>
    (await api.post(`/cases/${caseId}/reviewed`)).data;

export const getTestReview = async (
    projectId: number, status: 'needs_review' | 'all' = 'needs_review', owner?: ReviewOwnerFilter,
): Promise<TestReviewList> =>
    (await api.get(`/projects/${projectId}/test-review`, { params: { status, owner } })).data;

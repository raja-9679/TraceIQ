import { api } from '@/lib/api';

// ---------------------------------------------------------------------------
// Types — mirror backend/app/models.py VisualBaselineRead
// ---------------------------------------------------------------------------

export interface VisualBaseline {
    id: number;
    test_case_id: number;
    step_id: string;
    browser: string;
    device: string | null;
    viewport: string | null;
    image_url: string;
    tolerance: number;
    created_at: string;
    /** '' = full-page baseline; `el-…` = one element's (WP14e). */
    element_key?: string;
    selector?: string | null;
}

export interface VisualBaselineCreate {
    test_case_id: number;
    step_id: string;
    image_url: string;
    browser?: string;
    device?: string | null;
    viewport?: string | null;
    tolerance?: number;
    mask_regions?: Array<{ x: number; y: number; width: number; height: number }>;
}

export interface PromoteBaselineRequest {
    test_case_id: number;
    step_id: string;
    run_id: number;
    browser?: string;
    device?: string | null;
    tolerance?: number;
    mask_regions?: Record<string, unknown>[] | null;
    element_key?: string;
    /** Promote exactly this stored comparison's candidate (its slot wins). */
    comparison_id?: number;
}

export const visualBaselinesApi = {
    list: async (opts?: { testCaseId?: number; stepId?: string; projectId?: number }): Promise<VisualBaseline[]> => {
        const params = new URLSearchParams();
        if (opts?.testCaseId) params.append('test_case_id', opts.testCaseId.toString());
        if (opts?.stepId) params.append('step_id', opts.stepId);
        if (opts?.projectId) params.append('project_id', opts.projectId.toString());
        const response = await api.get(`/visual-baselines?${params.toString()}`);
        return response.data;
    },

    create: async (data: VisualBaselineCreate): Promise<VisualBaseline> => {
        const response = await api.post('/visual-baselines', data);
        return response.data;
    },

    // Durable promotion: copies a run's candidate screenshot into a stable
    // baseline object server-side (survives artifact cleanup / URL expiry).
    promote: async (data: PromoteBaselineRequest): Promise<VisualBaseline> => {
        const response = await api.post('/visual-baselines/promote', data);
        return response.data;
    },

    delete: async (id: number): Promise<{ status: string; id: number }> => {
        const response = await api.delete(`/visual-baselines/${id}`);
        return response.data;
    },

    // Record a "keep the current baseline" decision for a run's visual change.
    keep: async (id: number, runId?: number | null): Promise<{ status: string }> => {
        const response = await api.post(`/visual-baselines/${id}/keep`, { run_id: runId ?? null });
        return response.data;
    },

    // Accept / keep / rollback decisions for a baseline, newest first.
    history: async (id: number): Promise<BaselineHistoryEntry[]> => {
        const response = await api.get(`/visual-baselines/${id}/history`);
        return response.data;
    },

    // Re-pin an earlier version (must be one this baseline has used).
    rollback: async (id: number, image: string): Promise<VisualBaseline> => {
        const response = await api.post(`/visual-baselines/${id}/rollback`, { image });
        return response.data;
    },
};

export interface BaselineHistoryEntry {
    action: 'accept' | 'keep' | 'rollback' | string;
    at: string;
    user_id: number | null;
    actor_label: string | null;
    run_id: number | null;
    image: string | null;
    previous_image: string | null;
    is_current: boolean;
}

// ---------------------------------------------------------------------------
// Run-artifact helpers — the worker uploads `visual-{stepId}.png` (candidate)
// and `visual-{stepId}.diff.png` (pixelmatch diff) among run screenshots as
// keys like `runs/{runId}/screenshots/{jobId}-visual-{stepId}.png`.
// ---------------------------------------------------------------------------

export interface CaseRunHistoryEntry {
    run_id: number;
    status: string;
    created_at: string | null;
    duration_ms: number | null;
    git_commit: string | null;
    triggered_by: string;
    via_suite_run?: boolean;
}

export interface CaseRunHistory {
    case: {
        id: number;
        name: string;
        is_ai_authored: boolean;
        ai_confidence: number | null;
        last_human_reviewed_at: string | null;
    };
    history: CaseRunHistoryEntry[];
    summary: {
        sample_size: number;
        passes: number;
        failures: number;
        last_failure_at: string | null;
    };
}

export const getCaseRunHistory = async (caseId: number, limit = 10): Promise<CaseRunHistory> => {
    const response = await api.get(`/cases/${caseId}/run-history?limit=${limit}`);
    return response.data;
};

interface RunScreenshots {
    id: number;
    status: string;
    created_at: string;
    screenshots?: string[];
    results?: Array<{ screenshots?: string[] }>;
}

export interface VisualComparisonArtifacts {
    runId: number;
    runStatus: string;
    runCreatedAt: string;
    candidateKey: string | null;
    diffKey: string | null;
}

/**
 * Walk recent runs of a test case looking for the newest run that uploaded a
 * visual-match candidate screenshot for the given step. Returns the MinIO
 * object keys for the candidate and (if present) the pixel diff image.
 */
export const findLatestComparison = async (
    caseId: number,
    stepId: string,
    maxRunsToInspect = 6,
    elementKey = '',
): Promise<VisualComparisonArtifacts | null> => {
    // Element captures are visual-{step}-{key}.png; the full-page name never
    // matches them (nor they it).
    const stem = elementKey ? `visual-${stepId}-${elementKey}` : `visual-${stepId}`;
    const history = await getCaseRunHistory(caseId, 15);
    const runIds = history.history.map((h) => h.run_id);
    // De-dup while preserving recency order.
    const seen = new Set<number>();
    const ordered = runIds.filter((id) => (seen.has(id) ? false : (seen.add(id), true)));

    let inspected = 0;
    for (const runId of ordered) {
        if (inspected >= maxRunsToInspect) break;
        inspected += 1;
        let run: RunScreenshots;
        try {
            const response = await api.get(`/runs/${runId}`);
            run = response.data;
        } catch {
            continue;
        }
        const allScreens: string[] = [
            ...(run.screenshots || []),
            ...(run.results || []).flatMap((r) => r.screenshots || []),
        ];
        const candidateKey =
            allScreens.find((s) => s.endsWith(`${stem}.png`) && !s.includes('.diff.png')) || null;
        const diffKey = allScreens.find((s) => s.endsWith(`${stem}.diff.png`)) || null;
        if (candidateKey || diffKey) {
            return {
                runId,
                runStatus: run.status,
                runCreatedAt: run.created_at,
                candidateKey,
                diffKey,
            };
        }
    }
    return null;
};

/**
 * Resolve an image reference to a browser-fetchable URL. Baseline rows may
 * store a full URL (worker fetches it directly) or a bare MinIO object key —
 * the latter is exchanged for a presigned URL via GET /api/artifacts/{key}.
 */
export const resolveImageUrl = async (ref: string): Promise<string> => {
    if (/^https?:\/\//i.test(ref)) return ref;
    const response = await api.get(`/artifacts/${encodeURIComponent(ref)}`);
    return response.data.url;
};

// ---------------------------------------------------------------------------
// Visual review (WP14e) — a run's stored comparisons, grouped by likely same
// change, with comment threads. Mirrors backend/app/api/visual_review.py.
// ---------------------------------------------------------------------------

export interface DiffRegion {
    x: number;
    y: number;
    width: number;
    height: number;
    pixels: number;
}

export interface VisualReviewRunSummary {
    run_id: number;
    created_at: string | null;
    status: string | null;
    git_branch: string | null;
    comparisons: number;
    failed: number;
    changed: number;
}

export interface VisualReviewItem {
    id: number;
    test_case_id: number | null;
    test_name: string | null;
    case_name: string | null;
    step_id: string;
    browser: string;
    device: string | null;
    element_key: string;
    selector: string | null;
    kind: 'element' | 'full_page';
    status: 'passed' | 'failed' | 'capture_only' | string;
    diff_ratio: number | null;
    diff_pixels: number | null;
    total_pixels: number | null;
    width: number | null;
    height: number | null;
    size_mismatch: boolean;
    tolerance: number | null;
    regions: DiffRegion[];
    baseline_id: number | null;
    baseline_url: string | null;
    candidate_url: string | null;
    has_candidate: boolean;
    diff_url: string | null;
    decision: { action: 'accept' | 'keep' | string; at: string; actor_label: string | null } | null;
    group_key: string | null;
    is_change: boolean;
    comment_count: number;
    open_comment_count: number;
}

export interface VisualReviewGroup {
    key: string;
    item_ids: number[];
    size: number;
    case_count: number;
    region: { x: number; y: number; width: number; height: number };
    max_diff_ratio: number;
    comment_count: number;
    open_comment_count: number;
}

export interface VisualReviewRun {
    run: { id: number; created_at: string; status: string; git_branch: string | null } | null;
    items: VisualReviewItem[];
    groups: VisualReviewGroup[];
}

export interface ReviewComment {
    id: number;
    comparison_id: number | null;
    run_id: number | null;
    group_key: string | null;
    body: string;
    resolved: boolean;
    resolved_at: string | null;
    created_at: string;
    author: { id: number; name: string } | null;
}

export type CommentTarget = { comparisonId: number } | { runId: number; groupKey: string };

export const visualReviewApi = {
    runs: async (projectId: number): Promise<VisualReviewRunSummary[]> =>
        (await api.get(`/projects/${projectId}/visual-review/runs`)).data,

    review: async (projectId: number, runId?: number | null): Promise<VisualReviewRun> =>
        (await api.get(`/projects/${projectId}/visual-review`, { params: runId ? { run_id: runId } : {} })).data,

    acceptGroup: async (projectId: number, groupKey: string, runId: number) =>
        (await api.post(`/projects/${projectId}/visual-review/groups/${groupKey}/accept`, { run_id: runId })).data as
            { accepted: { id: number; baseline_id: number }[]; skipped: { id: number; reason: string }[] },

    keepGroup: async (projectId: number, groupKey: string, runId: number, note?: string) =>
        (await api.post(`/projects/${projectId}/visual-review/groups/${groupKey}/keep`, { run_id: runId, note })).data as
            { kept: { id: number; baseline_id: number }[]; skipped: { id: number; reason: string }[] },

    comments: async (projectId: number, target: CommentTarget): Promise<ReviewComment[]> => {
        const params = 'comparisonId' in target
            ? { comparison_id: target.comparisonId }
            : { run_id: target.runId, group_key: target.groupKey };
        return (await api.get(`/projects/${projectId}/visual-review/comments`, { params })).data;
    },

    addComment: async (projectId: number, target: CommentTarget, body: string): Promise<ReviewComment> => {
        const payload = 'comparisonId' in target
            ? { body, comparison_id: target.comparisonId }
            : { body, run_id: target.runId, group_key: target.groupKey };
        return (await api.post(`/projects/${projectId}/visual-review/comments`, payload)).data;
    },

    resolveComment: async (projectId: number, commentId: number, resolved: boolean): Promise<ReviewComment> =>
        (await api.post(`/projects/${projectId}/visual-review/comments/${commentId}/resolve`, { resolved })).data,
};

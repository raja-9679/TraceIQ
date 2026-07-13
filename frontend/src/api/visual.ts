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
};

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
): Promise<VisualComparisonArtifacts | null> => {
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
            allScreens.find((s) => s.includes(`visual-${stepId}.png`) && !s.includes('.diff.png')) || null;
        const diffKey = allScreens.find((s) => s.includes(`visual-${stepId}.diff.png`)) || null;
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

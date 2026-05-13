// Tiny HTTP helper used by `expect-visual-match` to fetch a pinned
// VisualBaseline from the backend.

const BACKEND_URL = process.env.BACKEND_URL || 'http://backend:8000';
const WORKER_TOKEN = process.env.WORKER_WEBHOOK_SECRET || process.env.WEBHOOK_SECRET || '';

export interface BaselineRecord {
    id: number;
    test_case_id: number;
    step_id: string;
    browser: string;
    device?: string;
    viewport?: string;
    image_url: string;
    tolerance: number;
    mask_regions?: any[];
}

export async function resolveBaseline(query: {
    testCaseId?: number;
    stepId: string;
    browser?: string;
    device?: string;
}): Promise<BaselineRecord | null> {
    if (!query.testCaseId) return null;
    const params = new URLSearchParams();
    params.set('test_case_id', String(query.testCaseId));
    params.set('step_id', query.stepId);
    const url = `${BACKEND_URL}/api/visual-baselines?${params.toString()}`;
    try {
        const res = await fetch(url, {
            headers: {
                // The endpoints require auth; in production the worker would
                // mint a service token. For now we lean on an internal-only
                // mode; if unauthorized we return null and degrade gracefully.
                'X-TraceIQ-Secret': WORKER_TOKEN,
            },
        });
        if (!res.ok) return null;
        const items = (await res.json()) as BaselineRecord[];
        // Prefer browser+device exact match; otherwise first hit.
        const preferred = items.find(
            (b) => (!query.browser || b.browser === query.browser) && (!query.device || b.device === query.device),
        );
        return preferred ?? items[0] ?? null;
    } catch (err) {
        console.warn('[baseline-client] resolveBaseline failed:', err);
        return null;
    }
}

export async function fetchImageBytes(imageUrl: string): Promise<Buffer> {
    const res = await fetch(imageUrl);
    if (!res.ok) throw new Error(`baseline image fetch ${res.status}`);
    const arr = await res.arrayBuffer();
    return Buffer.from(arr);
}

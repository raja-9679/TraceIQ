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
    if (!query.testCaseId) {
        console.warn('[baseline-client] no testCaseId in context — cannot resolve baseline');
        return null;
    }
    const params = new URLSearchParams();
    params.set('test_case_id', String(query.testCaseId));
    params.set('step_id', query.stepId);
    if (query.browser) params.set('browser', query.browser);
    if (query.device) params.set('device', query.device);
    const url = `${BACKEND_URL}/api/internal/visual-baselines/resolve?${params.toString()}`;
    try {
        const res = await fetch(url, {
            headers: { 'X-Worker-Secret': WORKER_TOKEN },
        });
        if (res.status === 404) return null; // genuinely no baseline yet
        if (!res.ok) {
            console.warn(`[baseline-client] resolve returned ${res.status} — check WEBHOOK_SECRET on the worker`);
            return null;
        }
        return (await res.json()) as BaselineRecord;
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

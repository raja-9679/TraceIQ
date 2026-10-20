/**
 * Network stubbing steps (WP15d): `mock-response` (now with `sequence`) and
 * `har-replay`.
 *
 * Both register PAGE routes. Playwright tries page routes before context
 * routes, and the egress guard (core/egress-guard.ts) is a context route — so
 * anything these hand on with `route.fallback()` (a sequence that has run
 * out with after_sequence=fallthrough, a HAR miss with not_found=fallback, a
 * URL the HAR filter does not cover) still passes the egress policy before it
 * reaches the network. Fulfilled responses never touch the network at all.
 */
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { backendFetch } from './backend-http';

export type MockEntry = {
    status?: number;
    json?: any;
    body?: any;
    content_type?: string;
    headers?: Record<string, string>;
};

/** Playwright `route.fulfill` options for one mock entry. */
export function fulfillOptions(entry: MockEntry, resolve: (v: any) => any = v => v) {
    const contentType = entry.content_type
        || (entry.json !== undefined ? 'application/json' : 'text/plain');
    const body = entry.json !== undefined
        ? JSON.stringify(entry.json)
        : String(resolve(entry.body ?? '') ?? '');
    const status = Number.isInteger(Number(entry.status)) ? Number(entry.status) : 200;
    return { status, contentType, headers: entry.headers || {}, body };
}

export type SequenceDecision = { kind: 'fulfill'; entry: MockEntry; index: number } | { kind: 'fallback' };

/**
 * nth matching call → nth entry. After the list: `repeat_last` (default) keeps
 * answering with the last entry; `fallthrough` hands the request on (to the
 * network, through the egress guard). Counting is per registered step.
 */
export function sequenceResponder(sequence: MockEntry[], after: string = 'repeat_last'): () => SequenceDecision {
    if (!Array.isArray(sequence) || sequence.length === 0) {
        throw new Error('mock-response: params.sequence must be a non-empty list of responses');
    }
    if (after !== 'repeat_last' && after !== 'fallthrough') {
        throw new Error(`mock-response: params.after_sequence must be 'repeat_last' or 'fallthrough', got '${after}'`);
    }
    let calls = 0;
    return () => {
        const i = calls++;
        if (i < sequence.length) return { kind: 'fulfill', entry: sequence[i] || {}, index: i };
        if (after === 'fallthrough') return { kind: 'fallback' };
        return { kind: 'fulfill', entry: sequence[sequence.length - 1] || {}, index: sequence.length - 1 };
    };
}

export async function executeMockResponse(page: any, step: any, resolve: (v: any) => any): Promise<void> {
    const urlPattern = step.selector || '**/*';
    const params = step.params || {};
    if (params.sequence !== undefined) {
        const next = sequenceResponder(params.sequence, params.after_sequence || 'repeat_last');
        await page.route(urlPattern, async (route: any) => {
            const d = next();
            if (d.kind === 'fallback') { await route.fallback(); return; }
            await route.fulfill(fulfillOptions(d.entry, resolve));
        });
        console.log(`  [Mock] ${urlPattern} -> sequence of ${params.sequence.length} (then ${params.after_sequence || 'repeat_last'})`);
        return;
    }
    const opts = fulfillOptions({
        status: params.status ?? 200, json: params.json, content_type: params.content_type,
        headers: params.headers, body: step.value ?? params.body ?? '',
    }, resolve);
    await page.route(urlPattern, (route: any) => route.fulfill(opts));
    console.log(`  [Mock] ${urlPattern} -> ${opts.status} (${opts.contentType})`);
}

export type HarFetcher = (harId: number, runId: any, caseId: any) => Promise<string>;

/** Default fetcher: the backend's internal endpoint, which re-checks that the
 * HAR, the case and the live run are in one project (no presigned URL ever
 * rides on the job payload). */
export const fetchHarFromBackend: HarFetcher = async (harId, runId, caseId) => {
    const q = `run_id=${encodeURIComponent(String(runId))}&test_case_id=${encodeURIComponent(String(caseId))}`;
    const res = await backendFetch(`/api/internal/har-files/${harId}?${q}`, { timeoutMs: 60_000 });
    if (!res.ok) {
        let detail = '';
        try { detail = (await res.json())?.detail || ''; } catch { /* non-JSON */ }
        throw new Error(`har-replay: HAR ${harId} could not be fetched (${res.status}${detail ? `: ${detail}` : ''})`);
    }
    return res.text();
};

export function harOptions(step: any): { notFound: 'abort' | 'fallback'; url?: string } {
    const nf = step.params?.not_found ?? 'abort';
    if (nf !== 'abort' && nf !== 'fallback') {
        throw new Error(`har-replay: params.not_found must be 'abort' or 'fallback', got '${nf}'`);
    }
    return step.selector ? { notFound: nf, url: step.selector } : { notFound: nf };
}

export async function executeHarReplay(page: any, step: any, ctx: any, globalSettings: any,
                                       fetcher: HarFetcher = fetchHarFromBackend): Promise<any> {
    const params = step.params || {};
    if (params.har_error) throw new Error(`har-replay: ${params.har_error}`);
    const harId = Number(params.har?.id ?? params.har_id);
    if (!Number.isInteger(harId) || harId <= 0) {
        throw new Error('har-replay needs params.har_id (an uploaded HAR of this project)');
    }
    const opts = harOptions(step);
    const runId = ctx?.run_id ?? globalSettings?.run?.id;
    const caseId = ctx?.id;
    if (!runId || !caseId) throw new Error('har-replay: no run/case context to fetch the HAR with');
    const text = await fetcher(harId, runId, caseId);
    try { JSON.parse(text); } catch { throw new Error(`har-replay: HAR ${harId} is not valid JSON`); }
    // routeFromHAR reads a file; Playwright loads it fully when the route is
    // registered, so the temp copy is removed straight after. Not the
    // artifacts dir — that would upload the recording with the run.
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'traceiq-har-'));
    const file = path.join(dir, `${harId}.har`);
    try {
        fs.writeFileSync(file, text, { mode: 0o600 });
        await page.routeFromHAR(file, { notFound: opts.notFound, ...(opts.url ? { url: opts.url } : {}) });
    } finally {
        fs.rmSync(dir, { recursive: true, force: true });
    }
    console.log(`  [HAR] replaying HAR ${harId}${opts.url ? ` for ${opts.url}` : ''} (not found: ${opts.notFound})`);
    return { type: 'har-replay', har_id: harId, not_found: opts.notFound };
}

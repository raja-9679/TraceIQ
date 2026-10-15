/**
 * Access-matrix cell execution (`job_type: 'access_check'`).
 *
 * One job = one (target, identity) cell of an AccessMatrix. The worker only
 * OBSERVES — status, element presence, redacted headers and body excerpt, a
 * screenshot for page rows — and parks the observation under the job's
 * `result_key`. The verdict (pass / fail / inconclusive) is computed by the
 * backend (app/services/access_matrix.py) so one rule set decides what
 * "denied" means, whatever this image's age.
 *
 * Safety is re-checked here even though the backend checked it at dispatch:
 * the target host must be on the job's allowlist, and a mutating request
 * (including the ID-swap owner request) needs `authorized: true`. A refused
 * job reports `error_kind: 'refused'`, which the backend marks inconclusive.
 */
import { request as pwRequest, APIRequestContext, Browser } from 'playwright';
import { jsonPath } from './core/test-executor';
import { redactAccessObservation, policyFromDataPolicy } from './core/redact';

export const RESULT_KEY_PREFIX = 'access_check:result:';
const BODY_EXCERPT_CHARS = 2000;
const REQUEST_TIMEOUT_MS = 30000;

export interface Identity {
    anonymous?: boolean;
    storage_state?: any;
    headers?: Record<string, string>;
}

export interface Extractor {
    source: 'json' | 'regex' | 'header';
    expr: string;
}

export interface OwnerSpec {
    identity: Identity;
    method: string;
    url: string;
    body?: any;
    extractor: Extractor;
    placeholder: string;
}

export interface AccessCheckJob {
    job_id: string;
    cell_id: number;
    matrix_run_id: number;
    result_key: string;
    target: {
        kind: 'request' | 'page';
        method: string;
        url: string;
        body?: any;
        headers?: Record<string, string>;
        selector?: string | null;
    };
    identity: Identity;
    owner?: OwnerSpec | null;
    allowed_domains: string[];
    authorized: boolean;
    data_policy?: any;
}

export type ErrorKind = 'timeout' | 'network' | 'refused' | 'swap_failed' | 'extract_failed' | 'error';

export interface Observation {
    url: string;
    status?: number;
    final_url?: string;
    headers?: Record<string, string>;
    content_type?: string;
    body_excerpt?: string;
    element_present?: boolean | null;
    screenshot?: string | null;
    owner_status?: number;
    swapped_id?: string;
    error_kind?: ErrorKind;
    error?: string;
}

export interface PageObservation {
    status?: number;
    final_url?: string;
    element_present?: boolean | null;
    screenshot?: string | null;
}

export interface AccessCheckDeps {
    newRequestContext(identity: Identity): Promise<APIRequestContext>;
    openPage?(identity: Identity, url: string, selector: string | null | undefined): Promise<PageObservation>;
}

// --------------------------------------------------------------------------
// Pure helpers
// --------------------------------------------------------------------------

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

export function isMutating(method: string | undefined): boolean {
    return !SAFE_METHODS.has(String(method || 'GET').toUpperCase());
}

/** Exact host or subdomain match — the same rule as the backend and active scans. */
export function hostAllowed(url: string, allowed: string[] | undefined): boolean {
    let host = '';
    try { host = new URL(url).hostname.toLowerCase(); } catch { return false; }
    if (!host) return false;
    return (allowed || []).some(d => {
        const dom = String(d || '').trim().toLowerCase();
        return !!dom && (host === dom || host.endsWith('.' + dom));
    });
}

/** Why this job must not run, or null. */
export function refusalReason(job: AccessCheckJob): string | null {
    const targets: Array<[string, string]> = [[job.target.method, job.target.url]];
    if (job.owner) targets.push([job.owner.method, job.owner.url]);
    for (const [method, url] of targets) {
        if (!/^https?:\/\//i.test(url || '')) return `${url} is not an http(s) url`;
        // The placeholder is substituted later; check the host of the template.
        if (!hostAllowed(url, job.allowed_domains)) return `host of ${url} is not on the security allowlist`;
        if (isMutating(method) && !job.authorized) return `${method} needs authorization attestation`;
    }
    return null;
}

/** Classify a thrown request/navigation error. Never "denied": see module doc. */
export function classifyError(err: any): ErrorKind {
    const msg = String(err?.message || err || '');
    if (/timeout|timed out/i.test(msg)) return 'timeout';
    if (/ECONNREFUSED|ECONNRESET|ENOTFOUND|EAI_AGAIN|EHOSTUNREACH|socket hang up|net::ERR_|getaddrinfo|certificate|SSL|TLS/i.test(msg)) {
        return 'network';
    }
    return 'error';
}

/** Pull the owner's resource id out of a response. null when absent. */
export function extractId(
    extractor: Extractor,
    resp: { headers: Record<string, string>; body: string },
): string | null {
    if (!extractor || !extractor.expr) return null;
    if (extractor.source === 'header') {
        const want = extractor.expr.toLowerCase();
        const hit = Object.entries(resp.headers || {}).find(([k]) => k.toLowerCase() === want);
        return hit && hit[1] ? String(hit[1]) : null;
    }
    if (extractor.source === 'regex') {
        let re: RegExp;
        try { re = new RegExp(extractor.expr); } catch { return null; }
        const m = re.exec(resp.body || '');
        if (!m) return null;
        return String(m[1] ?? m[0]);
    }
    let parsed: any;
    try { parsed = JSON.parse(resp.body || ''); } catch { return null; }
    const v = jsonPath(parsed, extractor.expr);
    if (v === undefined || v === null || typeof v === 'object') return null;
    return String(v);
}

/** Put the extracted id into the target: URL-encoded in the url, verbatim in the body. */
export function substitute(target: AccessCheckJob['target'], placeholder: string, id: string): AccessCheckJob['target'] {
    const out = { ...target };
    out.url = target.url.split(placeholder).join(encodeURIComponent(id));
    if (target.body !== undefined && target.body !== null) {
        if (typeof target.body === 'string') {
            out.body = target.body.split(placeholder).join(id);
        } else {
            out.body = JSON.parse(JSON.stringify(target.body).split(placeholder).join(id.replace(/["\\]/g, '\\$&')));
        }
    }
    return out;
}

// --------------------------------------------------------------------------
// Execution
// --------------------------------------------------------------------------

async function fetchAs(
    deps: AccessCheckDeps, identity: Identity, method: string, url: string, body: any,
    headers: Record<string, string> | undefined,
): Promise<{ status: number; headers: Record<string, string>; body: string }> {
    const ctx = await deps.newRequestContext(identity);
    try {
        const resp = await ctx.fetch(url, {
            method,
            headers: headers || {},
            data: body === null ? undefined : body,
            // A redirect is evidence in its own right (302 to /login); don't
            // follow it into a 200 login page that would read as "granted".
            maxRedirects: 0,
            timeout: REQUEST_TIMEOUT_MS,
            failOnStatusCode: false,
        });
        return { status: resp.status(), headers: resp.headers(), body: await resp.text().catch(() => '') };
    } finally {
        await ctx.dispose().catch(() => undefined);
    }
}

export async function runAccessCheck(job: AccessCheckJob, deps: AccessCheckDeps): Promise<Observation> {
    let target = job.target;
    const obs: Observation = { url: target.url };
    const refused = refusalReason(job);
    if (refused) return { ...obs, error_kind: 'refused', error: refused };

    if (job.owner) {
        let ownerResp;
        try {
            ownerResp = await fetchAs(deps, job.owner.identity, job.owner.method, job.owner.url,
                job.owner.body, undefined);
        } catch (e: any) {
            return { ...obs, error_kind: classifyError(e), error: `owner request: ${e?.message || e}`.slice(0, 300) };
        }
        obs.owner_status = ownerResp.status;
        if (ownerResp.status < 200 || ownerResp.status >= 300) {
            return { ...obs, error_kind: 'swap_failed', error: `owner request returned ${ownerResp.status}` };
        }
        const id = extractId(job.owner.extractor, ownerResp);
        if (!id) return { ...obs, error_kind: 'extract_failed', error: 'no id found in the owner response' };
        target = substitute(target, job.owner.placeholder || '{id}', id);
        obs.url = target.url;
        obs.swapped_id = id;
    }

    if (target.kind === 'page') {
        if (!deps.openPage) return { ...obs, error_kind: 'error', error: 'page checks need a browser' };
        try {
            const page = await deps.openPage(job.identity, target.url, target.selector);
            return { ...obs, ...page };
        } catch (e: any) {
            return { ...obs, error_kind: classifyError(e), error: String(e?.message || e).slice(0, 300) };
        }
    }

    try {
        const resp = await fetchAs(deps, job.identity, target.method, target.url, target.body, target.headers);
        const ctype = resp.headers['content-type'] || '';
        return {
            ...obs,
            status: resp.status,
            headers: resp.headers,
            content_type: ctype,
            body_excerpt: resp.body.slice(0, BODY_EXCERPT_CHARS),
        };
    } catch (e: any) {
        return { ...obs, error_kind: classifyError(e), error: String(e?.message || e).slice(0, 300) };
    }
}

// --------------------------------------------------------------------------
// Worker glue
// --------------------------------------------------------------------------

export function requestContextFactory(): AccessCheckDeps['newRequestContext'] {
    return async (identity: Identity) => pwRequest.newContext({
        storageState: identity?.anonymous ? undefined : (identity?.storage_state || undefined),
        extraHTTPHeaders: identity?.anonymous ? {} : (identity?.headers || {}),
    });
}

export function pageOpener(
    browser: Browser, job: AccessCheckJob,
    upload: (key: string, png: Buffer) => Promise<string | null>,
): AccessCheckDeps['openPage'] {
    return async (identity, url, selector) => {
        const context = await browser.newContext({
            storageState: identity?.anonymous ? undefined : (identity?.storage_state || undefined),
            extraHTTPHeaders: identity?.anonymous ? {} : (identity?.headers || {}),
        });
        try {
            const page = await context.newPage();
            const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: REQUEST_TIMEOUT_MS });
            // Let a SPA render before judging "absent"; a page that never goes
            // idle is still judged — absence after load is the deny signal.
            await page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => undefined);
            let element_present: boolean | null | undefined = undefined;
            if (selector) {
                element_present = await page.locator(selector).first().isVisible().catch(() => null);
            }
            let screenshot: string | null = null;
            try {
                const { TestExecutor } = require('./core/test-executor');
                const png = await page.screenshot(TestExecutor.screenshotOptions(
                    page, { data_policy: job.data_policy }, { type: 'png' }));
                screenshot = await upload(`access-matrix/${job.matrix_run_id}/${job.cell_id}.png`, png);
            } catch (e: any) {
                console.warn(`[AccessCheck] screenshot failed: ${e?.message || e}`);
            }
            return { status: resp?.status(), final_url: page.url(), element_present, screenshot };
        } finally {
            await context.close().catch(() => undefined);
        }
    };
}

/** Run a job and return the redacted observation to park under result_key. */
export async function executeAccessCheckJob(job: AccessCheckJob, deps: AccessCheckDeps): Promise<Observation> {
    let obs: Observation;
    try {
        obs = await runAccessCheck(job, deps);
    } catch (e: any) {
        obs = { url: job.target?.url, error_kind: 'error', error: String(e?.message || e).slice(0, 300) };
    }
    return redactAccessObservation(obs, policyFromDataPolicy(job.data_policy));
}

/** A stream entry may only write its own result key — never an arbitrary Redis key. */
export function safeResultKey(job: AccessCheckJob): string | null {
    const key = String(job.result_key || '');
    return key === `${RESULT_KEY_PREFIX}${Number(job.cell_id)}` ? key : null;
}

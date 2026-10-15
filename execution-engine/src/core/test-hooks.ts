/**
 * wait-for-email / wait-for-webhook support.
 *
 * The worker holds no mailbox credentials and cannot mint addresses or capture
 * tokens: the backend derived this case's values at dispatch
 * (`test_case.test_hooks`), and the steps poll two internal endpoints with the
 * worker secret, naming (run, case, row, label). The backend re-derives the
 * address/token from those, so a worker can only ever read its own run's mail
 * and captures.
 *
 * Email matching and extraction happen in the backend, on the raw message (a
 * reset link must survive; the preview we get back is redacted). Webhook
 * captures arrive already redacted and are matched/extracted here.
 */
import { jsonPath } from './json-path';
import { redactMessageText } from './redact';
import { backendFetch } from './backend-http';


export const DEFAULT_TIMEOUT_MS = 60_000;
export const MAX_TIMEOUT_MS = 300_000;
export const POLL_INTERVAL_MS = 2_000;

export interface TestHooks {
    run_id: number;
    test_case_id: number;
    row_index?: number | null;
    inbox?: Record<string, string>;
    webhook?: Record<string, string>;
    inbox_error?: string;
}

/** A 4xx the step cannot recover from by waiting (bad regex, no inbox configured). */
export class HookRequestError extends Error {}

/** Overridable in tests: clock, sleep and transport. */
export const hooksRuntime = {
    now: () => Date.now(),
    sleep: (ms: number) => new Promise<void>(r => setTimeout(r, ms)),
    post: async (path: string, body: any): Promise<{ status: number; json: any }> => {
        // backendFetch adds X-Worker-Secret and, when configured, the mTLS
        // client certificate (WP8).
        const res = await backendFetch(path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        let json: any = null;
        try { json = await res.json(); } catch { /* non-JSON error page */ }
        return { status: res.status, json };
    },
};

export function clampTimeout(raw: any): number {
    const n = Number(raw);
    if (!Number.isFinite(n) || n <= 0) return DEFAULT_TIMEOUT_MS;
    return Math.min(n, MAX_TIMEOUT_MS);
}

/**
 * Which label a step's `to`/`url` refers to. The param is resolved first (so
 * `{{inbox.address.signup}}` has become an address); anything that is not
 * one of this case's own values is refused — the step cannot be pointed at
 * an arbitrary mailbox, and the backend would refuse it anyway.
 */
export function labelFor(kind: 'inbox' | 'webhook', resolved: string, hooks: TestHooks | undefined): string {
    const values = (kind === 'inbox' ? hooks?.inbox : hooks?.webhook) || {};
    const want = (resolved || '').trim();
    if (!want) {
        if (values.default) return 'default';
    } else {
        for (const [label, value] of Object.entries(values)) {
            if (value.toLowerCase() === want.toLowerCase()) return label;
        }
    }
    const token = kind === 'inbox' ? '{{inbox.address}}' : '{{webhook.url}}';
    if (kind === 'inbox' && hooks?.inbox_error) {
        throw new HookRequestError(`wait-for-email: ${hooks.inbox_error}`);
    }
    if (!hooks) {
        throw new HookRequestError(`${kind === 'inbox' ? 'wait-for-email' : 'wait-for-webhook'}: this job carries no test hooks — the backend that dispatched it predates the feature, or this is a local worker (not supported)`);
    }
    throw new HookRequestError(`${kind === 'inbox' ? 'wait-for-email' : 'wait-for-webhook'}: "${want || '(empty)'}" is not one of this run's ${kind === 'inbox' ? 'inbox addresses' : 'capture URLs'} — use ${token} or a labelled variant`);
}

async function call(path: string, body: any): Promise<any | null> {
    let res;
    try {
        res = await hooksRuntime.post(path, body);
    } catch (err: any) {
        // Backend blip: keep polling until the step's own timeout.
        console.warn(`  [hooks] ${path} unreachable: ${err?.message || err}`);
        return null;
    }
    if (res.status === 403) throw new HookRequestError('worker secret rejected by the backend — check WEBHOOK_SECRET on the worker');
    if (res.status === 404) throw new HookRequestError(`backend refused the lookup: ${res.json?.detail || 'run is not live'}`);
    if (res.status === 422 || res.status === 400) throw new HookRequestError(res.json?.detail || `bad request (${res.status})`);
    if (res.status >= 400) {
        console.warn(`  [hooks] ${path} returned ${res.status}: ${res.json?.detail || ''}`);
        return null;
    }
    return res.json;
}

/** Poll `probe` until it returns a non-null value or the timeout passes. */
export async function pollUntil<T>(timeoutMs: number, probe: () => Promise<T | null>): Promise<T | null> {
    const deadline = hooksRuntime.now() + timeoutMs;
    for (;;) {
        const got = await probe();
        if (got !== null && got !== undefined) return got;
        const left = deadline - hooksRuntime.now();
        if (left <= 0) return null;
        await hooksRuntime.sleep(Math.min(POLL_INTERVAL_MS, left));
    }
}

// --------------------------------------------------------------------------
// Email
// --------------------------------------------------------------------------

export const EMAIL_EXTRACT_MODES = ['link', 'code', 'body', 'subject', 'none'] as const;
const DEFAULT_EMAIL_VAR: Record<string, string> = {
    link: 'email_link', code: 'email_code', body: 'email_body', subject: 'email_subject',
};

export interface EmailWaitResult { value: string; variableName: string | null; summary: string }

export async function waitForEmail(params: any, resolve: (v: any) => any, hooks: TestHooks | undefined): Promise<EmailWaitResult> {
    const extract = String(params?.extract || 'link').toLowerCase();
    if (!(EMAIL_EXTRACT_MODES as readonly string[]).includes(extract)) {
        throw new HookRequestError(`wait-for-email: extract must be one of ${EMAIL_EXTRACT_MODES.join(', ')}`);
    }
    const label = labelFor('inbox', String(resolve(params?.to ?? '') || ''), hooks);
    const timeoutMs = clampTimeout(params?.timeout);
    const request = {
        run_id: hooks!.run_id, test_case_id: hooks!.test_case_id, row_index: hooks!.row_index ?? null,
        label,
        subject: String(resolve(params?.subject ?? '') || ''),
        subject_regex: String(params?.subjectRegex ?? ''),
        from_contains: String(resolve(params?.from ?? '') || ''),
        extract,
        pattern: String(params?.pattern ?? ''),
    };
    let last: any = null;
    const hit = await pollUntil(timeoutMs, async () => {
        const res = await call('/api/internal/test-inbox/search', request);
        if (res) last = res;
        return res?.found ? res : null;
    });
    const address = last?.address || hooks?.inbox?.[label] || label;
    if (!hit) {
        let why = `no email to ${address} matched within ${Math.round(timeoutMs / 1000)}s`;
        if (last?.near_miss) {
            why += ` — "${last.near_miss.subject}" arrived but had no ${extract}${request.pattern ? ` matching /${request.pattern}/` : ''}`;
        } else if (last?.subjects?.length) {
            why += ` — received instead: ${last.subjects.map((s: string) => `"${s}"`).join(', ')}`;
        }
        throw new Error(`wait-for-email: ${why}`);
    }
    const variableName = extract === 'none' ? null : String(params?.variableName || DEFAULT_EMAIL_VAR[extract]);
    return {
        value: String(hit.value ?? ''),
        variableName,
        summary: `"${hit.message?.subject || ''}" from ${hit.message?.from || '?'}`,
    };
}

// --------------------------------------------------------------------------
// Webhooks
// --------------------------------------------------------------------------

export interface Capture {
    id: number;
    method: string;
    path: string;
    query: string;
    headers: Record<string, string>;
    content_type: string;
    body: string;
    received_at: string;
}

export function parseBody(c: Capture): any {
    const body = c.body ?? '';
    const looksJson = /json/i.test(c.content_type || '') || /^\s*[[{]/.test(body);
    if (looksJson) {
        try { return JSON.parse(body); } catch { /* fall through */ }
    }
    return body;
}

function sameValue(actual: any, expected: any): boolean {
    if (actual === undefined) return false;
    if (typeof expected === 'object' && expected !== null) {
        return JSON.stringify(actual) === JSON.stringify(expected);
    }
    return String(actual) === String(expected);
}

/** Does a capture satisfy the step's method / path / where conditions? */
export function captureMatches(c: Capture, cond: { method?: string; path?: string; where?: Record<string, any> }): boolean {
    if (cond.method && c.method.toUpperCase() !== cond.method.toUpperCase()) return false;
    if (cond.path && !(c.path || '').includes(cond.path)) return false;
    const where = cond.where && typeof cond.where === 'object' ? cond.where : null;
    if (where && Object.keys(where).length) {
        const body = parseBody(c);
        if (typeof body !== 'object' || body === null) return false;
        for (const [path, expected] of Object.entries(where)) {
            if (!sameValue(jsonPath(body, path), expected)) return false;
        }
    }
    return true;
}

export interface WebhookWaitResult { value: string; variableName: string; capture: Capture }

export async function waitForWebhook(params: any, resolve: (v: any) => any, hooks: TestHooks | undefined): Promise<WebhookWaitResult> {
    const label = labelFor('webhook', String(resolve(params?.url ?? '') || ''), hooks);
    const timeoutMs = clampTimeout(params?.timeout);
    let where = params?.where;
    if (typeof where === 'string' && where.trim()) {
        try { where = JSON.parse(where); } catch {
            throw new HookRequestError('wait-for-webhook: "where" must be a JSON object, e.g. {"event": "paid"}');
        }
    }
    const cond = {
        method: params?.method ? String(params.method) : undefined,
        path: params?.path ? String(resolve(params.path)) : undefined,
        where: where ? resolve(where) : undefined,
    };
    const request = {
        run_id: hooks!.run_id, test_case_id: hooks!.test_case_id,
        row_index: hooks!.row_index ?? null, label,
    };
    let seen: Capture[] = [];
    const hit = await pollUntil(timeoutMs, async () => {
        const res = await call('/api/internal/webhook-captures/search', request);
        if (!res) return null;
        seen = res.captures || [];
        // Newest first from the backend; the newest match wins.
        return seen.find(c => captureMatches(c, cond)) || null;
    });
    if (!hit) {
        let why = `no request to ${hooks?.webhook?.[label] || label} matched within ${Math.round(timeoutMs / 1000)}s`;
        if (seen.length) {
            const last = seen[0];
            // Captures are stored redacted, but a third-party payload in a
            // failure message is still worth a second, stricter pass.
            why += ` — ${seen.length} other request(s) arrived; newest: ${last.method} ${last.path || '/'} ${redactMessageText(String(last.body || '').slice(0, 200))}`;
        }
        throw new Error(`wait-for-webhook: ${why}`);
    }
    const body = parseBody(hit);
    let value: any;
    let variableName: string;
    if (params?.extract) {
        value = jsonPath(body, String(params.extract));
        variableName = String(params?.variableName || 'webhook_value');
        if (value === undefined) {
            throw new Error(`wait-for-webhook: matched a ${hit.method} request but its body has no "${params.extract}"`);
        }
    } else {
        value = body;
        variableName = String(params?.variableName || 'webhook_body');
    }
    const asString = typeof value === 'object' && value !== null ? JSON.stringify(value) : String(value);
    if (params?.equals !== undefined && params?.equals !== null && params?.equals !== '') {
        const expected = String(resolve(params.equals));
        if (asString !== expected) {
            throw new Error(`wait-for-webhook: ${params.extract || 'body'} was "${redactMessageText(asString.slice(0, 200))}", expected "${expected}"`);
        }
    }
    return { value: asString, variableName, capture: hit };
}

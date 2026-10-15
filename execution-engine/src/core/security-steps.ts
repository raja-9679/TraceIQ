/**
 * Security assertion steps (WP4), usable in any web test:
 *
 *   expect-header         a response header is present / absent / equals / matches
 *   expect-cookie-flags   a named cookie carries Secure / HttpOnly / SameSite=…
 *   expect-blocked-after  repeating a request gets throttled/locked by attempt N
 *   expect-session-dead   after logout, the pre-logout session is refused
 *
 * Captured header values are redacted before they reach the step result —
 * a `Set-Cookie` or `Authorization` echo is exactly what these steps look at.
 * Cookie VALUES are never reported at all, only their flags.
 *
 * `expect-blocked-after` deliberately hammers an endpoint (usually a login),
 * so it is gated like an active scan: the host must be on the project's
 * security allowlist (`security_allowlist`, injected at dispatch only when
 * security testing is enabled) and the step must carry `authorized: true`.
 */
import { request as pwRequest } from 'playwright';
import { redactHeaders, policyFromDataPolicy } from './redact';

const DEFAULT_BLOCK_STATUSES = [429, 423, 403];
const DEFAULT_DEAD_STATUSES = [401, 403, 440];
const DEFAULT_LOGIN_PATTERN = '/login|sign-?in|/auth|/session';
const MAX_ATTEMPTS = 50;
const TIMEOUT_MS = 30000;

export const SECURITY_STEP_TYPES: readonly string[] = [
    'expect-header', 'expect-cookie-flags', 'expect-blocked-after', 'expect-session-dead',
];

type Resolve = (v: any) => any;

function hostAllowed(url: string, allowed: string[] | undefined): boolean {
    let host = '';
    try { host = new URL(url).hostname.toLowerCase(); } catch { return false; }
    return !!host && (allowed || []).some(d => {
        const dom = String(d || '').trim().toLowerCase();
        return !!dom && (host === dom || host.endsWith('.' + dom));
    });
}

function absolutize(page: any, url: string, globalSettings: any): string {
    if (url && url.startsWith('/')) {
        const base = globalSettings?.environment?.base_url;
        if (base) return String(base).replace(/\/$/, '') + url;
        try { return new URL(url, page.url()).toString(); } catch { /* fall through */ }
    }
    return url;
}

function statusList(raw: any, fallback: number[]): number[] {
    if (Array.isArray(raw)) return raw.map(Number).filter(Number.isFinite);
    if (typeof raw === 'string' && raw.trim()) {
        return raw.split(',').map(s => Number(s.trim())).filter(Number.isFinite);
    }
    return fallback;
}

function headerValue(headers: Record<string, string>, name: string): string | undefined {
    const want = name.toLowerCase();
    for (const [k, v] of Object.entries(headers || {})) {
        if (k.toLowerCase() === want) return v;
    }
    return undefined;
}

function redactedValue(name: string, value: string | undefined, globalSettings: any): string | undefined {
    if (value === undefined) return undefined;
    return redactHeaders({ [name]: value }, policyFromDataPolicy(globalSettings?.data_policy))[name];
}

function fail(message: string, stepResult: any): never {
    const err = new Error(message);
    (err as any).stepResult = stepResult;
    throw err;
}

// --------------------------------------------------------------------------

async function expectHeader(page: any, step: any, globalSettings: any, resolve: Resolve) {
    const p = step.params || {};
    const name = String(resolve(p.name || step.selector || '')).trim();
    if (!name) throw new Error('expect-header: params.name (the header) is required');
    const mode = String(p.mode || 'present');
    const expected = resolve(p.value ?? step.value);
    let url = resolve(p.url) || page.url();
    url = absolutize(page, url, globalSettings);
    if (!/^https?:\/\//i.test(url || '')) throw new Error(`expect-header: no http(s) page to check (${url})`);

    const resp = await page.request.fetch(url, { method: 'GET', timeout: TIMEOUT_MS, failOnStatusCode: false });
    const actual = headerValue(resp.headers(), name);
    const result: any = {
        type: 'expect-header', url, header: name, mode, status: resp.status(),
        present: actual !== undefined, value: redactedValue(name, actual, globalSettings),
    };
    switch (mode) {
        case 'present':
            if (actual === undefined) fail(`Expected header ${name} to be present on ${url}`, result);
            break;
        case 'absent':
            if (actual !== undefined) fail(`Expected header ${name} to be absent on ${url}`, result);
            break;
        case 'equals':
            if (actual === undefined || actual.trim() !== String(expected ?? '').trim()) {
                fail(`Expected header ${name} to equal "${expected}" but got ${actual === undefined ? 'nothing' : `"${result.value}"`}`, result);
            }
            break;
        case 'regex': {
            let re: RegExp;
            try { re = new RegExp(String(expected ?? ''), 'i'); } catch (e: any) {
                throw new Error(`expect-header: invalid regex ${expected}: ${e.message}`);
            }
            if (actual === undefined || !re.test(actual)) {
                fail(`Expected header ${name} to match /${expected}/i but got ${actual === undefined ? 'nothing' : `"${result.value}"`}`, result);
            }
            break;
        }
        default:
            throw new Error(`expect-header: unknown mode ${mode} (present|absent|equals|regex)`);
    }
    return result;
}

async function expectCookieFlags(page: any, step: any, globalSettings: any, resolve: Resolve) {
    const p = step.params || {};
    const name = String(resolve(p.name || step.selector || '')).trim();
    if (!name) throw new Error('expect-cookie-flags: params.name (the cookie) is required');
    const url = p.url ? absolutize(page, resolve(p.url), globalSettings) : null;
    if (url) {
        // Shares the page's cookie jar, so Set-Cookie on this response lands there.
        await page.request.fetch(url, { method: 'GET', timeout: TIMEOUT_MS, failOnStatusCode: false });
    }
    const cookies = (await page.context().cookies()).filter((c: any) => c.name === name);
    // Flags only — a cookie value is a credential by definition.
    const observed = cookies.map((c: any) => ({
        domain: c.domain, path: c.path, secure: !!c.secure, httpOnly: !!c.httpOnly, sameSite: c.sameSite,
    }));
    const result: any = { type: 'expect-cookie-flags', cookie: name, url, observed };
    if (!cookies.length) fail(`Expected cookie ${name} to be set`, result);

    const problems: string[] = [];
    for (const c of observed) {
        if (p.secure !== undefined && p.secure !== null && c.secure !== Boolean(p.secure)) {
            problems.push(`Secure=${c.secure}`);
        }
        if (p.httpOnly !== undefined && p.httpOnly !== null && c.httpOnly !== Boolean(p.httpOnly)) {
            problems.push(`HttpOnly=${c.httpOnly}`);
        }
        if (p.sameSite && String(c.sameSite).toLowerCase() !== String(p.sameSite).toLowerCase()) {
            problems.push(`SameSite=${c.sameSite}`);
        }
    }
    if (problems.length) {
        const want = [p.secure !== undefined ? `Secure=${!!p.secure}` : '',
            p.httpOnly !== undefined ? `HttpOnly=${!!p.httpOnly}` : '',
            p.sameSite ? `SameSite=${p.sameSite}` : ''].filter(Boolean).join(', ');
        fail(`Cookie ${name} flags: expected ${want}, got ${Array.from(new Set(problems)).join(', ')}`, result);
    }
    return result;
}

async function expectBlockedAfter(page: any, step: any, globalSettings: any, resolve: Resolve) {
    const p = step.params || {};
    const url = absolutize(page, resolve(p.url || step.value || ''), globalSettings);
    const method = String(p.method || 'POST').toUpperCase();
    const attempts = Math.max(1, Math.min(MAX_ATTEMPTS, parseInt(p.attempts ?? '10', 10) || 10));
    const blockStatuses = statusList(p.statuses, DEFAULT_BLOCK_STATUSES);
    const intervalMs = Math.max(0, Math.min(5000, parseInt(p.interval_ms ?? '0', 10) || 0));

    if (!/^https?:\/\//i.test(url || '')) throw new Error(`expect-blocked-after: ${url} is not an http(s) url`);
    if (!hostAllowed(url, globalSettings?.security_allowlist)) {
        throw new Error(`expect-blocked-after refused: the host of ${url} is not on this project's ` +
            'security allowlist (enable security testing and allowlist it in Security settings)');
    }
    if (p.authorized !== true) {
        throw new Error('expect-blocked-after refused: set params.authorized=true to attest you are ' +
            'authorized to send repeated requests to this target');
    }

    const headers = { ...(globalSettings?.headers || {}), ...(resolve(p.headers || {}) || {}) };
    const body = resolve(p.body);
    const statuses: number[] = [];
    for (let i = 1; i <= attempts; i++) {
        const resp = await page.request.fetch(url, {
            method, headers, data: body ?? undefined, maxRedirects: 0,
            timeout: TIMEOUT_MS, failOnStatusCode: false,
        });
        statuses.push(resp.status());
        if (blockStatuses.includes(resp.status())) {
            return { type: 'expect-blocked-after', url, method, attempts, blocked_at: i, statuses };
        }
        if (intervalMs) await new Promise(r => setTimeout(r, intervalMs));
    }
    fail(`Not blocked after ${attempts} ${method} requests to ${url} (expected one of ` +
        `${blockStatuses.join('/')}; got ${Array.from(new Set(statuses)).join('/')})`,
        { type: 'expect-blocked-after', url, method, attempts, blocked_at: null, statuses });
}

async function expectSessionDead(page: any, context: any, step: any, globalSettings: any, resolve: Resolve) {
    const p = step.params || {};
    const url = absolutize(page, resolve(p.url || step.value || ''), globalSettings);
    if (!/^https?:\/\//i.test(url || '')) throw new Error(`expect-session-dead: ${url} is not an http(s) url`);
    const deadStatuses = statusList(p.statuses, DEFAULT_DEAD_STATUSES);
    let loginRe: RegExp;
    try { loginRe = new RegExp(String(p.login_pattern || DEFAULT_LOGIN_PATTERN), 'i'); } catch (e: any) {
        throw new Error(`expect-session-dead: invalid login_pattern: ${e.message}`);
    }

    // Hold on to the session BEFORE logging out — the logout clears the
    // browser's jar, and the point is to replay what an attacker who stole
    // the cookies would still hold.
    const snapshot = await page.context().cookies();
    const replayHeaders = resolve(p.headers || {}) || {};

    if (p.logout_selector) {
        await context.locator(resolve(p.logout_selector)).first().click({ timeout: TIMEOUT_MS });
        await page.waitForLoadState('domcontentloaded', { timeout: TIMEOUT_MS }).catch(() => undefined);
    } else if (p.logout_url) {
        const logoutUrl = absolutize(page, resolve(p.logout_url), globalSettings);
        await page.request.fetch(logoutUrl, {
            method: String(p.logout_method || 'POST').toUpperCase(), timeout: TIMEOUT_MS, failOnStatusCode: false,
        });
    } else {
        throw new Error('expect-session-dead: params.logout_url or params.logout_selector is required ' +
            '(the step logs out itself so it can keep the pre-logout session)');
    }

    const replay = await pwRequest.newContext({
        storageState: { cookies: snapshot, origins: [] },
        extraHTTPHeaders: replayHeaders,
    });
    try {
        const resp = await replay.fetch(url, { method: 'GET', maxRedirects: 0, timeout: TIMEOUT_MS, failOnStatusCode: false });
        const status = resp.status();
        const location = headerValue(resp.headers(), 'location') || '';
        const result: any = {
            type: 'expect-session-dead', url, status, replayed_cookies: snapshot.length,
            redirect: location ? redactedValue('location', location, globalSettings) : undefined,
        };
        if (deadStatuses.includes(status)) return { ...result, verdict: 'refused' };
        if (status >= 300 && status < 400 && loginRe.test(location)) return { ...result, verdict: 'redirected-to-login' };
        fail(`The pre-logout session is still accepted: ${url} returned ${status}` +
            (location ? ` (redirect to ${result.redirect})` : ''), result);
    } finally {
        await replay.dispose().catch(() => undefined);
    }
}

export async function executeSecurityStep(
    page: any, context: any, step: any, globalSettings: any, resolve: Resolve,
): Promise<any> {
    switch (step.type) {
        case 'expect-header': return expectHeader(page, step, globalSettings, resolve);
        case 'expect-cookie-flags': return expectCookieFlags(page, step, globalSettings, resolve);
        case 'expect-blocked-after': return expectBlockedAfter(page, step, globalSettings, resolve);
        case 'expect-session-dead': return expectSessionDead(page, context, step, globalSettings, resolve);
        default: throw new Error(`not a security step: ${step.type}`);
    }
}

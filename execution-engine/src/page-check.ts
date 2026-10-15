/**
 * Monitored-page check (`job_type: 'page_check'`, WP9).
 *
 * One headless load of a payment/critical page, exactly as a visitor's
 * browser would make it: certificate verification on, no request
 * interception, nothing injected. The worker only OBSERVES and parks the
 * observation under `page_check:result:{page_id}`; the backend
 * (app/tasks/estate_tasks.py) diffs it against the script inventory and the
 * baselines, so one rule set decides what counts as a change.
 *
 * Recorded (PCI DSS 4.0 6.4.3 / 11.6.1):
 *  - every script: external src + the SHA-384 of the body the browser
 *    actually received; inline scripts as a SHA-384 only (an inline config
 *    block can hold a token, and the hash is all the inventory needs);
 *    scripts loaded dynamically (not in document.scripts) are included too;
 *  - the document's security headers (CSP, HSTS, …) — never cookies;
 *  - when content tracking is on: the visible text and the innerText of the
 *    configured selectors, redacted.
 *
 * The host is re-checked against the job's allowlist here (defence in depth:
 * a crafted stream entry should not be able to aim the worker elsewhere).
 */
import { Browser } from 'playwright';
import { createHash } from 'crypto';
import { hostAllowed } from './access-check';
import { redactHeaders, redactText, policyFromDataPolicy } from './core/redact';

export const RESULT_KEY_PREFIX = 'page_check:result:';
const LOAD_TIMEOUT_MS = 45000;
const TEXT_CHARS = 200_000;
const ELEMENT_CHARS = 5_000;
const MAX_SCRIPTS = 500;

export const SECURITY_HEADERS = [
    'content-security-policy', 'content-security-policy-report-only',
    'strict-transport-security', 'x-frame-options', 'x-content-type-options',
    'referrer-policy', 'permissions-policy',
];

export interface PageCheckJob {
    job_id: string;
    page_id: number;
    result_key: string;
    url: string;
    track_content?: boolean;
    element_selectors?: string[];
    allowed_domains: string[];
    data_policy?: any;
}

export interface ScriptObservation {
    src?: string | null;
    inline_sha384?: string | null;
    integrity?: string | null;
    type?: string | null;
    dynamic?: boolean;
}

export interface PageObservation {
    url: string;
    final_url?: string;
    status?: number;
    headers?: Record<string, string>;
    scripts?: ScriptObservation[];
    external_hashes?: Record<string, string>;
    text?: string;
    elements?: Record<string, string | null>;
    error?: string;
}

export function sri384(data: Buffer | string): string {
    return 'sha384-' + createHash('sha384').update(data).digest('base64');
}

/** Why this job must not run, or null. */
export function refusalReason(job: PageCheckJob): string | null {
    if (!/^https?:\/\//i.test(job.url || '')) return `${job.url} is not an http(s) url`;
    if (!hostAllowed(job.url, job.allowed_domains)) return `host of ${job.url} is not on the security allowlist`;
    return null;
}

/** A stream entry may only write its own result key — never an arbitrary Redis key. */
export function safeResultKey(job: PageCheckJob): string | null {
    const key = String(job.result_key || '');
    return key === `${RESULT_KEY_PREFIX}${Number(job.page_id)}` ? key : null;
}

export function securityHeaders(headers: Record<string, string> | undefined): Record<string, string> {
    const out: Record<string, string> = {};
    for (const [k, v] of Object.entries(headers || {})) {
        if (SECURITY_HEADERS.includes(k.toLowerCase())) out[k.toLowerCase()] = v;
    }
    return out;
}

/**
 * Merge document.scripts with the script responses seen on the wire. A script
 * fetched by code (import(), injected then removed) never shows up in
 * document.scripts — exactly the skimmer pattern 11.6.1 is about.
 */
export function mergeScripts(domScripts: ScriptObservation[], networkUrls: string[]): ScriptObservation[] {
    const out = domScripts.filter(s => s.src || s.inline_sha384).slice(0, MAX_SCRIPTS);
    const seen = new Set(out.map(s => s.src).filter(Boolean) as string[]);
    for (const url of networkUrls) {
        if (out.length >= MAX_SCRIPTS) break;
        if (!seen.has(url) && /^https?:/i.test(url)) {
            seen.add(url);
            out.push({ src: url, dynamic: true });
        }
    }
    return out;
}

/** Load the page in a fresh context and observe. */
export async function observePage(browser: Browser, job: PageCheckJob): Promise<PageObservation> {
    const context = await browser.newContext();
    const hashes: Record<string, string> = {};
    const networkScripts: string[] = [];
    const pending: Promise<void>[] = [];
    try {
        const page = await context.newPage();
        page.on('response', resp => {
            if (resp.request().resourceType() !== 'script') return;
            const url = resp.url();
            networkScripts.push(url);
            pending.push(resp.body().then(b => { hashes[url] = sri384(b); }).catch(() => undefined));
        });
        const resp = await page.goto(job.url, { waitUntil: 'load', timeout: LOAD_TIMEOUT_MS });
        // Tag managers inject late; give them a moment, but a page that never
        // goes idle is still observed.
        await page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => undefined);
        await Promise.all(pending);

        const raw: Array<{ src: string | null; inline: string | null; integrity: string | null; type: string | null }> =
            await page.evaluate(() => Array.from(document.scripts).map(s => ({
                src: s.getAttribute('src') ? s.src : null,
                inline: s.getAttribute('src') ? null : (s.textContent || ''),
                integrity: s.getAttribute('integrity'),
                type: s.getAttribute('type'),
            })));
        const dom: ScriptObservation[] = raw.map(s => ({
            src: s.src,
            // Hash exactly what the backend would hash; whitespace-only blocks are noise.
            inline_sha384: s.inline !== null && s.inline.trim() ? sri384(Buffer.from(s.inline, 'utf8')) : null,
            integrity: s.integrity,
            type: s.type,
        }));
        const obs: PageObservation = {
            url: job.url,
            final_url: page.url(),
            status: resp?.status(),
            headers: securityHeaders(resp ? await resp.allHeaders() : {}),
            scripts: mergeScripts(dom, networkScripts),
            external_hashes: hashes,
        };
        if (job.track_content) {
            obs.text = String(await page.evaluate(() => document.body ? document.body.innerText : '')).slice(0, TEXT_CHARS);
            const elements: Record<string, string | null> = {};
            for (const sel of (job.element_selectors || []).slice(0, 50)) {
                try {
                    const loc = page.locator(sel).first();
                    elements[sel] = (await loc.count()) ? String(await loc.innerText({ timeout: 2000 })).slice(0, ELEMENT_CHARS) : null;
                } catch {
                    elements[sel] = null;   // invalid selector reads as "not found"
                }
            }
            obs.elements = elements;
        }
        return obs;
    } finally {
        await context.close().catch(() => undefined);
    }
}

/** Redact what could carry a secret: script URLs (keys in query strings), headers, text. */
export function redactPageObservation(obs: PageObservation, dataPolicy?: any): PageObservation {
    const policy = policyFromDataPolicy(dataPolicy);
    const out: PageObservation = { ...obs };
    if (out.headers) out.headers = redactHeaders(out.headers, policy);
    if (out.scripts) out.scripts = out.scripts.map(s => (s.src ? { ...s, src: redactText(s.src, policy) } : s));
    if (out.external_hashes) {
        const h: Record<string, string> = {};
        for (const [k, v] of Object.entries(out.external_hashes)) h[redactText(k, policy)] = v;
        out.external_hashes = h;
    }
    if (typeof out.text === 'string') out.text = redactText(out.text, policy);
    if (out.elements) {
        const e: Record<string, string | null> = {};
        for (const [k, v] of Object.entries(out.elements)) e[k] = typeof v === 'string' ? redactText(v, policy) : v;
        out.elements = e;
    }
    if (out.error) out.error = redactText(out.error, policy);
    return out;
}

/** Run a job and return the redacted observation to park under result_key. */
export async function executePageCheckJob(
    job: PageCheckJob, startBrowser: () => Promise<Browser>,
): Promise<PageObservation> {
    const refused = refusalReason(job);
    if (refused) return { url: job.url, error: refused };
    let obs: PageObservation;
    try {
        const browser = await startBrowser();
        obs = await observePage(browser, job);
    } catch (e: any) {
        obs = { url: job.url, error: String(e?.message || e).slice(0, 300) };
    }
    return redactPageObservation(obs, job.data_policy);
}

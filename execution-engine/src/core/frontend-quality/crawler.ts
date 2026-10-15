/**
 * Broken link & asset crawler (WP10), `job_type=site_crawl`.
 *
 * Breadth-first from the start URL over the crawl hosts the backend allowed
 * (start origin + validated extras), rendering each page in Playwright so
 * script-inserted links count, and collecting every <a href>, <img src>,
 * <script src>, <link href> and CSS url() reference. Each distinct target is
 * checked once from Node (HEAD, falling back to GET; redirects followed by
 * hand so the chain and loops are visible) and reported per page as:
 *
 *   broken          4xx / 5xx final status
 *   network         DNS / TLS / connection / timeout failure
 *   slow            answered, but slower than slow_threshold_ms
 *   redirect_loop   a URL repeats in its own redirect chain (or > 10 hops)
 *   redirect_chain  3+ hops before the final answer
 *   mixed_content   an http:// subresource on an https:// page
 *   not_checked     the target (or a redirect hop) is a private, loopback,
 *                   link-local or metadata address — see "Targets" below
 *
 * Targets: every URL here comes from page content the site controls, so
 * each request — every redirect hop, and the page loads' subresources — is
 * checked first (egress-guard.ts `targetVerdict`: literal IPs in all their
 * encodings, and names by resolving them). Link-local / metadata / reserved
 * addresses are never requested; private and loopback ones only on the
 * crawl's own hosts or when the instance allows private targets
 * (`allow_private`, from ALLOW_PRIVATE_NETWORK_TARGETS). In production the
 * link checks also re-check the address at connect time (`guardedFetch`), so
 * DNS rebinding between check and connect is refused too. Worker-side
 * ceilings (pages, depth, duration, total checks, bytes read) apply whatever
 * the job says.
 *
 * Politeness: robots.txt of each crawl host is honoured unless disabled, and
 * requests to one host are spaced by max(rate_limit_ms, Crawl-delay). Links
 * to other hosts are checked (status only, body discarded) when
 * check_external is on — never crawled.
 *
 * The page loader and fetch are injected so tests can drive it against a
 * local server.
 */
import type { Browser } from 'playwright';
import * as http from 'http';
import * as https from 'https';
import { CRAWLER_UA_TOKEN, Robots, isAllowed, parseRobots } from './robots';
import { Resolver, Verdict, canonicalHost, guardedLookup, targetVerdict } from '../egress-guard';

export type LinkElement = 'a' | 'img' | 'script' | 'link' | 'css' | 'page';

export interface CrawlConfig {
    start_url: string;
    crawl_hosts: string[];
    max_depth: number;
    max_pages: number;
    respect_robots: boolean;
    rate_limit_ms: number;
    slow_threshold_ms: number;
    check_external: boolean;
    max_duration_ms?: number;
    // Instance ALLOW_PRIVATE_NETWORK_TARGETS, resolved by the backend.
    allow_private?: boolean;
}

export interface FoundLink { url: string; element: LinkElement }
export interface LoadedPage {
    status: number | null;
    finalUrl: string;
    isHtml: boolean;
    loadMs: number;
    links: FoundLink[];
    error?: string;
    // The page answered a redirect that the loader did NOT let the browser
    // follow (see installRequestGuard); the crawler queues the target itself.
    redirectTo?: string;
}
export interface PageLoader {
    load(url: string): Promise<LoadedPage>;
    close?(): Promise<void>;
}

export interface CheckResult {
    url: string;
    status: number | null;
    durationMs: number;
    chain: { url: string; status: number | null }[];
    loop: boolean;
    error?: string;
    // Set when a hop was refused by the target guard (nothing was requested).
    refused?: string;
}

export interface CrawlIssue {
    page_url: string;
    target_url: string;
    element: LinkElement;
    category: 'broken' | 'network' | 'slow' | 'redirect_loop' | 'redirect_chain' | 'mixed_content' | 'not_checked';
    status_code?: number | null;
    duration_ms?: number;
    redirect_chain?: { url: string; status: number | null }[];
    detail?: string;
}

export interface CrawlReport {
    status: 'ok' | 'error';
    pages: { url: string; depth: number; status: number | null; load_ms: number; links: number; issues: number; error?: string }[];
    issues: CrawlIssue[];
    links_checked: number;
    robots: { respected: boolean; fetched: boolean; skipped: number; crawl_delay_ms: number | null };
    truncated: boolean;
    error?: string;
}

type FetchFn = (url: string, init?: any) => Promise<any>;

const MAX_REDIRECTS = 10;
const CHECK_TIMEOUT_MS = 15_000;
const MAX_ISSUES = 5000;
const MAX_LINKS_PER_PAGE = 1000;
const CHECK_CONCURRENCY = 4;
// Worker-side ceilings: the job's numbers are clamped to these whatever it
// says (a crafted or stale job must not pin a worker).
export const MAX_CRAWL_PAGES = 1000;
export const MAX_CRAWL_DEPTH = 10;
export const MAX_CRAWL_DURATION_MS = 15 * 60_000;
export const MAX_LINK_CHECKS = 20_000;
const ROBOTS_MAX_BYTES = 512 * 1024;
export const USER_AGENT = `Mozilla/5.0 (compatible; ${CRAWLER_UA_TOKEN}-linkcheck/1.0)`;

export function normaliseUrl(raw: string, base?: string): string | null {
    try {
        const u = new URL(raw, base);
        if (u.protocol !== 'http:' && u.protocol !== 'https:') return null;
        u.hash = '';
        return u.toString();
    } catch {
        return null;
    }
}

function hostOf(url: string): string {
    try { return new URL(url).hostname.toLowerCase(); } catch { return ''; }
}

/** Per-host request spacing. */
export class HostGate {
    private next = new Map<string, number>();
    constructor(private spacingMs: (host: string) => number, private now = () => Date.now(),
                private sleep = (ms: number) => new Promise(r => setTimeout(r, ms))) { }

    async wait(url: string): Promise<void> {
        const host = hostOf(url);
        const t = this.now();
        const at = Math.max(t, this.next.get(host) ?? 0);
        this.next.set(host, at + this.spacingMs(host));
        if (at > t) await this.sleep(at - t);
    }
}

export type TargetGuard = (url: string) => Promise<Verdict>;

export async function checkUrl(url: string, fetchImpl: FetchFn, gate: HostGate,
                               guard?: TargetGuard): Promise<CheckResult> {
    const chain: { url: string; status: number | null }[] = [];
    const seen = new Set<string>();
    const started = Date.now();
    let current = url;
    for (let hop = 0; hop <= MAX_REDIRECTS; hop++) {
        if (seen.has(current)) {
            return { url, status: chain[chain.length - 1]?.status ?? null, durationMs: Date.now() - started, chain, loop: true };
        }
        seen.add(current);
        if (guard) {
            const v = await guard(current);
            if (!v.allowed) {
                return { url, status: null, durationMs: Date.now() - started, chain, loop: false,
                    refused: v.reason };
            }
        }
        let res: any;
        try {
            await gate.wait(current);
            res = await timedFetch(fetchImpl, current, 'HEAD');
            // Servers that refuse HEAD answer 405/501 (some 403/404): ask again with GET.
            if ([403, 404, 405, 501].includes(res.status)) {
                await gate.wait(current);
                res = await timedFetch(fetchImpl, current, 'GET');
            }
        } catch (err: any) {
            chain.push({ url: current, status: null });
            return {
                url, status: null, durationMs: Date.now() - started, chain, loop: false,
                error: String(err?.cause?.code || err?.name === 'AbortError' && 'timeout' || err?.message || err).slice(0, 200),
            };
        }
        chain.push({ url: current, status: res.status });
        const location = res.status >= 300 && res.status < 400 ? res.headers?.get?.('location') : null;
        if (!location) {
            return { url, status: res.status, durationMs: Date.now() - started, chain, loop: false };
        }
        const next = normaliseUrl(location, current);
        if (!next) {
            return { url, status: res.status, durationMs: Date.now() - started, chain, loop: false,
                error: `redirect to unsupported location` };
        }
        current = next;
    }
    return { url, status: chain[chain.length - 1]?.status ?? null, durationMs: Date.now() - started, chain, loop: true };
}

async function timedFetch(fetchImpl: FetchFn, url: string, method: 'HEAD' | 'GET'): Promise<any> {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), CHECK_TIMEOUT_MS);
    try {
        const res = await fetchImpl(url, {
            method, redirect: 'manual', signal: ctrl.signal,
            headers: { 'user-agent': USER_AGENT, accept: '*/*' },
        });
        // Status only: the body is never read or kept.
        try { await res.body?.cancel?.(); } catch { /* already consumed */ }
        return res;
    } finally {
        clearTimeout(timer);
    }
}

export function classify(pageUrl: string, link: FoundLink, check: CheckResult, slowMs: number): CrawlIssue[] {
    const out: CrawlIssue[] = [];
    const base = { page_url: pageUrl, target_url: link.url, element: link.element };
    if (pageUrl.startsWith('https:') && link.url.startsWith('http:') && link.element !== 'a') {
        out.push({ ...base, category: 'mixed_content', detail: 'insecure subresource on an https page (browsers block or warn)' });
    }
    const chainOut = check.chain.length > 1 ? check.chain : undefined;
    if (check.refused) {
        out.push({ ...base, category: 'not_checked', redirect_chain: check.chain.length ? check.chain : undefined,
            detail: `not checked (private address): ${check.refused}`.slice(0, 300) });
        return out;
    }
    if (check.loop) {
        out.push({ ...base, category: 'redirect_loop', status_code: check.status, duration_ms: check.durationMs,
            redirect_chain: check.chain, detail: `redirect loop after ${check.chain.length} hop(s)` });
        return out;
    }
    if (check.status === null) {
        out.push({ ...base, category: 'network', duration_ms: check.durationMs, redirect_chain: chainOut,
            detail: check.error || 'request failed' });
        return out;
    }
    if (check.status >= 400) {
        out.push({ ...base, category: 'broken', status_code: check.status, duration_ms: check.durationMs,
            redirect_chain: chainOut });
        return out;
    }
    if (check.chain.length > 3) {
        out.push({ ...base, category: 'redirect_chain', status_code: check.status, duration_ms: check.durationMs,
            redirect_chain: check.chain, detail: `${check.chain.length - 1} redirects before the final answer` });
    }
    if (check.durationMs > slowMs) {
        out.push({ ...base, category: 'slow', status_code: check.status, duration_ms: check.durationMs,
            detail: `${check.durationMs} ms > ${slowMs} ms` });
    }
    return out;
}

export interface CrawlDeps {
    loader: PageLoader;
    fetchImpl: FetchFn;
    now?: () => number;
    resolver?: Resolver;
}

/** A response body as text, reading at most `max` bytes. */
async function readCapped(res: any, max: number): Promise<string> {
    const reader = res?.body?.getReader?.();
    if (!reader) return String(await res.text()).slice(0, max);
    const chunks: Uint8Array[] = [];
    let total = 0;
    try {
        while (total < max) {
            const { done, value } = await reader.read();
            if (done) break;
            chunks.push(value);
            total += value.length;
        }
    } finally {
        try { await reader.cancel(); } catch { /* closed */ }
    }
    return Buffer.concat(chunks.map(c => Buffer.from(c))).subarray(0, max).toString('utf8');
}

export async function crawlSite(config: CrawlConfig, deps: CrawlDeps): Promise<CrawlReport> {
    const now = deps.now || (() => Date.now());
    const started = now();
    const start = normaliseUrl(config.start_url);
    const report: CrawlReport = {
        status: 'ok', pages: [], issues: [], links_checked: 0,
        robots: { respected: !!config.respect_robots, fetched: false, skipped: 0, crawl_delay_ms: null },
        truncated: false,
    };
    if (!start) return { ...report, status: 'error', error: 'invalid start URL' };
    const crawlHosts = new Set((config.crawl_hosts || []).slice(0, 20).map(h => String(h).toLowerCase()));
    crawlHosts.add(hostOf(start));
    const maxPages = clamp(config.max_pages, 1, MAX_CRAWL_PAGES, 100);
    const maxDepth = clamp(config.max_depth, 0, MAX_CRAWL_DEPTH, 3);
    const rate = clamp(config.rate_limit_ms, 50, 10_000, 250);
    const slowMs = clamp(config.slow_threshold_ms, 100, 60_000, 3000);
    const deadline = started + clamp(config.max_duration_ms ?? MAX_CRAWL_DURATION_MS, 1000,
        MAX_CRAWL_DURATION_MS, MAX_CRAWL_DURATION_MS);
    const trusted = new Set(Array.from(crawlHosts).map(h => canonicalHost(h) || h));
    const guard: TargetGuard = (u) => targetVerdict(u, {
        allowPrivate: !!config.allow_private, trustedHosts: trusted, resolver: deps.resolver,
    });

    // robots.txt per crawl host, fetched once.
    const robots = new Map<string, Robots | null>();
    const robotsFor = async (url: string): Promise<Robots | null> => {
        if (!config.respect_robots) return null;
        const u = new URL(url);
        const key = u.origin;
        if (robots.has(key)) return robots.get(key)!;
        let parsed: Robots | null = null;
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), CHECK_TIMEOUT_MS);
        try {
            const res = await deps.fetchImpl(`${u.origin}/robots.txt`,
                { headers: { 'user-agent': USER_AGENT }, signal: ctrl.signal });
            if (res && res.status === 200) {
                const text = await readCapped(res, ROBOTS_MAX_BYTES);
                parsed = parseRobots(text);
                report.robots.fetched = true;
                if (parsed.crawlDelayMs !== null) {
                    report.robots.crawl_delay_ms = Math.max(report.robots.crawl_delay_ms ?? 0, parsed.crawlDelayMs);
                }
            }
        } catch { /* unreachable robots.txt = no rules */ } finally {
            clearTimeout(timer);
        }
        robots.set(key, parsed);
        return parsed;
    };
    const gate = new HostGate((host) => {
        const delay = crawlHosts.has(host) ? (report.robots.crawl_delay_ms ?? 0) : 0;
        return Math.max(rate, delay);
    }, now);
    const allowedByRobots = async (url: string): Promise<boolean> => {
        if (!crawlHosts.has(hostOf(url))) return true;
        const r = await robotsFor(url);
        const u = new URL(url);
        return isAllowed(r, u.pathname + u.search);
    };

    const checks = new Map<string, Promise<CheckResult>>();
    let active = 0;
    const waiters: (() => void)[] = [];
    const runLimited = async <T>(fn: () => Promise<T>): Promise<T> => {
        if (active >= CHECK_CONCURRENCY) await new Promise<void>(r => waiters.push(r));
        active++;
        try { return await fn(); } finally {
            active--;
            waiters.shift()?.();
        }
    };
    const check = (url: string): Promise<CheckResult> | null => {
        let p = checks.get(url);
        if (!p) {
            if (checks.size >= MAX_LINK_CHECKS) {
                report.truncated = true;
                return null;
            }
            p = runLimited(() => checkUrl(url, deps.fetchImpl, gate, guard));
            checks.set(url, p);
            report.links_checked++;
        }
        return p;
    };
    const addIssues = (items: CrawlIssue[]) => {
        for (const i of items) {
            if (report.issues.length >= MAX_ISSUES) { report.truncated = true; return; }
            report.issues.push(i);
        }
    };

    const queue: { url: string; depth: number }[] = [{ url: start, depth: 0 }];
    const queued = new Set<string>([start]);
    try {
        while (queue.length) {
            if (report.pages.length >= maxPages || now() > deadline) {
                report.truncated = true;
                break;
            }
            const { url, depth } = queue.shift()!;
            if (!(await allowedByRobots(url))) {
                report.robots.skipped++;
                continue;
            }
            // Queued pages are on crawl hosts, but a crawl host that resolves
            // to a metadata address is still never loaded.
            const pageVerdict = await guard(url);
            if (!pageVerdict.allowed) {
                addIssues([{ page_url: url, target_url: url, element: 'page', category: 'not_checked',
                    detail: `not checked (private address): ${pageVerdict.reason}`.slice(0, 300) }]);
                report.pages.push({ url, depth, status: null, load_ms: 0, links: 0, issues: 1, error: 'not loaded' });
                continue;
            }
            await gate.wait(url);
            let page: LoadedPage;
            try {
                page = await deps.loader.load(url);
            } catch (err: any) {
                page = { status: null, finalUrl: url, isHtml: false, loadMs: 0, links: [], error: String(err?.message || err).slice(0, 300) };
            }
            const pageIssues: CrawlIssue[] = [];
            if (depth === 0 && (page.status === null || page.status >= 400)) {
                pageIssues.push({ page_url: url, target_url: url, element: 'page',
                    category: page.status === null ? 'network' : 'broken', status_code: page.status,
                    detail: page.error || 'start page did not load' });
            }
            const links = dedupeLinks(page.links, page.finalUrl).slice(0, MAX_LINKS_PER_PAGE);
            const results = await Promise.all(links.map(async (link) => {
                const internal = crawlHosts.has(hostOf(link.url));
                if (!internal && !config.check_external) return { link, issues: classifyMixedOnly(page.finalUrl, link) };
                if (internal && !(await allowedByRobots(link.url))) {
                    report.robots.skipped++;
                    return { link, issues: classifyMixedOnly(page.finalUrl, link) };
                }
                const pending = check(link.url);
                if (!pending) return { link, issues: classifyMixedOnly(page.finalUrl, link) };
                return { link, issues: classify(page.finalUrl, link, await pending, slowMs) };
            }));
            for (const r of results) pageIssues.push(...r.issues);
            addIssues(pageIssues);
            report.pages.push({
                url, depth, status: page.status, load_ms: page.loadMs, links: links.length,
                issues: pageIssues.length, ...(page.error ? { error: page.error } : {}),
            });
            if (page.redirectTo) {
                // A redirect is not a level of depth: the target replaces this page.
                const next = normaliseUrl(page.redirectTo, url);
                if (next && !queued.has(next) && crawlHosts.has(hostOf(next))) {
                    queued.add(next);
                    queue.unshift({ url: next, depth });
                }
                continue;
            }
            if (depth >= maxDepth || !page.isHtml) continue;
            for (const link of links) {
                if (link.element !== 'a' || queued.has(link.url) || !crawlHosts.has(hostOf(link.url))) continue;
                queued.add(link.url);
                queue.push({ url: link.url, depth: depth + 1 });
            }
        }
    } finally {
        try { await deps.loader.close?.(); } catch { /* ignore */ }
    }
    return report;
}

function classifyMixedOnly(pageUrl: string, link: FoundLink): CrawlIssue[] {
    if (pageUrl.startsWith('https:') && link.url.startsWith('http:') && link.element !== 'a') {
        return [{ page_url: pageUrl, target_url: link.url, element: link.element, category: 'mixed_content',
            detail: 'insecure subresource on an https page (browsers block or warn)' }];
    }
    return [];
}

function dedupeLinks(links: FoundLink[], base: string): FoundLink[] {
    const seen = new Set<string>();
    const out: FoundLink[] = [];
    for (const l of links || []) {
        const url = normaliseUrl(l.url, base);
        if (!url) continue;
        const key = `${l.element} ${url}`;
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({ url, element: l.element });
    }
    return out;
}

function clamp(v: any, lo: number, hi: number, dflt: number): number {
    const n = Number(v);
    if (!Number.isFinite(n)) return dflt;
    return Math.max(lo, Math.min(hi, Math.round(n)));
}

/** Runs in the page: every link / asset reference, resolved to absolute URLs. */
function extractLinks(max: number) {
    const out: { url: string; element: string }[] = [];
    const push = (url: string | null | undefined, element: string) => {
        if (url && out.length < max) out.push({ url, element });
    };
    document.querySelectorAll('a[href]').forEach(a => push((a as HTMLAnchorElement).href, 'a'));
    document.querySelectorAll('img[src]').forEach(i => push((i as HTMLImageElement).src, 'img'));
    document.querySelectorAll('script[src]').forEach(s => push((s as HTMLScriptElement).src, 'script'));
    document.querySelectorAll('link[href]').forEach(l => {
        const rel = (l.getAttribute('rel') || '').toLowerCase();
        // Connection hints name an origin, not a resource; fetching "/" there
        // reports nothing useful.
        if (/\b(preconnect|dns-prefetch)\b/.test(rel)) return;
        push((l as HTMLLinkElement).href, 'link');
    });
    const cssUrls = (text: string, base: string) => {
        const re = /url\(([^)]*)\)/g;
        let m: RegExpExecArray | null;
        let n = 0;
        while ((m = re.exec(text)) && n++ < 500) {
            const raw = m[1].trim().replace(/^['"]|['"]$/g, '');
            if (!raw || raw.startsWith('data:') || raw.startsWith('#')) continue;
            try { push(new URL(raw, base).href, 'css'); } catch { /* bad url */ }
        }
    };
    for (const sheet of Array.from(document.styleSheets)) {
        let rules: CSSRuleList | null = null;
        try { rules = sheet.cssRules; } catch { continue; /* cross-origin sheet: reported as its <link> */ }
        const base = sheet.href || document.baseURI;
        for (const rule of Array.from(rules || [])) cssUrls(rule.cssText, base);
    }
    document.querySelectorAll('[style*="url("]').forEach(el => cssUrls(el.getAttribute('style') || '', document.baseURI));
    return out;
}

export function playwrightLoader(browser: Browser,
                                 opts: { timeoutMs?: number; guard?: TargetGuard } = {}): PageLoader {
    let ctxPromise: Promise<any> | null = null;
    const redirects = new Map<string, { status: number; location: string }>();
    const context = () => (ctxPromise ||= (async () => {
        const ctx = await browser.newContext({ userAgent: USER_AGENT });
        if (opts.guard) await installRequestGuard(ctx, opts.guard, redirects);
        return ctx;
    })());
    return {
        async load(url: string): Promise<LoadedPage> {
            const ctx = await context();
            const page = await ctx.newPage();
            const t0 = Date.now();
            try {
                const res = await page.goto(url, { waitUntil: 'load', timeout: opts.timeoutMs ?? 30_000 });
                const redirected = redirects.get(url);
                if (redirected) {
                    redirects.delete(url);
                    return { status: redirected.status, finalUrl: url, isHtml: false, loadMs: Date.now() - t0,
                        links: [], redirectTo: redirected.location };
                }
                const ct = res?.headers()['content-type'] || '';
                const isHtml = /html/i.test(ct) || (!ct && !!res);
                const links = isHtml ? await page.evaluate(extractLinks, MAX_LINKS_PER_PAGE) as FoundLink[] : [];
                return { status: res ? res.status() : null, finalUrl: page.url(), isHtml, loadMs: Date.now() - t0, links };
            } catch (err: any) {
                return { status: null, finalUrl: url, isHtml: false, loadMs: Date.now() - t0, links: [],
                    error: String(err?.message || err).split('\n')[0].slice(0, 300) };
            } finally {
                await page.close().catch(() => undefined);
            }
        },
        async close() {
            if (ctxPromise) await (await ctxPromise).close().catch(() => undefined);
        },
    };
}

/**
 * Every request a crawled page makes (subresources included) passes the
 * target guard, and so does every redirect hop. Playwright only routes the
 * FIRST request of a redirect chain — the browser follows a 3xx on its own —
 * so requests are fetched here with redirects off:
 *   - a subresource redirect is walked hop by hop (each hop checked) and the
 *     final response handed to the browser;
 *   - a page navigation is not followed at all: the loader reports the
 *     redirect and the crawler queues the target as its own page, which is
 *     loaded (and checked) like any other — so the page keeps its real URL.
 */
async function installRequestGuard(ctx: any, guard: TargetGuard,
                                   redirects: Map<string, { status: number; location: string }>): Promise<void> {
    const verdict = async (u: string): Promise<Verdict> => {
        try { return await guard(u); } catch (err: any) { return { allowed: false, reason: String(err?.message || err) }; }
    };
    await ctx.route('**/*', async (route: any) => {
        const request = route.request();
        let url: string = request.url();
        if (!(await verdict(url)).allowed) {
            await route.abort('blockedbyclient').catch(() => undefined);
            return;
        }
        if (!/^https?:/i.test(url)) {
            await route.continue().catch(() => undefined);
            return;
        }
        try {
            let response = await route.fetch({ maxRedirects: 0, timeout: CHECK_TIMEOUT_MS * 2 });
            const isNav = request.isNavigationRequest() && request.frame() === request.frame()?.page?.()?.mainFrame?.();
            for (let hop = 0; response.status() >= 300 && response.status() < 400; hop++) {
                const location = response.headers()['location'];
                const next = location ? normaliseUrl(location, url) : null;
                if (!next) break;
                if (isNav) {
                    redirects.set(request.url(), { status: response.status(), location: next });
                    await route.fulfill({ status: 200, contentType: 'text/html', body: '' });
                    return;
                }
                if (hop >= MAX_REDIRECTS || !(await verdict(next)).allowed) {
                    await route.abort('blockedbyclient').catch(() => undefined);
                    return;
                }
                url = next;
                response = await route.fetch({ url, maxRedirects: 0, timeout: CHECK_TIMEOUT_MS * 2 });
            }
            await route.fulfill({ response });
        } catch {
            await route.abort('failed').catch(() => undefined);
        }
    });
}

/**
 * fetch-compatible link checker for production: http(s).request with a
 * connect-time address check (guardedLookup) on top of the per-hop verdict.
 * Status + headers only; the body is never read except through `text()`,
 * which is capped.
 */
export function guardedFetch(opts: { allowPrivate: boolean; trustedHosts: Set<string> }): FetchFn {
    return (rawUrl: string, init: any = {}) => new Promise((resolve, reject) => {
        let url: URL;
        try { url = new URL(rawUrl); } catch (e) { reject(e); return; }
        const mod = url.protocol === 'https:' ? https : http;
        const req = mod.request(url, {
            method: init.method || 'GET',
            headers: init.headers || {},
            signal: init.signal,
            lookup: guardedLookup({ ...opts, host: canonicalHost(url.hostname) || url.hostname }) as any,
        }, (res) => {
            const headers = { get: (name: string) => {
                const v = res.headers[name.toLowerCase()];
                return Array.isArray(v) ? v[0] : (v ?? null);
            } };
            resolve({
                status: res.statusCode || 0,
                headers,
                body: { cancel: async () => { res.destroy(); } },
                text: (max = ROBOTS_MAX_BYTES) => new Promise<string>((ok) => {
                    const chunks: Buffer[] = [];
                    let total = 0;
                    res.on('data', (c: Buffer) => {
                        chunks.push(c);
                        total += c.length;
                        if (total >= max) res.destroy();
                    });
                    const done = () => ok(Buffer.concat(chunks).subarray(0, max).toString('utf8'));
                    res.on('end', done);
                    res.on('close', done);
                }),
            });
        });
        req.on('error', reject);
        req.end();
    });
}

export const RESULT_KEY_PREFIX = 'site_crawl:result:';

/** The Redis key a crawl job may write: derived from its run id, never trusted as sent. */
export function safeResultKey(job: any): string | null {
    const id = Number(job?.crawl_run_id);
    if (!Number.isInteger(id) || id <= 0) return null;
    const key = `${RESULT_KEY_PREFIX}${id}`;
    return job?.result_key === key ? key : null;
}

/** The target guard a crawl's page loads use (same rules as its link checks). */
export function crawlTargetGuard(config: CrawlConfig, resolver?: Resolver) {
    const hosts = new Set<string>([...(config.crawl_hosts || []), hostOf(normaliseUrl(config.start_url) || '')]
        .map(h => canonicalHost(String(h)) || '').filter(Boolean));
    const allowPrivate = !!config.allow_private;
    const guard: TargetGuard = (u) => targetVerdict(u, { allowPrivate, trustedHosts: hosts, resolver });
    return { guard, hosts, allowPrivate };
}

export async function executeCrawlJob(job: any, browser: Browser): Promise<CrawlReport> {
    const config = job?.config as CrawlConfig;
    if (!config?.start_url) {
        return { status: 'error', pages: [], issues: [], links_checked: 0, truncated: false,
            robots: { respected: false, fetched: false, skipped: 0, crawl_delay_ms: null }, error: 'job has no config' };
    }
    const { guard, hosts, allowPrivate } = crawlTargetGuard(config);
    return crawlSite(config, {
        loader: playwrightLoader(browser, { guard }),
        fetchImpl: guardedFetch({ allowPrivate, trustedHosts: hosts }),
    });
}

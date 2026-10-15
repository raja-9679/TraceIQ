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
import { CRAWLER_UA_TOKEN, Robots, isAllowed, parseRobots } from './robots';

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
}

export interface FoundLink { url: string; element: LinkElement }
export interface LoadedPage {
    status: number | null;
    finalUrl: string;
    isHtml: boolean;
    loadMs: number;
    links: FoundLink[];
    error?: string;
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
}

export interface CrawlIssue {
    page_url: string;
    target_url: string;
    element: LinkElement;
    category: 'broken' | 'network' | 'slow' | 'redirect_loop' | 'redirect_chain' | 'mixed_content';
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

export async function checkUrl(url: string, fetchImpl: FetchFn, gate: HostGate): Promise<CheckResult> {
    const chain: { url: string; status: number | null }[] = [];
    const seen = new Set<string>();
    const started = Date.now();
    let current = url;
    for (let hop = 0; hop <= MAX_REDIRECTS; hop++) {
        if (seen.has(current)) {
            return { url, status: chain[chain.length - 1]?.status ?? null, durationMs: Date.now() - started, chain, loop: true };
        }
        seen.add(current);
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
    const crawlHosts = new Set((config.crawl_hosts || []).map(h => String(h).toLowerCase()));
    crawlHosts.add(hostOf(start));
    const maxPages = clamp(config.max_pages, 1, 1000, 100);
    const maxDepth = clamp(config.max_depth, 0, 10, 3);
    const rate = clamp(config.rate_limit_ms, 50, 10_000, 250);
    const slowMs = clamp(config.slow_threshold_ms, 100, 60_000, 3000);
    const deadline = started + clamp(config.max_duration_ms ?? 15 * 60_000, 1000, 60 * 60_000, 15 * 60_000);

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
                const text = String(await res.text()).slice(0, 512 * 1024);
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
    const check = (url: string): Promise<CheckResult> => {
        let p = checks.get(url);
        if (!p) {
            p = runLimited(() => checkUrl(url, deps.fetchImpl, gate));
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
                return { link, issues: classify(page.finalUrl, link, await check(link.url), slowMs) };
            }));
            for (const r of results) pageIssues.push(...r.issues);
            addIssues(pageIssues);
            report.pages.push({
                url, depth, status: page.status, load_ms: page.loadMs, links: links.length,
                issues: pageIssues.length, ...(page.error ? { error: page.error } : {}),
            });
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

export function playwrightLoader(browser: Browser, opts: { timeoutMs?: number } = {}): PageLoader {
    let ctxPromise: Promise<any> | null = null;
    const context = () => (ctxPromise ||= browser.newContext({ userAgent: USER_AGENT }));
    return {
        async load(url: string): Promise<LoadedPage> {
            const ctx = await context();
            const page = await ctx.newPage();
            const t0 = Date.now();
            try {
                const res = await page.goto(url, { waitUntil: 'load', timeout: opts.timeoutMs ?? 30_000 });
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

export const RESULT_KEY_PREFIX = 'site_crawl:result:';

/** The Redis key a crawl job may write: derived from its run id, never trusted as sent. */
export function safeResultKey(job: any): string | null {
    const id = Number(job?.crawl_run_id);
    if (!Number.isInteger(id) || id <= 0) return null;
    const key = `${RESULT_KEY_PREFIX}${id}`;
    return job?.result_key === key ? key : null;
}

export async function executeCrawlJob(job: any, browser: Browser): Promise<CrawlReport> {
    const config = job?.config as CrawlConfig;
    if (!config?.start_url) {
        return { status: 'error', pages: [], issues: [], links_checked: 0, truncated: false,
            robots: { respected: false, fetched: false, skipped: 0, crawl_delay_ms: null }, error: 'job has no config' };
    }
    return crawlSite(config, { loader: playwrightLoader(browser), fetchImpl: (globalThis as any).fetch });
}

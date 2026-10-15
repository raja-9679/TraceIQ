/**
 * `check-page-budget` (WP10): Lighthouse-style measurements of the current
 * page, compared against per-metric budgets.
 *
 * Lighthouse itself is deliberately NOT a dependency: since v10 it is
 * ESM-only (this worker is CommonJS), it brings its own Chrome launcher and
 * needs a remote-debugging port the worker's Playwright browsers don't open,
 * and it adds tens of MB to every worker image for one step type. The subset
 * that matters for a budget is measurable with Playwright directly:
 *
 *   Web vitals  LCP, CLS, FCP, TTFB from buffered PerformanceObserver /
 *               Navigation Timing; INP approximated as the longest Event
 *               Timing entry (null when nothing was interacted with).
 *   Weight      page / image / script bytes and request count from Resource
 *               Timing (cross-origin entries without Timing-Allow-Origin report
 *               0 bytes — counted separately as `unmeasured`).
 *   SEO         title / meta description length, canonical, robots noindex,
 *               h1 count, img alt coverage, <html lang>, hreflang validity,
 *               JSON-LD parse errors, viewport meta.
 *
 * Fact collection runs in the page; evaluation is pure so it can be unit-tested.
 */
import type { Page } from 'playwright';

export const METRIC_KEYS = [
    'lcp_ms', 'cls', 'inp_ms', 'fcp_ms', 'ttfb_ms', 'dom_content_loaded_ms', 'load_ms',
    'page_weight_kb', 'request_count', 'image_weight_kb', 'largest_image_kb',
    'script_weight_kb', 'third_party_requests',
] as const;
export type MetricKey = typeof METRIC_KEYS[number];
export type Metrics = Partial<Record<MetricKey, number | null>> & { unmeasured_requests?: number };

export interface BudgetCheck { metric: MetricKey; value: number | null; budget: number; passed: boolean }

export interface SeoFacts {
    title: string | null;
    description: string | null;
    canonicals: string[];
    robots: string | null;
    h1_count: number;
    images: number;
    images_with_alt: number;
    lang: string | null;
    hreflangs: { lang: string; href: string }[];
    jsonld: { ok: boolean; error?: string; has_context?: boolean }[];
    viewport_meta: string | null;
    url: string;
}

export interface SeoCheck {
    check: string;
    passed: boolean;
    severity: 'error' | 'warning' | 'info';
    value?: string | null;
    detail?: string;
}

const LANG_TAG_RE = /^[a-z]{2,3}(-[a-z0-9]{2,8}){0,3}$/i;
// hreflang: language, optional script, optional region (ISO 3166 / UN M.49), or x-default.
const HREFLANG_RE = /^(x-default|[a-z]{2,3}(-[a-z]{4})?(-([a-z]{2}|[0-9]{3}))?)$/i;

/** Runs in the page. Self-contained. */
function collectInPage(args: { origin: string }) {
    return new Promise<any>((resolve) => {
        const round = (v: any) => (typeof v === 'number' && isFinite(v) ? Math.round(v * 1000) / 1000 : null);
        const nav = performance.getEntriesByType('navigation')[0] as any;
        const observers: Record<string, PerformanceObserver | null> = { lcp: null, cls: null, ev: null };
        const tryObserve = (name: string, opts: any) => {
            try {
                const o = new PerformanceObserver(() => { /* read via takeRecords */ });
                o.observe(opts);
                observers[name] = o;
            } catch { /* unsupported entry type in this browser */ }
        };
        tryObserve('lcp', { type: 'largest-contentful-paint', buffered: true });
        tryObserve('cls', { type: 'layout-shift', buffered: true });
        tryObserve('ev', { type: 'event', buffered: true, durationThreshold: 16 });
        setTimeout(() => {
            let lcp: number | null = null, cls: number | null = null, inp: number | null = null;
            try {
                const l = (observers.lcp?.takeRecords() || []) as any[];
                if (l.length) lcp = l[l.length - 1].startTime;
                if (observers.cls) {
                    // Session windows (1 s gap, 5 s cap), worst window — the
                    // CLS definition, not the plain sum.
                    let worst = 0, cur = 0, start = 0, prev = 0;
                    for (const e of (observers.cls.takeRecords() as any[])) {
                        if (e.hadRecentInput) continue;
                        if (cur && (e.startTime - prev > 1000 || e.startTime - start > 5000)) { cur = 0; }
                        if (!cur) start = e.startTime;
                        cur += e.value; prev = e.startTime;
                        worst = Math.max(worst, cur);
                    }
                    cls = worst;
                }
                const ev = (observers.ev?.takeRecords() || []) as any[];
                if (ev.length) inp = Math.max(...ev.filter(e => e.interactionId).map(e => e.duration), 0) || null;
            } catch { /* leave nulls */ }
            Object.values(observers).forEach(o => o?.disconnect());
            // Read late: the first paint can land after the load event.
            const fcp = performance.getEntriesByType('paint').find(p => p.name === 'first-contentful-paint');
            const res = performance.getEntriesByType('resource') as any[];
            const size = (e: any) => e.transferSize || e.encodedBodySize || 0;
            const isImg = (e: any) => e.initiatorType === 'img' || e.initiatorType === 'image'
                || /\.(png|jpe?g|gif|webp|avif|svg|ico|bmp)(\?|#|$)/i.test(e.name);
            let total = nav ? size(nav) : 0, img = 0, largestImg = 0, script = 0, third = 0, unmeasured = 0;
            for (const e of res) {
                const s = size(e);
                if (!s) unmeasured++;
                total += s;
                if (isImg(e)) { img += s; largestImg = Math.max(largestImg, s); }
                if (e.initiatorType === 'script') script += s;
                try { if (new URL(e.name).origin !== args.origin) third++; } catch { /* data: etc */ }
            }
            const kb = (b: number) => Math.round(b / 102.4) / 10;
            const metrics = {
                lcp_ms: round(lcp), cls: round(cls), inp_ms: round(inp),
                fcp_ms: fcp ? round(fcp.startTime) : null,
                ttfb_ms: nav ? round(nav.responseStart - nav.startTime) : null,
                dom_content_loaded_ms: nav ? round(nav.domContentLoadedEventEnd) : null,
                load_ms: nav && nav.loadEventEnd > 0 ? round(nav.loadEventEnd) : null,
                page_weight_kb: kb(total), request_count: res.length + (nav ? 1 : 0),
                image_weight_kb: kb(img), largest_image_kb: kb(largestImg),
                script_weight_kb: kb(script), third_party_requests: third,
                unmeasured_requests: unmeasured,
            };
            const meta = (name: string) => {
                const el = document.querySelector(`meta[name="${name}" i]`);
                return el ? el.getAttribute('content') : null;
            };
            const imgs = Array.from(document.images);
            const seo = {
                title: document.title || null,
                description: meta('description'),
                canonicals: Array.from(document.querySelectorAll('link[rel~="canonical" i]'))
                    .map(l => l.getAttribute('href') || ''),
                robots: meta('robots'),
                h1_count: document.querySelectorAll('h1').length,
                images: imgs.length,
                images_with_alt: imgs.filter(i => i.hasAttribute('alt')).length,
                lang: document.documentElement.getAttribute('lang'),
                hreflangs: Array.from(document.querySelectorAll('link[rel~="alternate" i][hreflang]'))
                    .map(l => ({ lang: l.getAttribute('hreflang') || '', href: l.getAttribute('href') || '' })),
                jsonld: Array.from(document.querySelectorAll('script[type="application/ld+json" i]')).map(s => {
                    try {
                        const parsed = JSON.parse(s.textContent || '');
                        const items = Array.isArray(parsed) ? parsed : [parsed];
                        return { ok: true, has_context: items.every((i: any) => i && (i['@context'] || i['@graph'])) };
                    } catch (e: any) {
                        return { ok: false, error: String(e && e.message || e).slice(0, 200) };
                    }
                }),
                viewport_meta: meta('viewport'),
                url: location.href,
            };
            resolve({ metrics, seo });
        }, 60);
    });
}

export async function collectPageFacts(page: Page, settleMs = 1000): Promise<{ metrics: Metrics; seo: SeoFacts }> {
    const wait = Math.max(0, Math.min(10_000, Number(settleMs) || 0));
    if (wait) await page.waitForTimeout(wait);
    let origin = '';
    try { origin = new URL(page.url()).origin; } catch { /* about:blank */ }
    return page.evaluate(collectInPage, { origin });
}

export function evaluateBudgets(metrics: Metrics, budgets: any): BudgetCheck[] {
    const out: BudgetCheck[] = [];
    if (!budgets || typeof budgets !== 'object') return out;
    for (const key of METRIC_KEYS) {
        const raw = budgets[key];
        if (raw === undefined || raw === null || raw === '') continue;
        const budget = Number(raw);
        if (!isFinite(budget)) continue;
        const value = metrics[key];
        // A metric the browser could not measure (e.g. LCP in WebKit, INP
        // without an interaction) is reported but does not fail the budget:
        // "unknown" is not "over".
        const passed = value === null || value === undefined ? true : value <= budget;
        out.push({ metric: key, value: value ?? null, budget, passed });
    }
    return out;
}

function range(raw: any, fallback: [number, number]): [number, number] {
    if (Array.isArray(raw) && raw.length === 2 && raw.every(n => Number.isFinite(Number(n)))) {
        return [Number(raw[0]), Number(raw[1])];
    }
    return fallback;
}

function isAbsoluteHttp(href: string): boolean {
    try { return /^https?:$/.test(new URL(href).protocol); } catch { return false; }
}

export function evaluateSeo(f: SeoFacts, opts: any = {}): SeoCheck[] {
    const out: SeoCheck[] = [];
    const [tMin, tMax] = range(opts.title_length, [10, 60]);
    const title = (f.title || '').trim();
    out.push({
        check: 'title', severity: 'error', value: title || null,
        passed: title.length >= tMin && title.length <= tMax,
        detail: title ? `${title.length} chars (want ${tMin}-${tMax})` : 'missing <title>',
    });
    const [dMin, dMax] = range(opts.description_length, [50, 160]);
    const desc = (f.description || '').trim();
    out.push({
        check: 'meta_description', severity: 'warning', value: desc || null,
        passed: desc.length >= dMin && desc.length <= dMax,
        detail: desc ? `${desc.length} chars (want ${dMin}-${dMax})` : 'missing meta description',
    });
    const canon = f.canonicals.filter(Boolean);
    out.push({
        check: 'canonical', severity: 'warning', value: canon[0] || null,
        passed: canon.length === 1 && isAbsoluteHttp(canon[0]),
        detail: canon.length === 0 ? 'no rel=canonical'
            : canon.length > 1 ? `${canon.length} canonical links (want exactly one)`
                : isAbsoluteHttp(canon[0]) ? 'ok' : 'canonical href is not an absolute URL',
    });
    const robots = (f.robots || '').toLowerCase();
    const noindex = /\b(noindex|none)\b/.test(robots);
    out.push({
        check: 'robots', severity: opts.allow_noindex ? 'info' : 'error', value: f.robots,
        passed: !noindex || !!opts.allow_noindex,
        detail: noindex ? 'page is marked noindex' : 'indexable',
    });
    out.push({
        check: 'h1', severity: 'warning', value: String(f.h1_count),
        passed: f.h1_count === 1,
        detail: f.h1_count === 1 ? 'one h1' : `${f.h1_count} h1 elements (want exactly one)`,
    });
    const altPct = f.images ? Math.round((f.images_with_alt / f.images) * 100) : 100;
    out.push({
        check: 'img_alt', severity: 'error', value: `${altPct}%`,
        passed: f.images_with_alt === f.images,
        detail: `${f.images_with_alt}/${f.images} images have an alt attribute`,
    });
    out.push({
        check: 'lang', severity: 'error', value: f.lang,
        passed: !!f.lang && LANG_TAG_RE.test(f.lang),
        detail: !f.lang ? 'missing <html lang>' : LANG_TAG_RE.test(f.lang) ? 'ok' : `invalid lang "${f.lang}"`,
    });
    if (f.hreflangs.length) {
        const problems: string[] = [];
        const seen = new Set<string>();
        let selfRef = false;
        for (const h of f.hreflangs) {
            if (!HREFLANG_RE.test(h.lang)) problems.push(`invalid code "${h.lang}"`);
            if (!isAbsoluteHttp(h.href)) problems.push(`"${h.lang}" href is not absolute`);
            const key = h.lang.toLowerCase();
            if (seen.has(key)) problems.push(`duplicate "${h.lang}"`);
            seen.add(key);
            try { if (new URL(h.href).href.split('#')[0] === new URL(f.url).href.split('#')[0]) selfRef = true; } catch { /* reported above */ }
        }
        if (!selfRef) problems.push('no self-referencing hreflang');
        out.push({
            check: 'hreflang', severity: 'error', value: `${f.hreflangs.length} alternates`,
            passed: problems.length === 0,
            detail: problems.length ? problems.slice(0, 5).join('; ') : 'ok',
        });
    }
    if (f.jsonld.length) {
        const bad = f.jsonld.filter(j => !j.ok);
        const noCtx = f.jsonld.filter(j => j.ok && !j.has_context);
        out.push({
            check: 'structured_data', severity: 'error', value: `${f.jsonld.length} JSON-LD block(s)`,
            passed: bad.length === 0 && noCtx.length === 0,
            detail: bad.length ? `parse error: ${bad[0].error}`
                : noCtx.length ? `${noCtx.length} block(s) without @context` : 'ok',
        });
    }
    out.push({
        check: 'viewport_meta', severity: 'warning', value: f.viewport_meta,
        passed: !!f.viewport_meta && /width\s*=\s*device-width/i.test(f.viewport_meta),
        detail: f.viewport_meta ? 'ok' : 'missing <meta name="viewport">',
    });
    return out;
}

export interface BudgetOutcome {
    step_id?: string;
    url: string;
    metrics: Metrics;
    checks: BudgetCheck[];
    seo: SeoCheck[];
    passed: boolean;
    failure?: string;
}

export function judge(url: string, metrics: Metrics, seoFacts: SeoFacts | null, params: any, stepId?: string): BudgetOutcome {
    const checks = evaluateBudgets(metrics, params?.budgets);
    const seoMode = ['warn', 'fail', 'off'].includes(params?.seo) ? params.seo : 'warn';
    const seo = seoMode === 'off' || !seoFacts ? [] : evaluateSeo(seoFacts, params);
    const over = checks.filter(c => !c.passed);
    const seoErrors = seoMode === 'fail' ? seo.filter(c => !c.passed && c.severity === 'error') : [];
    const reasons = [
        ...over.map(c => `${c.metric} ${c.value} > ${c.budget}`),
        ...seoErrors.map(c => `SEO ${c.check}: ${c.detail}`),
    ];
    return {
        ...(stepId ? { step_id: stepId } : {}),
        url, metrics, checks, seo,
        passed: reasons.length === 0,
        ...(reasons.length ? { failure: `Page budget exceeded on ${url}: ${reasons.join('; ')}` } : {}),
    };
}

/** The step: measure, judge, record into the case's sink, maybe throw. */
export async function executeBudgetStep(page: Page, step: any, sink?: any[]): Promise<BudgetOutcome> {
    const params = step?.params || {};
    const { metrics, seo } = await collectPageFacts(page, params.settle_ms ?? 1000);
    const outcome = judge(seo?.url || page.url(), metrics, seo, params, step?.id);
    if (Array.isArray(sink) && sink.length < 20) sink.push(outcome);
    console.log(`  [Budget] ${outcome.url}: ${outcome.checks.length} budget(s), `
        + `${outcome.checks.filter(c => !c.passed).length} over; SEO ${outcome.seo.filter(c => !c.passed).length} issue(s)`);
    if (!outcome.passed && params.fail !== false) {
        // No `stepResult`: the worker would show it as an HTTP response.
        throw new Error(outcome.failure);
    }
    return outcome;
}

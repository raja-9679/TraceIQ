/**
 * WP10 against a real browser and a local fixture site (node `http`):
 * the crawler end to end, overflow / RTL checks, SEO + metric collection,
 * and emulation applied to a real context.
 *
 * Needs a Chromium for the installed Playwright. When the exact build is
 * missing, any cached chromium headless shell is used; with none at all the
 * tests skip (the pure suite still covers the logic).
 */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'fs';
import * as http from 'http';
import * as os from 'os';
import * as path from 'path';
import { AddressInfo } from 'net';
import { Browser, chromium } from 'playwright';
import { crawlSite, playwrightLoader } from './crawler';
import { checkHorizontalOverflow, overflowFailure } from './layout-checks';
import { collectPageFacts, evaluateSeo } from './page-budget';
import { applyEmulationOptions, applyEmulationToContext } from './emulation';
import { executeFrontendQualityStep } from './steps';
import { TestExecutor } from '../test-executor';

let server: http.Server;
let base = '';
let altBase = '';     // same server, other host name: an "external" link
let browser: Browser | null = null;
let slowHits = 0;

const html = (body: string, head = '') =>
    `<!doctype html><html lang="en"><head><meta charset="utf-8">${head}</head><body>${body}</body></html>`;

const pages: Record<string, () => { status?: number; type?: string; body: string; headers?: Record<string, string>; delay?: number }> = {
    '/': () => ({ body: html(`
        <a href="/about">About</a> <a href="/missing">Missing</a> <a href="/loop-a">Loop</a>
        <a href="/chain1">Chain</a> <a href="/slow">Slow</a> <a href="/private/area">Private</a>
        <a href="mailto:x@example.com">Mail</a> <a href="#top">Top</a>
        <a href="EXTERNAL/ext-ok">Ext</a> <a href="EXTERNAL/ext-missing">Ext missing</a>
        <img src="/img/ok.png" alt="ok"><img src="/img/missing.png" alt="missing">
        <script src="/app.js"></script>
        <div style="background-image:url('/img/inline-missing.png')">x</div>`,
        '<link rel="stylesheet" href="/style.css"><link rel="preconnect" href="https://fonts.example">') }),
    '/about': () => ({ body: html('<a href="/">Home</a> <a href="/deep1">Deep</a>') }),
    '/deep1': () => ({ body: html('<a href="/deep2">deeper</a>') }),
    '/deep2': () => ({ body: html('<a href="/deep3">deeper</a>') }),
    '/deep3': () => ({ body: html('end') }),
    '/missing': () => ({ status: 404, body: 'nope' }),
    '/loop-a': () => ({ status: 302, headers: { location: '/loop-b' }, body: '' }),
    '/loop-b': () => ({ status: 302, headers: { location: '/loop-a' }, body: '' }),
    '/chain1': () => ({ status: 301, headers: { location: '/chain2' }, body: '' }),
    '/chain2': () => ({ status: 301, headers: { location: '/chain3' }, body: '' }),
    '/chain3': () => ({ status: 301, headers: { location: '/about' }, body: '' }),
    '/slow': () => { slowHits++; return { body: html('slow'), delay: 400 }; },
    '/private/area': () => ({ body: html('should never be fetched') }),
    '/robots.txt': () => ({ type: 'text/plain', body: 'User-agent: *\nDisallow: /private\n' }),
    '/img/ok.png': () => ({ type: 'image/png', body: 'png' }),
    '/app.js': () => ({ type: 'text/javascript', body: 'window.x = 1;' }),
    '/style.css': () => ({ type: 'text/css', body: 'body { background: url("/img/bg-missing.png"); }' }),
    '/ext-ok': () => ({ body: html('ext') }),

    '/overflow': () => ({ body: html('<div id="wide" style="width:2000px;height:20px">wide</div>') }),
    '/no-overflow': () => ({ body: html('<div style="max-width:100%">fits</div>') }),
    '/clipped': () => ({ body: html('<div style="overflow:hidden;width:300px"><div style="width:3000px">carousel</div></div>') }),
    '/rtl': () => ({ body: `<!doctype html><html dir="rtl" lang="ar"><body><p>مرحبا</p></body></html>` }),
    '/rtl-broken': () => ({ body: `<!doctype html><html dir="rtl" lang="ar"><body>
        <div style="position:absolute;left:-400px;width:300px" class="menu">قائمة</div></body></html>` }),
    '/ltr': () => ({ body: html('<p>hello</p>') }),
    '/seo-good': () => ({ body: html(`<h1>Checkout</h1><img src="/img/ok.png" alt="logo">
        <script type="application/ld+json">{"@context":"https://schema.org","@type":"Product","name":"x"}</script>`,
        `<title>Checkout — Example Shop</title>
         <meta name="description" content="Pay for your order securely with cards, UPI or net banking at Example Shop.">
         <link rel="canonical" href="SELF/seo-good"><meta name="viewport" content="width=device-width, initial-scale=1">
         <link rel="alternate" hreflang="en" href="SELF/seo-good"><link rel="alternate" hreflang="x-default" href="SELF/seo-good">`) }),
    '/seo-bad': () => ({ body: `<!doctype html><html><head><title>x</title><meta name="robots" content="noindex"></head>
        <body><h1>a</h1><h1>b</h1><img src="/img/ok.png">
        <script type="application/ld+json">{ not json</script></body></html>` }),
    '/heavy': () => ({ body: html('<h1>heavy</h1>' + Array.from({ length: 5 }, (_, i) => `<script src="/big${i}.js"></script>`).join('')) }),
    '/whoami': () => ({ body: html('<p id="out"></p><script>document.getElementById("out").textContent = JSON.stringify({lang: navigator.language, dark: matchMedia("(prefers-color-scheme: dark)").matches, cookie: document.cookie, w: innerWidth});</script>') }),
};

function findChromium(): string | undefined {
    const cache = path.join(os.homedir(), '.cache', 'ms-playwright');
    try {
        for (const dir of fs.readdirSync(cache).filter(d => d.startsWith('chromium_headless_shell-')).sort().reverse()) {
            const exe = path.join(cache, dir, 'chrome-headless-shell-linux64', 'chrome-headless-shell');
            if (fs.existsSync(exe)) return exe;
        }
    } catch { /* no cache */ }
    return undefined;
}

before(async () => {
    server = http.createServer((req, res) => {
        const url = new URL(req.url || '/', 'http://x');
        let route = pages[url.pathname];
        if (!route && /^\/big\d\.js$/.test(url.pathname)) {
            route = () => ({ type: 'text/javascript', body: '//' + 'x'.repeat(200 * 1024) });
        }
        if (!route) {
            res.statusCode = 404;
            res.end('not found');
            return;
        }
        const r = route();
        const send = () => {
            res.statusCode = r.status || 200;
            res.setHeader('content-type', r.type || 'text/html; charset=utf-8');
            res.setHeader('timing-allow-origin', '*');
            for (const [k, v] of Object.entries(r.headers || {})) res.setHeader(k, v);
            res.end(req.method === 'HEAD' ? undefined : r.body
                .replace(/EXTERNAL/g, altBase).replace(/SELF/g, `http://${req.headers.host}`));
        };
        if (r.delay) setTimeout(send, r.delay); else send();
    });
    await new Promise<void>(r => server.listen(0, '127.0.0.1', () => r()));
    const port = (server.address() as AddressInfo).port;
    base = `http://127.0.0.1:${port}`;
    altBase = `http://localhost:${port}`;
    try {
        browser = await chromium.launch();
    } catch {
        const exe = findChromium();
        if (exe) browser = await chromium.launch({ executablePath: exe }).catch(() => null);
    }
});

after(async () => {
    await browser?.close();
    await new Promise<void>(r => server.close(() => r()));
});

const crawlConfig = (over: any = {}) => ({
    start_url: `${base}/`, crawl_hosts: ['127.0.0.1'], max_depth: 3, max_pages: 50,
    respect_robots: true, rate_limit_ms: 50, slow_threshold_ms: 300, check_external: true, ...over,
});

test('crawler: finds broken, looping, chained, slow and external links; honours robots and depth', async (t) => {
    if (!browser) return t.skip('no chromium available');
    const report = await crawlSite(crawlConfig(), { loader: playwrightLoader(browser), fetchImpl: fetch });
    assert.equal(report.status, 'ok');
    const by = (cat: string) => report.issues.filter(i => i.category === cat).map(i => new URL(i.target_url).pathname);
    assert.ok(by('broken').includes('/missing'), 'broken anchor');
    assert.ok(by('broken').includes('/img/missing.png'), 'broken img');
    assert.ok(by('broken').includes('/img/bg-missing.png'), 'broken css url() from a stylesheet');
    assert.ok(by('broken').includes('/img/inline-missing.png'), 'broken inline style url()');
    assert.ok(by('broken').includes('/ext-missing'), 'broken external link (checked, not crawled)');
    assert.deepEqual(by('redirect_loop'), ['/loop-a']);
    assert.ok(by('slow').includes('/slow'));
    const chain = report.issues.find(i => i.category === 'redirect_chain')!;
    assert.equal(chain.redirect_chain!.length, 4);
    const css = report.issues.find(i => i.target_url.endsWith('/img/bg-missing.png'))!;
    assert.equal(css.element, 'css');

    const crawled = report.pages.map(p => new URL(p.url).pathname);
    assert.ok(crawled.includes('/about') && crawled.includes('/deep2'));
    assert.ok(!crawled.includes('/deep3'), 'depth 3 page /deep3 is beyond max_depth from / (0) -> about (1) -> deep1 (2) -> deep2 (3)');
    assert.ok(!crawled.includes('/private/area'), 'robots-disallowed page is not crawled');
    assert.ok(!report.issues.some(i => i.target_url.includes('/private')), 'nor checked');
    assert.ok(report.robots.fetched && report.robots.skipped > 0);
    assert.ok(!crawled.some(p => p === '/ext-ok'), 'external hosts are never crawled');
    // Connection hints and non-http links are not checked.
    assert.ok(!report.issues.some(i => i.target_url.includes('fonts.example') || i.target_url.startsWith('mailto')));
});

test('crawler: max_pages truncates, check_external=false skips other hosts, robots can be ignored', async (t) => {
    if (!browser) return t.skip('no chromium available');
    const small = await crawlSite(crawlConfig({ max_pages: 2, check_external: false, respect_robots: false }),
        { loader: playwrightLoader(browser), fetchImpl: fetch });
    assert.equal(small.pages.length, 2);
    assert.equal(small.truncated, true);
    assert.ok(!small.issues.some(i => i.target_url.startsWith(altBase)));
    assert.ok(small.issues.some(i => i.target_url.endsWith('/missing')));
    const broken = await crawlSite(crawlConfig({ start_url: `${base}/missing` }), { loader: playwrightLoader(browser), fetchImpl: fetch });
    assert.equal(broken.issues[0].element, 'page');
    assert.equal(broken.issues[0].category, 'broken');
});

async function pageAt(url: string, options: any = {}) {
    const ctx = await browser!.newContext(options);
    const page = await ctx.newPage();
    await page.goto(url);
    return { ctx, page };
}

test('overflow: wide content fails, fitting and clipped content pass', async (t) => {
    if (!browser) return t.skip('no chromium available');
    const vp = { viewport: { width: 375, height: 700 } };
    let { ctx, page } = await pageAt(`${base}/overflow`, vp);
    const wide = await checkHorizontalOverflow(page);
    assert.equal(wide.document_overflows, true);
    assert.equal(wide.offenders[0].selector, 'div#wide');
    assert.match(overflowFailure(wide)!, /div#wide/);
    await ctx.close();
    for (const p of ['/no-overflow', '/clipped']) {
        ({ ctx, page } = await pageAt(`${base}${p}`, vp));
        assert.equal(overflowFailure(await checkHorizontalOverflow(page)), null, p);
        await ctx.close();
    }
    // The step form, with an ignore list that excuses the offender.
    ({ ctx, page } = await pageAt(`${base}/overflow`, vp));
    await assert.rejects(TestExecutor.executeStep(page, page, { type: 'expect-no-horizontal-overflow' }), /Horizontal overflow/);
    // Ignoring the element does not hide that the DOCUMENT scrolls sideways.
    await assert.rejects(TestExecutor.executeStep(page, page, { type: 'expect-no-horizontal-overflow', params: { ignore: ['#wide'] } }), /page scrolls horizontally/);
    await ctx.close();
});

test('rtl: dir=rtl passes, LTR or content off the left edge fails', async (t) => {
    if (!browser) return t.skip('no chromium available');
    const vp = { viewport: { width: 375, height: 700 } };
    let { ctx, page } = await pageAt(`${base}/rtl`, vp);
    const ok = await executeFrontendQualityStep(page, page, { type: 'expect-rtl-layout' });
    assert.equal(ok.direction.computed, 'rtl');
    await ctx.close();
    ({ ctx, page } = await pageAt(`${base}/ltr`, vp));
    await assert.rejects(executeFrontendQualityStep(page, page, { type: 'expect-rtl-layout' }), /Expected RTL/);
    await executeFrontendQualityStep(page, page, { type: 'expect-rtl-layout', params: { expect_dir: 'ltr' } });
    await ctx.close();
    ({ ctx, page } = await pageAt(`${base}/rtl-broken`, vp));
    await assert.rejects(executeFrontendQualityStep(page, page, { type: 'expect-rtl-layout' }),
        /RTL layout: .*div\.menu \(left edge -400px\)/);
    await ctx.close();
});

test('SEO facts and metrics are collected from the page', async (t) => {
    if (!browser) return t.skip('no chromium available');
    let { ctx, page } = await pageAt(`${base}/seo-good`);
    let facts = await collectPageFacts(page, 300);
    assert.deepEqual(evaluateSeo(facts.seo).filter(c => !c.passed), []);
    assert.equal(typeof facts.metrics.ttfb_ms, 'number');
    assert.equal(typeof facts.metrics.fcp_ms, 'number');
    assert.ok((facts.metrics.request_count ?? 0) >= 2);
    await ctx.close();
    ({ ctx, page } = await pageAt(`${base}/seo-bad`));
    facts = await collectPageFacts(page, 0);
    const failed = evaluateSeo(facts.seo).filter(c => !c.passed).map(c => c.check);
    for (const c of ['title', 'robots', 'h1', 'img_alt', 'lang', 'structured_data', 'canonical']) assert.ok(failed.includes(c), c);
    await ctx.close();
});

test('check-page-budget: page weight over budget fails the step and is recorded', async (t) => {
    if (!browser) return t.skip('no chromium available');
    const { ctx, page } = await pageAt(`${base}/heavy`);
    await page.waitForLoadState('load');
    const tc: any = {};
    await assert.rejects(TestExecutor.executeStep(page, page,
        { id: 'b1', type: 'check-page-budget', params: { budgets: { page_weight_kb: 100, script_weight_kb: 50 }, settle_ms: 0 } }, {}, tc),
        /Page budget exceeded .*page_weight_kb/);
    assert.equal(tc.page_budgets.length, 1);
    assert.equal(tc.page_budgets[0].step_id, 'b1');
    assert.ok(tc.page_budgets[0].metrics.script_weight_kb >= 900);
    const passing = await TestExecutor.executeStep(page, page,
        { type: 'check-page-budget', params: { budgets: { page_weight_kb: 100000 }, settle_ms: 0 } }, {}, tc);
    assert.equal(passing.passed, true);
    assert.equal(tc.page_budgets.length, 2);
    await ctx.close();
});

test('emulation reaches the page: width, locale, colour scheme, locale cookie and URL rewrite', async (t) => {
    if (!browser) return t.skip('no chromium available');
    const settings = { emulation: {
        viewport: { name: 'mobile', width: 390, height: 800 }, color_scheme: 'dark',
        locale: { locale: 'ar-EG', cookie: { name: 'lang', value: 'ar' }, query: { hl: '{lang}' } },
    }, environment: { base_url: base } };
    const opts: any = applyEmulationOptions({}, settings);
    const ctx = await browser.newContext(opts);
    await applyEmulationToContext(ctx, settings);
    const page = await ctx.newPage();
    await TestExecutor.executeStep(page, page, { type: 'goto', value: '/whoami' }, settings, {});
    assert.match(page.url(), /[?&]hl=ar\b/);
    const out = JSON.parse(await page.textContent('#out') || '{}');
    assert.equal(out.lang, 'ar-EG');
    assert.equal(out.dark, true);
    assert.equal(out.w, 390);
    assert.match(out.cookie, /lang=ar/);
    await ctx.close();
});

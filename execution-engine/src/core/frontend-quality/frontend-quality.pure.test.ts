/**
 * WP10 pure logic: emulation options, locale URL rewriting, robots.txt
 * matching, budget/SEO evaluation and crawl issue classification.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
    acceptLanguage, applyEmulationOptions, baselineDevice, cookieDomain, localiseUrl, sweepShotFile,
    wantsStepScreenshots,
} from './emulation';
import { isAllowed, parseRobots, patternMatches } from './robots';
import { evaluateBudgets, evaluateSeo, judge, SeoFacts } from './page-budget';
import { CheckResult, HostGate, checkUrl, classify, normaliseUrl, safeResultKey } from './crawler';
import { overflowFailure, directionFailure } from './layout-checks';

const emu = (e: any) => ({ emulation: e });

test('emulation: viewport, locale, scheme, reduced motion become context options', () => {
    const opts: any = { recordVideo: { dir: '/tmp', size: { width: 1280, height: 720 } } };
    applyEmulationOptions(opts, emu({
        viewport: { name: 'mobile', width: 375, height: 812 },
        locale: { locale: 'ar-EG' }, color_scheme: 'dark', reduced_motion: true,
    }));
    assert.deepEqual(opts.viewport, { width: 375, height: 812 });
    assert.equal(opts.locale, 'ar-EG');
    assert.equal(opts.extraHTTPHeaders['Accept-Language'], 'ar-EG,ar;q=0.9');
    assert.equal(opts.colorScheme, 'dark');
    assert.equal(opts.reducedMotion, 'reduce');
    // Video follows the viewport's aspect instead of letterboxing into 1280x720.
    assert.ok(opts.recordVideo.size.width < opts.recordVideo.size.height);
    assert.equal(opts.recordVideo.size.width % 2, 0);
});

test('emulation: absent or invalid fields are ignored', () => {
    const opts: any = { viewport: { width: 1280, height: 720 } };
    applyEmulationOptions(opts, {});
    assert.deepEqual(opts, { viewport: { width: 1280, height: 720 } });
    applyEmulationOptions(opts, emu({ viewport: { name: 'x', width: 50, height: 99999 }, locale: { locale: 'not a tag!' }, color_scheme: 'sepia' }));
    assert.deepEqual(opts, { viewport: { width: 1280, height: 720 } });
    assert.equal(acceptLanguage('en'), 'en');
});

test('localiseUrl adds the locale query and applies a same-origin template only', () => {
    const settings = emu({ locale: { locale: 'ar-EG', query: { lang: '{lang}' }, url_template: '{origin}/{lang}{path}{query}' } });
    assert.equal(localiseUrl('https://shop.example.com/cart?x=1', settings), 'https://shop.example.com/ar/cart?x=1&lang=ar');
    // A template that would leave the origin is discarded.
    const evil = emu({ locale: { locale: 'fr', url_template: '{origin}.evil.example{path}' } });
    assert.equal(localiseUrl('https://shop.example.com/', evil), 'https://shop.example.com/');
    // No locale, or a non-http URL: untouched.
    assert.equal(localiseUrl('https://a.example/', {}), 'https://a.example/');
    assert.equal(localiseUrl('about:blank', settings), 'about:blank');
});

test('cookie domain: explicit, then environment base_url, then first allowed domain', () => {
    const loc = { locale: 'de', cookie: { name: 'l', value: 'de' } };
    assert.equal(cookieDomain({ locale: { ...loc, cookie: { ...loc.cookie, domain: 'x.example' } } }, {}), 'x.example');
    assert.equal(cookieDomain({ locale: loc }, { environment: { base_url: 'https://stage.example.com/app' } }), 'stage.example.com');
    assert.equal(cookieDomain({ locale: loc }, { allowed_domains: [{ domain: '*.corp.example' }] }), '.corp.example');
    assert.equal(cookieDomain({ locale: loc }, {}), null);
});

test('baseline device keeps the vp: prefix so the backend never falls back across variants', () => {
    const s = emu({ baseline_key: 'vp:mobile|dark' });
    assert.equal(baselineDevice(null, s), 'vp:mobile|dark');
    assert.equal(baselineDevice('iPhone 12', s), 'vp:iPhone 12|mobile|dark');
    assert.equal(baselineDevice('iPhone 12', {}), 'iPhone 12');
    assert.equal(baselineDevice(undefined, {}), null);
    assert.equal(wantsStepScreenshots(emu({ step_screenshots: true })), true);
    assert.equal(wantsStepScreenshots({}), false);
    assert.equal(sweepShotFile(7, 3), 'sweep-7-s3.png');
});

test('robots: agent group selection, wildcards, longest match, allow wins ties', () => {
    const r = parseRobots([
        'User-agent: *', 'Disallow: /private', 'Disallow: /*.pdf$', 'Allow: /private/ok', 'Crawl-delay: 2',
        '', 'User-agent: OtherBot', 'Disallow: /',
    ].join('\n'));
    assert.equal(isAllowed(r, '/'), true);
    assert.equal(isAllowed(r, '/private/x'), false);
    assert.equal(isAllowed(r, '/private/ok/1'), true);
    assert.equal(isAllowed(r, '/docs/a.pdf'), false);
    assert.equal(isAllowed(r, '/docs/a.pdf?x'), true);
    assert.equal(r.crawlDelayMs, 2000);
    const mine = parseRobots('User-agent: TraceIQ\nDisallow: /beta\n\nUser-agent: *\nDisallow: /');
    assert.equal(isAllowed(mine, '/home'), true);   // our group replaces '*'
    assert.equal(isAllowed(mine, '/beta/x'), false);
    assert.equal(patternMatches('/a*b*c$', '/axxbyyc'), true);
    assert.equal(patternMatches('/a*b*c$', '/axxbyycd'), false);
    // Pathological patterns are matched without a RegExp: this returns promptly.
    const t0 = Date.now();
    patternMatches('/' + '*a'.repeat(200) + '$', '/' + 'a'.repeat(5000) + 'b');
    assert.ok(Date.now() - t0 < 500);
});

test('budgets: over budget fails, unmeasured metrics never fail', () => {
    const checks = evaluateBudgets({ lcp_ms: 3000, cls: 0.05, inp_ms: null, page_weight_kb: 900 },
        { lcp_ms: 2500, cls: 0.1, inp_ms: 200, page_weight_kb: '1000', bogus: 1 });
    const by = Object.fromEntries(checks.map(c => [c.metric, c]));
    assert.equal(by.lcp_ms.passed, false);
    assert.equal(by.cls.passed, true);
    assert.equal(by.inp_ms.passed, true);
    assert.equal(by.page_weight_kb.budget, 1000);
    assert.equal(checks.length, 4);
});

const goodSeo: SeoFacts = {
    title: 'Checkout — Example Shop', description: 'Pay for your order securely with cards, UPI or net banking at Example Shop.',
    canonicals: ['https://shop.example.com/checkout'], robots: 'index,follow', h1_count: 1,
    images: 2, images_with_alt: 2, lang: 'en-IN',
    hreflangs: [{ lang: 'en-IN', href: 'https://shop.example.com/checkout' }, { lang: 'x-default', href: 'https://shop.example.com/checkout' }],
    jsonld: [{ ok: true, has_context: true }], viewport_meta: 'width=device-width, initial-scale=1',
    url: 'https://shop.example.com/checkout',
};

test('SEO: a well-formed page passes every check', () => {
    const failed = evaluateSeo(goodSeo).filter(c => !c.passed);
    assert.deepEqual(failed, []);
});

test('SEO: each defect is reported with its own check', () => {
    const bad: SeoFacts = {
        ...goodSeo, title: 'Hi', description: null, canonicals: ['/rel', '/two'], robots: 'noindex', h1_count: 0,
        images_with_alt: 1, lang: null,
        hreflangs: [{ lang: 'english', href: '/x' }, { lang: 'en-IN', href: 'https://o.example/' }, { lang: 'EN-in', href: 'https://o.example/' }],
        jsonld: [{ ok: false, error: 'Unexpected token' }], viewport_meta: null,
    };
    const failed = new Set(evaluateSeo(bad).filter(c => !c.passed).map(c => c.check));
    for (const c of ['title', 'meta_description', 'canonical', 'robots', 'h1', 'img_alt', 'lang', 'hreflang', 'structured_data', 'viewport_meta']) {
        assert.ok(failed.has(c), `expected ${c} to fail`);
    }
    const hreflang = evaluateSeo(bad).find(c => c.check === 'hreflang')!;
    assert.match(hreflang.detail!, /invalid code "english"/);
    assert.match(hreflang.detail!, /duplicate/);
});

test('judge: SEO warns by default, fails only in fail mode', () => {
    const facts = { ...goodSeo, lang: null };
    assert.equal(judge(goodSeo.url, {}, facts, { seo: 'warn' }).passed, true);
    const strict = judge(goodSeo.url, {}, facts, { seo: 'fail' });
    assert.equal(strict.passed, false);
    assert.match(strict.failure!, /SEO lang/);
    assert.deepEqual(judge(goodSeo.url, {}, facts, { seo: 'off' }).seo, []);
    const over = judge(goodSeo.url, { cls: 0.4 }, null, { budgets: { cls: 0.1 } }, 'step-1');
    assert.equal(over.step_id, 'step-1');
    assert.match(over.failure!, /cls 0.4 > 0.1/);
});

const ok = (over: Partial<CheckResult> = {}): CheckResult =>
    ({ url: 'x', status: 200, durationMs: 10, chain: [{ url: 'x', status: 200 }], loop: false, ...over });

test('classify: broken, network, loop, chain, slow and mixed content', () => {
    const page = 'https://s.example/';
    const cat = (link: any, r: CheckResult) => classify(page, link, r, 1000).map(i => i.category);
    assert.deepEqual(cat({ url: 'https://s.example/a', element: 'a' }, ok({ status: 404 })), ['broken']);
    assert.deepEqual(cat({ url: 'https://s.example/a', element: 'a' }, ok({ status: null, error: 'ENOTFOUND' })), ['network']);
    assert.deepEqual(cat({ url: 'https://s.example/a', element: 'a' }, ok({ loop: true, status: 302 })), ['redirect_loop']);
    assert.deepEqual(cat({ url: 'https://s.example/a', element: 'a' },
        ok({ chain: [1, 2, 3, 4].map(n => ({ url: `u${n}`, status: n < 4 ? 301 : 200 })) })), ['redirect_chain']);
    assert.deepEqual(cat({ url: 'https://s.example/a', element: 'a' }, ok({ durationMs: 5000 })), ['slow']);
    assert.deepEqual(cat({ url: 'http://cdn.example/a.js', element: 'script' }, ok()), ['mixed_content']);
    // An http link (not a subresource) is not mixed content.
    assert.deepEqual(cat({ url: 'http://other.example/', element: 'a' }, ok()), []);
});

test('checkUrl follows redirects by hand, detects loops and falls back from HEAD to GET', async () => {
    const routes: Record<string, (m: string) => { status: number; location?: string }> = {
        'https://a.example/loop1': () => ({ status: 302, location: '/loop2' }),
        'https://a.example/loop2': () => ({ status: 302, location: '/loop1' }),
        'https://a.example/nohead': (m) => ({ status: m === 'HEAD' ? 405 : 200 }),
    };
    const fetchImpl = async (url: string, init: any) => {
        const r = routes[url]?.(init.method) ?? { status: 404 };
        return { status: r.status, headers: { get: (h: string) => (h === 'location' ? r.location ?? null : null) } };
    };
    const gate = new HostGate(() => 0);
    const loop = await checkUrl('https://a.example/loop1', fetchImpl, gate);
    assert.equal(loop.loop, true);
    assert.equal(loop.chain.length, 2);
    const nohead = await checkUrl('https://a.example/nohead', fetchImpl, gate);
    assert.equal(nohead.status, 200);
    const err = await checkUrl('https://a.example/x', async () => { throw Object.assign(new Error('x'), { cause: { code: 'ECONNREFUSED' } }); }, gate);
    assert.equal(err.status, null);
    assert.equal(err.error, 'ECONNREFUSED');
});

test('HostGate spaces requests per host, not globally', async () => {
    let clock = 0;
    const slept: number[] = [];
    const gate = new HostGate(() => 100, () => clock, async (ms) => { slept.push(ms); });
    await gate.wait('https://a.example/1');
    await gate.wait('https://b.example/1');
    await gate.wait('https://a.example/2');
    assert.deepEqual(slept, [100]);
});

test('crawl result key is derived from the run id, never trusted as sent', () => {
    assert.equal(safeResultKey({ crawl_run_id: 7, result_key: 'site_crawl:result:7' }), 'site_crawl:result:7');
    assert.equal(safeResultKey({ crawl_run_id: 7, result_key: 'persona_refresh:result:7' }), null);
    assert.equal(safeResultKey({ crawl_run_id: 'x', result_key: 'site_crawl:result:x' }), null);
    assert.equal(normaliseUrl('mailto:a@b.c'), null);
    assert.equal(normaliseUrl('/x#frag', 'https://a.example/y'), 'https://a.example/x');
});

test('layout failure messages name the offenders and the direction problem', () => {
    assert.equal(overflowFailure({ viewport_width: 375, document_scroll_width: 375, document_overflows: false, offenders: [], offender_count: 0 }), null);
    const msg = overflowFailure({ viewport_width: 375, document_scroll_width: 800, document_overflows: true,
        offenders: [{ selector: 'div.hero', left: 0, right: 800, width: 800, side: 'right' }], offender_count: 1 })!;
    assert.match(msg, /scrollWidth 800px > viewport 375px/);
    assert.match(msg, /div\.hero \(right edge 800px\)/);
    assert.match(directionFailure({ html_dir: null, body_dir: null, computed: 'ltr' }, 'rtl')!, /Expected RTL/);
    assert.match(directionFailure({ html_dir: null, body_dir: null, computed: 'rtl' }, 'rtl')!, /only through CSS/);
    assert.equal(directionFailure({ html_dir: 'rtl', body_dir: null, computed: 'rtl' }, 'rtl'), null);
});

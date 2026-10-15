/**
 * Crawler security review (WP10): content-driven SSRF and worker ceilings.
 * Pure — a fake loader, fetch and resolver; the browser-level test (route
 * guard across redirects) is in frontend-quality.browser.test.ts.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { crawlSite, CrawlConfig, LoadedPage, MAX_CRAWL_DURATION_MS, MAX_CRAWL_PAGES } from './crawler';
import { SweepShotBudget } from './emulation';
import { targetVerdict } from '../egress-guard';

const DNS: Record<string, string[]> = {
    'shop.example.com': ['93.184.216.34'],
    'cdn.example.net': ['151.101.1.1'],
    'internal.example.net': ['10.0.0.5'],
    'rebind.example.net': ['93.184.216.40', '169.254.169.254'],
    'meta.example.com': ['169.254.169.254'],
};
const resolver = async (h: string) => {
    if (!DNS[h]) throw Object.assign(new Error('ENOTFOUND'), { code: 'ENOTFOUND' });
    return DNS[h];
};

const LINKS = [
    'http://127.0.0.1/admin', 'http://169.254.169.254/latest/meta-data/', 'http://[::ffff:169.254.169.254]/',
    'http://0x7f000001/', 'http://2130706433/', 'http://[::1]/', 'http://[64:ff9b::a9fe:a9fe]/',
    'http://internal.example.net/', 'http://rebind.example.net/', 'http://localhost./', 'http://app.localhost/',
    'https://cdn.example.net/ok.js', 'https://cdn.example.net/redirects-inside',
];

function fakes(extra: Record<string, { status: number; location?: string }> = {}) {
    const fetched: string[] = [];
    const routes: Record<string, { status: number; location?: string }> = {
        'https://cdn.example.net/ok.js': { status: 200 },
        'https://cdn.example.net/redirects-inside': { status: 302, location: 'http://169.254.169.254/iam' },
        ...extra,
    };
    const fetchImpl = async (url: string) => {
        fetched.push(url);
        const r = routes[url] || { status: 200 };
        return { status: r.status, headers: { get: (n: string) => (n === 'location' ? r.location ?? null : null) },
            body: { cancel: async () => undefined }, text: async () => '' };
    };
    const loader = {
        async load(url: string): Promise<LoadedPage> {
            return { status: 200, finalUrl: url, isHtml: true, loadMs: 1,
                links: LINKS.map(u => ({ url: u, element: 'a' as const })) };
        },
    };
    return { fetched, fetchImpl, loader };
}

const config = (over: Partial<CrawlConfig> = {}): CrawlConfig => ({
    start_url: 'https://shop.example.com/', crawl_hosts: ['shop.example.com'], max_depth: 0, max_pages: 5,
    respect_robots: false, rate_limit_ms: 50, slow_threshold_ms: 60_000, check_external: true, ...over,
});

test('crawler never requests private / metadata targets found in page content', async () => {
    const f = fakes();
    const report = await crawlSite(config(), { loader: f.loader, fetchImpl: f.fetchImpl, resolver });
    const refusedHosts = ['127.0.0.1', '169.254.169.254', '[::ffff:a9fe:a9fe]', '0x7f000001', '2130706433',
        '[::1]', '[64:ff9b::a9fe:a9fe]', 'internal.example.net', 'rebind.example.net', 'localhost', 'app.localhost'];
    for (const h of refusedHosts) {
        assert.ok(!f.fetched.some(u => u.includes(h)), `${h} must not be requested (fetched: ${f.fetched})`);
    }
    assert.ok(!f.fetched.some(u => u.includes('169.254')), 'no metadata hop, including after a redirect');
    assert.ok(f.fetched.includes('https://cdn.example.net/ok.js'), 'public targets are still checked');
    const notChecked = report.issues.filter(i => i.category === 'not_checked');
    // 0x7f000001 / 2130706433 normalise to 127.0.0.1 and are checked once.
    const distinct = new Set(LINKS.map(u => new URL(u).href)).size;
    assert.equal(notChecked.length, distinct - 1, JSON.stringify(report.issues, null, 1));
    assert.ok(notChecked.every(i => i.detail!.startsWith('not checked (private address)')));
    // The redirect hop is the refused one: the chain records the public first hop.
    const viaRedirect = notChecked.find(i => i.target_url.endsWith('/redirects-inside'))!;
    assert.equal(viaRedirect.redirect_chain![0].status, 302);
});

test('allow_private opens private targets but never link-local / metadata', async () => {
    const f = fakes();
    await crawlSite(config({ allow_private: true }), { loader: f.loader, fetchImpl: f.fetchImpl, resolver });
    assert.ok(f.fetched.some(u => u.startsWith('http://127.0.0.1/')));
    assert.ok(f.fetched.some(u => u.startsWith('http://internal.example.net/')));
    assert.ok(!f.fetched.some(u => u.includes('169.254') || u.includes('a9fe') || u.includes('rebind')));
});

test('a crawl host may be private (an internal site) but a metadata crawl host is never loaded', async () => {
    const f = fakes();
    const loaded: string[] = [];
    const loader = { async load(url: string): Promise<LoadedPage> {
        loaded.push(url);
        return { status: 200, finalUrl: url, isHtml: true, loadMs: 1, links: [] };
    } };
    await crawlSite(config({ start_url: 'http://10.1.2.3/', crawl_hosts: ['10.1.2.3'] }),
        { loader, fetchImpl: f.fetchImpl, resolver });
    assert.deepEqual(loaded, ['http://10.1.2.3/']);
    loaded.length = 0;
    const r = await crawlSite(config({ start_url: 'http://meta.example.com/', crawl_hosts: ['meta.example.com'] }),
        { loader, fetchImpl: f.fetchImpl, resolver });
    assert.deepEqual(loaded, []);
    assert.equal(r.issues[0].category, 'not_checked');
});

test('targetVerdict: encodings and resolution', async () => {
    for (const u of ['http://169.254.169.254/', 'http://[fe80::1]/', 'http://0251.0376.0251.0376/', 'ftp://x/',
                     'http://meta.example.com/']) {
        assert.equal((await targetVerdict(u, { allowPrivate: true, resolver })).allowed, false, u);
    }
    assert.equal((await targetVerdict('http://10.0.0.1/', { allowPrivate: false, resolver })).allowed, false);
    assert.equal((await targetVerdict('http://10.0.0.1/', {
        allowPrivate: false, resolver, trustedHosts: new Set(['10.0.0.1']) })).allowed, true);
    assert.equal((await targetVerdict('https://cdn.example.net/', { allowPrivate: false, resolver })).allowed, true);
    // Unresolvable names are left to fail as a network error (a real broken link).
    assert.equal((await targetVerdict('https://nx.example.org/', { allowPrivate: false, resolver })).allowed, true);
});

test('worker ceilings apply whatever the job says', async () => {
    let pagesLoaded = 0;
    let t = 0;
    const loader = { async load(url: string): Promise<LoadedPage> {
        pagesLoaded++;
        t += 120_000;   // each page "takes" two minutes
        const n = Number(new URL(url).pathname.slice(1) || 0);
        return { status: 200, finalUrl: url, isHtml: true, loadMs: 1,
            links: [{ url: `https://shop.example.com/${n + 1}`, element: 'a' }] };
    } };
    const f = fakes();
    const report = await crawlSite(config({ max_pages: 10 ** 9, max_depth: 10 ** 9, max_duration_ms: 10 ** 12 }),
        { loader, fetchImpl: f.fetchImpl, resolver, now: () => t });
    assert.ok(report.truncated);
    assert.ok(pagesLoaded <= MAX_CRAWL_DURATION_MS / 120_000 + 1, `stopped by the 15-minute ceiling (${pagesLoaded})`);
    assert.ok(report.pages.every(p => p.depth <= 10));
    assert.ok(pagesLoaded <= MAX_CRAWL_PAGES);
});

test('sweep screenshots are capped per job', () => {
    const b = new SweepShotBudget(3);
    assert.deepEqual([1, 2, 3, 4].map(() => b.take('job-a')), [true, true, true, false]);
    assert.equal(b.take('job-b'), true, 'a new job starts a new budget');
});

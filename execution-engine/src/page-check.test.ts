/**
 * Monitored-page checks (WP9) against a local HTTP server and a real
 * headless Chromium. The browser tests skip when no Chromium can be launched
 * (CI images without browsers); the pure helpers always run.
 */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'fs';
import * as http from 'http';
import * as os from 'os';
import * as path from 'path';
import { AddressInfo } from 'net';
import { Browser, chromium } from 'playwright';
import {
    PageCheckJob, executePageCheckJob, mergeScripts, redactPageObservation, refusalReason,
    safeResultKey, securityHeaders, sri384,
} from './page-check';

const APP_JS = 'console.log("app");';
const DYN_JS = 'window.__dyn = 1;';
const INLINE = 'window.cfg = { region: "in" };';

let server: http.Server;
let base = '';
let browser: Browser | null = null;

function page(): string {
    return `<!doctype html><html><head>
<script src="/static/app.js?v=3" integrity="sha384-declared"></script>
<script>${INLINE}</script>
<script type="application/json">{"not": "code"}</script>
<script>
  // Injected and removed again: only the network sees it.
  const s = document.createElement('script'); s.src = '/dyn.js';
  s.onload = () => s.remove(); document.head.appendChild(s);
</script>
</head><body><h1 id="title">Checkout</h1><p>Pay securely. Card 4111 1111 1111 1111</p>
<div id="total">Total 1,299</div></body></html>`;
}

async function launch(): Promise<Browser | null> {
    try { return await chromium.launch(); } catch { /* fall through */ }
    const candidates: string[] = [];
    if (process.env.PLAYWRIGHT_CHROMIUM_PATH) candidates.push(process.env.PLAYWRIGHT_CHROMIUM_PATH);
    const cache = path.join(os.homedir(), '.cache', 'ms-playwright');
    try {
        for (const d of fs.readdirSync(cache).filter(n => n.startsWith('chromium_headless_shell-')).sort().reverse()) {
            candidates.push(path.join(cache, d, 'chrome-headless-shell-linux64', 'chrome-headless-shell'));
        }
    } catch { /* no cache */ }
    for (const exe of candidates) {
        try { return await chromium.launch({ executablePath: exe }); } catch { /* next */ }
    }
    return null;
}

before(async () => {
    server = http.createServer((req, res) => {
        const url = new URL(req.url || '/', 'http://x');
        if (url.pathname === '/checkout') {
            res.setHeader('content-type', 'text/html');
            res.setHeader('content-security-policy', "default-src 'self' 'unsafe-inline'");
            res.setHeader('x-frame-options', 'DENY');
            res.setHeader('set-cookie', 'sid=secret-session');
            res.end(page());
            return;
        }
        if (url.pathname === '/static/app.js') { res.setHeader('content-type', 'text/javascript'); res.end(APP_JS); return; }
        if (url.pathname === '/dyn.js') { res.setHeader('content-type', 'text/javascript'); res.end(DYN_JS); return; }
        res.statusCode = 404;
        res.end();
    });
    await new Promise<void>(r => server.listen(0, '127.0.0.1', () => r()));
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    browser = await launch();
});

after(async () => {
    if (browser) await browser.close().catch(() => undefined);
    await new Promise(r => server.close(r));
});

function job(over: Partial<PageCheckJob> = {}): PageCheckJob {
    return {
        job_id: 'j1', page_id: 7, result_key: 'page_check:result:7', url: `${base}/checkout`,
        track_content: true, element_selectors: ['#title', '#missing', '#total', '!!bad selector'],
        allowed_domains: ['127.0.0.1'], data_policy: {}, ...over,
    };
}

// --- pure helpers ------------------------------------------------------------------

test('sri384 matches the SRI attribute format', () => {
    assert.equal(sri384('alert(1)'), 'sha384-' + require('crypto').createHash('sha384').update('alert(1)').digest('base64'));
});

test('refuses hosts off the allowlist and non-http urls; result key is pinned to the page', () => {
    assert.match(refusalReason(job({ url: 'https://evil.example.net/' }))!, /allowlist/);
    assert.match(refusalReason(job({ url: 'file:///etc/passwd' }))!, /http/);
    assert.equal(safeResultKey(job()), 'page_check:result:7');
    assert.equal(safeResultKey(job({ result_key: 'runs:1:progress' })), null);
});

test('securityHeaders keeps only the security set, never cookies', () => {
    assert.deepEqual(securityHeaders({ 'Set-Cookie': 'a=b', 'X-Frame-Options': 'DENY', Server: 'x' }),
        { 'x-frame-options': 'DENY' });
});

test('mergeScripts adds network-only scripts once', () => {
    const merged = mergeScripts(
        [{ src: 'https://a.test/x.js' }, { inline_sha384: 'sha384-i' }, { src: null, inline_sha384: null }],
        ['https://a.test/x.js', 'https://b.test/skim.js', 'https://b.test/skim.js', 'data:text/javascript,1']);
    assert.deepEqual(merged.map(s => s.src ?? s.inline_sha384), ['https://a.test/x.js', 'sha384-i', 'https://b.test/skim.js']);
    assert.equal(merged[2].dynamic, true);
});

test('redaction covers script urls, headers and text', () => {
    const jwt = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJlLXZhbHVl';
    const out = redactPageObservation({
        url: 'u', headers: { 'content-security-policy': "default-src 'self'" },
        scripts: [{ src: `https://maps.test/api.js?token=${jwt}` }],
        external_hashes: { [`https://maps.test/api.js?token=${jwt}`]: 'sha384-h' },
        text: 'card 4111 1111 1111 1111', elements: { '#t': `bearer ${jwt}`, '#n': null },
    });
    assert.ok(!JSON.stringify(out).includes(jwt));
    assert.ok(!String(out.text).includes('4111 1111 1111 1111'));
    assert.equal(out.elements!['#n'], null);
});

// --- real page load ------------------------------------------------------------------

test('observes scripts, hashes, headers, text and elements from a normal load', async (t) => {
    if (!browser) { t.skip('no Chromium available'); return; }
    const obs = await executePageCheckJob(job(), async () => browser!);
    assert.equal(obs.error, undefined, obs.error);
    assert.equal(obs.status, 200);
    assert.deepEqual(obs.headers, {
        'content-security-policy': "default-src 'self' 'unsafe-inline'", 'x-frame-options': 'DENY' });

    const scripts = obs.scripts!;
    const app = scripts.find(s => s.src?.endsWith('/static/app.js?v=3'))!;
    assert.equal(app.integrity, 'sha384-declared');
    assert.equal(obs.external_hashes![app.src!], sri384(APP_JS));
    assert.ok(scripts.some(s => s.inline_sha384 === sri384(INLINE)));
    // Data blocks are reported with their type; the backend drops them.
    assert.ok(scripts.some(s => s.type === 'application/json'));
    // The script that removed itself is still in the inventory.
    const dyn = scripts.find(s => s.src?.endsWith('/dyn.js'))!;
    assert.equal(dyn.dynamic, true);
    assert.equal(obs.external_hashes![dyn.src!], sri384(DYN_JS));
    // No inline source leaves the worker.
    assert.ok(!JSON.stringify(obs).includes('window.cfg'));

    assert.match(obs.text!, /Pay securely/);
    assert.ok(!obs.text!.includes('4111 1111 1111 1111'));    // redacted PAN
    assert.equal(obs.elements!['#title'], 'Checkout');
    assert.equal(obs.elements!['#missing'], null);
    assert.equal(obs.elements!['!!bad selector'], null);
});

test('content is not collected unless tracked', async (t) => {
    if (!browser) { t.skip('no Chromium available'); return; }
    const obs = await executePageCheckJob(job({ track_content: false }), async () => browser!);
    assert.equal(obs.text, undefined);
    assert.equal(obs.elements, undefined);
    assert.ok((obs.scripts || []).length >= 3);
});

test('refused job never starts a browser; load failures are reported', async () => {
    let started = false;
    const refused = await executePageCheckJob(job({ url: 'https://elsewhere.test/' }), async () => {
        started = true; throw new Error('should not start');
    });
    assert.equal(started, false);
    assert.match(refused.error!, /allowlist/);
    const failed = await executePageCheckJob(job(), async () => { throw new Error('browser crashed'); });
    assert.match(failed.error!, /browser crashed/);
});

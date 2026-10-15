/**
 * Access-matrix cell execution against a local HTTP server: identities are
 * header-based (X-User), so request contexts are real Playwright
 * APIRequestContexts with no browser.
 */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import * as http from 'http';
import { AddressInfo } from 'net';
import {
    AccessCheckJob, classifyError, executeAccessCheckJob, extractId, hostAllowed,
    refusalReason, requestContextFactory, runAccessCheck, safeResultKey, substitute,
} from './access-check';

let server: http.Server;
let base = '';
const OWNERS: Record<string, string> = { '101': 'alice', '202': 'bob' };

before(async () => {
    server = http.createServer((req, res) => {
        const user = String(req.headers['x-user'] || '');
        const url = new URL(req.url || '/', 'http://x');
        let m;
        if (url.pathname === '/api/my-items') {
            const mine = Object.entries(OWNERS).filter(([, o]) => o === user).map(([id]) => ({ id }));
            if (!user) { res.statusCode = 401; res.end(); return; }
            res.setHeader('content-type', 'application/json');
            res.end(JSON.stringify({ items: mine }));
            return;
        }
        if ((m = /^\/api\/items\/(\d+)$/.exec(url.pathname))) {      // enforces ownership
            if (!user) { res.statusCode = 401; res.end(); return; }
            if (OWNERS[m[1]] !== user) { res.statusCode = 403; res.end(); return; }
            res.setHeader('content-type', 'application/json');
            res.setHeader('set-cookie', 'sid=secret-session; HttpOnly');
            res.end(JSON.stringify({ id: m[1], owner: user, password: 'hunter2' }));
            return;
        }
        if ((m = /^\/api\/leaky\/(\d+)$/.exec(url.pathname))) {      // IDOR: no ownership check
            res.end(JSON.stringify({ id: m[1], owner: OWNERS[m[1]] }));
            return;
        }
        if (url.pathname === '/admin') {
            res.statusCode = 302;
            res.setHeader('location', '/login');
            res.end();
            return;
        }
        res.statusCode = 404;
        res.end();
    });
    await new Promise<void>(r => server.listen(0, '127.0.0.1', () => r()));
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

after(async () => { await new Promise(r => server.close(r)); });

const deps = { newRequestContext: requestContextFactory() };
const as = (user: string) => ({ anonymous: false, headers: { 'X-User': user } });

function job(over: Partial<AccessCheckJob> = {}, target: Partial<AccessCheckJob['target']> = {}): AccessCheckJob {
    return {
        job_id: 'j', cell_id: 7, matrix_run_id: 3, result_key: 'access_check:result:7',
        target: { kind: 'request', method: 'GET', url: `${base}/api/items/101`, ...target },
        identity: as('alice'), allowed_domains: ['127.0.0.1'], authorized: false,
        ...over,
    };
}

test('observes 200 for the owner and 403 for another persona', async () => {
    const own = await runAccessCheck(job(), deps);
    assert.equal(own.status, 200);
    const other = await runAccessCheck(job({ identity: as('bob') }), deps);
    assert.equal(other.status, 403);
});

test('anonymous sends no identity headers', async () => {
    const anon = await runAccessCheck(job({ identity: { anonymous: true, headers: { 'X-User': 'alice' } } }), deps);
    assert.equal(anon.status, 401);
});

test('redirects are observed, not followed', async () => {
    const r = await runAccessCheck(job({}, { url: `${base}/admin` }), deps);
    assert.equal(r.status, 302);
});

test('a host off the allowlist is refused without sending anything', async () => {
    const r = await runAccessCheck(job({ allowed_domains: ['example.com'] }), deps);
    assert.equal(r.error_kind, 'refused');
    assert.equal(r.status, undefined);
});

test('a mutating request needs the attestation; the swap owner request too', async () => {
    assert.match(refusalReason(job({}, { method: 'DELETE' }))!, /authorization/);
    assert.equal(refusalReason(job({ authorized: true }, { method: 'DELETE' })), null);
    const withOwner = job({
        owner: { identity: as('alice'), method: 'POST', url: `${base}/api/my-items`,
            extractor: { source: 'json', expr: 'items.0.id' }, placeholder: '{id}' },
    });
    assert.match(refusalReason(withOwner)!, /POST needs authorization/);
});

test('a network error is classified as network (inconclusive), never as a denial', async () => {
    const r = await runAccessCheck(job({}, { url: 'http://127.0.0.1:1/api/items/101' }), deps);
    assert.equal(r.error_kind, 'network');
    assert.equal(r.status, undefined);
    assert.equal(classifyError(new Error('apiRequestContext.fetch: Timeout 30000ms exceeded.')), 'timeout');
});

test('ID swap: fetch as owner, extract, request as another persona', async () => {
    const owner = { identity: as('alice'), method: 'GET', url: `${base}/api/my-items`,
        extractor: { source: 'json' as const, expr: 'items.0.id' }, placeholder: '{id}' };
    const safe = await runAccessCheck(job({ identity: as('bob'), owner }, { url: `${base}/api/items/{id}` }), deps);
    assert.equal(safe.swapped_id, '101');
    assert.equal(safe.url, `${base}/api/items/101`);
    assert.equal(safe.status, 403);
    const leaky = await runAccessCheck(job({ identity: as('bob'), owner }, { url: `${base}/api/leaky/{id}` }), deps);
    assert.equal(leaky.status, 200);   // the IDOR the backend turns into a finding
});

test('ID swap with nothing to extract is extract_failed, not a verdict', async () => {
    const owner = { identity: as('carol'), method: 'GET', url: `${base}/api/my-items`,
        extractor: { source: 'json' as const, expr: 'items.0.id' }, placeholder: '{id}' };
    const r = await runAccessCheck(job({ identity: as('bob'), owner }, { url: `${base}/api/items/{id}` }), deps);
    assert.equal(r.error_kind, 'extract_failed');
});

test('extractors: json path, regex group, header', () => {
    const resp = { headers: { Location: '/items/55' }, body: '{"data":{"id":42}} id=99' };
    assert.equal(extractId({ source: 'regex', expr: 'id=(\\d+)' }, resp), '99');
    assert.equal(extractId({ source: 'header', expr: 'location' }, resp), '/items/55');
    assert.equal(extractId({ source: 'json', expr: 'data.id' }, { headers: {}, body: '{"data":{"id":42}}' }), '42');
    assert.equal(extractId({ source: 'json', expr: 'data' }, { headers: {}, body: '{"data":{"id":42}}' }), null);
});

test('substitute encodes in the url and replaces in JSON bodies', () => {
    const t = substitute({ kind: 'request', method: 'POST', url: 'https://a/x/{id}', body: { ref: '{id}' } }, '{id}', 'a/b');
    assert.equal(t.url, 'https://a/x/a%2Fb');
    assert.deepEqual(t.body, { ref: 'a/b' });
});

test('hostAllowed matches exact host and subdomains only', () => {
    assert.ok(hostAllowed('https://api.shop.example.com/x', ['shop.example.com']));
    assert.ok(!hostAllowed('https://evilshop.example.com/x', ['shop.example.com']));
});

test('a crafted result key is refused', () => {
    assert.equal(safeResultKey(job()), 'access_check:result:7');
    assert.equal(safeResultKey(job({ result_key: 'persona_refresh:result:x' })), null);
});

test('evidence is redacted before it leaves the worker', async () => {
    const r = await executeAccessCheckJob(job(), deps);
    assert.equal(r.headers?.['set-cookie'], '[REDACTED]');
    assert.ok(!String(r.body_excerpt).includes('hunter2'));
});

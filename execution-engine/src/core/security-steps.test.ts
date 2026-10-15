/**
 * Security assertion steps against a real local HTTP server (node `http`).
 * Playwright's APIRequestContext needs no browser, so `page.request` is real
 * and the page's cookie jar is that context's storageState.
 */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import * as http from 'http';
import { AddressInfo } from 'net';
import { request } from 'playwright';
import { TestExecutor } from './test-executor';

let server: http.Server;
let base = '';
const live = new Set<string>();
let loginAttempts = 0;
let counter = 0;

before(async () => {
    server = http.createServer((req, res) => {
        const url = new URL(req.url || '/', 'http://x');
        const cookie = String(req.headers.cookie || '');
        const sid = /sid=([^;]+)/.exec(cookie)?.[1];
        switch (url.pathname) {
            case '/hardened':
                res.setHeader('Strict-Transport-Security', 'max-age=63072000; includeSubDomains');
                res.setHeader('X-Frame-Options', 'DENY');
                res.setHeader('Authorization', 'Bearer super-secret-token');
                res.setHeader('Set-Cookie', ['good=1; HttpOnly; SameSite=Strict; Path=/', 'weak=1; Path=/']);
                res.end('ok');
                return;
            case '/login':
                loginAttempts++;
                res.statusCode = loginAttempts >= 4 ? 429 : 401;
                res.end();
                return;
            case '/no-limit':
                res.statusCode = 401;
                res.end();
                return;
            case '/session': {
                const id = `s${++counter}`;
                live.add(id);
                res.setHeader('Set-Cookie', `sid=${id}; Path=/; HttpOnly`);
                res.end('logged in');
                return;
            }
            case '/logout':
                if (sid) live.delete(sid);
                res.setHeader('Set-Cookie', 'sid=; Path=/; Max-Age=0');
                res.end();
                return;
            case '/fake-logout':     // clears the cookie but never kills the session
                res.setHeader('Set-Cookie', 'sid=; Path=/; Max-Age=0');
                res.end();
                return;
            case '/me':
                if (sid && live.has(sid)) { res.end('{"me":1}'); return; }
                res.statusCode = 401;
                res.end();
                return;
            case '/me-redirect':
                if (sid && live.has(sid)) { res.end('{"me":1}'); return; }
                res.statusCode = 302;
                res.setHeader('Location', '/login?next=/me');
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

async function fakePage(path = '/hardened') {
    const req = await request.newContext();
    const page: any = {
        request: req,
        url: () => base + path,
        context: () => ({ cookies: async () => (await req.storageState()).cookies }),
        waitForLoadState: async () => undefined,
        isClosed: () => false,
    };
    return page;
}

const run = (page: any, step: any, settings: any = {}) =>
    TestExecutor.executeStep(page, page, step, settings, { variables: {} });

test('expect-header present/equals/regex pass and the value is reported redacted', async () => {
    const page = await fakePage();
    const r = await run(page, { type: 'expect-header', params: { name: 'strict-transport-security', mode: 'regex', value: 'max-age=\\d+' } });
    assert.equal(r.present, true);
    await run(page, { type: 'expect-header', params: { name: 'X-Frame-Options', mode: 'equals', value: 'DENY' } });
    const auth = await run(page, { type: 'expect-header', params: { name: 'Authorization', mode: 'present' } });
    assert.equal(auth.value, '[REDACTED]');
    await page.request.dispose();
});

test('expect-header fails on a missing header and on a forbidden one', async () => {
    const page = await fakePage();
    await assert.rejects(run(page, { type: 'expect-header', params: { name: 'Content-Security-Policy' } }),
        /to be present/);
    await assert.rejects(run(page, { type: 'expect-header', params: { name: 'X-Frame-Options', mode: 'absent' } }),
        /to be absent/);
    await page.request.dispose();
});

test('expect-cookie-flags passes a hardened cookie, fails a weak one, never reports values', async () => {
    const page = await fakePage();
    const ok = await run(page, { type: 'expect-cookie-flags', params: { name: 'good', url: '/hardened', httpOnly: true, sameSite: 'Strict' } });
    assert.equal(ok.observed[0].httpOnly, true);
    assert.ok(!JSON.stringify(ok).includes('"value"'));
    await assert.rejects(run(page, { type: 'expect-cookie-flags', params: { name: 'weak', httpOnly: true } }),
        /HttpOnly=false/);
    await assert.rejects(run(page, { type: 'expect-cookie-flags', params: { name: 'absent' } }),
        /to be set/);
    await page.request.dispose();
});

test('expect-blocked-after passes when throttled by attempt N and fails when never blocked', async () => {
    const page = await fakePage();
    const settings = { security_allowlist: ['127.0.0.1'] };
    loginAttempts = 0;
    const r = await run(page, { type: 'expect-blocked-after', value: base + '/login', params: { attempts: 5, authorized: true } }, settings);
    assert.equal(r.blocked_at, 4);
    assert.deepEqual(r.statuses, [401, 401, 401, 429]);
    await assert.rejects(
        run(page, { type: 'expect-blocked-after', value: base + '/no-limit', params: { attempts: 3, authorized: true } }, settings),
        /Not blocked after 3/);
    await page.request.dispose();
});

test('expect-blocked-after refuses without the allowlist or the attestation', async () => {
    const page = await fakePage();
    await assert.rejects(
        run(page, { type: 'expect-blocked-after', value: base + '/login', params: { authorized: true } }, {}),
        /not on this project's security allowlist/);
    await assert.rejects(
        run(page, { type: 'expect-blocked-after', value: base + '/login', params: {} }, { security_allowlist: ['127.0.0.1'] }),
        /authorized=true/);
    await page.request.dispose();
});

test('expect-session-dead passes when logout kills the session server-side', async () => {
    const page = await fakePage();
    await page.request.get(base + '/session');
    const r = await run(page, { type: 'expect-session-dead', value: base + '/me', params: { logout_url: base + '/logout' } });
    assert.equal(r.status, 401);
    assert.equal(r.replayed_cookies, 1);
    await page.request.dispose();
});

test('expect-session-dead accepts a redirect to login', async () => {
    const page = await fakePage();
    await page.request.get(base + '/session');
    const r = await run(page, { type: 'expect-session-dead', value: base + '/me-redirect', params: { logout_url: base + '/logout' } });
    assert.equal(r.verdict, 'redirected-to-login');
    await page.request.dispose();
});

test('expect-session-dead fails when logout only clears the browser cookie', async () => {
    const page = await fakePage();
    await page.request.get(base + '/session');
    await assert.rejects(
        run(page, { type: 'expect-session-dead', value: base + '/me', params: { logout_url: base + '/fake-logout' } }),
        /still accepted: .* returned 200/);
    await page.request.dispose();
});

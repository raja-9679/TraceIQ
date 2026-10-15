import { test } from 'node:test';
import assert from 'node:assert/strict';

import { EgressGuard, classifyIp, hostAllowed, installEgressGuard } from './egress-guard';

const resolver = (map: Record<string, string[]>) => async (host: string) => {
    if (!(host in map)) throw new Error('ENOTFOUND');
    return map[host];
};

test('ip classification mirrors net_guard', () => {
    assert.equal(classifyIp('169.254.169.254'), 'never');
    assert.equal(classifyIp('0.0.0.0'), 'never');
    assert.equal(classifyIp('224.0.0.1'), 'never');
    assert.equal(classifyIp('10.1.2.3'), 'private');
    assert.equal(classifyIp('172.20.0.5'), 'private');
    assert.equal(classifyIp('192.168.1.1'), 'private');
    assert.equal(classifyIp('127.0.0.1'), 'private');
    assert.equal(classifyIp('8.8.8.8'), 'public');
    assert.equal(classifyIp('::1'), 'private');
    assert.equal(classifyIp('fe80::1'), 'never');
    assert.equal(classifyIp('fd00::1'), 'private');
    assert.equal(classifyIp('::ffff:169.254.169.254'), 'never');
    assert.equal(classifyIp('2606:4700::1111'), 'public');
});

test('allowlist entries cover subdomains but not look-alikes', () => {
    assert.ok(hostAllowed('app.example.com', ['example.com']));
    assert.ok(hostAllowed('example.com', ['*.example.com']));
    assert.ok(!hostAllowed('evilexample.com', ['example.com']));
    assert.ok(!hostAllowed('example.com.evil.io', ['example.com']));
});

test('hosts off the allowlist are blocked; listed public hosts pass', async () => {
    const g = new EgressGuard({ enforce: true, allowed_hosts: ['example.com'], allow_private: false },
        resolver({ 'app.example.com': ['93.184.216.34'] }));
    assert.deepEqual(await g.decide('https://app.example.com/x'), { allowed: true });
    const v = await g.decide('https://tracker.io/pixel');
    assert.equal(v.allowed, false);
    assert.match((v as any).reason, /not on the egress allowlist/);
});

test('an allowlisted name resolving to a private address is refused (rebinding)', async () => {
    const g = new EgressGuard({ enforce: true, allowed_hosts: ['example.com'], allow_private: false },
        resolver({ 'example.com': ['10.0.0.8'] }));
    const v = await g.decide('http://example.com/');
    assert.equal(v.allowed, false);
    assert.match((v as any).reason, /private address/);
});

test('allow_private admits internal targets but never metadata', async () => {
    const g = new EgressGuard({ enforce: true, allowed_hosts: ['todolite', 'meta.internal'], allow_private: true },
        resolver({ todolite: ['172.18.0.4'], 'meta.internal': ['169.254.169.254'] }));
    assert.deepEqual(await g.decide('http://todolite:3000/'), { allowed: true });
    assert.deepEqual(await g.decide('http://10.0.0.9/'), { allowed: true });
    assert.equal((await g.decide('http://meta.internal/latest')).allowed, false);
    assert.equal((await g.decide('http://169.254.169.254/latest/meta-data')).allowed, false);
});

test('non-network schemes are untouched', async () => {
    const g = new EgressGuard({ enforce: true, allowed_hosts: [], allow_private: false }, resolver({}));
    assert.deepEqual(await g.decide('data:text/plain,hi'), { allowed: true });
    assert.deepEqual(await g.decide('blob:https://x/1'), { allowed: true });
});

function fakeContext() {
    let handler: any = null;
    return {
        route: async (_: string, h: any) => { handler = h; },
        fire: async (url: string) => {
            const calls: string[] = [];
            await handler({
                request: () => ({ url: () => url, method: () => 'GET', resourceType: () => 'script' }),
                fallback: async () => { calls.push('fallback'); },
                abort: async (code: string) => { calls.push(`abort:${code}`); },
            });
            return calls;
        },
        get installed() { return handler !== null; },
    };
}

test('not enforced = no route installed (unchanged behaviour)', async () => {
    const ctx = fakeContext();
    assert.equal(await installEgressGuard(ctx, undefined, []), null);
    assert.equal(await installEgressGuard(ctx, { enforce: false, allowed_hosts: [], allow_private: false }, []), null);
    assert.equal(ctx.installed, false);
});

test('blocked requests are aborted and recorded as evidence; allowed ones fall through', async () => {
    const ctx = fakeContext();
    const sink: any[] = [];
    await installEgressGuard(ctx, { enforce: true, allowed_hosts: ['example.com'], allow_private: false }, sink, {
        testCaseName: () => 'Login', redact: (u) => u.replace(/token=[^&]+/, 'token=[REDACTED]'),
        resolver: resolver({ 'example.com': ['93.184.216.34'] }),
    });
    assert.deepEqual(await ctx.fire('https://example.com/'), ['fallback']);
    assert.deepEqual(await ctx.fire('https://ads.io/p?token=abc'), ['abort:blockedbyclient']);
    assert.equal(sink.length, 1);
    assert.equal(sink[0].blocked, true);
    assert.equal(sink[0].status, 0);
    assert.equal(sink[0].testCaseName, 'Login');
    assert.equal(sink[0].url, 'https://ads.io/p?token=[REDACTED]');
    assert.match(sink[0].blockReason, /ads\.io/);
});

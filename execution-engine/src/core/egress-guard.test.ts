import { test } from 'node:test';
import assert from 'node:assert/strict';

import { EgressGuard, canonicalHost, classifyIp, hostAllowed, installEgressGuard } from './egress-guard';

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

// --- parser differentials (WP8 security review) --------------------------------
// Every form below is how a browser can be pointed at the metadata service or
// loopback without writing the dotted address. Each must classify and decide
// exactly as the canonical address would.

test('IPv6 forms embedding a blocked IPv4 address are classified by that address', () => {
    // The URL serialiser rewrites [::ffff:169.254.169.254] as ::ffff:a9fe:a9fe.
    assert.equal(classifyIp('::ffff:a9fe:a9fe'), 'never');
    assert.equal(classifyIp('::ffff:7f00:1'), 'private');
    assert.equal(classifyIp('0:0:0:0:0:ffff:a9fe:a9fe'), 'never');
    assert.equal(classifyIp('::ffff:0:a9fe:a9fe'), 'never');           // IPv4-translated
    assert.equal(classifyIp('::a9fe:a9fe'), 'never');                   // IPv4-compatible
    assert.equal(classifyIp('::127.0.0.1'), 'private');
    assert.equal(classifyIp('64:ff9b::a9fe:a9fe'), 'never');            // NAT64
    assert.equal(classifyIp('64:ff9b::a00:1'), 'private');
    assert.equal(classifyIp('2002:a9fe:a9fe::1'), 'never');             // 6to4
    assert.equal(classifyIp('2002:0a00:0001::'), 'private');
    assert.equal(classifyIp('2001:0:4136:e378:8000:63bf:3fff:fdd2'), 'never');   // Teredo
    assert.equal(classifyIp('fec0::1'), 'never');                        // site-local
    assert.equal(classifyIp('FE80::1'), 'never');
    assert.equal(classifyIp('[::1]'), 'private');
    assert.equal(classifyIp('::ffff:8.8.8.8'), 'public');
    assert.equal(classifyIp('not-an-ip'), 'never');
});

test('IPv4 shorthand is classified as the browser parses it', () => {
    for (const form of ['2130706433', '0x7f.1', '127.1', '0177.0.0.1', '0x7f000001']) {
        assert.equal(classifyIp(form), 'private', form);
    }
    for (const form of ['0xa9fea9fe', '2852039166', '169.254.43518', '0251.0376.0251.0376']) {
        assert.equal(classifyIp(form), 'never', form);
    }
});

test('canonicalHost matches the WHATWG host parser', () => {
    assert.equal(canonicalHost('LOCALHOST.'), 'localhost');
    assert.equal(canonicalHost('127.0.0.1.'), '127.0.0.1');
    assert.equal(canonicalHost('2130706433'), '127.0.0.1');
    assert.equal(canonicalHost('[::FFFF:169.254.169.254]'), '::ffff:a9fe:a9fe');
    assert.equal(canonicalHost('::ffff:169.254.169.254'), '::ffff:a9fe:a9fe');
    assert.equal(canonicalHost('Bücher.Example'), 'xn--bcher-kva.example');
    assert.equal(canonicalHost('*.Example.COM.'), 'example.com');
    assert.equal(canonicalHost('exa mple.com'), null);
});

test('allowlist entries and hosts are compared in canonical form', () => {
    assert.ok(hostAllowed('xn--bcher-kva.example', ['bücher.example']));
    assert.ok(hostAllowed('APP.example.com.', ['example.com.']));
    assert.ok(hostAllowed('::ffff:a9fe:a9fe', ['::ffff:169.254.169.254']));   // same address...
    assert.ok(!hostAllowed('10.0.0.1', ['0.0.1']));                          // ...but no suffix games on IPs
    assert.ok(!hostAllowed('1.10.0.0.1', ['10.0.0.1']));
});

test('no URL spelling of a blocked address gets through, even when allowlisted', async () => {
    const allowed_hosts = ['::ffff:169.254.169.254', '::ffff:127.0.0.1', '169.254.169.254',
        '127.0.0.1', 'example.com'];
    const g = new EgressGuard({ enforce: true, allowed_hosts, allow_private: false },
        resolver({ 'example.com': ['93.184.216.34'] }));
    const urls = [
        'http://[::ffff:169.254.169.254]/latest/meta-data/',
        'http://[::ffff:a9fe:a9fe]/',
        'http://[0:0:0:0:0:ffff:a9fe:a9fe]/',
        'http://[::a9fe:a9fe]/',
        'http://[64:ff9b::a9fe:a9fe]/',
        'http://[2002:a9fe:a9fe::]/',
        'http://[::ffff:127.0.0.1]:8080/',
        'http://2852039166/',
        'http://0xa9.0xfe.0xa9.0xfe/',
        'http://169.254.169.254./',
        'http://2130706433/',
        'http://0x7f.1/',
        'http://127.1/',
        'http://①②⑦.0.0.1/',
        'http://example.com@169.254.169.254/',
        'HTTP://[::FFFF:A9FE:A9FE]/',
    ];
    for (const u of urls) {
        const v = await g.decide(u);
        assert.equal(v.allowed, false, u);
    }
    // userinfo is not the host, and for http(s) a backslash is a path
    // separator — browser and guard both see example.com here.
    assert.deepEqual(await g.decide('http://169.254.169.254@example.com/'), { allowed: true });
    assert.deepEqual(await g.decide('http://example.com\\@169.254.169.254/'), { allowed: true });
});

test('allowlisted names resolving to embedded-IPv4 IPv6 forms are refused', async () => {
    const g = new EgressGuard({ enforce: true, allowed_hosts: ['example.com'], allow_private: true },
        resolver({
            'a.example.com': ['::ffff:a9fe:a9fe'],
            'b.example.com': ['64:ff9b::a9fe:a9fe'],
            'c.example.com': ['93.184.216.34', '::a9fe:a9fe'],
        }));
    for (const h of ['a', 'b', 'c']) {
        assert.equal((await g.decide(`https://${h}.example.com/`)).allowed, false, h);
    }
});

test('a resolver failure fails closed', async () => {
    const g = new EgressGuard({ enforce: true, allowed_hosts: ['example.com'], allow_private: false },
        resolver({}));
    const v = await g.decide('https://nowhere.example.com/');
    assert.equal(v.allowed, false);
    assert.match((v as any).reason, /could not be resolved/);
});

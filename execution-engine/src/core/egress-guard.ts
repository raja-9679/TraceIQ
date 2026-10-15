// Worker egress allowlist (WP8): what the browser under test may reach.
//
// The backend resolves the policy at dispatch (app/services/network_policy.py)
// and ships it as job.settings.egress_policy; absent means "not enforced" and
// nothing here runs. When enforced, every http(s)/ws(s) request is checked
// before it leaves the browser and aborted (net::ERR_BLOCKED_BY_CLIENT) unless
// allowed. Each block is recorded as a network event with `blocked: true`, so
// it shows up in the run's network evidence next to the requests that did go
// out — "the page called a host it should not have" is itself a finding.
//
// Rules mirror the backend's net_guard:
//   * link-local / cloud metadata, multicast, unspecified and reserved
//     addresses are NEVER allowed, whatever the allowlist says;
//   * private + loopback addresses need allow_private (which the backend only
//     grants when the instance allows private targets at all) — an allowlisted
//     public name that resolves to 10.x is refused;
//   * an allowlist entry covers the host and its subdomains.
//
// Limits, stated so nobody mistakes this for a firewall: Playwright routing
// does not see WebSocket upgrades or service-worker fetches, and the browser
// resolves DNS itself, so a rebinding host can still differ between our
// lookup and its connection. Network egress rules on the worker remain the
// real control; this is the per-project policy layer above them.

import * as dns from 'dns';
import * as net from 'net';

export interface EgressPolicy {
    enforce: boolean;
    allowed_hosts: string[];
    allow_private: boolean;
}

export type Verdict = { allowed: true } | { allowed: false; reason: string };

export type Resolver = (host: string) => Promise<string[]>;

const defaultResolver: Resolver = async (host) =>
    (await dns.promises.lookup(host, { all: true })).map(a => a.address);

// --- address parsing ---------------------------------------------------------
//
// Every host is canonicalised exactly the way the browser does before it is
// compared or classified: Chromium follows the WHATWG URL host parser, so
// `http://2130706433/`, `http://0x7f.1/`, `http://127.0.0.1./` and
// `http://①②⑦.0.0.1/` are all 127.0.0.1 to it, and they must be to us too.
// IPv6 is then parsed to its 128 bits rather than pattern-matched, because the
// serialiser writes `[::ffff:169.254.169.254]` as `::ffff:a9fe:a9fe` and a
// regex over the dotted form never sees it. IPv6 forms that embed an IPv4
// address the host stack will actually reach (v4-mapped, v4-compatible, NAT64
// 64:ff9b::/96, 6to4 2002::/16) are classified by that IPv4 address.

function ipv4ToInt(ip: string): number {
    return ip.split('.').reduce((acc, oct) => (acc * 256) + parseInt(oct, 10), 0) >>> 0;
}

function intToIpv4(n: number): string {
    return [n >>> 24, (n >>> 16) & 255, (n >>> 8) & 255, n & 255].join('.');
}

function inV4(ip: string, base: string, bits: number): boolean {
    const mask = bits === 0 ? 0 : (0xffffffff << (32 - bits)) >>> 0;
    return ((ipv4ToInt(ip) & mask) >>> 0) === ((ipv4ToInt(base) & mask) >>> 0);
}

/** Eight 16-bit groups, or null if `ip` is not an IPv6 literal. */
export function parseIpv6(ip: string): number[] | null {
    let text = ip.toLowerCase().replace(/^\[|\]$/g, '');
    const zone = text.indexOf('%');
    if (zone >= 0) text = text.slice(0, zone);
    if (net.isIP(text) !== 6) return null;
    // A dotted IPv4 tail becomes two groups.
    const tail = text.match(/(\d+\.\d+\.\d+\.\d+)$/);
    if (tail) {
        const n = ipv4ToInt(tail[1]);
        text = text.slice(0, -tail[1].length) + `${(n >>> 16).toString(16)}:${(n & 0xffff).toString(16)}`;
    }
    const halves = text.split('::');
    const head = halves[0] ? halves[0].split(':') : [];
    const rest = halves.length > 1 && halves[1] ? halves[1].split(':') : [];
    const fill = halves.length > 1 ? 8 - head.length - rest.length : 0;
    const groups = [...head, ...Array(fill).fill('0'), ...rest].map(g => parseInt(g, 16));
    return groups.length === 8 && groups.every(g => g >= 0 && g <= 0xffff) ? groups : null;
}

function v4FromGroups(hi: number, lo: number): string {
    return intToIpv4(((hi << 16) | lo) >>> 0);
}

/** 'never' | 'private' | 'public' for an IP literal (any textual form). */
export function classifyIp(ip: string): 'never' | 'private' | 'public' {
    const v4 = canonicalIpv4(ip);
    if (v4) {
        if (inV4(v4, '169.254.0.0', 16)) return 'never';   // link-local / metadata
        if (inV4(v4, '0.0.0.0', 8)) return 'never';        // unspecified / "this network"
        if (inV4(v4, '224.0.0.0', 4)) return 'never';      // multicast
        if (inV4(v4, '240.0.0.0', 4)) return 'never';      // reserved + broadcast
        if (inV4(v4, '127.0.0.0', 8) || inV4(v4, '10.0.0.0', 8) || inV4(v4, '172.16.0.0', 12)
            || inV4(v4, '192.168.0.0', 16) || inV4(v4, '100.64.0.0', 10)
            || inV4(v4, '198.18.0.0', 15) || inV4(v4, '192.0.0.0', 24)
            || inV4(v4, '192.0.2.0', 24) || inV4(v4, '198.51.100.0', 24)
            || inV4(v4, '203.0.113.0', 24)) return 'private';
        return 'public';
    }
    const g = parseIpv6(ip);
    if (!g) return 'never';                      // not an IP at all: refuse, never guess
    const zeroUpTo = (n: number) => g.slice(0, n).every(x => x === 0);
    if (zeroUpTo(8)) return 'never';                                  // ::
    if (zeroUpTo(7) && g[7] === 1) return 'private';                  // ::1
    if (zeroUpTo(5) && g[5] === 0xffff) return classifyIp(v4FromGroups(g[6], g[7]));  // ::ffff:a.b.c.d
    if (zeroUpTo(4) && g[4] === 0xffff && g[5] === 0) return classifyIp(v4FromGroups(g[6], g[7])); // ::ffff:0:a.b.c.d
    if (zeroUpTo(6)) return classifyIp(v4FromGroups(g[6], g[7]));     // ::a.b.c.d (v4-compatible)
    if (g[0] === 0x64 && g[1] === 0xff9b && g.slice(2, 6).every(x => x === 0)) {
        return classifyIp(v4FromGroups(g[6], g[7]));                  // NAT64 64:ff9b::/96
    }
    if (g[0] === 0x64 && g[1] === 0xff9b && g[2] === 1) return 'private';   // 64:ff9b:1::/48 local NAT64
    if (g[0] === 0x2002) return classifyIp(v4FromGroups(g[1], g[2]));       // 6to4
    if (g[0] === 0x2001 && g[1] === 0) return 'never';                // Teredo: embedded client is obfuscated
    if (g[0] === 0x2001 && g[1] === 0x0db8) return 'never';           // documentation
    if (g[0] === 0x0100 && g[1] === 0 && g[2] === 0 && g[3] === 0) return 'never';  // 100::/64 discard
    if ((g[0] & 0xffc0) === 0xfe80) return 'never';                   // fe80::/10 link-local
    if ((g[0] & 0xffc0) === 0xfec0) return 'never';                   // fec0::/10 site-local
    if ((g[0] & 0xff00) === 0xff00) return 'never';                   // multicast
    if ((g[0] & 0xfe00) === 0xfc00) return 'private';                 // fc00::/7 ULA
    if (g[0] < 0x0100) return 'never';                                // rest of ::/8 is reserved
    return 'public';
}

/** Dotted-quad for an IPv4 literal in any form the URL parser accepts, else null. */
function canonicalIpv4(host: string): string | null {
    if (net.isIP(host) === 4) return host;
    if (host.includes(':') || host.startsWith('[')) return null;
    try {
        const h = new URL(`http://${host}/`).hostname;
        return net.isIP(h) === 4 ? h : null;
    } catch {
        return null;
    }
}

/**
 * Canonical host as the browser sees it: WHATWG-parsed (IPv4 shorthand,
 * IDN → punycode, case, percent-decoding), brackets and a trailing dot
 * removed, IPv6 in its compressed serialisation. Returns null for something
 * that is not a valid host.
 */
export function canonicalHost(raw: string): string | null {
    let text = String(raw ?? '').trim();
    if (!text) return null;
    text = text.replace(/^\*\./, '');
    const bare = text.replace(/^\[|\]$/g, '');
    const literal6 = net.isIP(bare.split('%')[0]) === 6;
    let host: string;
    try {
        host = new URL(`http://${literal6 ? `[${bare}]` : text}/`).hostname;
    } catch {
        return null;
    }
    host = host.toLowerCase().replace(/^\[|\]$/g, '');
    // `a.b.` and `a.b` are the same name to DNS; the URL parser keeps the dot
    // for names (only an IPv4 tail is normalised), so drop it here.
    host = host.replace(/\.$/, '');
    return host || null;
}

export function hostAllowed(host: string, allowed: string[]): boolean {
    const h = canonicalHost(host);
    if (!h) return false;
    const isLiteral = net.isIP(h) !== 0;
    return allowed.some(entry => {
        const e = canonicalHost(String(entry));
        if (!e) return false;
        if (h === e) return true;
        // An IP entry covers exactly itself; names cover their subdomains.
        return !isLiteral && net.isIP(e) === 0 && h.endsWith(`.${e}`);
    });
}

const NETWORK_SCHEMES = new Set(['http:', 'https:', 'ws:', 'wss:']);

export class EgressGuard {
    private cache = new Map<string, { at: number; ips: string[] }>();

    constructor(private policy: EgressPolicy, private resolver: Resolver = defaultResolver,
                private cacheMs = 30_000) {}

    private async resolve(host: string): Promise<string[]> {
        if (net.isIP(host)) return [host];
        const hit = this.cache.get(host);
        if (hit && Date.now() - hit.at < this.cacheMs) return hit.ips;
        const ips = await this.resolver(host);
        this.cache.set(host, { at: Date.now(), ips });
        return ips;
    }

    async decide(rawUrl: string): Promise<Verdict> {
        let url: URL;
        try {
            url = new URL(rawUrl);
        } catch {
            return { allowed: false, reason: 'unparseable URL' };
        }
        if (!NETWORK_SCHEMES.has(url.protocol)) return { allowed: true };   // data:, blob:, about:
        const host = canonicalHost(url.hostname);
        if (!host) return { allowed: false, reason: 'URL has no usable host' };
        const listed = hostAllowed(host, this.policy.allowed_hosts || []);
        const literal = net.isIP(host) !== 0;
        if (literal) {
            const cls = classifyIp(host);
            if (cls === 'never') return { allowed: false, reason: `${host} is a link-local/metadata or reserved address` };
            if (cls === 'private' && !this.policy.allow_private) return { allowed: false, reason: `${host} is a private address and private targets are not allowed` };
            if (listed || (cls === 'private' && this.policy.allow_private)) return { allowed: true };
            return { allowed: false, reason: `${host} is not on the egress allowlist` };
        }
        if (!listed) return { allowed: false, reason: `${host} is not on the egress allowlist` };
        let ips: string[];
        try {
            ips = await this.resolve(host);
        } catch {
            // Fail closed. Chromium has its own resolver (and /etc/hosts
            // handling); "we could not resolve it" is not evidence that the
            // browser cannot, and an unchecked address is exactly what this
            // guard exists to stop.
            return { allowed: false, reason: `${host} could not be resolved for the egress check` };
        }
        if (!ips.length) return { allowed: false, reason: `${host} resolved to no address` };
        for (const ip of ips) {
            const cls = classifyIp(ip);
            if (cls === 'never') return { allowed: false, reason: `${host} resolves to ${ip}, a link-local/metadata or reserved address` };
            if (cls === 'private' && !this.policy.allow_private) {
                return { allowed: false, reason: `${host} resolves to the private address ${ip} and private targets are not allowed` };
            }
        }
        return { allowed: true };
    }
}

/**
 * Install the guard on a browser context. Register AFTER any other
 * context.route('**\/*') handler: Playwright runs the most recently added
 * route first, and an allowed request is handed on with route.fallback().
 */
export async function installEgressGuard(
    context: { route: (url: string, handler: (route: any) => any) => Promise<unknown> },
    policy: EgressPolicy | undefined | null,
    sink: any[],
    opts: { testCaseName?: () => string | null; redact?: (s: string) => string; resolver?: Resolver; maxEvents?: number } = {},
): Promise<EgressGuard | null> {
    if (!policy || !policy.enforce) return null;
    const guard = new EgressGuard(policy, opts.resolver);
    const max = opts.maxEvents ?? 2000;
    await context.route('**/*', async (route: any) => {
        const request = route.request();
        const url: string = request.url();
        let verdict: Verdict;
        try {
            verdict = await guard.decide(url);
        } catch (err: any) {
            verdict = { allowed: false, reason: `egress check failed: ${err?.message || err}` };
        }
        if (verdict.allowed) {
            await route.fallback();
            return;
        }
        if (sink.length < max) {
            sink.push({
                testCaseName: opts.testCaseName ? opts.testCaseName() : null,
                url: opts.redact ? opts.redact(url) : url,
                method: request.method(),
                resourceType: request.resourceType(),
                status: 0,
                blocked: true,
                blockReason: verdict.reason,
                startTime: Date.now(),
                endTime: Date.now(),
                duration: 0,
            });
        }
        await route.abort('blockedbyclient');
    });
    return guard;
}

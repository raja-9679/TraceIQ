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

function ipv4ToInt(ip: string): number {
    return ip.split('.').reduce((acc, oct) => (acc * 256) + parseInt(oct, 10), 0);
}

function inV4(ip: string, base: string, bits: number): boolean {
    const mask = bits === 0 ? 0 : (0xffffffff << (32 - bits)) >>> 0;
    return ((ipv4ToInt(ip) & mask) >>> 0) === ((ipv4ToInt(base) & mask) >>> 0);
}

/** 'never' | 'private' | 'public' for an IP literal. */
export function classifyIp(ip: string): 'never' | 'private' | 'public' {
    const kind = net.isIP(ip);
    if (kind === 4) {
        if (inV4(ip, '169.254.0.0', 16)) return 'never';   // link-local / metadata
        if (inV4(ip, '0.0.0.0', 8)) return 'never';        // unspecified / "this network"
        if (inV4(ip, '224.0.0.0', 4)) return 'never';      // multicast
        if (inV4(ip, '240.0.0.0', 4)) return 'never';      // reserved + broadcast
        if (inV4(ip, '127.0.0.0', 8) || inV4(ip, '10.0.0.0', 8) || inV4(ip, '172.16.0.0', 12)
            || inV4(ip, '192.168.0.0', 16) || inV4(ip, '100.64.0.0', 10)
            || inV4(ip, '198.18.0.0', 15) || inV4(ip, '192.0.0.0', 24)) return 'private';
        return 'public';
    }
    if (kind === 6) {
        const lower = ip.toLowerCase();
        const mapped = lower.match(/^::ffff:(\d+\.\d+\.\d+\.\d+)$/);
        if (mapped) return classifyIp(mapped[1]);
        if (lower === '::') return 'never';
        if (lower === '::1') return 'private';
        if (/^fe[89ab]/.test(lower)) return 'never';           // fe80::/10
        if (/^ff/.test(lower)) return 'never';                 // multicast
        if (/^f[cd]/.test(lower)) return 'private';            // fc00::/7 ULA
        return 'public';
    }
    return 'public';
}

export function hostAllowed(host: string, allowed: string[]): boolean {
    const h = host.toLowerCase().replace(/^\[|\]$/g, '').replace(/\.$/, '');
    return allowed.some(entry => {
        const e = String(entry).toLowerCase().replace(/^\*\./, '');
        return !!e && (h === e || (!net.isIP(h) && h.endsWith(`.${e}`)));
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
        const host = url.hostname.toLowerCase().replace(/^\[|\]$/g, '');
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
            // The browser would fail to resolve it as well; let it, so the
            // step error is the usual DNS one rather than a policy block.
            return { allowed: true };
        }
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

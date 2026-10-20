// Trace correlation with the application under test (WP16a, decision V1).
//
// When the project opted in, the backend ships `settings.trace_correlation =
// {enabled, hosts, cross_origin}` (app/services/trace_correlation.py): the
// project's OWN hosts — environment base URLs, suite allowed_domains and the
// security allowlist. Requests to those hosts, and only those, get a W3C
// `traceparent: 00-<trace id>-<step span id>-01`; third parties never see it.
// One trace id per case attempt, one span id per step, both recorded in the
// step's evidence (`trace`) so the backend can read the application's spans
// back from Jaeger/Tempo. `tracestate` is not sent: we carry no vendor state.
//
// CORS: a custom header on a cross-origin fetch/XHR can trigger a preflight,
// and an API whose CORS policy does not allow `traceparent` would then fail
// the request under test. Whether a header added at the interception layer
// does that differs per engine, so it is not relied on either way: browser
// requests get the header only when they are navigations (never CORS) or
// same-origin with the frame that made them, unless the project set
// `cross_origin` (its API allows the header). API steps go through
// Playwright's request context — no browser, no CORS — and always get it for
// own hosts.
//
// Wiring: the browser side rides the existing header-injection route
// (network-interceptor.ts), which is the route that finally `continue()`s; the
// egress guard is registered after it, so it runs first and a blocked request
// never reaches the injection. An existing `traceparent` (the app's own
// browser instrumentation, or a suite header) is never overwritten.
import { randomBytes } from 'crypto';

export interface TraceCorrelationConfig {
    enabled: boolean;
    hosts: string[];
    cross_origin?: boolean;
}

export interface StepTrace { trace_id: string; span_id: string; requests: number }

type RandomSource = (n: number) => Buffer;

function hexId(bytes: number, rand: RandomSource): string {
    // All-zero ids are invalid per W3C; vanishingly unlikely, but cheap to rule out.
    for (;;) {
        const id = rand(bytes).toString('hex');
        if (/[^0]/.test(id)) return id;
    }
}

export const newTraceId = (rand: RandomSource = randomBytes) => hexId(16, rand);
export const newSpanId = (rand: RandomSource = randomBytes) => hexId(8, rand);

export function traceparent(traceId: string, spanId: string): string {
    return `00-${traceId}-${spanId}-01`;
}

function hostOf(url: string): string | null {
    try {
        const u = new URL(url);
        if (u.protocol !== 'http:' && u.protocol !== 'https:') return null;
        return u.hostname.toLowerCase().replace(/\.$/, '') || null;
    } catch {
        return null;
    }
}

/**
 * Same matching as the backend's `site_crawl.host_allowed`: a plain entry is
 * that host exactly; `*.example.com` / `.example.com` cover its subdomains
 * (and the apex).
 */
export function hostIsOwn(host: string | null, hosts: string[]): boolean {
    if (!host) return false;
    const h = host.toLowerCase();
    for (const raw of hosts || []) {
        const entry = String(raw || '').toLowerCase().trim();
        if (!entry) continue;
        if (entry.startsWith('*.') || entry.startsWith('.')) {
            const suffix = entry.replace(/^\*/, '');            // ".example.com"
            if (h.endsWith(suffix) || h === suffix.slice(1)) return true;
        } else if (h === entry) {
            return true;
        }
    }
    return false;
}

function originOf(url: string | null | undefined): string | null {
    if (!url) return null;
    try {
        const o = new URL(url).origin;
        return o === 'null' ? null : o;
    } catch {
        return null;
    }
}

function hasTraceparent(headers: Record<string, any> | undefined): boolean {
    return !!headers && Object.keys(headers).some(k => k.toLowerCase() === 'traceparent');
}

export interface BrowserRequestFacts {
    url: string;
    navigation: boolean;
    frameUrl?: string | null;
    headers?: Record<string, any>;
}

/** Should a browser request carry the header? Pure; exported for tests. */
export function browserInjectionDecision(
    cfg: TraceCorrelationConfig, req: BrowserRequestFacts,
): { inject: boolean; reason: string } {
    if (!cfg?.enabled) return { inject: false, reason: 'disabled' };
    if (!hostIsOwn(hostOf(req.url), cfg.hosts)) return { inject: false, reason: 'third_party' };
    if (hasTraceparent(req.headers)) return { inject: false, reason: 'already_set' };
    if (req.navigation) return { inject: true, reason: 'navigation' };
    const target = originOf(req.url);
    if (target && target === originOf(req.frameUrl)) return { inject: true, reason: 'same_origin' };
    if (cfg.cross_origin) return { inject: true, reason: 'cross_origin_opt_in' };
    return { inject: false, reason: 'cross_origin' };
}

/** Should an API-step request (no browser CORS) carry the header? */
export function apiInjectionDecision(
    cfg: TraceCorrelationConfig, url: string, headers?: Record<string, any>,
): boolean {
    return !!cfg?.enabled && hostIsOwn(hostOf(url), cfg.hosts) && !hasTraceparent(headers);
}

/**
 * Per-context state: the current attempt's trace id and the open step's span.
 * Outside a step (about:blank, suite hooks, background polling between steps)
 * nothing is injected — there is no step to attribute the spans to.
 */
export class TraceCorrelation {
    private traceId: string;
    private span: { id: string; requests: number } | null = null;

    constructor(readonly config: TraceCorrelationConfig, private readonly rand: RandomSource = randomBytes) {
        this.traceId = newTraceId(rand);
    }

    static from(settings: any): TraceCorrelation | null {
        const cfg = settings?.trace_correlation;
        if (!cfg || cfg.enabled !== true || !Array.isArray(cfg.hosts) || cfg.hosts.length === 0) return null;
        return new TraceCorrelation({
            enabled: true,
            hosts: cfg.hosts.filter((h: any) => typeof h === 'string'),
            cross_origin: cfg.cross_origin === true,
        });
    }

    get currentTraceId(): string { return this.traceId; }

    /** A new case, or a retry of one: fresh trace id. */
    startAttempt(): void {
        this.traceId = newTraceId(this.rand);
        this.span = null;
    }

    beginStep(): void {
        this.span = { id: newSpanId(this.rand), requests: 0 };
    }

    endStep(): StepTrace | null {
        if (!this.span) return null;
        const out = { trace_id: this.traceId, span_id: this.span.id, requests: this.span.requests };
        this.span = null;
        return out;
    }

    private header(): Record<string, string> {
        this.span!.requests++;
        return { traceparent: traceparent(this.traceId, this.span!.id) };
    }

    browserHeaders(req: BrowserRequestFacts): Record<string, string> | null {
        if (!this.span || !browserInjectionDecision(this.config, req).inject) return null;
        return this.header();
    }

    apiHeaders(url: string, headers?: Record<string, any>): Record<string, string> | null {
        if (!this.span || !apiInjectionDecision(this.config, url, headers)) return null;
        return this.header();
    }
}

/** Headers to merge into an API step's request (empty when not applicable). */
export function traceApiHeaders(ctx: any, url: string, headers?: Record<string, any>): Record<string, string> {
    const trace: TraceCorrelation | undefined = ctx?.trace;
    return (trace && trace.apiHeaders(url, headers)) || {};
}

/** The URL of the frame that issued a Playwright request, if knowable. */
export function frameUrlOf(request: any): string | null {
    try {
        return request.frame()?.url() || null;
    } catch {
        return null;   // service-worker requests have no frame
    }
}

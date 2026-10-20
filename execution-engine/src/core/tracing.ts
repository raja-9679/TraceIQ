/**
 * OpenTelemetry for the distributed workers (WP15b H3).
 *
 * One run = one trace: the backend puts a W3C `traceparent` on every job it
 * dispatches (app/services/job_dispatcher.py), and the worker continues that
 * trace with ONE span per job plus, after the job, one child span per case
 * and per step rebuilt from the result's step evidence.
 *
 * - Off unless OTEL_EXPORTER_OTLP_ENDPOINT (or ..._TRACES_ENDPOINT, or
 *   OTEL_ENABLED) is set — the same switch as app/core/telemetry.py — and
 *   OTEL_SDK_DISABLED=true wins. When off, the SDK is never loaded: the
 *   packages are required lazily, so an unconfigured worker pays nothing and
 *   an image built without them still runs (it logs once and stays off).
 * - Attributes are an ALLOW-LIST: ids, job type, step type, status, counts,
 *   durations. Never step values, URLs, selectors, headers, bodies or error
 *   text — those are exactly what test authors put secrets in, and a tracing
 *   backend is outside every redaction and retention policy we have.
 * - Case/step spans are reconstructed from durations (the executor is not
 *   threaded with span contexts), laid end to end from the job start, and
 *   say so (`traceiq.timing = reconstructed`). Capped so one 2000-step job
 *   cannot flood the collector.
 */

export interface RemoteSpanContext {
    traceId: string;
    spanId: string;
    traceFlags: number;
}

const TRACEPARENT_RE = /^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$/;

/** Parse a W3C traceparent; null for anything malformed (spec: then start a new trace). */
export function parseTraceparent(raw: unknown): RemoteSpanContext | null {
    if (typeof raw !== 'string') return null;
    const m = TRACEPARENT_RE.exec(raw.trim());
    if (!m) return null;
    const [, version, traceId, spanId, flags] = m;
    if (version === 'ff') return null;
    if (/^0+$/.test(traceId) || /^0+$/.test(spanId)) return null;
    // Version 00 has exactly four fields (the regex enforces that); later
    // versions may append fields, which this regex rejects — acceptable, since
    // the only producer is our own backend.
    return { traceId, spanId, traceFlags: parseInt(flags, 16) & 0xff };
}

export function formatTraceparent(ctx: RemoteSpanContext): string {
    return `00-${ctx.traceId}-${ctx.spanId}-${(ctx.traceFlags & 0xff).toString(16).padStart(2, '0')}`;
}

export function tracingConfigured(env: NodeJS.ProcessEnv = process.env): boolean {
    if ((env.OTEL_SDK_DISABLED || '').toLowerCase() === 'true') return false;
    if (['1', 'true', 'yes'].includes((env.OTEL_ENABLED || '').toLowerCase())) return true;
    return !!(env.OTEL_EXPORTER_OTLP_ENDPOINT || env.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT);
}

// ---------------------------------------------------------------------------
// attributes
// ---------------------------------------------------------------------------

const ALLOWED_ATTRIBUTES = new Set([
    'traceiq.run_id', 'traceiq.job_id', 'traceiq.job_type', 'traceiq.worker_kind',
    'traceiq.execution_mode', 'traceiq.unit_type', 'traceiq.unit_id',
    'traceiq.case_id', 'traceiq.case_count', 'traceiq.browser', 'traceiq.region',
    'traceiq.step.index', 'traceiq.step.type', 'traceiq.step_count', 'traceiq.steps_dropped',
    'traceiq.cases_dropped', 'traceiq.status', 'traceiq.error_kind', 'traceiq.duration_ms',
    'traceiq.retry_count', 'traceiq.timing', 'traceiq.error_class',
]);

/** Short identifier-like strings only: step types, statuses, browsers. */
const SAFE_STRING = /^[A-Za-z0-9_.:-]{1,64}$/;

export type AttrValue = string | number | boolean;

export function safeAttributes(attrs: Record<string, unknown>): Record<string, AttrValue> {
    const out: Record<string, AttrValue> = {};
    for (const [key, value] of Object.entries(attrs)) {
        if (!ALLOWED_ATTRIBUTES.has(key)) continue;
        if (typeof value === 'number') {
            if (Number.isFinite(value)) out[key] = value;
        } else if (typeof value === 'boolean') {
            out[key] = value;
        } else if (typeof value === 'string' && SAFE_STRING.test(value)) {
            out[key] = value;
        }
    }
    return out;
}

function safeName(raw: unknown, fallback: string): string {
    return typeof raw === 'string' && SAFE_STRING.test(raw) ? raw : fallback;
}

function asInt(raw: unknown): number | undefined {
    const n = typeof raw === 'string' ? Number(raw) : raw;
    return typeof n === 'number' && Number.isInteger(n) ? n : undefined;
}

export function jobAttributes(job: any, workerKind: string): Record<string, AttrValue> {
    const cases = Array.isArray(job?.test_cases) ? job.test_cases.length : (job?.test_case ? 1 : undefined);
    return safeAttributes({
        'traceiq.run_id': asInt(job?.run_id),
        'traceiq.job_id': job?.job_id,
        'traceiq.job_type': safeName(job?.job_type, 'test'),
        'traceiq.worker_kind': workerKind,
        'traceiq.execution_mode': job?.execution_mode,
        'traceiq.unit_type': job?.unit_type,
        'traceiq.unit_id': asInt(job?.unit_id),
        'traceiq.case_id': asInt(job?.test_case_id ?? job?.test_case?.id),
        'traceiq.case_count': cases,
        'traceiq.browser': job?.browser,
        'traceiq.region': job?.region,
    });
}

// ---------------------------------------------------------------------------
// reconstructed case / step spans (pure, tested)
// ---------------------------------------------------------------------------

export const MAX_CASE_SPANS = 100;
export const MAX_STEP_SPANS = 300;

export type SpanOutcome = 'ok' | 'error' | 'unset';

export interface PlannedSpan {
    name: string;
    /** Index into the plan of the parent span; -1 = the job span. */
    parent: number;
    startMs: number;
    endMs: number;
    attributes: Record<string, AttrValue>;
    outcome: SpanOutcome;
}

export function outcomeFor(status: unknown): SpanOutcome {
    switch (status) {
        case 'passed': return 'ok';
        case 'failed': case 'error': case 'timed_out': return 'error';
        default: return 'unset';   // cancelled, skipped, unknown
    }
}

function stepOutcome(status: unknown): SpanOutcome {
    if (status === 'passed' || status === 'healed') return 'ok';
    if (status === 'failed') return 'error';
    return 'unset';
}

function nonNegative(raw: unknown): number {
    return typeof raw === 'number' && Number.isFinite(raw) && raw > 0 ? raw : 0;
}

/**
 * Case and step spans for a finished job, laid end to end from `jobStartMs`.
 * Single-case jobs get one case span from the job's own fields; continuous
 * jobs one per `test_results` entry.
 */
export function planResultSpans(result: any, jobStartMs: number): PlannedSpan[] {
    if (!result || typeof result !== 'object') return [];
    const cases: any[] = Array.isArray(result.test_results) && result.test_results.length
        ? result.test_results
        : (result.test_case_id != null || Array.isArray(result.step_evidence)
            ? [{
                test_case_id: result.test_case_id, status: result.status, error_kind: result.error_kind,
                duration_ms: result.duration_ms, step_evidence: result.step_evidence,
                retry_count: result.retry_count,
            }]
            : []);
    const plan: PlannedSpan[] = [];
    let cursor = jobStartMs;
    let stepBudget = MAX_STEP_SPANS;
    for (let i = 0; i < cases.length && i < MAX_CASE_SPANS; i++) {
        const c = cases[i] || {};
        const steps: any[] = Array.isArray(c.step_evidence) ? c.step_evidence : [];
        const stepTotal = steps.reduce((sum, s) => sum + nonNegative(s?.duration_ms), 0);
        const duration = Math.max(nonNegative(c.duration_ms), stepTotal);
        const keep = Math.min(steps.length, stepBudget);
        stepBudget -= keep;
        const caseIndex = plan.length;
        plan.push({
            name: 'case',
            parent: -1,
            startMs: cursor,
            endMs: cursor + duration,
            attributes: safeAttributes({
                'traceiq.case_id': asInt(c.test_case_id),
                'traceiq.status': c.status,
                'traceiq.error_kind': c.error_kind,
                'traceiq.duration_ms': nonNegative(c.duration_ms),
                'traceiq.step_count': steps.length,
                'traceiq.steps_dropped': steps.length - keep || undefined,
                'traceiq.retry_count': asInt(c.retry_count),
                'traceiq.timing': 'reconstructed',
            }),
            outcome: outcomeFor(c.status),
        });
        let stepCursor = cursor;
        for (let j = 0; j < keep; j++) {
            const s = steps[j] || {};
            const d = nonNegative(s.duration_ms);
            const type = safeName(s.type, 'unknown');
            plan.push({
                name: `step ${type}`,
                parent: caseIndex,
                startMs: stepCursor,
                endMs: stepCursor + d,
                attributes: safeAttributes({
                    'traceiq.step.index': asInt(s.index) ?? j,
                    'traceiq.step.type': type,
                    'traceiq.status': s.status,
                    'traceiq.duration_ms': d,
                    'traceiq.timing': 'reconstructed',
                }),
                outcome: stepOutcome(s.status),
            });
            stepCursor += d;
        }
        cursor += duration;
    }
    if (cases.length > MAX_CASE_SPANS && plan.length) {
        plan[0].attributes['traceiq.cases_dropped'] = cases.length - MAX_CASE_SPANS;
    }
    return plan;
}

// ---------------------------------------------------------------------------
// the SDK, loaded lazily
// ---------------------------------------------------------------------------

/** The slice of @opentelemetry/api + sdk this file uses (typed locally so the
 * engine compiles without the packages' type declarations). */
interface OtelSpan {
    setAttribute(key: string, value: AttrValue): unknown;
    setStatus(status: { code: number }): unknown;
    end(endTime?: number): void;
}

interface OtelTracer {
    startSpan(name: string, options: Record<string, unknown>, context?: unknown): OtelSpan;
}

interface Otel {
    tracer: OtelTracer;
    rootContext: unknown;
    withRemoteParent(ctx: RemoteSpanContext): unknown;
    withParent(span: OtelSpan): unknown;
    statusOk: number;
    statusError: number;
    kindConsumer: number;
    kindInternal: number;
    shutdown(): Promise<void>;
}

export type ModuleLoader = (name: string) => Promise<any>;

// A variable specifier keeps tsc from resolving (and requiring types for)
// the optional packages; at runtime this is a plain lazy require.
const defaultLoader: ModuleLoader = (name: string) => import(name);

let otel: Otel | null = null;
let initPromise: Promise<boolean> | null = null;

export function tracingActive(): boolean {
    return otel !== null;
}

/**
 * Start the tracer provider if tracing is configured. Safe to call more than
 * once; resolves false (and loads nothing) when it is not configured.
 */
export function initTracing(
    serviceName: string,
    opts: { env?: NodeJS.ProcessEnv; loader?: ModuleLoader } = {},
): Promise<boolean> {
    const env = opts.env ?? process.env;
    if (!tracingConfigured(env)) return Promise.resolve(false);
    if (!initPromise) initPromise = load(serviceName, env, opts.loader ?? defaultLoader);
    return initPromise;
}

async function load(serviceName: string, env: NodeJS.ProcessEnv, loader: ModuleLoader): Promise<boolean> {
    try {
        const api = await loader('@opentelemetry/api');
        const sdk = await loader('@opentelemetry/sdk-trace-base');
        const exporterMod = await loader('@opentelemetry/exporter-trace-otlp-http');
        const resources = await loader('@opentelemetry/resources');
        const resource = resources.resourceFromAttributes({
            'service.name': env.OTEL_SERVICE_NAME || serviceName,
        });
        // The exporter reads OTEL_EXPORTER_OTLP_[TRACES_]ENDPOINT / _HEADERS
        // itself, exactly like the backend's. ParentBased sampling (the SDK
        // default) honours the backend's sampled flag.
        const provider = new sdk.BasicTracerProvider({
            resource,
            spanProcessors: [new sdk.BatchSpanProcessor(new exporterMod.OTLPTraceExporter())],
        });
        const tracer: OtelTracer = provider.getTracer('traceiq-worker');
        otel = {
            tracer,
            rootContext: api.ROOT_CONTEXT,
            withRemoteParent: (ctx) => api.trace.setSpanContext(api.ROOT_CONTEXT, { ...ctx, isRemote: true }),
            withParent: (span) => api.trace.setSpan(api.ROOT_CONTEXT, span),
            statusOk: api.SpanStatusCode.OK,
            statusError: api.SpanStatusCode.ERROR,
            kindConsumer: api.SpanKind.CONSUMER,
            kindInternal: api.SpanKind.INTERNAL,
            shutdown: () => provider.shutdown(),
        };
        console.log(`[Tracing] OpenTelemetry on (service ${env.OTEL_SERVICE_NAME || serviceName})`);
        return true;
    } catch (err: any) {
        console.warn(`[Tracing] OTEL endpoint set but the OpenTelemetry packages are unavailable ` +
            `(${err?.code || err?.message}); tracing stays off. Rebuild the worker image.`);
        otel = null;
        return false;
    }
}

export async function shutdownTracing(): Promise<void> {
    const o = otel;
    otel = null;
    initPromise = null;
    if (o) {
        try { await o.shutdown(); } catch { /* exporter unreachable at exit */ }
    }
}

export interface JobSpan {
    /** End the job span (and emit its case/step children). Idempotent. */
    finish(result?: any, error?: unknown): void;
}

const NOOP_SPAN: JobSpan = { finish() { /* tracing off */ } };

function applyOutcome(o: Otel, span: OtelSpan, outcome: SpanOutcome): void {
    if (outcome === 'ok') span.setStatus({ code: o.statusOk });
    else if (outcome === 'error') span.setStatus({ code: o.statusError });
}

/** One span per claimed job, continuing the dispatcher's trace when the job carries one. */
export function startJobSpan(job: any, workerKind: string, now: () => number = Date.now): JobSpan {
    const o = otel;
    if (!o) return NOOP_SPAN;
    const startMs = now();
    const remote = parseTraceparent(job?.traceparent);
    const jobType = safeName(job?.job_type, 'test');
    const span = o.tracer.startSpan(`job ${jobType}`, {
        kind: o.kindConsumer,
        attributes: jobAttributes(job, workerKind),
        startTime: startMs,
    }, remote ? o.withRemoteParent(remote) : o.rootContext);
    let done = false;
    return {
        finish(result?: any, error?: unknown) {
            if (done) return;
            done = true;
            try {
                const endMs = now();
                if (result && typeof result === 'object') {
                    const attrs = safeAttributes({
                        'traceiq.status': result.status,
                        'traceiq.error_kind': result.error_kind,
                        'traceiq.duration_ms': result.duration_ms,
                    });
                    for (const [k, v] of Object.entries(attrs)) span.setAttribute(k, v);
                    applyOutcome(o, span, outcomeFor(result.status));
                    const plan = planResultSpans(result, startMs);
                    const made: OtelSpan[] = [];
                    for (const p of plan) {
                        const parent = p.parent < 0 ? span : made[p.parent];
                        const child = o.tracer.startSpan(p.name, {
                            kind: o.kindInternal, attributes: p.attributes, startTime: p.startMs,
                        }, o.withParent(parent));
                        applyOutcome(o, child, p.outcome);
                        made.push(child);
                    }
                    // Children end in reverse so a child never outlives its parent.
                    for (let i = made.length - 1; i >= 0; i--) {
                        made[i].end(Math.max(plan[i].startMs, Math.min(plan[i].endMs, endMs)));
                    }
                }
                if (error !== undefined) {
                    // The class only: messages carry URLs, selectors and values.
                    const cls = safeName((error as any)?.name || (error as any)?.constructor?.name, 'Error');
                    span.setAttribute('traceiq.error_class', cls);
                    span.setStatus({ code: o.statusError });
                }
                span.end(endMs);
            } catch (err: any) {
                console.warn(`[Tracing] could not record job span: ${err?.message}`);
            }
        },
    };
}

/** Test hook: forget any initialised SDK without shutting it down. */
export function _resetTracingForTests(): void {
    otel = null;
    initPromise = null;
}

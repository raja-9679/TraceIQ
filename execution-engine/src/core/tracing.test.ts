import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import {
    _resetTracingForTests, formatTraceparent, initTracing, jobAttributes, MAX_STEP_SPANS,
    parseTraceparent, planResultSpans, safeAttributes, startJobSpan, tracingActive, tracingConfigured,
} from './tracing';

const TP = '00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01';

beforeEach(() => _resetTracingForTests());

test('parses a W3C traceparent and round-trips it', () => {
    const ctx = parseTraceparent(TP);
    assert.deepEqual(ctx, { traceId: '4bf92f3577b34da6a3ce929d0e0e4736', spanId: '00f067aa0ba902b7', traceFlags: 1 });
    assert.equal(formatTraceparent(ctx!), TP);
    // OTel Python 1.4x also sets the W3C random-trace-id bit (0x02).
    assert.equal(parseTraceparent(TP.replace(/-01$/, '-03'))!.traceFlags, 3);
});

test('rejects malformed traceparents (start a new trace instead)', () => {
    for (const bad of [
        undefined, null, 42, '', 'garbage',
        'ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01',      // forbidden version
        '00-00000000000000000000000000000000-00f067aa0ba902b7-01',      // zero trace id
        '00-4bf92f3577b34da6a3ce929d0e0e4736-0000000000000000-01',      // zero span id
        '00-4BF92F3577B34DA6A3CE929D0E0E4736-00f067aa0ba902b7-01',      // uppercase
        '00-4bf92f3577b34da6a3ce929d0e0e473-00f067aa0ba902b7-01',       // short
    ]) {
        assert.equal(parseTraceparent(bad), null, String(bad));
    }
});

test('the switch matches the backend: endpoint or OTEL_ENABLED, OTEL_SDK_DISABLED wins', () => {
    assert.equal(tracingConfigured({}), false);
    assert.equal(tracingConfigured({ OTEL_EXPORTER_OTLP_ENDPOINT: 'http://c:4318' }), true);
    assert.equal(tracingConfigured({ OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: 'http://c:4318/v1/traces' }), true);
    assert.equal(tracingConfigured({ OTEL_ENABLED: 'true' }), true);
    assert.equal(tracingConfigured({ OTEL_EXPORTER_OTLP_ENDPOINT: 'http://c', OTEL_SDK_DISABLED: 'true' }), false);
});

test('unconfigured: nothing is loaded and job spans are no-ops', async () => {
    let loads = 0;
    const ok = await initTracing('w', { env: {}, loader: async () => { loads++; return {}; } });
    assert.equal(ok, false);
    assert.equal(loads, 0);
    assert.equal(tracingActive(), false);
    startJobSpan({ job_id: 'j', run_id: 1, traceparent: TP }, 'web').finish({ status: 'passed' });
});

test('configured but packages missing: stays off instead of crashing', async () => {
    const ok = await initTracing('w', {
        env: { OTEL_EXPORTER_OTLP_ENDPOINT: 'http://c:4318' },
        loader: async (name) => { throw Object.assign(new Error(`Cannot find module '${name}'`), { code: 'MODULE_NOT_FOUND' }); },
    });
    assert.equal(ok, false);
    assert.equal(tracingActive(), false);
});

test('attributes are an allow-list of ids, types, statuses and numbers', () => {
    const out = safeAttributes({
        'traceiq.run_id': 7,
        'traceiq.step.type': 'click',
        'traceiq.status': 'failed',
        'traceiq.step.value': 'hunter2',                       // not allowed key
        'http.url': 'https://x.test/?token=abc',               // not allowed key
        'traceiq.browser': 'https://evil/?q=1',                // allowed key, unsafe string
        'traceiq.job_type': '#login > input[name="pw"]',       // selector-shaped
        'traceiq.duration_ms': Number.NaN,
    });
    assert.deepEqual(out, { 'traceiq.run_id': 7, 'traceiq.step.type': 'click', 'traceiq.status': 'failed' });
});

test('job attributes never carry case content', () => {
    const attrs = jobAttributes({
        job_id: 'job-1', run_id: 12, browser: 'chromium', test_case_id: 5,
        test_case: { id: 5, name: 'Login as admin', steps: [{ type: 'fill', selector: '#pw', value: 's3cret' }] },
        settings: { headers: { Authorization: 'Bearer x' }, secrets: { PW: 's3cret' } },
    }, 'web');
    const flat = JSON.stringify(attrs);
    for (const leaked of ['s3cret', '#pw', 'Login as admin', 'Bearer']) assert.ok(!flat.includes(leaked), leaked);
    assert.equal(attrs['traceiq.run_id'], 12);
    assert.equal(attrs['traceiq.case_id'], 5);
    assert.equal(attrs['traceiq.job_type'], 'test');
});

test('single-case result: one case span with its steps laid end to end', () => {
    const plan = planResultSpans({
        test_case_id: 3, status: 'failed', error_kind: 'test', duration_ms: 500,
        error: 'expected https://x/?token=abc',
        step_evidence: [
            { id: 's0', index: 0, type: 'goto', status: 'passed', duration_ms: 100, selector: '#a' },
            { id: 's1', index: 1, type: 'click', status: 'failed', duration_ms: 50, error: 'no #a' },
            { id: 's2', index: 2, type: 'fill', status: 'not_run' },
        ],
    }, 1000);
    assert.equal(plan.length, 4);
    assert.deepEqual([plan[0].name, plan[0].parent, plan[0].startMs, plan[0].endMs, plan[0].outcome],
        ['case', -1, 1000, 1500, 'error']);
    assert.deepEqual(plan.slice(1).map((p) => [p.name, p.parent, p.startMs, p.endMs, p.outcome]), [
        ['step goto', 0, 1000, 1100, 'ok'],
        ['step click', 0, 1100, 1150, 'error'],
        ['step fill', 0, 1150, 1150, 'unset'],
    ]);
    const flat = JSON.stringify(plan);
    for (const leaked of ['token', '#a']) assert.ok(!flat.includes(leaked), leaked);
    assert.equal(plan[0].attributes['traceiq.timing'], 'reconstructed');
});

test('continuous result: one case span per test result, sequential', () => {
    const plan = planResultSpans({
        status: 'passed',
        test_results: [
            { test_case_id: 1, status: 'passed', duration_ms: 10, step_evidence: [] },
            { test_case_id: 2, status: 'cancelled', duration_ms: 20 },
        ],
    }, 0);
    assert.deepEqual(plan.map((p) => [p.attributes['traceiq.case_id'], p.startMs, p.endMs, p.outcome]),
        [[1, 0, 10, 'ok'], [2, 10, 30, 'unset']]);
});

test('step spans are capped per job and the drop is counted', () => {
    const steps = Array.from({ length: MAX_STEP_SPANS + 25 }, (_, i) => ({ index: i, type: 'click', status: 'passed', duration_ms: 1 }));
    const plan = planResultSpans({ test_case_id: 1, status: 'passed', duration_ms: 1, step_evidence: steps }, 0);
    assert.equal(plan.length, 1 + MAX_STEP_SPANS);
    assert.equal(plan[0].attributes['traceiq.steps_dropped'], 25);
});

test('unknown or junk step types become "unknown" in span names', () => {
    const plan = planResultSpans({ test_case_id: 1, status: 'passed', step_evidence: [{ type: 'click; DROP TABLE', status: 'passed' }] }, 0);
    assert.equal(plan[1].name, 'step unknown');
});

/** A fake SDK that records what the worker asked for. */
function fakeOtel() {
    const spans: any[] = [];
    let n = 0;
    const ROOT = { root: true };
    const api = {
        ROOT_CONTEXT: ROOT,
        SpanStatusCode: { UNSET: 0, OK: 1, ERROR: 2 },
        SpanKind: { INTERNAL: 0, CONSUMER: 4 },
        trace: {
            setSpanContext: (_ctx: any, sc: any) => ({ remote: sc }),
            setSpan: (_ctx: any, span: any) => ({ parentSpan: span }),
        },
    };
    class BasicTracerProvider {
        constructor(public opts: any) {}
        getTracer() {
            return {
                startSpan: (name: string, options: any, ctx: any) => {
                    const span: any = {
                        id: ++n, name, options, ctx, attributes: { ...(options.attributes || {}) }, status: null, endTime: null,
                        setAttribute(k: string, v: any) { span.attributes[k] = v; },
                        setStatus(s: any) { span.status = s.code; },
                        end(t: number) { span.endTime = t; },
                    };
                    spans.push(span);
                    return span;
                },
            };
        }
        async shutdown() {}
    }
    const modules: Record<string, any> = {
        '@opentelemetry/api': api,
        '@opentelemetry/sdk-trace-base': { BasicTracerProvider, BatchSpanProcessor: class { constructor(public e: any) {} } },
        '@opentelemetry/exporter-trace-otlp-http': { OTLPTraceExporter: class {} },
        '@opentelemetry/resources': { resourceFromAttributes: (a: any) => a },
    };
    return { spans, loader: async (name: string) => modules[name] };
}

test('a job continues the dispatcher trace and parents its case/step spans', async () => {
    const fake = fakeOtel();
    assert.equal(await initTracing('traceiq-worker', { env: { OTEL_EXPORTER_OTLP_ENDPOINT: 'http://c:4318' }, loader: fake.loader }), true);
    let clock = 1000;
    const span = startJobSpan({ job_id: 'j1', run_id: 9, traceparent: TP, test_case_id: 4 }, 'web', () => clock);
    clock = 2000;
    span.finish({
        status: 'passed', duration_ms: 900, test_case_id: 4,
        step_evidence: [{ index: 0, type: 'goto', status: 'passed', duration_ms: 400 }],
    });
    span.finish({ status: 'failed' });   // idempotent

    const [job, kase, step] = fake.spans;
    assert.equal(fake.spans.length, 3);
    assert.equal(job.name, 'job test');
    assert.deepEqual(job.ctx, { remote: { traceId: '4bf92f3577b34da6a3ce929d0e0e4736', spanId: '00f067aa0ba902b7', traceFlags: 1, isRemote: true } });
    assert.equal(job.options.kind, 4);
    assert.equal(job.status, 1);
    assert.equal(job.endTime, 2000);
    assert.equal(job.attributes['traceiq.run_id'], 9);
    assert.equal(kase.ctx.parentSpan, job);
    assert.equal(step.ctx.parentSpan, kase);
    assert.equal(step.name, 'step goto');
    assert.deepEqual([kase.options.startTime, kase.endTime], [1000, 1900]);
});

test('a job without a traceparent starts its own trace; errors record the class only', async () => {
    const fake = fakeOtel();
    await initTracing('w', { env: { OTEL_ENABLED: 'true' }, loader: fake.loader });
    const span = startJobSpan({ job_id: 'j2', run_id: 1, job_type: 'page_check' }, 'web', () => 5);
    class JobTimeout extends Error { constructor(m: string) { super(m); this.name = 'JobTimeout'; } }
    span.finish(undefined, new JobTimeout('timed out on https://x/?token=abc'));
    const [job] = fake.spans;
    assert.equal(job.name, 'job page_check');
    assert.deepEqual(job.ctx, { root: true });
    assert.equal(job.status, 2);
    assert.equal(job.attributes['traceiq.error_class'], 'JobTimeout');
    assert.ok(!JSON.stringify(job.attributes).includes('token'));
});

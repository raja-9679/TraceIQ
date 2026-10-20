import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
    TraceCorrelation, apiInjectionDecision, browserInjectionDecision, hostIsOwn,
    newSpanId, newTraceId, traceApiHeaders, traceparent,
} from './trace-context';
import { StepEvidenceRecorder } from './evidence';
import { redactStepEvidence } from './redact';

const cfg = { enabled: true, hosts: ['app.example.com', '*.api.example.com'], cross_origin: false };

test('ids are W3C-shaped lowercase hex and never all zero', () => {
    assert.match(newTraceId(), /^[0-9a-f]{32}$/);
    assert.match(newSpanId(), /^[0-9a-f]{16}$/);
    let calls = 0;
    const rand = (n: number) => (calls++ === 0 ? Buffer.alloc(n) : Buffer.alloc(n, 0xab));
    assert.equal(newTraceId(rand), 'ab'.repeat(16));
    assert.equal(traceparent('a'.repeat(32), 'b'.repeat(16)), `00-${'a'.repeat(32)}-${'b'.repeat(16)}-01`);
});

test('own-host matching mirrors site_crawl.host_allowed', () => {
    assert.ok(hostIsOwn('app.example.com', cfg.hosts));
    assert.ok(hostIsOwn('v1.api.example.com', cfg.hosts));
    assert.ok(hostIsOwn('api.example.com', cfg.hosts));
    assert.ok(!hostIsOwn('evil.app.example.com', cfg.hosts));   // plain entry = exact host
    assert.ok(!hostIsOwn('app.example.com.evil.net', cfg.hosts));
    assert.ok(!hostIsOwn('cdn.thirdparty.com', cfg.hosts));
    assert.ok(!hostIsOwn(null, cfg.hosts));
});

test('browser decision: third parties never, navigations and same-origin yes, cross-origin only opted in', () => {
    const d = (req: any, c: any = cfg) => browserInjectionDecision(c, req).reason;
    assert.equal(d({ url: 'https://www.google-analytics.com/collect', navigation: false, frameUrl: 'https://app.example.com/' }), 'third_party');
    assert.equal(d({ url: 'https://app.example.com/login', navigation: true }), 'navigation');
    assert.equal(d({ url: 'https://app.example.com/api/x', navigation: false, frameUrl: 'https://app.example.com/page' }), 'same_origin');
    // Same site, different origin: a preflight risk, so skipped by default.
    assert.equal(d({ url: 'https://v1.api.example.com/x', navigation: false, frameUrl: 'https://app.example.com/' }), 'cross_origin');
    assert.equal(d({ url: 'https://v1.api.example.com/x', navigation: false, frameUrl: 'https://app.example.com/' },
        { ...cfg, cross_origin: true }), 'cross_origin_opt_in');
    // A traceparent the page / suite already set is left alone.
    assert.equal(d({ url: 'https://app.example.com/', navigation: true, headers: { TraceParent: 'x' } }), 'already_set');
    assert.equal(d({ url: 'https://app.example.com/', navigation: true }, { ...cfg, enabled: false }), 'disabled');
    // Non-http schemes are never own hosts.
    assert.equal(d({ url: 'data:text/plain,hi', navigation: false }), 'third_party');
});

test('API decision ignores CORS but not the host set', () => {
    assert.ok(apiInjectionDecision(cfg, 'https://v1.api.example.com/x'));
    assert.ok(!apiInjectionDecision(cfg, 'https://api.stripe.com/v1/charges'));
    assert.ok(!apiInjectionDecision(cfg, 'https://app.example.com/', { traceparent: 'mine' }));
});

test('one trace per attempt, one span per step, nothing outside a step', () => {
    const t = TraceCorrelation.from({ trace_correlation: cfg })!;
    assert.ok(t);
    assert.equal(t.browserHeaders({ url: 'https://app.example.com/', navigation: true }), null);
    t.beginStep();
    const h1 = t.browserHeaders({ url: 'https://app.example.com/', navigation: true })!;
    const h2 = t.apiHeaders('https://app.example.com/api')!;
    assert.equal(h1.traceparent, h2.traceparent);
    assert.equal(t.apiHeaders('https://third.party/'), null);
    const s1 = t.endStep()!;
    assert.equal(s1.requests, 2);
    assert.equal(h1.traceparent, traceparent(s1.trace_id, s1.span_id));
    t.beginStep();
    const s2 = t.endStep()!;
    assert.equal(s2.trace_id, s1.trace_id);
    assert.notEqual(s2.span_id, s1.span_id);
    assert.equal(s2.requests, 0);
    t.startAttempt();
    t.beginStep();
    assert.notEqual(t.endStep()!.trace_id, s1.trace_id);
});

test('from() is off without config or hosts', () => {
    assert.equal(TraceCorrelation.from({}), null);
    assert.equal(TraceCorrelation.from({ trace_correlation: { enabled: true, hosts: [] } }), null);
    assert.equal(TraceCorrelation.from({ trace_correlation: { enabled: false, hosts: ['a.com'] } }), null);
    assert.deepEqual(traceApiHeaders({}, 'https://app.example.com/'), {});
});

test('step evidence records the step trace, and redaction leaves the ids alone', () => {
    const sources = { consoleLogs: [], networkEvents: [], failedRequests: [] };
    const rec = new StepEvidenceRecorder(sources, 'case', 'none');
    const t = TraceCorrelation.from({ trace_correlation: cfg })!;
    rec.setTrace(t);
    rec.begin({ type: 'goto' }, 0);
    t.browserHeaders({ url: 'https://app.example.com/', navigation: true });
    const entry = rec.end('passed')!;
    assert.equal(entry.trace!.requests, 1);
    assert.match(entry.trace!.trace_id, /^[0-9a-f]{32}$/);
    // An all-digit id is the worst case for the PAN / Aadhaar patterns.
    const digits = { id: 's0', index: 0, type: 'goto', status: 'passed',
        trace: { trace_id: '4111111111111111'.repeat(2), span_id: '4111111111111111', requests: 1 } };
    assert.deepEqual(redactStepEvidence([digits])[0].trace, digits.trace);
    // Without a correlation there is no trace field.
    const plain = new StepEvidenceRecorder(sources, 'case', 'none');
    plain.begin({ type: 'goto' }, 0);
    assert.equal(plain.end('passed')!.trace, undefined);
});

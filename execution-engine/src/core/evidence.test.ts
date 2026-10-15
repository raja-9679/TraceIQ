import { test } from 'node:test';
import * as assert from 'node:assert/strict';
import { caseStepDom, consoleErrorsFor } from './evidence';

// Assembled at runtime: a JWT-shaped literal in source trips the gitleaks
// history scan in CI. Header/payload are base64 of {"alg":"HS256"} and
// {"sub":"1234567890"}; the signature is filler.
const JWT = ['eyJhbGciOiJIUzI1NiJ9', 'eyJzdWIiOiIxMjM0NTY3ODkwIn0', 'dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U'].join('.');

test('consoleErrorsFor keeps only this test\'s errors and warnings', () => {
    const logs = [
        { test: 'a', type: 'log', text: 'hello' },
        { test: 'a', type: 'error', text: 'TypeError: x', ts: 't1' },
        { test: 'b', type: 'error', text: 'other test' },
        { test: 'a', type: 'warning', text: 'deprecated' },
    ];
    assert.deepEqual(consoleErrorsFor(logs, 'a').map(e => e.text), ['TypeError: x', 'deprecated']);
});

test('consoleErrorsFor redacts, truncates and caps', () => {
    const logs = Array.from({ length: 30 }, () => ({ test: 'a', type: 'error', text: `token ${JWT} ${'x'.repeat(900)}` }));
    const out = consoleErrorsFor(logs, 'a');
    assert.equal(out.length, 20);
    assert.ok(!out[0].text.includes(JWT));
    assert.ok(out[0].text.length <= 520);
});

test('caseStepDom picks one case out of a continuous job map', () => {
    const all = { '1:s1': '<a>', '1:s2': '<b>', '12:s1': '<c>' };
    assert.deepEqual(caseStepDom(all, 1), { step_dom: { s1: '<a>', s2: '<b>' } });
    assert.deepEqual(caseStepDom(all, 2), {});
});

// --------------------------------------------------------------------------
// Per-step evidence
// --------------------------------------------------------------------------

import {
    StepEvidenceRecorder, blockingConsoleErrors, consoleFailureMessage, consoleFailurePolicy,
    matchesIgnore, safeIgnoreRegex, resolveScreenshotKeys, safeUrl,
} from './evidence';

function sources() {
    return { consoleLogs: [] as any[], networkEvents: [] as any[], failedRequests: [] as any[] };
}

test('safeUrl keeps origin, path and query names but drops query values', () => {
    assert.equal(safeUrl('https://api.test/v1/items?token=abc123&page=2#frag'), 'https://api.test/v1/items?token=…&page=…');
    assert.equal(safeUrl('https://api.test/plain'), 'https://api.test/plain');
    // Paths carry tokens too.
    assert.ok(!safeUrl(`https://api.test/reset/${JWT}`).includes(JWT));
    // Unparseable input still loses its query string.
    assert.equal(safeUrl('/relative/path?secret=1'), '/relative/path');
});

test('recorder slices console and failed requests per step', () => {
    const src = sources();
    const r = new StepEvidenceRecorder(src, 'case A', 'standard');
    r.begin({ id: 'a', type: 'goto' }, 0);
    src.consoleLogs.push({ type: 'log', text: 'noise', test: 'case A' });
    src.consoleLogs.push({ type: 'error', text: 'boom', test: 'case A' });
    src.networkEvents.push({ method: 'GET', url: 'https://x.test/ok', status: 200, testCaseName: 'case A' });
    src.networkEvents.push({ method: 'POST', url: 'https://x.test/api?k=v', status: 503, resourceType: 'fetch', testCaseName: 'case A' });
    r.end('passed');
    r.begin({ id: 'b', type: 'click', selector: '#go' }, 1);
    src.failedRequests.push({ method: 'GET', url: 'https://cdn.test/x.js', failure: 'net::ERR_NAME_NOT_RESOLVED', testCaseName: 'case A' });
    src.failedRequests.push({ method: 'GET', url: 'https://cdn.test/y.js', failure: 'net::ERR_ABORTED', testCaseName: 'case A' });
    src.consoleLogs.push({ type: 'pageerror', text: 'TypeError: x is undefined', test: 'case A' });
    r.end('failed', 'Timeout 5000ms exceeded');

    const [s0, s1] = r.list();
    assert.equal(s0.id, 's0');
    assert.deepEqual(s0.console, [{ id: 's0.c0', type: 'error', text: 'boom' }]);
    assert.deepEqual(s0.requests, [{ id: 's0.r0', method: 'POST', url: 'https://x.test/api?k=…', status: 503, resource_type: 'fetch' }]);
    assert.equal(s1.status, 'failed');
    assert.equal(s1.selector, '#go');
    assert.equal(s1.error, 'Timeout 5000ms exceeded');
    // An aborted request is the page cancelling, not a failure.
    assert.equal(s1.requests!.length, 1);
    assert.equal(s1.requests![0].failure, 'net::ERR_NAME_NOT_RESOLVED');
    assert.equal(s1.console![0].type, 'pageerror');
});

test('recorder ignores another test\'s events in a shared continuous context', () => {
    const src = sources();
    const r = new StepEvidenceRecorder(src, 'mine', 'minimal');
    r.begin({ type: 'click' }, 0);
    src.consoleLogs.push({ type: 'error', text: 'theirs', test: 'other' });
    src.networkEvents.push({ method: 'GET', url: 'https://x.test', status: 500, testCaseName: 'other' });
    r.end('passed');
    assert.equal(r.list()[0].console, undefined);
    assert.equal(r.list()[0].requests, undefined);
});

test('capture level none records step outcome only', () => {
    const src = sources();
    const r = new StepEvidenceRecorder(src, 't', 'none');
    r.begin({ type: 'click', selector: '#x' }, 0);
    src.consoleLogs.push({ type: 'error', text: 'boom', test: 't' });
    r.end('failed', 'nope');
    r.attachToLast({ screenshot_file: 'failure.png', dom: '<div></div>', dom_scope: 'target' });
    const [e] = r.list();
    assert.equal(e.status, 'failed');
    assert.equal(e.console, undefined);
    assert.equal(e.screenshot_file, undefined);
    assert.equal(e.dom, undefined);
});

test('DOM excerpt only at standard and above, redacted and capped', () => {
    for (const [level, expectDom] of [['minimal', false], ['standard', true], ['full', true]] as const) {
        const r = new StepEvidenceRecorder(sources(), 't', level);
        r.begin({ type: 'click' }, 0);
        r.end('failed', 'x');
        r.attachToLast({ dom: `<p>${JWT}</p>${'y'.repeat(9000)}`, dom_scope: 'target' });
        const e = r.list()[0];
        assert.equal(!!e.dom, expectDom, level);
        if (e.dom) {
            assert.ok(!e.dom.includes(JWT));
            assert.ok(e.dom.length <= 4100);
            assert.equal(e.dom_scope, 'target');
        }
    }
});

test('recorder redacts captured text with the project policy', () => {
    const src = sources();
    const r = new StepEvidenceRecorder(src, 't', 'standard', { patterns: ['email'] });
    r.begin({ type: 'fill', selector: '#email' }, 0);
    src.consoleLogs.push({ type: 'error', text: 'bad user a@b.test', test: 't' });
    r.end('failed', 'user a@b.test not found');
    const e = r.list()[0];
    assert.ok(!e.error!.includes('a@b.test'));
    assert.ok(!e.console![0].text.includes('a@b.test'));
});

test('skipRemaining lists unexecuted steps without evidence', () => {
    const r = new StepEvidenceRecorder(sources(), 't', 'standard');
    const steps = [{ type: 'goto' }, { type: 'click', id: 'c' }, { type: 'expect-visible', selector: '#done' }];
    r.begin(steps[0], 0);
    r.end('failed', 'x');
    r.skipRemaining(steps, 1);
    assert.deepEqual(r.list().map(e => [e.id, e.status]), [['s0', 'failed'], ['s1', 'skipped'], ['s2', 'skipped']]);
    assert.equal(r.list()[1].step_id, 'c');
});

test('resolveScreenshotKeys keeps only uploaded keys and never a local path', () => {
    const entries: any[] = [
        { id: 's0', screenshot_file: 'shot.png' },
        { id: 's1', screenshot_file: 'failure.png' },
    ];
    resolveScreenshotKeys(entries, ['runs/1/screenshots/j-failure.png'], f => `runs/1/screenshots/j-${f}`);
    assert.equal(entries[0].screenshot, undefined);
    assert.equal(entries[0].screenshot_file, undefined);
    assert.equal(entries[1].screenshot, 'runs/1/screenshots/j-failure.png');
});

test('consoleFailurePolicy: case flag overrides the suite, default off', () => {
    assert.equal(consoleFailurePolicy({}, {}).enabled, false);
    assert.equal(consoleFailurePolicy({ fail_on_console_errors: true }, {}).enabled, true);
    assert.equal(consoleFailurePolicy({ fail_on_console_errors: true }, { fail_on_console_errors: false }).enabled, false);
    assert.equal(consoleFailurePolicy({}, { fail_on_console_errors: true }).enabled, true);
    assert.deepEqual(consoleFailurePolicy({ console_error_ignore_patterns: [' favicon ', '', 3] }, {}).ignore, ['favicon']);
});

test('matchesIgnore: substrings case-insensitively, /regex/ literally, bad regex never throws', () => {
    assert.ok(matchesIgnore('GET /favicon.ico 404', ['FAVICON']));
    assert.ok(matchesIgnore('ResizeObserver loop limit exceeded', ['/^ResizeObserver/']));
    assert.ok(!matchesIgnore('TypeError', ['/^Resize/']));
    assert.ok(!matchesIgnore('anything', ['/(/']));
});

test('blockingConsoleErrors: errors and page errors fail, warnings and ignored lines do not', () => {
    const logs = [
        { type: 'error', text: 'old', test: 't' },
        { type: 'warning', text: 'deprecated', test: 't' },
        { type: 'error', text: 'favicon 404', test: 't' },
        { type: 'pageerror', text: 'TypeError: boom', test: 't' },
        { type: 'error', text: 'other test', test: 'u' },
    ];
    const policy = { enabled: true, ignore: ['favicon'] };
    assert.deepEqual(blockingConsoleErrors(logs, 1, 't', policy).map(l => l.text), ['TypeError: boom']);
    assert.deepEqual(blockingConsoleErrors(logs, 0, 't', { enabled: false, ignore: [] }), []);
    const msg = consoleFailureMessage(blockingConsoleErrors(logs, 0, 't', policy));
    assert.match(msg, /JavaScript error on page \(fail_on_console_errors\): console\.error: old \(\+1 more\)/);
});

test('console/request items are budgeted across the whole test', () => {
    const src = sources();
    const r = new StepEvidenceRecorder(src, 't', 'minimal');
    for (let i = 0; i < 40; i++) {
        r.begin({ type: 'click' }, i);
        for (let k = 0; k < 10; k++) src.consoleLogs.push({ type: 'error', text: `e${k}`, test: 't' });
        r.end('passed');
    }
    const total = r.list().reduce((n, e) => n + (e.console?.length ?? 0), 0);
    assert.equal(total, 150);
    assert.equal(r.list().length, 40);
});

test('ignore regexes cannot stall the worker, however they are written', () => {
    assert.equal(safeIgnoreRegex('x'.repeat(201), ''), null);
    assert.ok(safeIgnoreRegex('ResizeObserver loop', 'i'));
    for (const p of ['/(a+)+$/', '/(a|aa)+$/', '/(\\w*)*x/']) {
        const start = Date.now();
        assert.equal(matchesIgnore('a'.repeat(1990) + '!', [p]), false);
        assert.ok(Date.now() - start < 1000, `${p} took ${Date.now() - start}ms`);
    }
    assert.equal(matchesIgnore('ResizeObserver loop limit exceeded', ['/resizeobserver/i']), true);
});

test('a timed-out ignore regex is dropped even when its flags or slashes are normalised', () => {
    const p = '/(a|aa)+\\/x$/mi';   // flags "mi" normalise to "im"; "/" escapes in source
    const text = 'a'.repeat(1990) + '!';
    matchesIgnore(text, [p]);             // first call: one timeout, pattern recorded
    const start = Date.now();
    for (let i = 0; i < 20; i++) matchesIgnore(text, [p]);
    assert.ok(Date.now() - start < 200, `dropped pattern still ran: ${Date.now() - start}ms`);
});

test('ignore list is capped', () => {
    const many = Array.from({ length: 80 }, (_, i) => `p${i}`);
    assert.equal(consoleFailurePolicy({ fail_on_console_errors: true, console_error_ignore_patterns: many }, {}).ignore.length, 50);
});

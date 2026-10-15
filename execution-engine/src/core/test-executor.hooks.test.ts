/**
 * wait-for-email / wait-for-webhook: interpolation of the per-run values,
 * polling + timeout, label scoping, webhook matching/extraction, and the
 * variable hand-off to later steps. The backend is faked at the transport.
 */
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { TestExecutor } from './test-executor';
import { resolveTemplates } from './interpolate';
import {
    Capture, HookRequestError, captureMatches, clampTimeout, hooksRuntime, labelFor,
    MAX_TIMEOUT_MS, pollUntil,
} from './test-hooks';

const HOOKS = {
    run_id: 7, test_case_id: 11, row_index: null,
    inbox: { default: 'tiqabc@inbox.test', signup: 'tiqdef@inbox.test' },
    webhook: { default: 'https://tiq.example.com/api/hooks/capture/7.11.0.default.x', paid: 'https://tiq.example.com/api/hooks/capture/7.11.0.paid.y' },
};

class Recorder {
    public filled: string[] = [];
    public visited: string[] = [];
    locator() {
        const rec = this;
        return { first() { return this; }, async waitFor() {}, async fill(v: string) { rec.filled.push(v); } };
    }
    async goto(url: string) { this.visited.push(url); }
    isClosed() { return false; }
}

const real = { ...hooksRuntime };
afterEach(() => Object.assign(hooksRuntime, real));

/** Fake clock + a scripted backend. Each poll pops the next response. */
function fakeBackend(responses: Array<{ status: number; json: any } | Error>) {
    let now = 1_000_000;
    const calls: Array<{ path: string; body: any }> = [];
    const slept: number[] = [];
    hooksRuntime.now = () => now;
    hooksRuntime.sleep = async (ms: number) => { slept.push(ms); now += ms; };
    hooksRuntime.post = async (path: string, body: any) => {
        calls.push({ path, body });
        const next = responses.length > 1 ? responses.shift()! : responses[0];
        if (next instanceof Error) throw next;
        return next;
    };
    return { calls, slept };
}

async function run(steps: any[], hooks: any = HOOKS, rec = new Recorder()) {
    const ctx: any = { variables: {}, hooks };
    for (const step of steps) await TestExecutor.executeStep(rec as any, rec as any, step, {}, ctx);
    return { rec, ctx };
}

// --------------------------------------------------------------------------
// Interpolation
// --------------------------------------------------------------------------

test('{{inbox.address}} and labelled variants resolve from the job hooks', () => {
    const out = resolveTemplates('a={{inbox.address}} b={{ inbox.address.signup }} c={{webhook.url.paid}}', { hooks: HOOKS });
    assert.equal(out, `a=tiqabc@inbox.test b=tiqdef@inbox.test c=${HOOKS.webhook.paid}`);
});

test('an unknown label stays visible instead of becoming blank', () => {
    assert.equal(resolveTemplates('{{inbox.address.nope}}', { hooks: HOOKS }), '{{inbox.address.nope}}');
    assert.equal(resolveTemplates('{{webhook.url}}', {}), '{{webhook.url}}');
});

test('fill types the run address into the page', async () => {
    const { rec } = await run([{ type: 'fill', selector: '#email', value: '{{inbox.address}}' }]);
    assert.deepEqual(rec.filled, ['tiqabc@inbox.test']);
});

// --------------------------------------------------------------------------
// Scoping
// --------------------------------------------------------------------------

test('labelFor maps an address back to its label and refuses foreign ones', () => {
    assert.equal(labelFor('inbox', '', HOOKS), 'default');
    assert.equal(labelFor('inbox', 'TIQDEF@inbox.test', HOOKS), 'signup');
    assert.equal(labelFor('webhook', HOOKS.webhook.paid, HOOKS), 'paid');
    assert.throws(() => labelFor('inbox', 'ceo@company.com', HOOKS), /not one of this run's inbox addresses/);
    assert.throws(() => labelFor('inbox', '', undefined), /carries no test hooks/);
    assert.throws(() => labelFor('inbox', '', { run_id: 1, test_case_id: 2, inbox_error: 'no test inbox is configured' }),
        /no test inbox is configured/);
});

test('timeouts default to 60 s and are capped', () => {
    assert.equal(clampTimeout(undefined), 60_000);
    assert.equal(clampTimeout('abc'), 60_000);
    assert.equal(clampTimeout(5000), 5000);
    assert.equal(clampTimeout(10 * MAX_TIMEOUT_MS), MAX_TIMEOUT_MS);
});

// --------------------------------------------------------------------------
// Polling
// --------------------------------------------------------------------------

test('pollUntil returns as soon as the probe yields, and gives up at the deadline', async () => {
    const { slept } = fakeBackend([{ status: 200, json: {} }]);
    let n = 0;
    assert.equal(await pollUntil(10_000, async () => (++n === 3 ? 'ok' : null)), 'ok');
    assert.deepEqual(slept, [2000, 2000]);
    const t0 = hooksRuntime.now();
    assert.equal(await pollUntil(5000, async () => null), null);
    assert.equal(hooksRuntime.now() - t0, 5000);   // last sleep is trimmed to the deadline
});

// --------------------------------------------------------------------------
// wait-for-email
// --------------------------------------------------------------------------

test('wait-for-email polls until the backend finds the mail, stores the link, goto uses it', async () => {
    const { calls } = fakeBackend([
        { status: 200, json: { found: false, seen: 0, address: 'tiqdef@inbox.test' } },
        { status: 200, json: { found: true, value: 'https://shop.test/reset?t=abc', address: 'tiqdef@inbox.test',
                               message: { subject: 'Reset your password', from: 'Shop <no-reply@shop.test>' } } },
    ]);
    const { rec, ctx } = await run([
        { type: 'wait-for-email', params: { to: '{{inbox.address.signup}}', subject: 'Reset', extract: 'link', pattern: '/reset', variableName: 'reset' } },
        { type: 'goto', value: '{{reset}}' },
    ]);
    assert.equal(ctx.variables.reset, 'https://shop.test/reset?t=abc');
    assert.deepEqual(rec.visited, ['https://shop.test/reset?t=abc']);
    assert.equal(calls.length, 2);
    assert.equal(calls[0].path, '/api/internal/test-inbox/search');
    // The worker names (run, case, row, label) — never an address.
    assert.deepEqual(calls[0].body, {
        run_id: 7, test_case_id: 11, row_index: null, label: 'signup', subject: 'Reset',
        subject_regex: '', from_contains: '', extract: 'link', pattern: '/reset',
    });
});

test('wait-for-email defaults: default inbox, link mode, email_link variable', async () => {
    fakeBackend([{ status: 200, json: { found: true, value: 'https://x.test/l', message: {} } }]);
    const { ctx } = await run([{ type: 'wait-for-email', params: {} }]);
    assert.equal(ctx.variables.email_link, 'https://x.test/l');
});

test('wait-for-email code mode stores email_code for a later fill', async () => {
    fakeBackend([{ status: 200, json: { found: true, value: '482913', message: {} } }]);
    const { rec, ctx } = await run([
        { type: 'wait-for-email', params: { extract: 'code' } },
        { type: 'fill', selector: '#otp', value: '{{email_code}}' },
    ]);
    assert.equal(ctx.variables.email_code, '482913');
    assert.deepEqual(rec.filled, ['482913']);
});

test('wait-for-email times out with the near miss in the message', async () => {
    fakeBackend([{ status: 200, json: { found: false, seen: 1, near_miss: { subject: 'Welcome!' } } }]);
    await assert.rejects(
        run([{ type: 'wait-for-email', params: { extract: 'link', pattern: '/reset', timeout: 6000 } }]),
        /no email to tiqabc@inbox.test matched within 6s — "Welcome!" arrived but had no link matching \/\/reset\//,
    );
});

test('wait-for-email keeps polling through a backend outage', async () => {
    fakeBackend([
        new Error('ECONNREFUSED'),
        { status: 502, json: { detail: 'Mailpit unreachable' } },
        { status: 200, json: { found: true, value: 'v', message: {} } },
    ]);
    const { ctx } = await run([{ type: 'wait-for-email', params: { extract: 'body' } }]);
    assert.equal(ctx.variables.email_body, 'v');
});

test('wait-for-email fails at once on a step error the backend reports (no waiting out the timeout)', async () => {
    const { calls } = fakeBackend([{ status: 422, json: { detail: 'subject pattern is not a valid regular expression' } }]);
    await assert.rejects(
        run([{ type: 'wait-for-email', params: { subjectRegex: '(' } }]),
        (err: any) => err instanceof HookRequestError && /not a valid regular expression/.test(err.message),
    );
    assert.equal(calls.length, 1);
});

test('wait-for-email refuses an address that is not the run\'s own', async () => {
    const { calls } = fakeBackend([{ status: 200, json: { found: true, value: 'x' } }]);
    await assert.rejects(run([{ type: 'wait-for-email', params: { to: 'ceo@company.com' } }]), /not one of this run's/);
    assert.equal(calls.length, 0);
});

test('wait-for-email rejects an unknown extract mode', async () => {
    fakeBackend([{ status: 200, json: {} }]);
    await assert.rejects(run([{ type: 'wait-for-email', params: { extract: 'attachment' } }]), /extract must be one of/);
});

// --------------------------------------------------------------------------
// wait-for-webhook
// --------------------------------------------------------------------------

function cap(over: Partial<Capture>): Capture {
    return { id: 1, method: 'POST', path: '', query: '', headers: {}, content_type: 'application/json',
             body: '{}', received_at: '2026-10-15T00:00:00Z', ...over };
}

test('captureMatches checks method, path and JSON conditions', () => {
    const c = cap({ path: '/events', body: JSON.stringify({ type: 'order.paid', data: { id: 42, items: [{ sku: 'A' }] } }) });
    assert.ok(captureMatches(c, {}));
    assert.ok(captureMatches(c, { method: 'post', path: '/events', where: { type: 'order.paid', 'data.id': 42, 'data.items[0].sku': 'A' } }));
    assert.ok(captureMatches(c, { where: { 'data.id': '42' } }));          // string/number compare
    assert.ok(!captureMatches(c, { method: 'PUT' }));
    assert.ok(!captureMatches(c, { path: '/other' }));
    assert.ok(!captureMatches(c, { where: { type: 'order.refunded' } }));
    assert.ok(!captureMatches(c, { where: { missing: 'x' } }));
    assert.ok(!captureMatches(cap({ body: 'not json', content_type: 'text/plain' }), { where: { a: 1 } }));
});

test('wait-for-webhook waits for the matching capture, extracts and asserts', async () => {
    const { calls } = fakeBackend([
        { status: 200, json: { captures: [] } },
        { status: 200, json: { captures: [
            cap({ id: 3, body: JSON.stringify({ type: 'order.created', data: { id: 9 } }) }),
            cap({ id: 2, body: JSON.stringify({ type: 'order.paid', data: { id: 42 } }) }),
        ] } },
    ]);
    const { rec, ctx } = await run([
        { type: 'wait-for-webhook', params: { url: '{{webhook.url.paid}}', method: 'POST', where: { type: 'order.paid' }, extract: 'data.id', equals: '42', variableName: 'order_id' } },
        { type: 'fill', selector: '#order', value: '{{order_id}}' },
    ]);
    assert.equal(ctx.variables.order_id, '42');
    assert.deepEqual(rec.filled, ['42']);
    assert.equal(calls[0].path, '/api/internal/webhook-captures/search');
    assert.deepEqual(calls[0].body, { run_id: 7, test_case_id: 11, row_index: null, label: 'paid' });
});

test('wait-for-webhook "where" may be given as a JSON string (editor form)', async () => {
    fakeBackend([{ status: 200, json: { captures: [cap({ body: '{"ok":true}' })] } }]);
    const { ctx } = await run([{ type: 'wait-for-webhook', params: { where: '{"ok": true}' } }]);
    assert.equal(ctx.variables.webhook_body, '{"ok":true}');
});

test('wait-for-webhook fails when the extracted value differs', async () => {
    fakeBackend([{ status: 200, json: { captures: [cap({ body: '{"status":"failed"}' })] } }]);
    await assert.rejects(
        run([{ type: 'wait-for-webhook', params: { extract: 'status', equals: 'succeeded' } }]),
        /status was "failed", expected "succeeded"/,
    );
});

test('wait-for-webhook times out naming what did arrive', async () => {
    fakeBackend([{ status: 200, json: { captures: [cap({ method: 'GET', path: '/ping', body: '' })] } }]);
    await assert.rejects(
        run([{ type: 'wait-for-webhook', params: { method: 'POST', timeout: 4000 } }]),
        /no request to .* matched within 4s — 1 other request\(s\) arrived; newest: GET \/ping/,
    );
});

test('wait-for-webhook stops on a rejected worker secret', async () => {
    fakeBackend([{ status: 403, json: { detail: 'Invalid worker secret' } }]);
    await assert.rejects(run([{ type: 'wait-for-webhook', params: {} }]), /worker secret rejected/);
});

test('both steps are listed as supported step types', () => {
    assert.ok(TestExecutor.SUPPORTED_STEP_TYPES.includes('wait-for-email'));
    assert.ok(TestExecutor.SUPPORTED_STEP_TYPES.includes('wait-for-webhook'));
});

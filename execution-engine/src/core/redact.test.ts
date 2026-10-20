/**
 * Worker-side redaction corpus.
 *
 * This deliberately mirrors backend/tests/test_redaction.py case for case. The
 * two implementations run at different points in the pipeline — this one at
 * capture time (the only layer that keeps secrets out of MinIO artifacts), the
 * Python one at ingestion (because the worker image bakes its code at build
 * time and may be older than the backend) — so they must agree on what counts
 * as sensitive. If you change one corpus, change both.
 *
 * The false-positive tests carry as much weight as the true-positive ones: a
 * redaction layer that mangles a debuggable response gets turned off, and a
 * layer that is turned off protects nothing.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
    REDACTED,
    RedactionPolicy,
    jobDataPolicy,
    luhnValid,
    maskSelectorsFrom,
    policyFromDataPolicy,
    redactBody,
    redactCrawlReport,
    redactHar,
    redactHeaders,
    redactHookReport,
    queryNamesOnly,
    redactDom,
    redactPageSource,
    redactJobResult,
    redactMessageText,
    redactNetworkEvents,
    redactPageBudgets,
    redactText,
    verhoeffValid,
} from './redact';

const VALID_PAN = '4111111111111111';
const NOT_A_PAN = '1234567890123456';
const VALID_AADHAAR = '234123412346';
const JWT = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk';

// --------------------------------------------------------------------------
// Checksums
// --------------------------------------------------------------------------

test('luhn accepts a real card number and rejects a lookalike', () => {
    assert.equal(luhnValid(VALID_PAN), true);
    assert.equal(luhnValid(NOT_A_PAN), false);
});

test('verhoeff accepts a valid aadhaar and rejects a lookalike', () => {
    assert.equal(verhoeffValid(VALID_AADHAAR), true);
    assert.equal(verhoeffValid('234123412341'), false);
});

// --------------------------------------------------------------------------
// Headers
// --------------------------------------------------------------------------

test('authorization header is redacted', () => {
    assert.equal(redactHeaders({ Authorization: 'Bearer abc.def.ghi' }).Authorization, REDACTED);
});

test('header matching is case insensitive', () => {
    const out = redactHeaders({ AUTHORIZATION: 'x', 'set-cookie': 's=1', 'X-Api-Key': 'k' });
    assert.equal(out.AUTHORIZATION, REDACTED);
    assert.equal(out['set-cookie'], REDACTED);
    assert.equal(out['X-Api-Key'], REDACTED);
});

test('benign headers survive untouched', () => {
    const input = { 'Content-Type': 'application/json', 'X-Request-Id': 'req-42' };
    assert.deepEqual(redactHeaders(input), input);
});

test('header names are preserved so the shape stays debuggable', () => {
    assert.deepEqual(Object.keys(redactHeaders({ Cookie: 'session=abc' })), ['Cookie']);
});

test('policy can add extra header names', () => {
    const policy: RedactionPolicy = { headerNames: ['x-tenant-token'] };
    assert.equal(redactHeaders({ 'X-Tenant-Token': 't' }, policy)['X-Tenant-Token'], REDACTED);
});

test('non-object headers do not explode', () => {
    assert.equal(redactHeaders(null as any), null);
    assert.deepEqual(redactHeaders(undefined as any), undefined);
});

// --------------------------------------------------------------------------
// JSON bodies — key denylist
// --------------------------------------------------------------------------

test('password field is redacted by key name', () => {
    const out = JSON.parse(redactBody(JSON.stringify({ email: 'a@b.com', password: 'hunter2' })));
    assert.equal(out.password, REDACTED);
});

test('key denylist is case and separator insensitive', () => {
    const body = JSON.stringify({ API_KEY: '1', apiKey: '2', 'api-key': '3', Access_Token: '4' });
    const out = JSON.parse(redactBody(body));
    for (const value of Object.values(out)) assert.equal(value, REDACTED);
});

test('nested objects are redacted', () => {
    const body = JSON.stringify({ user: { profile: { ssn: '123-45-6789' } } });
    assert.equal(JSON.parse(redactBody(body)).user.profile.ssn, REDACTED);
});

test('arrays of objects are redacted', () => {
    const body = JSON.stringify({ cards: [{ cvv: '123' }, { cvv: '456' }] });
    const out = JSON.parse(redactBody(body));
    assert.deepEqual(out.cards.map((c: any) => c.cvv), [REDACTED, REDACTED]);
});

test('non-sensitive fields are preserved exactly', () => {
    const payload = { orderId: 991, status: 'shipped', items: ['a', 'b'], total: 12.5 };
    assert.deepEqual(JSON.parse(redactBody(JSON.stringify(payload))), payload);
});

test('json structure and types are preserved', () => {
    const out = JSON.parse(redactBody(JSON.stringify({ count: 3, ok: true, nothing: null, password: 'x' })));
    assert.equal(out.count, 3);
    assert.equal(out.ok, true);
    assert.equal(out.nothing, null);
});

// --------------------------------------------------------------------------
// Value patterns
// --------------------------------------------------------------------------

test('luhn-valid card number is redacted even under an innocuous key', () => {
    const out = redactBody(JSON.stringify({ note: `charged ${VALID_PAN} today` }));
    assert.ok(!out.includes(VALID_PAN));
    assert.ok(out.includes('[REDACTED:pan]'));
});

test('card number with spaces is redacted', () => {
    assert.ok(!redactBody(JSON.stringify({ note: '4111 1111 1111 1111' })).includes('4111'));
});

test('number that fails luhn is left alone', () => {
    assert.ok(redactBody(JSON.stringify({ orderRef: NOT_A_PAN })).includes(NOT_A_PAN));
});

test('aadhaar with valid checksum is redacted', () => {
    assert.ok(!redactBody(JSON.stringify({ note: `id ${VALID_AADHAAR}` })).includes(VALID_AADHAAR));
});

test('twelve digits failing verhoeff are left alone', () => {
    assert.ok(redactBody(JSON.stringify({ ref: '234123412341' })).includes('234123412341'));
});

test('jwt is redacted wherever it appears', () => {
    const out = redactBody(JSON.stringify({ debug: `token=${JWT}` }));
    assert.ok(!out.includes(JWT));
    assert.ok(out.includes('[REDACTED:jwt]'));
});

// --------------------------------------------------------------------------
// Cloud keys (WP7) — twin of the Python corpus. Built by concatenation so the
// literal never appears in the repo (push protection / secret scanners).
// --------------------------------------------------------------------------

const AWS_KEY_ID = 'AKIA' + 'IOSFODNN7EXAMPLE';
const GH_TOKEN = 'ghp' + '_' + 'a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8';
const SLACK_TOKEN = 'xox' + 'b-' + '123456789012-abcdefghijkl';
const STRIPE_KEY = 'sk' + '_live_' + '4eC39HqLyjWDarjtT1zdp7dc';
const GOOGLE_KEY = 'AIza' + 'SyA1234567890abcdefghijklmnopqrstuv';
const AZURE_KEY = 'A'.repeat(86) + '==';
const PEM = '-----BEGIN RSA ' + 'PRIVATE KEY-----\nMIIEowIBAAKCAQEA7\n' + '-----END RSA ' + 'PRIVATE KEY-----';

for (const [secret, name] of [
    [AWS_KEY_ID, 'aws_access_key'],
    [GH_TOKEN, 'github_token'],
    [SLACK_TOKEN, 'slack_token'],
    [STRIPE_KEY, 'stripe_key'],
    [GOOGLE_KEY, 'google_api_key'],
] as const) {
    test(`cloud key ${name} is redacted by default`, () => {
        const out = redactBody(JSON.stringify({ note: `key is ${secret} ok` }));
        assert.ok(!out.includes(secret));
        assert.ok(out.includes(`[REDACTED:${name}]`));
    });
}

test('azure storage key in a connection string is redacted', () => {
    const conn = `DefaultEndpointsProtocol=https;AccountName=x;AccountKey=${AZURE_KEY};`;
    const out = redactText(conn);
    assert.ok(!out.includes(AZURE_KEY));
    assert.ok(out.includes('AccountKey=[REDACTED:azure_storage_key];'));
});

test('pem private key block is redacted whole', () => {
    assert.equal(redactText(`cfg ${PEM} tail`), 'cfg [REDACTED:private_key] tail');
});

test('truncated pem private key is still redacted', () => {
    assert.equal(redactText('-----BEGIN ' + 'PRIVATE KEY-----\nMIIEvQIBADANBgkq'), '[REDACTED:private_key]');
});

test('aws key digits are not eaten by the pan rule first', () => {
    assert.equal(redactText('AKIA' + '4111111111111111'), '[REDACTED:aws_access_key]');
});

test('cloud key lookalikes are left alone', () => {
    for (const text of ['AKIA-not-a-key', 'ghp_short', 'sk_test_4eC39HqLyjWDarjtT1zdp7dc', 'xoxb-short', 'AIzaShort']) {
        assert.equal(redactText(text), text);
    }
});

test('cloud keys can be switched off by policy', () => {
    assert.equal(redactText(AWS_KEY_ID, { patterns: ['jwt'] }), AWS_KEY_ID);
});

test('email is kept by default', () => {
    assert.ok(redactBody(JSON.stringify({ to: 'a@b.com' })).includes('a@b.com'));
});

test('email is redacted when the policy opts in', () => {
    const policy: RedactionPolicy = { patterns: ['email'] };
    assert.ok(!redactBody(JSON.stringify({ to: 'a@b.com' }), undefined, policy).includes('a@b.com'));
});

test('policy can disable all value patterns', () => {
    const policy: RedactionPolicy = { patterns: [] };
    assert.ok(redactBody(JSON.stringify({ note: VALID_PAN }), undefined, policy).includes(VALID_PAN));
});

// --------------------------------------------------------------------------
// Non-JSON bodies
// --------------------------------------------------------------------------

test('form encoded body is redacted by key', () => {
    const out = redactBody('username=alice&password=hunter2', 'application/x-www-form-urlencoded');
    assert.ok(!out.includes('hunter2'));
    assert.ok(out.includes('username=alice'));
});

test('plain text body still gets a pattern sweep', () => {
    assert.ok(!redactBody(`card was ${VALID_PAN}`, 'text/plain').includes(VALID_PAN));
});

test('malformed json falls back to a text sweep rather than throwing', () => {
    const out = redactBody('{"password": "hunter2", ', 'application/json');
    assert.equal(typeof out, 'string');
});

test('html response body is not mangled', () => {
    const html = '<html><body><h1>Order 1234567890123456</h1></body></html>';
    assert.equal(redactBody(html, 'text/html'), html);
});

test('empty and null bodies are passed through', () => {
    assert.equal(redactBody(null as any), null);
    assert.equal(redactBody(''), '');
});

// --------------------------------------------------------------------------
// Free text
// --------------------------------------------------------------------------

test('redactText sweeps patterns', () => {
    assert.ok(!redactText(`failed charging ${VALID_PAN}`)!.includes(VALID_PAN));
});

test('redactText leaves ordinary error messages intact', () => {
    const msg = 'Expected text "Welcome back" not found in element "#greeting"';
    assert.equal(redactText(msg), msg);
});

test('redaction is idempotent', () => {
    const body = JSON.stringify({ password: 'x', note: VALID_PAN, jwt: JWT });
    const once = redactBody(body);
    assert.equal(redactBody(once), once);
});

// --------------------------------------------------------------------------
// Network events — the array the worker ships back and uploads as JSON
// --------------------------------------------------------------------------

test('network event headers are redacted in both directions', () => {
    const events = [{
        url: 'https://api.example.com/login',
        status: 200,
        requestHeaders: { authorization: 'Bearer x', accept: 'application/json' },
        responseHeaders: { 'set-cookie': 'sid=abc', 'content-type': 'application/json' },
    }];
    const out = redactNetworkEvents(events);
    assert.equal(out[0].requestHeaders.authorization, REDACTED);
    assert.equal(out[0].requestHeaders.accept, 'application/json');
    assert.equal(out[0].responseHeaders['set-cookie'], REDACTED);
});

test('a token in a network event query string is redacted', () => {
    const events = [{ url: `https://api.example.com/cb?id_token=${JWT}`, status: 200 }];
    assert.ok(!redactNetworkEvents(events)[0].url.includes(JWT));
});

test('network events keep their timing and status fields', () => {
    const events = [{ url: 'https://x/y', status: 404, duration: 12, method: 'GET' }];
    const out = redactNetworkEvents(events)[0];
    assert.equal(out.status, 404);
    assert.equal(out.duration, 12);
    assert.equal(out.method, 'GET');
});

// --------------------------------------------------------------------------
// HAR — the highest-risk artifact that CAN be scrubbed
// --------------------------------------------------------------------------

function sampleHar() {
    return {
        log: {
            version: '1.2',
            entries: [{
                request: {
                    method: 'POST',
                    url: 'https://api.example.com/pay',
                    headers: [
                        { name: 'Authorization', value: 'Bearer secret-token' },
                        { name: 'Content-Type', value: 'application/json' },
                    ],
                    cookies: [{ name: 'sid', value: 'abc123' }],
                    postData: { mimeType: 'application/json', text: JSON.stringify({ cvv: '123', amount: 10 }) },
                },
                response: {
                    status: 200,
                    headers: [{ name: 'Set-Cookie', value: 'sid=xyz' }],
                    cookies: [{ name: 'sid', value: 'xyz' }],
                    content: { mimeType: 'application/json', text: JSON.stringify({ card: VALID_PAN }) },
                },
            }],
        },
    };
}

test('har request headers are redacted', () => {
    const out = redactHar(sampleHar());
    const headers = out.log.entries[0].request.headers;
    assert.equal(headers.find((h: any) => h.name === 'Authorization').value, REDACTED);
    assert.equal(headers.find((h: any) => h.name === 'Content-Type').value, 'application/json');
});

test('har response headers are redacted', () => {
    const out = redactHar(sampleHar());
    assert.equal(out.log.entries[0].response.headers[0].value, REDACTED);
});

test('har cookies are redacted on both request and response', () => {
    const out = redactHar(sampleHar());
    assert.equal(out.log.entries[0].request.cookies[0].value, REDACTED);
    assert.equal(out.log.entries[0].response.cookies[0].value, REDACTED);
});

test('har post data is redacted by key', () => {
    const out = redactHar(sampleHar());
    const body = JSON.parse(out.log.entries[0].request.postData.text);
    assert.equal(body.cvv, REDACTED);
    assert.equal(body.amount, 10);
});

test('har response content is pattern-swept', () => {
    const out = redactHar(sampleHar());
    assert.ok(!out.log.entries[0].response.content.text.includes(VALID_PAN));
});

test('har structure is preserved so the file still parses', () => {
    const out = redactHar(sampleHar());
    assert.equal(out.log.version, '1.2');
    assert.equal(out.log.entries.length, 1);
    assert.equal(out.log.entries[0].response.status, 200);
    assert.equal(out.log.entries[0].request.url, 'https://api.example.com/pay');
});

test('redactHar tolerates a har with no entries', () => {
    assert.deepEqual(redactHar({ log: { version: '1.2' } }), { log: { version: '1.2' } });
    assert.deepEqual(redactHar({} as any), {});
});

// --------------------------------------------------------------------------
// Job result — the single chokepoint every worker result passes through
// --------------------------------------------------------------------------

function sampleResult(): any {
    return {
        job_id: 'j-1',
        run_id: 7,
        status: 'failed',
        duration: 1234,
        error: `card ${VALID_PAN} declined`,
        artifacts: { screenshots: ['runs/7/screenshots/j-1-failure.png'] },
        response_data: {
            status: 401,
            headers: { 'set-cookie': 'sid=abc', 'content-type': 'application/json' },
            body: JSON.stringify({ password: 'hunter2', ok: false }),
            request: {
                headers: { Authorization: 'Bearer tok' },
                body: JSON.stringify({ cvv: '123' }),
            },
        },
        network_events: [{ url: 'https://x/y', requestHeaders: { cookie: 'a=b' } }],
        execution_log: [{ step: 'fill', message: `typed ${VALID_PAN}` }],
    };
}

test('job result redacts response headers and body', () => {
    const out = redactJobResult(sampleResult());
    assert.equal(out.response_data.headers['set-cookie'], REDACTED);
    assert.equal(out.response_data.headers['content-type'], 'application/json');
    assert.equal(JSON.parse(out.response_data.body).password, REDACTED);
});

test('job result redacts the nested request headers and body', () => {
    const out = redactJobResult(sampleResult());
    assert.equal(out.response_data.request.headers.Authorization, REDACTED);
    assert.equal(JSON.parse(out.response_data.request.body).cvv, REDACTED);
});

test('job result redacts network events and the execution log', () => {
    const out = redactJobResult(sampleResult());
    assert.equal(out.network_events[0].requestHeaders.cookie, REDACTED);
    assert.ok(!JSON.stringify(out.execution_log).includes(VALID_PAN));
});

test('job result redacts the error message', () => {
    assert.ok(!redactJobResult(sampleResult()).error.includes(VALID_PAN));
});

test('job result redacts each retry attempt error', () => {
    const out = redactJobResult({
        ...sampleResult(),
        attempts: [{ attempt: 1, status: 'failed', error: `card ${VALID_PAN} declined` },
                   { attempt: 2, status: 'passed' }],
    });
    assert.ok(!out.attempts[0].error.includes(VALID_PAN));
    assert.equal(out.attempts[1].status, 'passed');
});

test('job result redacts per-step evidence text, console, requests and DOM', () => {
    const out = redactJobResult({
        ...sampleResult(),
        step_evidence: [{
            id: 's0', index: 0, type: 'fill', status: 'failed', duration_ms: 12,
            error: `card ${VALID_PAN} rejected`,
            selector: `#card-${VALID_PAN}`,
            console: [{ id: 's0.c0', type: 'error', text: `token ${JWT}` }],
            requests: [{ id: 's0.r0', method: 'GET', url: `https://x.test/reset/${JWT}`, status: 500 }],
            dom: `<div data-pan="${VALID_PAN}">aadhaar ${VALID_AADHAAR}</div>`,
            screenshot: 'runs/7/screenshots/j-1-failure.png',
        }],
    });
    const e = out.step_evidence[0];
    const flat = JSON.stringify(e);
    assert.ok(!flat.includes(VALID_PAN));
    assert.ok(!flat.includes(JWT));
    assert.ok(!flat.includes(VALID_AADHAAR));
    // Ids, status, status codes and artifact keys are routing data — untouched.
    assert.equal(e.id, 's0');
    assert.equal(e.requests[0].id, 's0.r0');
    assert.equal(e.requests[0].status, 500);
    assert.equal(e.screenshot, 'runs/7/screenshots/j-1-failure.png');
});

test('per-step evidence is redacted inside continuous test_results too', () => {
    const out = redactJobResult({
        job_id: 'j-3', run_id: 9, status: 'failed',
        test_results: [{ test_case_id: 1, status: 'failed',
            step_evidence: [{ id: 's0', console: [{ id: 's0.c0', type: 'error', text: `pan ${VALID_PAN}` }] }] }],
    });
    assert.ok(!JSON.stringify(out.test_results[0].step_evidence).includes(VALID_PAN));
});

test('jobDataPolicy reads the dispatcher\'s settings.data_policy as well as a top-level one', () => {
    assert.equal(jobDataPolicy({ settings: { data_policy: { capture_level: 'full' } } }).capture_level, 'full');
    assert.equal(jobDataPolicy({ data_policy: { capture_level: 'none' }, settings: { data_policy: { capture_level: 'full' } } }).capture_level, 'none');
    assert.equal(jobDataPolicy({}), undefined);
});

test('job result preserves routing and status fields exactly', () => {
    // These drive aggregation and finalization; mangling one loses a result.
    const out = redactJobResult(sampleResult());
    assert.equal(out.job_id, 'j-1');
    assert.equal(out.run_id, 7);
    assert.equal(out.status, 'failed');
    assert.equal(out.duration, 1234);
    assert.equal(out.response_data.status, 401);
    assert.deepEqual(out.artifacts.screenshots, ['runs/7/screenshots/j-1-failure.png']);
});

test('job result redacts each entry of a continuous job test_results array', () => {
    const result = {
        job_id: 'j-2', run_id: 8, status: 'passed',
        test_results: [
            { test_case_id: 1, status: 'passed', response_data: { headers: { cookie: 'x' } } },
            { test_case_id: 2, status: 'failed', error: `pan ${VALID_PAN}` },
        ],
    };
    const out = redactJobResult(result);
    assert.equal(out.test_results[0].response_data.headers.cookie, REDACTED);
    assert.ok(!out.test_results[1].error.includes(VALID_PAN));
    assert.equal(out.test_results[0].test_case_id, 1);
    assert.equal(out.test_results[1].status, 'failed');
});

test('job result redaction never throws on a minimal result', () => {
    const minimal = { job_id: 'j', run_id: 1, status: 'passed' };
    assert.deepEqual(redactJobResult(minimal), minimal);
});

test('job result redaction is idempotent', () => {
    const once = redactJobResult(sampleResult());
    assert.deepEqual(redactJobResult(once), once);
});

// --------------------------------------------------------------------------
// Screenshot mask selectors
// --------------------------------------------------------------------------

test('mask selectors are read from the data policy', () => {
    assert.deepEqual(
        maskSelectorsFrom({ mask_selectors: ['#ssn', '[data-pii]'] }),
        ['#ssn', '[data-pii]']);
});

test('mask selectors default to none when the policy is absent', () => {
    assert.deepEqual(maskSelectorsFrom(undefined), []);
    assert.deepEqual(maskSelectorsFrom({}), []);
    assert.deepEqual(maskSelectorsFrom({ mask_selectors: 'not-an-array' }), []);
});

test('blank and non-string mask selectors are dropped', () => {
    // An empty selector would match nothing useful and Playwright throws on
    // some malformed ones — a bad policy entry must not fail the whole run.
    assert.deepEqual(maskSelectorsFrom({ mask_selectors: ['#a', '', '   ', 42, null] }), ['#a']);
});

test('mask selectors are trimmed', () => {
    assert.deepEqual(maskSelectorsFrom({ mask_selectors: ['  #ssn  '] }), ['#ssn']);
});

// --------------------------------------------------------------------------
// Policy construction from the backend's data_policy blob
// --------------------------------------------------------------------------

test('policy is built from the data policy blob', () => {
    const policy = policyFromDataPolicy({
        redact_headers: ['x-tenant'], redact_body_fields: ['policyno'], redact_patterns: ['pan', 'email'],
    });
    assert.deepEqual(policy.headerNames, ['x-tenant']);
    assert.deepEqual(policy.bodyKeys, ['policyno']);
    assert.deepEqual(policy.patterns, ['pan', 'email']);
});

test('an absent data policy yields the built-in defaults, not an empty policy', () => {
    const policy = policyFromDataPolicy(undefined);
    // patterns undefined means "use DEFAULT_PATTERNS" — an empty array would
    // mean "scan for nothing", which is the opposite of what a missing policy
    // should imply.
    assert.equal(policy.patterns, undefined);
    assert.equal(redactBody(JSON.stringify({ note: VALID_PAN }), undefined, policy).includes(VALID_PAN), false);
});

test('a policy naming extra fields still applies the built-in denylist', () => {
    const policy = policyFromDataPolicy({ redact_body_fields: ['policyno'] });
    const out = JSON.parse(redactBody(JSON.stringify({ password: 'x', policyno: 'P-1', ok: 1 }), undefined, policy));
    assert.equal(out.password, REDACTED);
    assert.equal(out.policyno, REDACTED);
    assert.equal(out.ok, 1);
});

// --------------------------------------------------------------------------
// Inbound messages (test inbox previews, captured webhooks)
// Mirrors the redact_message_text block in tests/test_redaction.py.
// --------------------------------------------------------------------------

test('message: a reset link loses its query string and opaque path token', () => {
    assert.equal(
        redactMessageText('Reset: https://shop.example.com/reset/9f8a7b6c5d4e3f2a1b0c9d8e?token=abc&x=1 now'),
        'Reset: https://shop.example.com/reset/[REDACTED:link]?[REDACTED:link] now',
    );
});

test('message: fragments are dropped too (implicit-flow style links)', () => {
    assert.equal(redactMessageText('go https://x.io/cb#access=abc'), 'go https://x.io/cb#[REDACTED:link]');
});

test('message: readable paths survive', () => {
    const text = 'See https://shop.example.com/blog/announcing-the-new-dashboard today';
    assert.equal(redactMessageText(text), text);
});

test('message: a standalone 6-8 digit code is masked, even before a full stop', () => {
    assert.equal(redactMessageText('Your code is 482913.'), 'Your code is [REDACTED:code].');
    assert.equal(redactMessageText('PIN 12345678 expires'), 'PIN [REDACTED:code] expires');
});

test('message: years, decimals and alphanumeric ids are not codes', () => {
    const text = 'In 2026 the total was 1234567.89 for order A1234567';
    assert.equal(redactMessageText(text), text);
});

test('message: the regular value patterns still apply', () => {
    assert.equal(redactMessageText(`card ${VALID_PAN} jwt ${JWT}`), 'card [REDACTED:pan] jwt [REDACTED:jwt]');
});

test('message: non-strings pass through', () => {
    assert.equal(redactMessageText(''), '');
    assert.equal(redactMessageText(null), null);
});

// --- access-matrix observations (twin: test_access_observation_* in test_redaction.py)

test('an access observation has its headers, body excerpt and urls scrubbed', async () => {
    const { redactAccessObservation } = await import('./redact');
    const obs = redactAccessObservation({
        status: 200, element_present: null,
        headers: { 'set-cookie': 'sid=abc', 'content-type': 'application/json' },
        body_excerpt: JSON.stringify({ token: 'abc', id: 7 }),
        url: `https://shop.example.com/pay?card=${VALID_PAN}`,
        swapped_id: '101',
    });
    assert.equal(obs.headers['set-cookie'], REDACTED);
    assert.equal(obs.headers['content-type'], 'application/json');
    assert.equal(JSON.parse(obs.body_excerpt).token, REDACTED);
    assert.equal(JSON.parse(obs.body_excerpt).id, 7);
    assert.equal(obs.url.includes(VALID_PAN), false);
    assert.equal(obs.status, 200);
    assert.equal(obs.swapped_id, '101');
});

test('a truncated body excerpt degrades to a text sweep, not a throw', async () => {
    const { redactAccessObservation } = await import('./redact');
    const obs = redactAccessObservation({ body_excerpt: `{"note": "${VALID_PAN}", "x": [1, 2` });
    assert.equal(obs.body_excerpt.includes(VALID_PAN), false);
});

// ---- hook_report (suite setup/teardown + cleanup). Twin corpus:
// tests/test_redaction.py::test_hook_report_*

const HOOK_JWT = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U';

test('hook report: cleanup URL keeps query names only and phase errors are scrubbed', () => {
    const out = redactJobResult({
        hook_report: {
            setup: [{ suite_id: 1, phase: 'setup', status: 'failed', error: `401 for token ${HOOK_JWT}`,
                      steps: [{ index: 0, type: 'http-request', status: 'failed', error: `bad ${HOOK_JWT}` }] }],
            teardown: [],
            cleanup: [{ method: 'DELETE', url: 'https://api.test/items/9?token=s3cret&x=1', status: 'failed',
                        error: `card 4111 1111 1111 1111 rejected`, origin: 'case' }],
        },
    });
    const r = out.hook_report;
    assert.ok(!r.setup[0].error.includes(HOOK_JWT));
    assert.ok(!r.setup[0].steps[0].error.includes(HOOK_JWT));
    assert.equal(r.cleanup[0].url, 'https://api.test/items/9?token=…&x=…');
    assert.ok(!r.cleanup[0].error.includes('4111 1111 1111 1111'));
    assert.equal(r.cleanup[0].status, 'failed');
    assert.equal(r.setup[0].suite_id, 1);
});

test('hook report: continuous results are scrubbed too and odd shapes pass through', () => {
    const out = redactJobResult({ test_results: [{ hook_report: { cleanup: [{ url: '/x?a=1#frag' }] } }] });
    assert.equal(out.test_results[0].hook_report.cleanup[0].url, '/x?a=…');
    assert.deepEqual(redactHookReport(['x']), ['x']);
    assert.equal(queryNamesOnly('{{api}}/items/{{id}}'), '{{api}}/items/{{id}}');
});

// --- WP10: page budgets + crawl reports (twins in tests/test_redaction.py) ---

test('page budget outcomes: URL, failure and SEO values are scrubbed, numbers kept', () => {
    const [out] = redactPageBudgets([{
        step_id: 'b1', url: `https://shop.example.com/reset/${JWT}`, passed: false,
        failure: `Page budget exceeded on https://shop.example.com/reset/${JWT}: lcp_ms 3000 > 2500`,
        metrics: { lcp_ms: 3000 }, checks: [{ metric: 'lcp_ms', value: 3000, budget: 2500, passed: false }],
        seo: [{ check: 'canonical', passed: true, value: `https://shop.example.com/reset/${JWT}`, detail: 'ok' }],
    }]);
    assert.ok(!JSON.stringify(out).includes(JWT));
    assert.equal(out.metrics.lcp_ms, 3000);
    assert.equal(out.checks[0].budget, 2500);
    // Also reached through a job result.
    const res = redactJobResult({ page_budgets: [{ url: `https://x.test/?t=${JWT}` }] });
    assert.ok(!JSON.stringify(res).includes(JWT));
});

test('crawl report: every URL incl. redirect hops is scrubbed; counts and statuses kept', () => {
    const out = redactCrawlReport({
        status: 'ok', links_checked: 3,
        pages: [{ url: `https://s.example/p?card=${VALID_PAN}`, status: 200 }],
        issues: [{
            page_url: 'https://s.example/', target_url: `https://s.example/reset/${JWT}`, category: 'broken',
            status_code: 404, detail: `see ${JWT}`,
            redirect_chain: [{ url: `https://s.example/r?token=${JWT}`, status: 302 }],
        }],
    });
    const flat = JSON.stringify(out);
    assert.ok(!flat.includes(JWT));
    assert.ok(!flat.includes(VALID_PAN));
    assert.equal(out.issues[0].status_code, 404);
    assert.equal(out.issues[0].redirect_chain[0].status, 302);
    assert.equal(out.links_checked, 3);
});

// --------------------------------------------------------------------------
// DOM snapshots for selector heal (twin: test_redaction.py "redact_dom")
// --------------------------------------------------------------------------

const DOM_FIXTURE = `<!DOCTYPE html><html><head>
<meta name="csrf-token" content="s3cr3t-csrf"><link rel="stylesheet" href="/a.css?v=1">
<script>window.API_KEY = "sk_live_abc123";</script><style>.x{color:red}</style></head>
<body><!-- build 4f2a debug token=xyz -->
<form id="login" action="/session?next=/home&amp;sig=abcdef">
  <input type="hidden" name="authenticity_token" value="tok-123">
  <input id="email" name="email" type="email" value="jane@corp.example" placeholder="Email">
  <input id="password" name="password" type="password" value="hunter2">
  <input type="submit" value="Sign in" onclick="steal()">
  <textarea name="notes">private notes</textarea>
  <button data-testid="login-submit" class="btn primary" aria-label="Log in" style="color:red">Log in</button>
  <img src="data:image/png;base64,AAAA" alt="logo">
  <div class="api-token-display">ghp_abcdefghijklmnopqrstuvwxyz0123456789</div>
  <span data-token="abc">Card 4111 1111 1111 1111</span>
  <div style="display: none" title="${'x'.repeat(300)}">Hidden panel</div>
  <div class="profile-card"><p>Jane Doe, 12 Main St</p></div>
</form></body></html>`;

test('redactDom strips scripts, styles, comments, meta and hidden inputs', () => {
    const out = redactDom(DOM_FIXTURE);
    for (const leaked of ['sk_live_abc123', 'color:red}', 'build 4f2a', 's3cr3t-csrf', 'tok-123', 'authenticity_token']) {
        assert.ok(!out.includes(leaked), `leaked ${leaked}`);
    }
    assert.ok(out.includes('<script></script>'));
});

test('redactDom drops input values and textarea text but keeps button labels', () => {
    const out = redactDom(DOM_FIXTURE);
    assert.ok(!out.includes('jane@corp.example'));
    assert.ok(!out.includes('hunter2'));
    assert.ok(!out.includes('private notes'));
    assert.ok(out.includes('value="Sign in"'));
});

test('redactDom removes text inside credential-looking elements and secret attributes', () => {
    const out = redactDom(DOM_FIXTURE);
    assert.ok(!out.includes('ghp_abcdefghijklmnopqrstuvwxyz0123456789'));
    assert.ok(!out.includes('data-token'));
    assert.ok(!out.includes('4111 1111 1111 1111'));
});

test('redactDom strips handlers, data URIs, URL queries and long attributes', () => {
    const out = redactDom(DOM_FIXTURE);
    assert.ok(!out.includes('onclick'));
    assert.ok(!out.includes('base64,AAAA'));
    assert.ok(out.includes('src="data:[REDACTED]"'));
    assert.ok(out.includes('action="/session"'));
    assert.ok(!out.includes('xxxxxxxxxx'));
    assert.ok(out.includes('style="display:none"'));
    assert.ok(!out.includes('color:red'));
});

test('redactDom keeps what selectors are built from', () => {
    const out = redactDom(DOM_FIXTURE);
    for (const kept of ['id="email"', 'name="email"', 'placeholder="Email"', 'data-testid="login-submit"',
        'class="btn primary"', 'aria-label="Log in"', '>Log in</button>', 'id="password"', 'alt="logo"', 'Hidden panel']) {
        assert.ok(out.includes(kept), `lost ${kept}`);
    }
});

test('redactDom applies project mask selectors and body keys', () => {
    const out = redactDom(DOM_FIXTURE, { maskSelectors: ['div.profile-card'], policy: { bodyKeys: ['notes'] } });
    assert.ok(!out.includes('Jane Doe'));
    assert.ok(out.includes('class="profile-card"'));
    const plain = redactDom(DOM_FIXTURE);
    assert.ok(plain.includes('Jane Doe'));
});

test('redactDom is idempotent and tolerates junk', () => {
    const once = redactDom(DOM_FIXTURE);
    assert.equal(redactDom(once), once);
    assert.equal(redactDom(''), '');
    assert.equal(redactDom(null as any), '');
    assert.ok(redactDom('<div <p>broken < 3 </div>').length > 0);
});

test('redactPageSource blanks password fields and keeps resource ids', () => {
    const xml = '<hierarchy><android.widget.EditText resource-id="com.app:id/password" password="true" text="hunter2"/>'
        + '<android.widget.EditText resource-id="com.app:id/user" text="jane"/></hierarchy>';
    const out = redactPageSource(xml);
    assert.ok(!out.includes('hunter2'));
    assert.ok(out.includes('resource-id="com.app:id/password"'));
    assert.ok(out.includes('android.widget.EditText'));
    assert.ok(out.includes('text="jane"'));
});

test('a job result carrying heal snapshots is redacted as a backstop', () => {
    const out = redactJobResult({
        heal_suggestions: [{ dom_snapshot: '<input type="password" value="hunter2">' }],
        step_dom: { s1: '<script>secret()</script><input value="x1">' },
    });
    assert.ok(!JSON.stringify(out).includes('hunter2'));
    assert.ok(!JSON.stringify(out).includes('secret()'));
});

test('step evidence trace ids (WP16a) pass through redaction untouched', () => {
    // All-digit ids are the worst case for the PAN / Aadhaar patterns. Twin:
    // test_step_evidence_trace_ids_are_untouched in test_redaction.py.
    const trace = { trace_id: '4111111111111111'.repeat(2), span_id: '4111111111111111', requests: 3 };
    const out = redactJobResult({ step_evidence: [{ id: 's0', index: 0, type: 'goto', status: 'passed', trace }] });
    assert.deepEqual(out.step_evidence[0].trace, trace);
});

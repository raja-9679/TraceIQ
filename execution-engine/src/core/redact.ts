/**
 * Capture-time redaction of credentials and PII.
 *
 * This is the layer that matters. The backend redacts again on ingestion
 * (`app/services/redaction.py`), but by then the artifact bytes — screenshots,
 * HAR, the network-log JSON — have already been written to MinIO. Only this
 * module runs early enough to keep them out.
 *
 * Two mechanisms, deliberately different in character:
 *
 * *Key denylist* — a field called `password` or `cvv` is sensitive whatever it
 * contains. This catches what no regex can: a three-digit CVV, a short PIN, an
 * opaque session blob.
 *
 * *Value patterns* — a card number is sensitive wherever it appears, including
 * under an innocuous key or buried in prose. These are checksum-validated
 * (Luhn for PAN, Verhoeff for Aadhaar) rather than "any 16 digits", because
 * over-redaction destroys the debugging value of a captured response, and a
 * redaction layer nobody trusts is a redaction layer somebody switches off.
 *
 * `email` and `phone` are implemented but OFF by default — email addresses are
 * ordinary test-fixture data. Deployments handling health or KYC data enable
 * them per project via `Project.data_policy`.
 *
 * Keep in lockstep with backend/app/services/redaction.py; the two corpora
 * (redact.test.ts and tests/test_redaction.py) mirror each other on purpose.
 */

export const REDACTED = '[REDACTED]';

const token = (name: string) => `[REDACTED:${name}]`;

// --------------------------------------------------------------------------
// Denylists
// --------------------------------------------------------------------------

export const DEFAULT_HEADER_NAMES: readonly string[] = [
    'authorization', 'proxy-authorization', 'cookie', 'set-cookie',
    'x-api-key', 'x-auth-token', 'x-access-token', 'x-csrf-token',
    'x-xsrf-token', 'x-session-token', 'x-traceiq-secret', 'x-worker-secret',
];

export const DEFAULT_BODY_KEYS: readonly string[] = [
    'password', 'passwd', 'pwd', 'currentpassword', 'newpassword',
    'secret', 'clientsecret', 'apikey', 'apisecret', 'token',
    'accesstoken', 'refreshtoken', 'idtoken', 'sessiontoken', 'authtoken',
    'authorization', 'auth', 'credentials', 'privatekey', 'sessionstate',
    'cookie', 'otp', 'pin', 'mfacode', 'totp', 'recoverycode',
    'cvv', 'cvc', 'cardcode', 'securitycode',
    'cardnumber', 'creditcard', 'pan', 'accountnumber',
    'ssn', 'socialsecuritynumber', 'aadhaar', 'aadhar', 'taxid',
];

export type PatternName = 'pan' | 'aadhaar' | 'jwt' | 'cloud_keys' | 'email' | 'phone';

/**
 * `cloud_keys` is a family of vendor credential formats (AWS, GitHub, Slack,
 * Stripe, Google, Azure, PEM private keys). Each has a fixed prefix, so unlike
 * email/phone it does not collide with ordinary test data.
 */
export const DEFAULT_PATTERNS: readonly PatternName[] = ['pan', 'aadhaar', 'jwt', 'cloud_keys'];

export interface RedactionPolicy {
    /** Extra header names beyond DEFAULT_HEADER_NAMES. */
    headerNames?: readonly string[];
    /** Extra body field names beyond DEFAULT_BODY_KEYS. */
    bodyKeys?: readonly string[];
    /** Value patterns to apply. Omit for DEFAULT_PATTERNS; [] disables them. */
    patterns?: readonly string[];
}

const normalizeKey = (key: string) => String(key).toLowerCase().replace(/[^a-z0-9]/g, '');

const DEFAULT_BODY_KEY_SET = new Set(DEFAULT_BODY_KEYS.map(normalizeKey));
const DEFAULT_HEADER_SET = new Set(DEFAULT_HEADER_NAMES);

function headerSet(policy?: RedactionPolicy): Set<string> {
    if (!policy?.headerNames?.length) return DEFAULT_HEADER_SET;
    return new Set([...DEFAULT_HEADER_SET, ...policy.headerNames.map(h => h.toLowerCase())]);
}

function bodyKeySet(policy?: RedactionPolicy): Set<string> {
    if (!policy?.bodyKeys?.length) return DEFAULT_BODY_KEY_SET;
    return new Set([...DEFAULT_BODY_KEY_SET, ...policy.bodyKeys.map(normalizeKey)]);
}

function patternNames(policy?: RedactionPolicy): readonly string[] {
    return policy?.patterns === undefined ? DEFAULT_PATTERNS : policy.patterns;
}

/**
 * The `data_policy` a job carries. The dispatcher has always put it under
 * `settings` (`_settings_payload`), while the workers read it from the job's
 * top level — so every web and mobile job silently ran at the default
 * capture level with only the built-in redaction rules. Accept both.
 */
export function jobDataPolicy(job: any): any {
    return job?.data_policy ?? job?.settings?.data_policy;
}

/** Build a policy from the `data_policy` blob the backend attaches to a job. */
export function policyFromDataPolicy(dataPolicy: any): RedactionPolicy {
    if (!dataPolicy || typeof dataPolicy !== 'object') return {};
    return {
        headerNames: Array.isArray(dataPolicy.redact_headers) ? dataPolicy.redact_headers : undefined,
        bodyKeys: Array.isArray(dataPolicy.redact_body_fields) ? dataPolicy.redact_body_fields : undefined,
        patterns: Array.isArray(dataPolicy.redact_patterns) ? dataPolicy.redact_patterns : undefined,
    };
}

/**
 * CSS selectors to mask out of every screenshot, from the job's `data_policy`.
 *
 * Screenshots are the one artifact that can be made safe without being dropped:
 * Playwright paints a solid box over each masked locator at capture time, so
 * the pixels never exist. Anything not expressible as a selector — text baked
 * into a canvas, a PDF preview — is not covered, which is part of why `full`
 * capture stays an explicit opt-in.
 */
export function maskSelectorsFrom(dataPolicy: any): string[] {
    const raw = dataPolicy?.mask_selectors;
    if (!Array.isArray(raw)) return [];
    return raw.filter((s: any) => typeof s === 'string' && s.trim()).map((s: string) => s.trim());
}

// --------------------------------------------------------------------------
// Checksums
// --------------------------------------------------------------------------

/** Mod-10 check used by every major card scheme. */
export function luhnValid(digits: string): boolean {
    let total = 0;
    const parity = digits.length % 2;
    for (let i = 0; i < digits.length; i++) {
        let digit = digits.charCodeAt(i) - 48;
        if (i % 2 === parity) {
            digit *= 2;
            if (digit > 9) digit -= 9;
        }
        total += digit;
    }
    return total % 10 === 0;
}

const VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
];
const VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
];

/** Aadhaar's 12th digit is a Verhoeff check over the first 11. */
export function verhoeffValid(digits: string): boolean {
    let check = 0;
    const reversed = digits.split('').reverse();
    for (let i = 0; i < reversed.length; i++) {
        check = VERHOEFF_D[check][VERHOEFF_P[i % 8][reversed[i].charCodeAt(0) - 48]];
    }
    return check === 0;
}

// --------------------------------------------------------------------------
// Value patterns
// --------------------------------------------------------------------------

const PAN_RE = /(?<![\d.])(?:\d[ -]?){12,18}\d(?![\d.])/g;
const AADHAAR_RE = /(?<![\d.])\d{4}[ -]?\d{4}[ -]?\d{4}(?![\d.])/g;
const JWT_RE = /eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}/g;
const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;
const PHONE_RE = /(?<![\d.])\+?\d[\d ()-]{8,16}\d(?![\d.])/g;

/**
 * Mirrors CLOUD_KEY_PATTERNS in backend/app/services/redaction.py. The PEM rule
 * runs to the END line or to the end of the text, because a capture truncated
 * mid-key still leaks a private key.
 */
export const CLOUD_KEY_PATTERNS: ReadonlyArray<readonly [string, RegExp]> = [
    ['private_key', /-----BEGIN ([A-Z0-9 ]*)PRIVATE KEY-----[\s\S]*?(?:-----END \1PRIVATE KEY-----|$)/g],
    ['aws_access_key', /(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])/g],
    ['github_token', /(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255})/g],
    ['slack_token', /(?<![A-Za-z0-9])xox[abposr]-[A-Za-z0-9-]{10,}/g],
    ['stripe_key', /(?<![A-Za-z0-9])[sr]k_live_[A-Za-z0-9]{20,}/g],
    ['google_api_key', /(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])/g],
    ['azure_storage_key', /(?<=AccountKey=)[A-Za-z0-9+/]{86}==/g],
];

const PATTERN_FUNCS: Record<string, (text: string) => string> = {
    cloud_keys: text => CLOUD_KEY_PATTERNS.reduce(
        (out, [name, re]) => out.replace(re, token(name)), text),
    pan: text => text.replace(PAN_RE, match => {
        const digits = match.replace(/[ -]/g, '');
        return digits.length >= 13 && digits.length <= 19 && luhnValid(digits) ? token('pan') : match;
    }),
    aadhaar: text => text.replace(AADHAAR_RE, match => {
        const digits = match.replace(/[ -]/g, '');
        return digits.length === 12 && verhoeffValid(digits) ? token('aadhaar') : match;
    }),
    jwt: text => text.replace(JWT_RE, token('jwt')),
    email: text => text.replace(EMAIL_RE, token('email')),
    phone: text => text.replace(PHONE_RE, token('phone')),
};

// Cloud keys first so an AWS key id's digit run is not half-eaten by the PAN
// rule; JWT before PAN so a token's digit runs are already gone; Aadhaar before
// PAN so a 12-digit id is not first swallowed by the wider PAN window.
const PATTERN_ORDER: readonly string[] = ['cloud_keys', 'jwt', 'aadhaar', 'pan', 'email', 'phone'];

export function redactText(text: any, policy?: RedactionPolicy): any {
    if (typeof text !== 'string' || !text) return text;
    const enabled = new Set(patternNames(policy));
    let out = text;
    for (const name of PATTERN_ORDER) {
        if (enabled.has(name)) out = PATTERN_FUNCS[name](out);
    }
    return out;
}

// --------------------------------------------------------------------------
// Inbound messages (test inbox emails, captured webhooks)
// --------------------------------------------------------------------------

const URL_RE = /https?:\/\/[^\s<>"')\]]+/g;
// An opaque credential in a path: long, URL-safe, with a letter AND a digit,
// so /blog/announcing-the-new-dashboard survives.
const PATH_TOKEN_RE = /^(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{20,}$/;
// A one-time code: 6-8 digits standing alone (a sentence-ending period is
// fine; a decimal point on either side is not).
const CODE_RE = /(?<!\w)(?<!\d\.)\d{6,8}(?!\w)(?!\.\d)/g;

function redactUrl(url: string): string {
    let cut = url.indexOf('?');
    if (cut < 0) cut = url.indexOf('#');
    const base = cut < 0 ? url : url.slice(0, cut);
    const sep = cut < 0 ? '' : url[cut];
    const [scheme, remainder] = [base.slice(0, base.indexOf('://')), base.slice(base.indexOf('://') + 3)];
    const slashAt = remainder.indexOf('/');
    let out = `${scheme}://${remainder}`;
    if (slashAt >= 0) {
        const host = remainder.slice(0, slashAt);
        const path = remainder.slice(slashAt + 1).split('/')
            .map(seg => (PATH_TOKEN_RE.test(seg) ? token('link') : seg)).join('/');
        out = `${scheme}://${host}/${path}`;
    }
    return out + (sep ? sep + token('link') : '');
}

/**
 * Free-text redaction for messages the APP UNDER TEST sends — a reset email,
 * a captured webhook body shown as text. Those carry live credentials by
 * design (the reset link IS the password), so beyond `redactText` this drops
 * link query strings/fragments, masks opaque path segments and masks
 * standalone 6-8 digit codes. Previews and logs only: a step extracts its
 * value before redaction. Twin of redaction.py:redact_message_text.
 */
export function redactMessageText(text: any, policy?: RedactionPolicy): any {
    if (typeof text !== 'string' || !text) return text;
    let out = redactText(text, policy) as string;
    out = out.replace(URL_RE, redactUrl);
    return out.replace(CODE_RE, token('code'));
}

// --------------------------------------------------------------------------
// Headers
// --------------------------------------------------------------------------

export function redactHeaders(headers: any, policy?: RedactionPolicy): any {
    if (!headers || typeof headers !== 'object' || Array.isArray(headers)) return headers;
    const denied = headerSet(policy);
    const out: Record<string, any> = {};
    for (const [key, value] of Object.entries(headers)) {
        out[key] = denied.has(key.toLowerCase()) ? REDACTED : redactText(value, policy);
    }
    return out;
}

// --------------------------------------------------------------------------
// Bodies
// --------------------------------------------------------------------------

function redactJson(value: any, denied: Set<string>, policy?: RedactionPolicy): any {
    if (Array.isArray(value)) return value.map(item => redactJson(item, denied, policy));
    if (value && typeof value === 'object') {
        const out: Record<string, any> = {};
        for (const [key, val] of Object.entries(value)) {
            out[key] = denied.has(normalizeKey(key)) ? REDACTED : redactJson(val, denied, policy);
        }
        return out;
    }
    if (typeof value === 'string') return redactText(value, policy);
    return value;
}

function redactForm(body: string, denied: Set<string>, policy?: RedactionPolicy): string {
    const params = new URLSearchParams(body);
    const out = new URLSearchParams();
    let sawAny = false;
    params.forEach((value, key) => {
        sawAny = true;
        out.append(key, denied.has(normalizeKey(key)) ? REDACTED : redactText(value, policy));
    });
    return sawAny ? out.toString() : redactText(body, policy);
}

/**
 * Redact a request or response body, returning the same shape it was given.
 * Malformed JSON degrades to a text sweep rather than throwing — a body that
 * failed to parse is exactly the kind that carries a stack trace with a token
 * in it.
 */
export function redactBody(body: any, contentType?: string, policy?: RedactionPolicy): any {
    if (body === null || body === undefined || body === '') return body;

    const denied = bodyKeySet(policy);

    if (typeof body === 'object') return redactJson(body, denied, policy);
    if (typeof body !== 'string') return body;

    const ctype = (contentType || '').toLowerCase();
    if (ctype.includes('x-www-form-urlencoded')) return redactForm(body, denied, policy);

    const looksJson = /^[\s]*[{[]/.test(body);
    if (ctype.includes('json') || looksJson) {
        try {
            return JSON.stringify(redactJson(JSON.parse(body), denied, policy));
        } catch {
            return redactText(body, policy);
        }
    }
    return redactText(body, policy);
}

// --------------------------------------------------------------------------
// Network events
// --------------------------------------------------------------------------

const EVENT_HEADER_FIELDS = ['requestHeaders', 'responseHeaders', 'request_headers', 'response_headers'];
const EVENT_TEXT_FIELDS = ['url', 'requestBody', 'responseBody', 'request_body', 'response_body'];

export function redactNetworkEvents(events: any, policy?: RedactionPolicy): any {
    if (!Array.isArray(events)) return events;
    return events.map(event => {
        if (!event || typeof event !== 'object') return event;
        const out = { ...event };
        for (const field of EVENT_HEADER_FIELDS) {
            if (field in out) out[field] = redactHeaders(out[field], policy);
        }
        for (const field of EVENT_TEXT_FIELDS) {
            if (typeof out[field] === 'string') out[field] = redactText(out[field], policy);
        }
        return out;
    });
}

// --------------------------------------------------------------------------
// Job result
// --------------------------------------------------------------------------

/** The `response_data` blob attached to an API step's result. */
function redactResponseData(data: any, policy?: RedactionPolicy): any {
    if (!data || typeof data !== 'object') return data;
    const out = { ...data };
    if ('headers' in out) out.headers = redactHeaders(out.headers, policy);
    if ('body' in out) out.body = redactBody(out.body, out.mimeType || out.content_type, policy);
    if (out.request && typeof out.request === 'object') {
        const request = { ...out.request };
        if ('headers' in request) request.headers = redactHeaders(request.headers, policy);
        if ('body' in request) request.body = redactBody(request.body, undefined, policy);
        if (typeof request.url === 'string') request.url = redactText(request.url, policy);
        out.request = request;
    }
    if (typeof out.url === 'string') out.url = redactText(out.url, policy);
    return out;
}

function redactCaseResult(result: any, policy?: RedactionPolicy): any {
    if (!result || typeof result !== 'object') return result;
    const out = { ...result };
    if ('response_data' in out) out.response_data = redactResponseData(out.response_data, policy);
    if ('network_events' in out) out.network_events = redactNetworkEvents(out.network_events, policy);
    if (typeof out.error === 'string') out.error = redactText(out.error, policy);
    if (typeof out.error_message === 'string') out.error_message = redactText(out.error_message, policy);
    if (Array.isArray(out.execution_log)) {
        out.execution_log = redactJson(out.execution_log, bodyKeySet(policy), policy);
    }
    if (Array.isArray(out.steps)) {
        out.steps = redactJson(out.steps, bodyKeySet(policy), policy);
    }
    if (Array.isArray(out.attempts)) {
        out.attempts = out.attempts.map((a: any) =>
            a && typeof a === 'object' && typeof a.error === 'string'
                ? { ...a, error: redactText(a.error, policy) } : a);
    }
    if (Array.isArray(out.step_evidence)) out.step_evidence = redactStepEvidence(out.step_evidence, policy);
    if (out.hook_report && typeof out.hook_report === 'object') out.hook_report = redactHookReport(out.hook_report, policy);
    return out;
}

/**
 * A URL (or URL template) with query parameter VALUES dropped — values are
 * where captured ids, tokens and signatures live. Twin: `_query_names_only`.
 */
export function queryNamesOnly(raw: any): string {
    const s = String(raw ?? '').split('#')[0];
    const q = s.indexOf('?');
    if (q < 0) return s;
    const names = Array.from(new Set(s.slice(q + 1).split('&').map(p => p.split('=')[0]).filter(Boolean)));
    return `${s.slice(0, q)}${names.length ? '?' + names.map(n => `${n}=…`).join('&') : ''}`;
}

/**
 * Suite setup/teardown + cleanup outcomes (`hook_report`). Errors can quote
 * response bodies and cleanup URLs carry ids captured at run time, so both are
 * scrubbed; statuses and suite ids are routing data. Twin: `redact_hook_report`.
 */
export function redactHookReport(report: any, policy?: RedactionPolicy): any {
    if (!report || typeof report !== 'object' || Array.isArray(report)) return report;
    const phase = (list: any) => Array.isArray(list) ? list.map((p: any) => {
        if (!p || typeof p !== 'object') return p;
        const out = { ...p };
        if (typeof out.error === 'string') out.error = redactText(out.error, policy);
        if (Array.isArray(out.steps)) {
            out.steps = out.steps.map((st: any) =>
                st && typeof st === 'object' && typeof st.error === 'string' ? { ...st, error: redactText(st.error, policy) } : st);
        }
        return out;
    }) : list;
    const out = { ...report };
    if ('setup' in out) out.setup = phase(out.setup);
    if ('teardown' in out) out.teardown = phase(out.teardown);
    if (Array.isArray(out.cleanup)) {
        out.cleanup = out.cleanup.map((c: any) => {
            if (!c || typeof c !== 'object') return c;
            const item = { ...c };
            if (typeof item.url === 'string') item.url = redactText(queryNamesOnly(item.url), policy);
            if (typeof item.error === 'string') item.error = redactText(item.error, policy);
            return item;
        });
    }
    return out;
}

/**
 * Backstop over per-step evidence. Each field is scrubbed where it is
 * captured (`core/evidence.ts`); this pass is what guarantees it even if a
 * future capture point forgets. Twin: `_redact_step_evidence` in redaction.py.
 */
export function redactStepEvidence(entries: any, policy?: RedactionPolicy): any {
    if (!Array.isArray(entries)) return entries;
    return entries.map((e: any) => {
        if (!e || typeof e !== 'object') return e;
        const out = { ...e };
        for (const f of ['error', 'selector', 'dom']) {
            if (typeof out[f] === 'string') out[f] = redactText(out[f], policy);
        }
        if (Array.isArray(out.console)) {
            out.console = out.console.map((c: any) =>
                c && typeof c === 'object' && typeof c.text === 'string' ? { ...c, text: redactText(c.text, policy) } : c);
        }
        if (Array.isArray(out.requests)) {
            out.requests = out.requests.map((r: any) =>
                r && typeof r === 'object' && typeof r.url === 'string' ? { ...r, url: redactText(r.url, policy) } : r);
        }
        return out;
    });
}

/**
 * Redact a whole worker result immediately before it is serialised onto the
 * results stream.
 *
 * This is the one place every result passes through — `JobQueue.completeJob`
 * is called by the web worker and the mobile worker alike — which is why it is
 * the right place for a backstop even though the individual capture points are
 * scrubbed too.
 *
 * Routing and status fields (`job_id`, `run_id`, `status`, durations, artifact
 * keys) are deliberately untouched: the aggregator uses them to finalize a run,
 * and mangling one silently loses a result.
 */
export function redactJobResult(result: any, policy?: RedactionPolicy): any {
    if (!result || typeof result !== 'object') return result;
    const out = redactCaseResult(result, policy);
    if (Array.isArray(out.test_results)) {
        out.test_results = out.test_results.map((r: any) => redactCaseResult(r, policy));
    }
    return out;
}

// --------------------------------------------------------------------------
// Access-matrix observations
// --------------------------------------------------------------------------

const OBSERVATION_TEXT_FIELDS = ['url', 'final_url', 'error', 'swapped_id', 'owner_url'];

/**
 * Scrub an access-matrix cell observation (twin of `redact_access_observation`
 * in backend/app/services/redaction.py). It carries response headers and a body
 * excerpt of a request made AS a persona — exactly where a session token gets
 * echoed back.
 */
export function redactAccessObservation(obs: any, policy?: RedactionPolicy): any {
    if (!obs || typeof obs !== 'object' || Array.isArray(obs)) return obs;
    const out = { ...obs };
    if ('headers' in out) out.headers = redactHeaders(out.headers, policy);
    if ('owner_headers' in out) out.owner_headers = redactHeaders(out.owner_headers, policy);
    if ('body_excerpt' in out) out.body_excerpt = redactBody(out.body_excerpt, out.content_type, policy);
    for (const field of OBSERVATION_TEXT_FIELDS) {
        if (typeof out[field] === 'string') out[field] = redactText(out[field], policy);
    }
    return out;
}

// --------------------------------------------------------------------------
// HAR
// --------------------------------------------------------------------------

/**
 * Redact a HAR `[{name, value}]` list.
 *
 * `alwaysSensitive` exists for cookie lists. A cookie is named whatever the
 * application felt like — `sid`, `JSESSIONID`, `_app_session` — so name
 * matching finds almost none of them, yet every value in a `cookies[]` array
 * is by definition a session credential. Header lists still match by name,
 * because most headers there are ordinary.
 */
function redactNameValueList(
    list: any, denied: Set<string>, policy?: RedactionPolicy, alwaysSensitive = false,
): any {
    if (!Array.isArray(list)) return list;
    const deniedHeaders = headerSet(policy);
    return list.map(item => {
        if (!item || typeof item !== 'object') return item;
        const name = String(item.name || '').toLowerCase();
        const sensitive = alwaysSensitive || deniedHeaders.has(name) || denied.has(normalizeKey(name));
        return { ...item, value: sensitive ? REDACTED : redactText(item.value, policy) };
    });
}

/**
 * Scrub a parsed HAR in place-of-value.
 *
 * A HAR is the single highest-risk artifact that can actually be redacted: it
 * carries every request header, cookie, and body the browser saw. Unlike a
 * trace it is plain JSON with a known schema, so the sensitive fields are
 * addressable. The structure is preserved so the file still opens in any HAR
 * viewer.
 */
export function redactHar(har: any, policy?: RedactionPolicy): any {
    if (!har || typeof har !== 'object' || !har.log || !Array.isArray(har.log.entries)) return har;
    const denied = bodyKeySet(policy);

    const entries = har.log.entries.map((entry: any) => {
        if (!entry || typeof entry !== 'object') return entry;
        const out = { ...entry };

        if (out.request && typeof out.request === 'object') {
            const request = { ...out.request };
            request.headers = redactNameValueList(request.headers, denied, policy);
            request.cookies = redactNameValueList(request.cookies, denied, policy, true);
            request.queryString = redactNameValueList(request.queryString, denied, policy);
            if (typeof request.url === 'string') request.url = redactText(request.url, policy);
            if (request.postData && typeof request.postData.text === 'string') {
                request.postData = {
                    ...request.postData,
                    text: redactBody(request.postData.text, request.postData.mimeType, policy),
                };
            }
            out.request = request;
        }

        if (out.response && typeof out.response === 'object') {
            const response = { ...out.response };
            response.headers = redactNameValueList(response.headers, denied, policy);
            response.cookies = redactNameValueList(response.cookies, denied, policy, true);
            if (response.content && typeof response.content.text === 'string') {
                response.content = {
                    ...response.content,
                    text: redactBody(response.content.text, response.content.mimeType, policy),
                };
            }
            out.response = response;
        }

        return out;
    });

    return { ...har, log: { ...har.log, entries } };
}

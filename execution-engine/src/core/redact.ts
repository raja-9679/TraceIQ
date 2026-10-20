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

import { escapeAttr, escapeText, matchesCompound, parseMaskSelector, tokenize, VOID_ELEMENTS } from './html-lite';

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

// Fullwidth digits (U+FF10..U+FF19) fold to ASCII for MATCHING only. Python's
// `\d` (the ingestion twin) matches them and redacts a fullwidth card number;
// JS `\d` is ASCII-only, so without this the worker would keep one the
// backend later removes. One UTF-16 unit each, so offsets line up; text that
// is not redacted keeps its original characters.
const FULLWIDTH_DIGIT = /[\uFF10-\uFF19]/g;

function foldDigits(text: string): string {
    return text.replace(FULLWIDTH_DIGIT, c => String.fromCharCode(c.charCodeAt(0) - 0xFF10 + 0x30));
}

function replaceFolded(text: string, re: RegExp, redact: (folded: string) => string | null): string {
    const folded = foldDigits(text);
    if (folded === text) return text.replace(re, m => redact(m) ?? m);
    let out = '';
    let last = 0;
    for (const m of folded.matchAll(re)) {
        const at = m.index ?? 0;
        const replacement = redact(m[0]);
        if (replacement === null) continue;
        out += text.slice(last, at) + replacement;
        last = at + m[0].length;
    }
    return out + text.slice(last);
}

const PATTERN_FUNCS: Record<string, (text: string) => string> = {
    cloud_keys: text => CLOUD_KEY_PATTERNS.reduce(
        (out, [name, re]) => out.replace(re, token(name)), text),
    pan: text => replaceFolded(text, PAN_RE, match => {
        const digits = match.replace(/[ -]/g, '');
        return digits.length >= 13 && digits.length <= 19 && luhnValid(digits) ? token('pan') : null;
    }),
    aadhaar: text => replaceFolded(text, AADHAAR_RE, match => {
        const digits = match.replace(/[ -]/g, '');
        return digits.length === 12 && verhoeffValid(digits) ? token('aadhaar') : null;
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

const SECRET_SELECTOR_RE = /pass(word|wd)?|secret|token|api[-_]?key|otp|cvv|cvc|pin\b|card[-_]?number/i;

/** `input[type=password]`, `#password`, `[name="cvv"]` and friends. Twin: `_looks_like_secret_field`. */
function looksLikeSecretField(selector: string): boolean {
    if (!selector) return false;
    if (selector.replace(/["' ]/g, '').includes('type=password')) return true;
    return SECRET_SELECTOR_RE.test(selector);
}

/**
 * Step definitions: a value typed into a password-ish field (or a step whose
 * `name` is a denied key) is a credential whatever it looks like; `{{…}}`
 * templates are safe by construction. Twin of `redact_steps` — the backstop
 * used to sweep steps with the JSON walker only, so a literal password typed
 * into `#password` reached the results stream.
 */
function redactSteps(steps: any[], policy?: RedactionPolicy): any[] {
    const denied = bodyKeySet(policy);
    return steps.map(step => {
        if (!step || typeof step !== 'object' || Array.isArray(step)) return step;
        const out = { ...step };
        const value = out.value;
        if (typeof value === 'string' && value && !value.includes('{{')) {
            out.value = looksLikeSecretField(String(out.selector || '')) || denied.has(normalizeKey(String(out.name || '')))
                ? REDACTED : redactText(value, policy);
        }
        if (out.params && typeof out.params === 'object') out.params = redactJson(out.params, denied, policy);
        return out;
    });
}

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
    // Legacy-engine shape (runner.ts) — scrubbed here as well as at ingestion.
    for (const f of ['request_headers', 'response_headers']) if (f in out) out[f] = redactHeaders(out[f], policy);
    for (const f of ['request_body', 'response_body']) if (f in out) out[f] = redactBody(out[f], undefined, policy);
    if (Array.isArray(out.execution_log)) {
        out.execution_log = redactJson(out.execution_log, bodyKeySet(policy), policy);
    }
    if (Array.isArray(out.steps)) out.steps = redactSteps(out.steps, policy);
    if (Array.isArray(out.console_errors)) {
        out.console_errors = out.console_errors.map((e: any) =>
            e && typeof e === 'object' && typeof e.text === 'string' ? { ...e, text: redactText(e.text, policy) } : e);
    }
    if (Array.isArray(out.attempts)) {
        out.attempts = out.attempts.map((a: any) =>
            a && typeof a === 'object' && typeof a.error === 'string'
                ? { ...a, error: redactText(a.error, policy) } : a);
    }
    if (Array.isArray(out.step_evidence)) out.step_evidence = redactStepEvidence(out.step_evidence, policy);
    if (out.hook_report && typeof out.hook_report === 'object') out.hook_report = redactHookReport(out.hook_report, policy);
    if (Array.isArray(out.page_budgets)) out.page_budgets = redactPageBudgets(out.page_budgets, policy);
    if (Array.isArray(out.accessibility_results)) {
        out.accessibility_results = redactAccessibilityResults(out.accessibility_results, policy);
    }
    // DOM captured for selector heal. The capture sites already redact; this
    // is the backstop for any path that forgets to.
    if (out.step_dom && typeof out.step_dom === 'object' && !Array.isArray(out.step_dom)) {
        out.step_dom = Object.fromEntries(Object.entries(out.step_dom).map(
            ([k, v]) => [k, typeof v === 'string' ? redactDom(v, { policy }) : v]));
    }
    if (Array.isArray(out.heal_suggestions)) {
        out.heal_suggestions = out.heal_suggestions.map((h: any) =>
            h && typeof h === 'object' && typeof h.dom_snapshot === 'string'
                ? { ...h, dom_snapshot: redactDom(h.dom_snapshot, { policy }) } : h);
    }
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

const textField = (v: any, policy?: RedactionPolicy) => (typeof v === 'string' ? redactText(v, policy) : v);

/**
 * `check-page-budget` outcomes (WP10): the measured page URL (query strings
 * carry session ids and signed tokens), the failure text and SEO check values
 * (a title or canonical can echo the URL). Twin: `redact_page_budgets`.
 */
export function redactPageBudgets(items: any, policy?: RedactionPolicy): any {
    if (!Array.isArray(items)) return items;
    return items.map((b: any) => {
        if (!b || typeof b !== 'object') return b;
        const out = { ...b, url: textField(b.url, policy), failure: textField(b.failure, policy) };
        if (Array.isArray(b.seo)) {
            out.seo = b.seo.map((c: any) => c && typeof c === 'object'
                ? { ...c, value: textField(c.value, policy), detail: textField(c.detail, policy) } : c);
        }
        return out;
    });
}

// --------------------------------------------------------------------------
// Accessibility results (WP15d) — twin of the block in redaction.py
// --------------------------------------------------------------------------

/** Attributes whose VALUE a selector may keep: they name or type an element.
 * href/src/alt/title/value/placeholder/aria-label are page content (or URLs
 * with tokens) and lose their value. Twin: `A11Y_SAFE_ATTRS`. */
export const A11Y_SAFE_ATTRS = new Set([
    'id', 'class', 'type', 'role', 'name', 'for', 'lang', 'dir',
    'data-testid', 'data-test', 'data-test-id', 'data-qa', 'data-cy',
]);
const A11Y_ATTR_VALUE_MAX = 64;
const A11Y_TARGET_MAX = 300;
const A11Y_TARGETS_PER_RULE = 25;
const A11Y_RULES_PER_CHECK = 100;
const A11Y_HELP_PREFIX = 'https://dequeuniversity.com/';
// One attribute selector. Fixed pattern over a capped string.
const A11Y_ATTR_RE = /\[\s*([A-Za-z_][-A-Za-z0-9_:.]*)\s*(?:([~|^$*]?=)\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[^\]\s'"]+)\s*([iIsS])?\s*)?\]/g;

/** An axe target selector with nothing in it but what addresses the element.
 * Twin: `redact_a11y_target`. */
export function redactA11yTarget(selector: any, policy?: RedactionPolicy): string {
    if (typeof selector !== 'string') return '';
    const out = selector.slice(0, 500).replace(A11Y_ATTR_RE, (whole, name, op, value) => {
        if (!op) return whole;
        const bare = value[0] === '"' || value[0] === "'" ? value.slice(1, -1) : value;
        if (A11Y_SAFE_ATTRS.has(String(name).toLowerCase()) && bare.length <= A11Y_ATTR_VALUE_MAX) return whole;
        return `[${name}]`;
    });
    return String(redactText(out, policy) || '').slice(0, A11Y_TARGET_MAX);
}

/** The rule's reference page, only on Deque's site, query dropped. */
export function a11yHelpUrl(raw: any): string | null {
    if (typeof raw !== 'string' || !raw.startsWith(A11Y_HELP_PREFIX)) return null;
    return raw.split('?')[0].split('#')[0].slice(0, 300);
}

const a11yInt = (v: any): number =>
    typeof v === 'number' && Number.isFinite(v) ? Math.max(0, Math.min(Math.trunc(v), 1_000_000)) : 0;

/**
 * `check-accessibility` outcomes, rebuilt from a whitelist: page URL redacted,
 * selectors reduced by `redactA11yTarget`, help URLs pinned to Deque. Anything
 * else (axe's `html` / `failureSummary` — page text) is dropped.
 * Twin: `redact_accessibility_results`.
 */
export function redactAccessibilityResults(items: any, policy?: RedactionPolicy): any {
    if (!Array.isArray(items)) return items;
    const out: any[] = [];
    for (const item of items.slice(0, 50)) {
        if (!item || typeof item !== 'object' || Array.isArray(item)) continue;
        const violations: any[] = [];
        for (const v of (Array.isArray(item.violations) ? item.violations : []).slice(0, A11Y_RULES_PER_CHECK)) {
            if (!v || typeof v !== 'object' || Array.isArray(v)) continue;
            const targets: any[] = [];
            for (const t of (Array.isArray(v.targets) ? v.targets : []).slice(0, A11Y_TARGETS_PER_RULE)) {
                if (t && typeof t === 'object' && !Array.isArray(t)) {
                    targets.push({ selector: redactA11yTarget(t.selector, policy), nodes: a11yInt(t.nodes) || 1 });
                }
            }
            violations.push({
                id: String(v.id || '').slice(0, 80),
                impact: String(v.impact || '').slice(0, 20),
                help_url: a11yHelpUrl(v.help_url),
                node_count: a11yInt(v.node_count),
                targets,
            });
        }
        out.push({
            step_id: String(item.step_id || '').slice(0, 100) || null,
            url: typeof item.url === 'string' ? redactText(item.url, policy) : null,
            violations,
        });
    }
    return out;
}

/**
 * Site-crawl report (WP10): every URL it names, redirect hops included —
 * links on a page can embed tokens (password-reset, signed asset URLs).
 * Status codes and counts are left alone. Twin: `redact_crawl_report`.
 */
export function redactCrawlReport(report: any, policy?: RedactionPolicy): any {
    if (!report || typeof report !== 'object') return report;
    const out = { ...report, error: textField(report.error, policy) };
    if (Array.isArray(report.pages)) {
        out.pages = report.pages.map((p: any) => p && typeof p === 'object'
            ? { ...p, url: textField(p.url, policy), error: textField(p.error, policy) } : p);
    }
    if (Array.isArray(report.issues)) {
        out.issues = report.issues.map((i: any) => {
            if (!i || typeof i !== 'object') return i;
            const item = { ...i };
            for (const f of ['page_url', 'target_url', 'detail']) item[f] = textField(i[f], policy);
            if (Array.isArray(i.redirect_chain)) {
                item.redirect_chain = i.redirect_chain.map((h: any) => h && typeof h === 'object'
                    ? { ...h, url: textField(h.url, policy) } : h);
            }
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
    // `results` is the legacy engine's key; the backend twin handles both.
    for (const key of ['test_results', 'results']) {
        if (Array.isArray(out[key])) out[key] = out[key].map((r: any) => redactCaseResult(r, policy));
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

// --------------------------------------------------------------------------
// DOM snapshots (selector heal) — twin of redaction.py:redact_dom
// --------------------------------------------------------------------------

/** Cap on a redacted snapshot; the LLM sees far less (ai.ts truncates). */
export const MAX_DOM_SNAPSHOT_CHARS = 200_000;
const MAX_DOM_TEXT_CHARS = 200;
const MAX_DOM_ATTR_CHARS = 200;
const MAX_DOM_CLASSES = 12;

/** Element/attribute names that mark a credential-bearing control. */
const SECRET_NAME_RE = /passw(or)?d|passwd|\bpwd\b|secret|token|api[-_]?key|\botp\b|\bcvv\b|\bcvc\b|\bpin\b|card[-_]?number|\bssn\b|csrf|xsrf|credential|private[-_]?key/i;
const SECRET_AUTOCOMPLETE_RE = /^(current-password|new-password|one-time-code|cc-.*)$/i;
/** Elements dropped outright: their only content is configuration or a secret. */
const DROPPED_DOM_ELEMENTS = new Set(['meta', 'link', 'base']);
/** Elements whose content is code or not rendered: kept as empty shells. */
const EMPTIED_DOM_ELEMENTS = new Set(['script', 'style', 'noscript', 'template', 'textarea', 'xmp']);
const URL_ATTRS = new Set(['href', 'src', 'action', 'formaction', 'poster', 'cite', 'background']);
const DROPPED_ATTRS = new Set(['srcdoc', 'srcset', 'nonce', 'integrity', 'content', 'ping']);
const LABEL_INPUT_TYPES = new Set(['submit', 'button', 'reset']);
/** What survives on a masked element: enough to keep it addressable. */
const MASKED_KEEP_ATTRS = new Set(['id', 'class', 'role', 'name', 'type', 'for', 'data-testid', 'data-test', 'data-test-id', 'data-qa', 'data-cy']);

export interface DomRedactionOptions {
    policy?: RedactionPolicy;
    /** The project's screenshot mask selectors; matching elements lose their text. */
    maskSelectors?: readonly string[];
    maxChars?: number;
}

function secretName(value: string | undefined, denied: Set<string>): boolean {
    if (!value) return false;
    if (SECRET_NAME_RE.test(value)) return true;
    return denied.has(normalizeKey(value));
}

/** True when an element is a credential-bearing control or container. */
function isSensitiveElement(tag: string, attrs: Map<string, string>, denied: Set<string>): boolean {
    if ((attrs.get('type') || '').toLowerCase() === 'password') return true;
    if (SECRET_AUTOCOMPLETE_RE.test(attrs.get('autocomplete') || '')) return true;
    for (const name of ['id', 'name', 'autocomplete', 'data-testid', 'data-test', 'data-qa', 'aria-label']) {
        if (secretName(attrs.get(name), denied)) return true;
    }
    return (attrs.get('class') || '').split(/\s+/).some(c => c && SECRET_NAME_RE.test(c));
}

function stripUrl(value: string): string {
    const cut = value.search(/[?#]/);
    return cut < 0 ? value : value.slice(0, cut);
}

/**
 * Redact a page's HTML before it is sent to an LLM or stored as a heal
 * snapshot. Keeps what selectors are made of — tags, ids, classes, roles,
 * aria-*, data-testid, names, placeholders, short visible text — and drops
 * everything else that could carry a secret:
 *
 * - comments, <meta>/<link>/<base>, hidden inputs (CSRF tokens live there);
 * - script/style/noscript/template/textarea contents (the tags stay);
 * - every input `value` except a submit/button/reset label;
 * - all text inside a credential-looking element (type=password, an id/name/
 *   class like `api-token`, a policy body key) or a project mask selector;
 * - event handlers, inline styles (a hidden marker is kept), srcdoc/srcset,
 *   data: URIs, URL query strings/fragments, attribute values over 200 chars;
 * - value patterns (cards, Aadhaar, JWTs, cloud keys …) in what remains.
 */
export function redactDom(html: string, options: DomRedactionOptions = {}): string {
    if (typeof html !== 'string' || !html) return '';
    const policy = options.policy;
    const denied = bodyKeySet(policy);
    const masks = (options.maskSelectors || [])
        .map(s => parseMaskSelector(s))
        .filter((c): c is NonNullable<ReturnType<typeof parseMaskSelector>> => !!c)
        .flat();
    const maxChars = options.maxChars ?? MAX_DOM_SNAPSHOT_CHARS;

    // Open elements: [tag, suppressesText, insideMaskedElement, dropsMarkup].
    // dropsMarkup: markup inside <template>/<noscript>/<xmp> is not rendered,
    // so none of it is emitted (twin: the 4th stack field in redaction.py).
    const stack: Array<[string, boolean, boolean, boolean]> = [];
    const suppressed = () => stack.length > 0 && stack[stack.length - 1][1];
    const inMasked = () => stack.length > 0 && stack[stack.length - 1][2];
    const dropsMarkup = () => stack.length > 0 && stack[stack.length - 1][3];
    let out = '';

    for (const tok of tokenize(html)) {
        if (out.length > maxChars) break;
        if (tok.kind === 'comment') continue;
        if (tok.kind === 'doctype') { out += '<!DOCTYPE html>'; continue; }
        if (tok.kind === 'text') {
            if (suppressed()) continue;
            let text = tok.text.replace(/[\s ]+/g, ' ');
            if (!text.trim()) { if (text && !out.endsWith(' ')) out += ' '; continue; }
            if (text.length > MAX_DOM_TEXT_CHARS) text = text.slice(0, MAX_DOM_TEXT_CHARS) + '…';
            out += escapeText(redactText(text, policy));
            continue;
        }
        if (tok.kind === 'end') {
            if (dropsMarkup() && stack[stack.length - 1][0] !== tok.tag) continue;
            if (DROPPED_DOM_ELEMENTS.has(tok.tag) || VOID_ELEMENTS.has(tok.tag)) continue;
            const at = stack.map(s => s[0]).lastIndexOf(tok.tag);
            if (at < 0) continue;
            stack.length = at;
            out += `</${tok.tag}>`;
            continue;
        }

        if (dropsMarkup()) continue;
        const tag = tok.tag;
        const attrs = new Map(tok.attrs.map(([k, v]) => [k, v ?? '']));
        const type = (attrs.get('type') || '').toLowerCase();
        if (DROPPED_DOM_ELEMENTS.has(tag)) continue;
        if (tag === 'input' && type === 'hidden') continue;

        const masked = inMasked() || masks.some(c => matchesCompound(tag, attrs, c));
        const sensitive = isSensitiveElement(tag, attrs, denied);
        const parts: string[] = [];
        for (const [name, raw] of tok.attrs) {
            let value = raw ?? '';
            if (masked && !MASKED_KEEP_ATTRS.has(name)) continue;
            if (name.startsWith('on') || DROPPED_ATTRS.has(name)) continue;
            if (name === 'value' && !(tag === 'input' && LABEL_INPUT_TYPES.has(type) && !sensitive)) continue;
            if (name === 'style') {
                if (!/display\s*:\s*none|visibility\s*:\s*hidden/i.test(value)) continue;
                value = 'display:none';
            } else if (secretName(name.replace(/^data-/, ''), denied) && name !== 'type') {
                continue;  // data-token="…", data-api-key="…"
            } else if (/^\s*data:/i.test(value)) {
                value = 'data:[REDACTED]';
            } else if (URL_ATTRS.has(name)) {
                value = stripUrl(value);
            } else if (name === 'class') {
                value = value.split(/\s+/).filter(Boolean).slice(0, MAX_DOM_CLASSES).join(' ');
            }
            if (value.length > MAX_DOM_ATTR_CHARS) continue;
            value = redactText(value, policy);
            parts.push(raw === null && name !== 'value' ? name : `${name}="${escapeAttr(value)}"`);
        }
        out += `<${tag}${parts.length ? ' ' + parts.join(' ') : ''}${tok.selfClosing && !VOID_ELEMENTS.has(tag) ? '/' : ''}>`;
        if (VOID_ELEMENTS.has(tag) || tok.selfClosing) continue;
        stack.push([tag, suppressed() || masked || sensitive || EMPTIED_DOM_ELEMENTS.has(tag), masked,
            tag === 'noscript' || tag === 'template' || tag === 'xmp']);
    }
    return out.length > maxChars ? out.slice(0, maxChars) : out;
}

/** Appium attributes that hold what the user typed or sees. */
const MOBILE_TEXT_ATTRS = new Set(['text', 'value', 'label', 'content-desc', 'hint']);

/**
 * Redact a native page source (Appium XML) before it reaches an LLM: a
 * password field (`password="true"` or a secret-looking resource-id/name)
 * loses its text/value, long attribute values are dropped and value patterns
 * are scrubbed from the rest. Element names and resource ids — what locators
 * are made of — survive.
 */
export function redactPageSource(xml: string, policy?: RedactionPolicy, maxChars = MAX_DOM_SNAPSHOT_CHARS): string {
    if (typeof xml !== 'string' || !xml) return '';
    const denied = bodyKeySet(policy);
    let out = '';
    const stack: boolean[] = [];
    for (const tok of tokenize(xml, { xml: true })) {
        if (out.length > maxChars) break;
        if (tok.kind === 'comment' || tok.kind === 'doctype') continue;
        if (tok.kind === 'text') {
            if (stack[stack.length - 1]) continue;
            const t = tok.text.trim();
            if (t) out += escapeText(redactText(t.slice(0, MAX_DOM_TEXT_CHARS), policy));
            continue;
        }
        if (tok.kind === 'end') { stack.pop(); out += `</${tok.tag}>`; continue; }
        const attrs = new Map(tok.attrs.map(([k, v]) => [k, v ?? '']));
        const secret = attrs.get('password') === 'true'
            || ['resource-id', 'name', 'content-desc'].some(a => secretName((attrs.get(a) || '').split('/').pop(), denied));
        const parts: string[] = [];
        for (const [name, raw] of tok.attrs) {
            const value = raw ?? '';
            if (secret && MOBILE_TEXT_ATTRS.has(name)) continue;
            if (value.length > MAX_DOM_ATTR_CHARS) continue;
            parts.push(`${name}="${escapeAttr(redactText(value, policy))}"`);
        }
        // Appium tag names keep their case (XCUIElementTypeButton).
        out += `<${tok.tag}${parts.length ? ' ' + parts.join(' ') : ''}${tok.selfClosing ? '/' : ''}>`;
        if (!tok.selfClosing) stack.push((stack[stack.length - 1] ?? false) || secret);
    }
    return out.length > maxChars ? out.slice(0, maxChars) : out;
}

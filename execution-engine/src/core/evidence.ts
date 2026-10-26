/**
 * Evidence a worker attaches to each test result for backend failure
 * analysis and proactive selector heal. Pure helpers, kept out of worker.ts
 * (which starts a worker on import) so they can be unit-tested.
 */
import { stepOrigin, type StepOrigin } from './case-calls';
import * as vm from 'vm';
import type { FrameLocator, Page } from 'playwright';
import type { CaptureLevel } from './artifact-store';
import { RedactionPolicy, redactText } from './redact';
import type { StepTrace, TraceCorrelation } from './trace-context';

// Evidence handed to backend failure analysis alongside each result. Kept
// small on purpose: the full console log is a separate (policy-gated) artifact.
const MAX_CONSOLE_ERRORS_PER_TEST = 20;
const MAX_CONSOLE_ERROR_CHARS = 500;

/** This case's entries from a continuous job's `${caseId}:${stepId}` DOM map. */
export function caseStepDom(all: Record<string, string>, caseId: number): { step_dom?: Record<string, string> } {
    const prefix = `${caseId}:`;
    const mine: Record<string, string> = {};
    for (const [k, v] of Object.entries(all)) {
        if (k.startsWith(prefix)) mine[k.slice(prefix.length)] = v;
    }
    return Object.keys(mine).length ? { step_dom: mine } : {};
}

/** Console errors/warnings for one test, redacted and truncated. */
export function consoleErrorsFor(logs: any[], testName: string, policy?: RedactionPolicy): any[] {
    return logs
        .filter(l => l.test === testName && (l.type === 'error' || l.type === 'warning' || l.type === 'pageerror'))
        .slice(0, MAX_CONSOLE_ERRORS_PER_TEST)
        .map(l => ({
            ts: l.ts,
            type: l.type,
            text: redactText(String(l.text || '').slice(0, MAX_CONSOLE_ERROR_CHARS), policy),
        }));
}

// One retry attempt, as reported in a result's `attempts` list. The error is
// truncated; redaction happens with the rest of the result in redactJobResult.
export interface AttemptSummary {
    attempt: number; // 1-based
    status: string;
    error?: string;
    duration_ms?: number;
}

const MAX_ATTEMPT_ERROR_CHARS = 1000;

export function summariseAttempt(
    attempt: number, result: { status: string; error?: string; duration_ms?: number },
): AttemptSummary {
    const out: AttemptSummary = { attempt, status: result.status };
    if (typeof result.error === 'string' && result.error) {
        out.error = result.error.slice(0, MAX_ATTEMPT_ERROR_CHARS);
    }
    if (typeof result.duration_ms === 'number') out.duration_ms = result.duration_ms;
    return out;
}

// --------------------------------------------------------------------------
// Per-step evidence
// --------------------------------------------------------------------------
//
// One compact entry per step of a test, sent on the result as `step_evidence`
// and stored in `TestCaseResult.result_payload`. Failure investigation cites
// these entries by id (`s3`, `s3.c0`, `s3.r1`, `s3.dom`, `s3.shot`), so the ids
// are positional and deterministic — the backend re-derives the same ids from
// the stored list and drops any citation that does not resolve.
//
// What is captured is gated by the job's capture level:
//   none      step type/status/duration/error only
//   minimal   + console errors/warnings, failed requests, screenshot key
//   standard+ + a DOM excerpt around the FAILING step's target
// The DOM excerpt sits at `standard` (unlike the whole-body `step_dom`, which
// needs `full`) because it is one size-capped fragment around the target with
// input values and masked selectors removed and its text redacted — not a
// snapshot of the page.


export const MAX_EVIDENCE_STEPS = 200;
const MAX_STEP_CONSOLE = 10;
const MAX_STEP_REQUESTS = 10;
const MAX_EVIDENCE_TEXT = 300;
export const MAX_DOM_EXCERPT_CHARS = 4000;
const MAX_SELECTOR_CHARS = 200;
// Console + request items across the WHOLE test: a noisy page would otherwise
// put 200 steps x 20 items on one result row.
export const MAX_EVIDENCE_ITEMS_PER_TEST = 150;

export type StepEvidenceStatus = 'passed' | 'failed' | 'skipped' | 'healed';

export interface StepConsoleItem { id: string; type: string; text: string }
export interface StepRequestItem {
    id: string; method: string; url: string; status?: number;
    failure?: string; resource_type?: string;
}

export interface StepEvidence {
    id: string;              // `s<index>`
    index: number;           // 0-based position in the case's steps
    step_id?: string;
    type: string;
    selector?: string;
    status: StepEvidenceStatus;
    duration_ms?: number;
    error?: string;
    console?: StepConsoleItem[];
    requests?: StepRequestItem[];
    screenshot?: string;     // artifact key, resolved after upload
    screenshot_file?: string; // local file name; replaced by `screenshot` before sending
    dom?: string;
    dom_scope?: 'target' | 'page';
    // WP16a: the trace id / span id this step propagated in `traceparent`
    // and how many requests carried them. Ids, not content: the redaction
    // twins leave them alone.
    trace?: StepTrace;
    // The test a 'Run another test' step pulled this step in from.
    from?: StepOrigin;
}

const LEVEL_RANK: Record<CaptureLevel, number> = { none: 0, minimal: 1, standard: 2, full: 3 };

export function levelAtLeast(level: CaptureLevel, min: CaptureLevel): boolean {
    return LEVEL_RANK[level] >= LEVEL_RANK[min];
}

/**
 * A URL safe to keep in evidence: origin + path, query parameter NAMES only.
 * Query values are where tokens, signed-URL signatures and emails live; the
 * names are enough to tell two calls apart. The remainder is pattern-redacted
 * because paths carry tokens too (`/reset/<jwt>`).
 */
export function safeUrl(raw: any, policy?: RedactionPolicy): string {
    const s = String(raw || '');
    let out: string;
    try {
        const u = new URL(s);
        const names = Array.from(new Set(Array.from(u.searchParams.keys())));
        out = `${u.origin}${u.pathname}${names.length ? '?' + names.map(n => `${n}=…`).join('&') : ''}`;
    } catch {
        out = s.split(/[?#]/)[0];
    }
    return redactText(out.slice(0, 500), policy);
}

function clip(text: any, max = MAX_EVIDENCE_TEXT): string {
    return String(text ?? '').slice(0, max);
}

// A request the page itself cancelled (navigation away, a `block-requests`
// step, a superseded fetch) is not evidence of anything going wrong.
const IGNORED_REQUEST_FAILURES = /ERR_ABORTED|NS_BINDING_ABORTED|cancelled/i;

export interface EvidenceSources {
    consoleLogs: any[];      // worker's console accumulator ({type, text, test})
    networkEvents: any[];    // responses ({method, url, status, resourceType, testCaseName})
    failedRequests: any[];   // requestfailed ({method, url, failure, resourceType, testCaseName})
}

/**
 * Records evidence step by step. Indices into the shared accumulators are
 * snapshotted at `begin`, so each step gets exactly what was emitted while it
 * ran — the accumulators are append-only and shared across a continuous job.
 */
export class StepEvidenceRecorder {
    private entries: StepEvidence[] = [];
    private mark = { console: 0, net: 0, failed: 0, t: 0 };
    private current: { index: number; step: any } | null = null;
    private itemBudget = MAX_EVIDENCE_ITEMS_PER_TEST;
    private trace: TraceCorrelation | null = null;

    constructor(
        private readonly sources: EvidenceSources,
        private readonly testName: string,
        private readonly level: CaptureLevel,
        private readonly policy?: RedactionPolicy,
    ) {}

    /** Trace correlation for this case (WP16a); a span opens with each step. */
    setTrace(trace: TraceCorrelation | null): void {
        this.trace = trace;
    }

    begin(step: any, index: number): void {
        this.trace?.beginStep();
        this.mark = {
            console: this.sources.consoleLogs.length,
            net: this.sources.networkEvents.length,
            failed: this.sources.failedRequests.length,
            t: Date.now(),
        };
        this.current = { index, step };
    }

    /** Close the step opened by `begin`. Returns the entry (or null past the cap). */
    end(status: StepEvidenceStatus, error?: string): StepEvidence | null {
        if (!this.current) return null;
        const { index, step } = this.current;
        this.current = null;
        const stepTrace = this.trace?.endStep() || null;
        if (this.entries.length >= MAX_EVIDENCE_STEPS) return null;
        const id = `s${index}`;
        const entry: StepEvidence = {
            id, index, type: String(step?.type || 'unknown'), status,
            duration_ms: Date.now() - this.mark.t,
        };
        if (step?.id) entry.step_id = String(step.id);
        const origin = stepOrigin(step);
        if (origin) entry.from = origin;
        if (step?.selector) entry.selector = redactText(clip(step.selector, MAX_SELECTOR_CHARS), this.policy);
        if (error) entry.error = redactText(clip(error, 1000), this.policy);
        if (stepTrace) entry.trace = stepTrace;

        if (levelAtLeast(this.level, 'minimal')) {
            const consoleItems = this.sources.consoleLogs.slice(this.mark.console)
                .filter(l => l && (l.test === undefined || l.test === this.testName))
                .filter(l => l.type === 'error' || l.type === 'warning' || l.type === 'pageerror')
                .slice(0, Math.min(MAX_STEP_CONSOLE, this.itemBudget))
                .map((l, i) => ({ id: `${id}.c${i}`, type: String(l.type), text: redactText(clip(l.text), this.policy) }));
            if (consoleItems.length) entry.console = consoleItems;
            this.itemBudget -= consoleItems.length;

            const forThisTest = (e: any) => e && (e.testCaseName === undefined || e.testCaseName === this.testName);
            const bad: any[] = [
                ...this.sources.networkEvents.slice(this.mark.net)
                    .filter(e => forThisTest(e) && typeof e.status === 'number' && e.status >= 400),
                ...this.sources.failedRequests.slice(this.mark.failed)
                    .filter(e => forThisTest(e) && !IGNORED_REQUEST_FAILURES.test(String(e.failure || ''))),
            ];
            const requests = bad.slice(0, Math.min(MAX_STEP_REQUESTS, this.itemBudget)).map((e, i) => {
                const item: StepRequestItem = {
                    id: `${id}.r${i}`, method: String(e.method || 'GET'), url: safeUrl(e.url, this.policy),
                };
                if (typeof e.status === 'number') item.status = e.status;
                if (e.failure) item.failure = clip(e.failure, 200);
                if (e.resourceType) item.resource_type = String(e.resourceType);
                return item;
            });
            if (requests.length) entry.requests = requests;
            this.itemBudget -= requests.length;
        }
        this.entries.push(entry);
        return entry;
    }

    /** Attach the failure screenshot / DOM excerpt to the failing step. */
    attachToLast(extra: { screenshot_file?: string; dom?: string | null; dom_scope?: 'target' | 'page' }): void {
        const last = this.entries[this.entries.length - 1];
        if (!last) return;
        if (extra.screenshot_file && levelAtLeast(this.level, 'minimal')) last.screenshot_file = extra.screenshot_file;
        if (extra.dom && levelAtLeast(this.level, 'standard')) {
            last.dom = redactText(extra.dom.slice(0, MAX_DOM_EXCERPT_CHARS), this.policy);
            last.dom_scope = extra.dom_scope || 'page';
        }
    }

    /** Steps after a failure never ran; listing them keeps the timeline whole. */
    skipRemaining(steps: any[], fromIndex: number): void {
        for (let i = fromIndex; i < steps.length && this.entries.length < MAX_EVIDENCE_STEPS; i++) {
            const step = steps[i];
            const entry: StepEvidence = { id: `s${i}`, index: i, type: String(step?.type || 'unknown'), status: 'skipped' };
            if (step?.id) entry.step_id = String(step.id);
            if (step?.selector) entry.selector = redactText(clip(step.selector, MAX_SELECTOR_CHARS), this.policy);
            const origin = stepOrigin(step);
            if (origin) entry.from = origin;
            this.entries.push(entry);
        }
    }

    list(): StepEvidence[] {
        return this.entries;
    }
}

/**
 * Replace each entry's local `screenshot_file` with the uploaded artifact key.
 * A screenshot the capture policy suppressed (or that failed to upload) leaves
 * no key, and the entry simply carries none — never a dangling local path.
 */
export function resolveScreenshotKeys(
    entries: StepEvidence[] | undefined, uploadedKeys: string[], keyFor: (file: string) => string,
): void {
    if (!entries) return;
    const uploaded = new Set(uploadedKeys || []);
    for (const e of entries) {
        if (!e.screenshot_file) continue;
        const key = keyFor(e.screenshot_file);
        if (uploaded.has(key)) e.screenshot = key;
        delete e.screenshot_file;
    }
}

// --------------------------------------------------------------------------
// Fail on JavaScript errors
// --------------------------------------------------------------------------

export interface ConsoleFailurePolicy {
    enabled: boolean;
    ignore: string[];
}

/**
 * The effective "fail on console errors" policy for one case: the case's own
 * flag (when set) overrides the suite chain's; ignore patterns come from the
 * suite chain. Default off.
 */
export function consoleFailurePolicy(settings: any, testCase: any): ConsoleFailurePolicy {
    const caseFlag = testCase?.fail_on_console_errors;
    const enabled = typeof caseFlag === 'boolean' ? caseFlag : settings?.fail_on_console_errors === true;
    const raw = settings?.console_error_ignore_patterns;
    // Capped so the worst case (every pattern times out once) stays bounded.
    const ignore = Array.isArray(raw)
        ? raw.filter((p: any) => typeof p === 'string' && p.trim()).map((p: string) => p.trim()).slice(0, MAX_IGNORE_PATTERNS)
        : [];
    return { enabled, ignore };
}

// Ignore regexes are editor-supplied and run against console text the site
// under test controls, inside a shared worker. No syntax check can rule out
// catastrophic backtracking (`(a|aa)+` passes any "nested quantifier" filter),
// so each match runs under a vm timeout — V8 interrupts a runaway regex — and a
// pattern that times out once is dropped for the rest of the worker's life.
const MAX_IGNORE_PATTERN_CHARS = 200;
const MAX_IGNORE_TEXT_CHARS = 2000;
const IGNORE_REGEX_TIMEOUT_MS = 50;
const regexSandbox = vm.createContext({ re: null as RegExp | null, text: '' });
const regexProbe = new vm.Script('re.test(text)');
const MAX_IGNORE_PATTERNS = 50;
const timedOutPatterns = new Set<string>();
// Keyed on the COMPILED regex: RegExp normalises flag order and escapes `/` in
// `source`, so a key built from the raw text would never match the one
// recorded on timeout and the pattern would cost a full timeout every line.
const patternKey = (re: RegExp) => `${re.flags}/${re.source}`;

export function safeIgnoreRegex(source: string, flags: string): RegExp | null {
    if (source.length > MAX_IGNORE_PATTERN_CHARS) return null;
    let re: RegExp;
    try { re = new RegExp(source, flags); } catch { return null; }
    return timedOutPatterns.has(patternKey(re)) ? null : re;
}

/** `re.test(text)` that gives up (false) instead of stalling the worker. */
export function boundedRegexTest(re: RegExp, text: string, timeoutMs = IGNORE_REGEX_TIMEOUT_MS): boolean {
    regexSandbox.re = re;
    regexSandbox.text = text;
    try {
        return regexProbe.runInContext(regexSandbox, { timeout: timeoutMs }) === true;
    } catch {
        timedOutPatterns.add(patternKey(re));
        return false;
    } finally {
        regexSandbox.re = null;
        regexSandbox.text = '';
    }
}

/**
 * Does `text` match an ignore pattern? Plain patterns are case-insensitive
 * substrings; `/.../` (optionally `/.../i`) is a regular expression. An
 * invalid regex matches nothing rather than throwing mid-test.
 */
export function matchesIgnore(text: string, patterns: string[]): boolean {
    const t = String(text || '').slice(0, MAX_IGNORE_TEXT_CHARS);
    for (const p of patterns) {
        const m = /^\/(.+)\/([imsu]*)$/.exec(p);
        if (m) {
            const re = safeIgnoreRegex(m[1], m[2]);
            if (re && boundedRegexTest(re, t)) return true;
        } else if (t.toLowerCase().includes(p.toLowerCase())) {
            return true;
        }
    }
    return false;
}

/**
 * Uncaught page errors and console.error lines for this test emitted since
 * `fromIndex`, minus ignored ones. Warnings never fail a test.
 */
export function blockingConsoleErrors(
    logs: any[], fromIndex: number, testName: string, policy: ConsoleFailurePolicy,
): any[] {
    if (!policy.enabled) return [];
    return logs.slice(fromIndex).filter(l =>
        l && (l.test === undefined || l.test === testName)
        && (l.type === 'error' || l.type === 'pageerror')
        && !matchesIgnore(String(l.text || ''), policy.ignore));
}

/** The error a test fails with when blocking console errors were seen. */
export function consoleFailureMessage(errors: any[], policy?: RedactionPolicy): string {
    const first = errors[0] || {};
    const kind = first.type === 'pageerror' ? 'Uncaught page error' : 'console.error';
    const more = errors.length > 1 ? ` (+${errors.length - 1} more)` : '';
    return redactText(
        `JavaScript error on page (fail_on_console_errors): ${kind}: ${clip(first.text, 500)}${more}`, policy);
}

// --------------------------------------------------------------------------
// DOM excerpt around a failing step's target
// --------------------------------------------------------------------------

/**
 * Sanitised outerHTML around the failing step's target: the target's
 * grandparent when the selector still resolves (`target`), else the start of
 * the body (`page`) — for a selector that no longer matches, what IS there is
 * the useful evidence. Input/textarea/select values, scripts, styles and SVG
 * are removed and `maskSelectors` elements are blanked BEFORE serialising, so
 * typed credentials and masked regions never leave the browser. The caller
 * pattern-redacts the text and caps it again.
 */
export async function captureDomExcerpt(
    page: Page, context: Page | FrameLocator, selector: string | undefined, maskSelectors: string[],
): Promise<{ dom: string; scope: 'target' | 'page' } | null> {
    // Runs in the page. Must be self-contained (it is serialised by Playwright).
    const excerpt = (el: Element, args: { masks: string[]; max: number; isTarget: boolean }) => {
        const MARK = 'data-traceiq-target';
        let root: Element = el;
        if (args.isTarget) {
            el.setAttribute(MARK, '');
            root = el.parentElement?.parentElement || el.parentElement || el;
        }
        const clone = root.cloneNode(true) as Element;
        if (args.isTarget) el.removeAttribute(MARK);
        clone.querySelectorAll('script,style,svg,noscript,template,iframe').forEach(n => n.remove());
        clone.querySelectorAll('input,textarea,select,option').forEach(n => {
            n.removeAttribute('value');
            if (n.tagName === 'TEXTAREA') n.textContent = '';
        });
        for (const m of args.masks) {
            try {
                clone.querySelectorAll(m).forEach(n => { n.innerHTML = '[masked]'; n.removeAttribute('value'); });
            } catch { /* not a CSS selector */ }
        }
        return clone.outerHTML.replace(/\s+/g, ' ').slice(0, args.max);
    };
    const args = { masks: maskSelectors, max: MAX_DOM_EXCERPT_CHARS };
    if (selector) {
        try {
            const dom = await context.locator(selector).first()
                .evaluate(excerpt, { ...args, isTarget: true }, { timeout: 1500 });
            if (dom) return { dom, scope: 'target' };
        } catch { /* target gone: fall back to the page */ }
    }
    try {
        if (page.isClosed()) return null;
        const dom = await page.locator('body').evaluate(excerpt, { ...args, isTarget: false }, { timeout: 1500 });
        return dom ? { dom, scope: 'page' } : null;
    } catch {
        return null;
    }
}

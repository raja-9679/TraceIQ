/**
 * Suite setup/teardown hooks and the cleanup lifecycle.
 *
 * The backend ships each case's hook chain as `test_case.suite_hooks`:
 * outermost suite first, already cut at the first suite with
 * inherit_settings=false. The worker keeps a STACK of entered frames and,
 * before each case, pops the frames the case does not share (teardown,
 * innermost first) and pushes the ones it adds (setup, outermost first).
 *
 * That one rule gives both documented behaviours:
 *   - single-case jobs (SEPARATE / PARALLEL / per-case CONTINUOUS dispatch):
 *     the stack starts empty and is emptied after the case, so the whole
 *     chain's setup runs before the case and its teardown after it;
 *   - multi-case continuous jobs (a sub-suite unit): cases arrive in suite
 *     order, so a suite's setup runs once before its first case and its
 *     teardown once after its last.
 *
 * Teardown ALWAYS runs for every frame that was entered — including the one
 * whose setup failed, so it must tolerate partial setup — and its failures
 * are reported, never turned into a case failure.
 *
 * Cleanups: a step with `params.cleanup` registers a request (usually a
 * DELETE) once it has created something. Case-registered cleanups run right
 * after the case (before suite teardown: a child resource is removed before
 * the parent the setup created); setup-registered ones run when their frame
 * is popped, after its teardown steps. Both LIFO.
 *
 * Kept free of Playwright so the ordering rules are unit-testable: the worker
 * injects `runStep` (TestExecutor.executeStep on the live page) and
 * `request` (the browser context's APIRequestContext).
 */
import { JobAbort } from './run-control';
import { RedactionPolicy, redactText } from './redact';
import { safeUrl } from './evidence';
import { resolveTemplates, TemplateContext, templateContextFrom } from './interpolate';

export interface HookFrame {
    suite_id: number;
    suite_name?: string;
    setup_steps?: any[];
    teardown_steps?: any[];
}

export type StepStatus = 'passed' | 'failed' | 'skipped';

export interface HookStepOutcome {
    index: number;
    type: string;
    status: StepStatus;
    error?: string;
    duration_ms?: number;
}

export interface HookPhaseOutcome {
    suite_id: number;
    suite_name?: string;
    phase: 'setup' | 'teardown';
    status: StepStatus;
    error?: string;
    duration_ms: number;
    steps: HookStepOutcome[];
}

export interface CleanupOutcome {
    method: string;
    /** The authored template (captured values unresolved) or a safeUrl. */
    url: string;
    status: 'ok' | 'failed' | 'skipped';
    http_status?: number;
    error?: string;
    origin: 'setup' | 'case';
    suite_id?: number;
    step_index?: number;
    step_type?: string;
}

export interface HookReport {
    setup: HookPhaseOutcome[];
    teardown: HookPhaseOutcome[];
    cleanup: CleanupOutcome[];
}

export interface CleanupRequest {
    method: string;
    url: string;           // resolved, absolute
    display: string;       // what the report shows
    headers: Record<string, string>;
    body?: any;
    okStatus?: number[];
    skip?: string;         // registered but not runnable (reason)
}

export type StepFn = (step: any, ctx: any) => Promise<any>;
export type RequestFn = (req: { method: string; url: string; headers: Record<string, string>; body?: any }) =>
    Promise<{ status: number }>;

const CLEANUP_METHODS = new Set(['DELETE', 'POST', 'PUT', 'PATCH', 'GET']);
const MAX_ERROR_CHARS = 1000;
export const HOOK_STEP_TIMEOUT_MS = parseInt(process.env.HOOK_STEP_TIMEOUT_MS || '30000', 10);

/** Steps with a cleanup spec on them — `params.cleanup` (true or an object). */
export function hasCleanupSpec(step: any): boolean {
    const spec = step?.params?.cleanup;
    return spec === true || (!!spec && typeof spec === 'object');
}

/** Does a case payload need the hook machinery at all? */
export function caseUsesHooks(testCase: any): boolean {
    const chain: HookFrame[] = Array.isArray(testCase?.suite_hooks) ? testCase.suite_hooks : [];
    if (chain.some(f => (f.setup_steps?.length || 0) + (f.teardown_steps?.length || 0) > 0)) return true;
    return (testCase?.steps || []).some(hasCleanupSpec);
}

export async function withTimeout<T>(p: Promise<T>, ms: number, label: string): Promise<T> {
    let timer: NodeJS.Timeout | undefined;
    const t = new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms);
    });
    try {
        return await Promise.race([p, t]);
    } finally {
        if (timer) clearTimeout(timer);
    }
}

function headerValue(headers: any, name: string): string | undefined {
    if (!headers || typeof headers !== 'object') return undefined;
    for (const [k, v] of Object.entries(headers)) {
        if (k.toLowerCase() === name) return String(v);
    }
    return undefined;
}

function absolutise(url: string, base: string | undefined): string | null {
    try {
        return new URL(url).toString();
    } catch { /* relative */ }
    if (!base) return null;
    try {
        return new URL(url, base).toString();
    } catch {
        return null;
    }
}

function originOf(url: string | undefined | null): string | null {
    if (!url) return null;
    try { return new URL(url).origin; } catch { return null; }
}

/**
 * A cleanup URL that came (wholly or partly) from the site under test — a
 * Location header, an extracted value — must stay on the origin the creating
 * request went to: the DELETE carries the suite's credential headers, so a
 * response pointing elsewhere would hand those credentials (and an SSRF
 * primitive) to whatever host the site names. An author who literally wrote a
 * different origin into the cleanup template meant it.
 */
function offOriginReason(url: string, base: string | undefined, literalTemplate?: string): string | null {
    // Fail closed: with no URL or no known creating origin there is nothing
    // to compare against, so only an origin the author wrote literally passes.
    const target = originOf(url);
    if (!target) return 'cleanup URL is not a valid absolute URL';
    if (literalTemplate && originOf(literalTemplate) === target) return null;
    const expected = originOf(base);
    if (!expected) {
        return `cleanup URL is on ${target} but the creating request's origin is unknown; `
            + 'not sending credentials there (write the full URL in params.cleanup.url)';
    }
    if (target === expected) return null;
    return `cleanup URL is on ${target}, not ${expected} where the resource was created; `
        + 'not sending credentials there (write the full URL in params.cleanup.url if this is intended)';
}

/**
 * The cleanup a step registers, or null when it registers none. `result` is
 * the step's return value or, for a step that threw after the request went
 * out, its `stepResult` — a POST that returned 201 and then failed an
 * assertion still created the resource.
 */
export function buildCleanup(
    step: any, result: any, globalSettings: any, testCaseContext: any, policy?: RedactionPolicy,
): CleanupRequest | null {
    const spec = step?.params?.cleanup;
    if (!hasCleanupSpec(step)) return null;
    const tctx: TemplateContext = templateContextFrom(globalSettings, testCaseContext);
    const requestUrl: string | undefined = result?.request?.url || result?.url;
    const base = requestUrl || globalSettings?.environment?.base_url;
    const suiteHeaders = { ...(globalSettings?.headers || {}) };

    if (spec === true) {
        let raw: string | undefined;
        if (step.type === 'http-request') {
            raw = headerValue(result?.headers, 'location');
            if (!raw) {
                return { method: 'DELETE', url: '', display: safeUrl(requestUrl, policy), headers: {},
                         skip: 'cleanup: true needs a Location header in the response; give params.cleanup a url instead' };
            }
        } else if (step.type === 'extract-value') {
            const v = testCaseContext?.variables?.[step.value];
            if (typeof v === 'string' && (/^https?:\/\//i.test(v) || v.startsWith('/'))) raw = v;
            if (!raw) {
                return { method: 'DELETE', url: '', display: `{{${step.value}}}`, headers: {},
                         skip: 'cleanup: true on extract-value needs the extracted value to be a URL' };
            }
        } else {
            return { method: 'DELETE', url: '', display: step.type, headers: {},
                     skip: `cleanup: true is only understood on http-request and extract-value; use an object with a url` };
        }
        const url = absolutise(raw, base);
        const off = url ? offOriginReason(url, base) : null;
        if (off) return { method: 'DELETE', url: '', display: safeUrl(url, policy), headers: {}, skip: off };
        const stepHeaders = step.type === 'http-request' ? resolveTemplates(step.params?.headers || {}, tctx) : {};
        return {
            method: 'DELETE', url: url || '', display: safeUrl(url || raw, policy),
            headers: { ...suiteHeaders, ...stepHeaders },
            ...(url ? {} : { skip: 'cleanup URL is relative and there is no base URL to resolve it against' }),
        };
    }

    const method = String(spec.method || 'DELETE').toUpperCase();
    const template = String(spec.url || '');
    // The template (captured values unresolved) is what reports show: it
    // identifies the call without echoing ids or tokens extracted at run time.
    const display = redactText(template.slice(0, 300), policy);
    if (!CLEANUP_METHODS.has(method)) {
        return { method, url: '', display, headers: {}, skip: `unsupported cleanup method ${method}` };
    }
    if (!template) return { method, url: '', display: '(no url)', headers: {}, skip: 'cleanup object has no url' };
    const resolved = String(resolveTemplates(template, tctx));
    if (/\{\{/.test(resolved)) {
        return { method, url: '', display, headers: {}, skip: 'cleanup url has an unresolved template' };
    }
    const url = absolutise(resolved, base);
    if (!url) {
        return { method, url: '', display, headers: {},
                 skip: 'cleanup URL is relative and there is no base URL to resolve it against' };
    }
    const off = offOriginReason(url, base, template);
    if (off) return { method, url: '', display, headers: {}, skip: off };
    // Reuse the creating request's headers by default — the DELETE nearly
    // always needs the same Authorization the POST had.
    const stepHeaders = step.type === 'http-request' ? resolveTemplates(step.params?.headers || {}, tctx) : {};
    const okStatus = Array.isArray(spec.expect_status)
        ? spec.expect_status.map((n: any) => parseInt(String(n), 10)).filter((n: number) => Number.isFinite(n))
        : undefined;
    return {
        method, url, display,
        headers: { ...suiteHeaders, ...stepHeaders, ...resolveTemplates(spec.headers || {}, tctx) },
        ...(spec.body !== undefined ? { body: resolveTemplates(spec.body, tctx) } : {}),
        ...(okStatus && okStatus.length ? { okStatus } : {}),
    };
}

/** Did this step (likely) create something worth cleaning up? */
function createdSomething(step: any, result: any, threw: boolean): boolean {
    if (!threw) return true;
    // A request step that failed an assertion after a 2xx still created it.
    const status = result?.status;
    return step?.type !== 'extract-value' && typeof status === 'number' && status >= 200 && status < 300;
}

interface Entered {
    frame: HookFrame;
    ok: boolean;
    error?: string;
    vars: Record<string, any>;
    cleanups: CleanupRequest[];
}

export class HookRunner {
    private stack: Entered[] = [];
    private caseCleanups: CleanupRequest[] = [];
    private pending: HookReport = { setup: [], teardown: [], cleanup: [] };

    constructor(private readonly deps: {
        runStep: StepFn;
        request: RequestFn;
        settings: any;
        /** The current case's runtime context (scope, data, variables…). */
        caseContext: () => any;
        policy?: RedactionPolicy;
        stepTimeoutMs?: number;
    }) {}

    /** Values exported by every successfully set-up frame, inner wins. */
    exports(): Record<string, any> {
        const out: Record<string, any> = {};
        for (const e of this.stack) if (e.ok) Object.assign(out, e.vars);
        return out;
    }

    /** Hook outcomes since the last drain (attached to a result by the worker). */
    drain(): HookReport {
        const out = this.pending;
        this.pending = { setup: [], teardown: [], cleanup: [] };
        return out;
    }

    get depth(): number {
        return this.stack.length;
    }

    /**
     * Make the stack equal `chain`: teardown what the case does not share,
     * set up what it adds. Returns ok=false (with a message for the case
     * result) when the case must not run because a setup failed.
     */
    async enter(chain: HookFrame[] | undefined | null): Promise<{ ok: boolean; error?: string }> {
        const want = Array.isArray(chain) ? chain : [];
        let common = 0;
        while (common < this.stack.length && common < want.length
               && this.stack[common].frame.suite_id === want[common].suite_id) common++;
        await this.popTo(common);
        const failed = this.stack.find(e => !e.ok);
        if (failed) return { ok: false, error: failed.error };
        for (let i = common; i < want.length; i++) {
            const entered: Entered = { frame: want[i], ok: true, vars: {}, cleanups: [] };
            this.stack.push(entered);
            const outcome = await this.runPhase(entered, 'setup');
            if (outcome.status === 'failed') {
                entered.ok = false;
                entered.error = `Suite setup failed (${want[i].suite_name || `suite ${want[i].suite_id}`}): ${outcome.error}`;
                return { ok: false, error: entered.error };
            }
        }
        return { ok: true };
    }

    /** Tear every entered frame down (end of job). */
    async exitAll(): Promise<void> {
        await this.popTo(0);
    }

    /**
     * Run one case step, registering its cleanup when it created something.
     * Errors propagate unchanged — registration never masks a failure.
     */
    async track<T>(step: any, ctx: any, exec: () => Promise<T>): Promise<T> {
        try {
            const result = await exec();
            this.register(this.caseCleanups, step, result, false, ctx);
            return result;
        } catch (err: any) {
            this.register(this.caseCleanups, step, err?.stepResult, true, ctx);
            throw err;
        }
    }

    /** Run the current case's cleanups (LIFO). Call after every case. */
    async finishCase(): Promise<void> {
        const list = this.caseCleanups;
        this.caseCleanups = [];
        await this.runCleanups(list, 'case');
    }

    private register(into: CleanupRequest[], step: any, result: any, threw: boolean, ctx: any): void {
        if (!hasCleanupSpec(step) || !createdSomething(step, result, threw)) return;
        try {
            const req = buildCleanup(step, result, this.deps.settings, ctx, this.deps.policy);
            if (req) into.push(req);
        } catch (e: any) {
            into.push({ method: 'DELETE', url: '', display: step?.type || 'step', headers: {},
                        skip: `could not build cleanup: ${e?.message || e}` });
        }
    }

    private async popTo(depth: number): Promise<void> {
        while (this.stack.length > depth) {
            const entered = this.stack[this.stack.length - 1];
            await this.runPhase(entered, 'teardown');
            await this.runCleanups(entered.cleanups.splice(0), 'setup', entered.frame.suite_id);
            this.stack.pop();
        }
    }

    private phaseContext(entered: Entered, phase: 'setup' | 'teardown'): any {
        const base = this.deps.caseContext() || {};
        if (phase === 'setup') {
            // Bare {{x}} inside a setup refers to this frame's own extracts;
            // outer frames' values are {{setup.x}}.
            return { ...base, variables: entered.vars, setup: this.exports() };
        }
        // Teardown also sees the case's runtime variables (lower precedence)
        // so it can delete what the case created by id.
        return {
            ...base,
            variables: { ...(base.variables || {}), ...entered.vars },
            setup: { ...this.exports(), ...entered.vars },
        };
    }

    private async runPhase(entered: Entered, phase: 'setup' | 'teardown'): Promise<HookPhaseOutcome> {
        const steps: any[] = (phase === 'setup' ? entered.frame.setup_steps : entered.frame.teardown_steps) || [];
        const started = Date.now();
        const outcome: HookPhaseOutcome = {
            suite_id: entered.frame.suite_id,
            ...(entered.frame.suite_name ? { suite_name: entered.frame.suite_name } : {}),
            phase, status: 'passed', duration_ms: 0, steps: [],
        };
        if (!steps.length) return outcome;   // nothing ran: not reported
        const ctx = this.phaseContext(entered, phase);
        const timeout = this.deps.stepTimeoutMs ?? HOOK_STEP_TIMEOUT_MS;
        let stop = false;
        for (let i = 0; i < steps.length; i++) {
            const step = steps[i];
            const type = String(step?.type || 'unknown');
            if (stop) {
                outcome.steps.push({ index: i, type, status: 'skipped' });
                continue;
            }
            const t0 = Date.now();
            try {
                const result = await withTimeout(this.deps.runStep(step, ctx), timeout, `${phase} step ${i + 1} (${type})`);
                if (phase === 'setup') this.register(entered.cleanups, step, result, false, ctx);
                outcome.steps.push({ index: i, type, status: 'passed', duration_ms: Date.now() - t0 });
            } catch (err: any) {
                if (phase === 'setup') this.register(entered.cleanups, step, err?.stepResult, true, ctx);
                const message = this.clean(err?.message || String(err));
                outcome.steps.push({ index: i, type, status: 'failed', error: message, duration_ms: Date.now() - t0 });
                if (outcome.status !== 'failed') {
                    outcome.status = 'failed';
                    outcome.error = `step ${i + 1} (${type}): ${message}`;
                }
                // Setup stops at the first failure; teardown is best effort
                // and keeps going — each step usually removes something else.
                if (phase === 'setup') stop = true;
            }
        }
        outcome.duration_ms = Date.now() - started;
        (phase === 'setup' ? this.pending.setup : this.pending.teardown).push(outcome);
        return outcome;
    }

    private async runCleanups(list: CleanupRequest[], origin: 'setup' | 'case', suiteId?: number): Promise<void> {
        for (let i = list.length - 1; i >= 0; i--) {
            const req = list[i];
            const base: CleanupOutcome = {
                method: req.method, url: req.display, status: 'skipped', origin,
                ...(suiteId !== undefined ? { suite_id: suiteId } : {}),
            };
            if (req.skip) {
                this.pending.cleanup.push({ ...base, error: this.clean(req.skip) });
                continue;
            }
            try {
                const res = await withTimeout(
                    this.deps.request({ method: req.method, url: req.url, headers: req.headers, body: req.body }),
                    this.deps.stepTimeoutMs ?? HOOK_STEP_TIMEOUT_MS, `cleanup ${req.method}`);
                // Default success: any 2xx, or 404/410 — already gone is the
                // outcome a cleanup wants.
                const ok = req.okStatus
                    ? req.okStatus.includes(res.status)
                    : (res.status >= 200 && res.status < 300) || res.status === 404 || res.status === 410;
                this.pending.cleanup.push({
                    ...base, status: ok ? 'ok' : 'failed', http_status: res.status,
                    ...(ok ? {} : { error: `unexpected status ${res.status}` }),
                });
            } catch (err: any) {
                this.pending.cleanup.push({ ...base, status: 'failed', error: this.clean(err?.message || String(err)) });
            }
        }
    }

    private clean(message: string): string {
        return redactText(String(message).slice(0, MAX_ERROR_CHARS), this.deps.policy);
    }
}

/** Warnings a hook report implies (teardown/cleanup failures never fail a case). */
export function hookWarnings(report: HookReport | undefined | null): string[] {
    if (!report) return [];
    const out: string[] = [];
    for (const t of report.teardown || []) {
        if (t.status === 'failed') out.push(`Teardown failed (${t.suite_name || `suite ${t.suite_id}`}): ${t.error}`);
    }
    for (const c of report.cleanup || []) {
        if (c.status === 'failed') out.push(`Cleanup ${c.method} ${c.url} failed: ${c.error}`);
    }
    return out;
}

/** Merge reports when several drains belong to one result. */
export function mergeReports(a: HookReport | undefined, b: HookReport): HookReport {
    if (!a) return b;
    return {
        setup: [...a.setup, ...b.setup],
        teardown: [...a.teardown, ...b.teardown],
        cleanup: [...a.cleanup, ...b.cleanup],
    };
}

export function isEmptyReport(r: HookReport | undefined | null): boolean {
    return !r || (!r.setup.length && !r.teardown.length && !r.cleanup.length);
}

/**
 * Time kept back from MAX_JOB_DURATION_MS for teardown and cleanups. The
 * job-level timeout abandons the job outright (no result, no teardown), so
 * when hooks are in play the case's own steps are raced against an earlier
 * deadline: a hung case then fails normally and teardown still gets to run.
 */
export const HOOK_TEARDOWN_BUDGET_MS = parseInt(process.env.HOOK_TEARDOWN_BUDGET_MS || '120000', 10);

/** Run one case step within the job's remaining hook-aware time budget. */
export async function withinDeadline<T>(deadline: number | undefined, exec: () => Promise<T>): Promise<T> {
    if (deadline === undefined) return exec();
    const remaining = deadline - Date.now();
    if (remaining <= 0) {
        throw new JobAbort('timed_out', 'Case timed out: the job time budget is exhausted (teardown time is reserved)');
    }
    // The platform's limit, not the test's: reported TIMED_OUT (WP15f), so
    // it never counts as a failure of the application.
    let timer: NodeJS.Timeout | undefined;
    const t = new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new JobAbort('timed_out', `Case timed out after ${remaining}ms (job time budget; teardown time is reserved)`)), remaining);
    });
    try {
        return await Promise.race([exec(), t]);
    } finally {
        if (timer) clearTimeout(timer);
    }
}

/** A cleanup requester over a Playwright APIRequestContext. */
export function playwrightRequester(getRequest: () => any): RequestFn {
    return async ({ method, url, headers, body }) => {
        const request = getRequest();
        if (!request) throw new Error('browser context unavailable for cleanup');
        const res = await request.fetch(url, {
            method, headers, ...(body !== undefined ? { data: body } : {}), timeout: 30000,
        });
        return { status: res.status() };
    };
}

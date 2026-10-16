/**
 * Selector heal on the web worker (W5), kept out of worker.ts so it can be
 * tested without a browser or a queue: the worker passes the live page, the
 * job and an LLM hook; this module redacts the DOM, ranks candidates and
 * decides what (if anything) to apply.
 */
import { chooseCandidate, HealCandidate, rankHealCandidates } from './heal-candidates';
import { jobDataPolicy, maskSelectorsFrom, MAX_DOM_SNAPSHOT_CHARS, policyFromDataPolicy, redactDom } from './redact';

/** The slice of a Playwright Page the heal needs (a fake in tests). */
export interface HealPage {
    content(): Promise<string>;
    isClosed(): boolean;
    locator(selector: string): {
        count(): Promise<number>;
        elementHandle(opts?: { timeout?: number }): Promise<any>;
        evaluate(fn: (el: any, arg: any) => any, arg: any, opts?: { timeout?: number }): Promise<any>;
    };
}

/** Asks the LLM for one replacement selector given a (redacted) DOM; '' for none. */
export type LlmHeal = (brokenSelector: string, redactedDom: string) => Promise<string>;

export interface HealSuggestion {
    test_case_id: number;
    step_id: string;
    old_selector: string;
    new_selector: string;
    matches: number;
    intent: string | null;
    candidates: HealCandidate[];
    dom_snapshot: string;
    applied: boolean;
}

/** Redact the live DOM with the job's policy, then rank every candidate against the live page. */
export async function healCandidatesFor(page: HealPage, step: any, job: any, llm: LlmHeal | null) {
    const dataPolicy = jobDataPolicy(job);
    const dom = redactDom(await page.content(), {
        policy: policyFromDataPolicy(dataPolicy),
        maskSelectors: maskSelectorsFrom(dataPolicy),
    });
    const llmSelector = llm ? ((await llm(step.selector, dom)) || '').trim() : '';
    const ranked = await rankHealCandidates({
        html: dom,
        oldSelector: step.selector,
        intent: step.intent,
        llmSelector,
        count: sel => page.locator(sel).count(),
        sameElement: async (a, b) => {
            const other = await page.locator(b).elementHandle({ timeout: 1000 });
            return !!other && await page.locator(a).evaluate((el, o) => el === o, other, { timeout: 1000 });
        },
    });
    return { ranked, dom };
}

/**
 * The suggestion the backend turns into a pending SelectorHealProposal: the
 * chosen candidate plus every ranked candidate and the redacted DOM. Null when
 * no candidate matched anything (nothing worth a reviewer's time).
 */
export function healSuggestion(step: any, testCaseId: number, ranked: HealCandidate[], dom: string,
                               applied: boolean): HealSuggestion | null {
    const top = chooseCandidate(ranked) || ranked.find(c => c.matches > 0);
    if (!top) return null;
    return {
        test_case_id: testCaseId,
        step_id: step.id || '',
        old_selector: step.selector,
        new_selector: top.selector,
        matches: top.matches,
        intent: step.intent || null,
        candidates: ranked,
        dom_snapshot: dom.slice(0, MAX_DOM_SNAPSHOT_CHARS),
        applied,
    };
}

/**
 * Runtime heal: rank, and retry the step with the best candidate that matches
 * exactly one element. `retry` runs the step with a replacement selector and
 * throws if it still fails. All candidates are recorded either way.
 */
export async function runtimeHeal(opts: {
    page: HealPage; step: any; testCaseId?: number; job: any; llm: LlmHeal | null;
    retry: (selector: string) => Promise<void>;
}): Promise<{ recovered: boolean; chosen: HealCandidate | null; suggestion: HealSuggestion | null }> {
    const { ranked, dom } = await healCandidatesFor(opts.page, opts.step, opts.job, opts.llm);
    const chosen = chooseCandidate(ranked);
    const suggest = (applied: boolean) =>
        opts.testCaseId ? healSuggestion(opts.step, opts.testCaseId, ranked, dom, applied) : null;
    if (!chosen) return { recovered: false, chosen: null, suggestion: suggest(false) };
    try {
        await opts.retry(chosen.selector);
    } catch {
        return { recovered: false, chosen, suggestion: suggest(false) };
    }
    return { recovered: true, chosen, suggestion: suggest(true) };
}

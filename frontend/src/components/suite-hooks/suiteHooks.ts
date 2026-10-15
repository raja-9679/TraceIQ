import type { TestStep } from '@/components/test-builder/StepComponent';

// Suite setup/teardown hooks + data seed, stored in TestSuite.settings.
// Semantics: backend/app/services/suite_hooks.py and
// execution-engine/src/core/suite-hooks.ts.

export type HookKey = 'setup_steps' | 'teardown_steps';

export interface SuiteHookSettings {
    setup_steps?: TestStep[];
    teardown_steps?: TestStep[];
    data_seed?: string;
    [key: string]: unknown;
}

export function hookStepsOf(settings: SuiteHookSettings | null | undefined, key: HookKey): TestStep[] {
    const raw = settings?.[key];
    return Array.isArray(raw) ? raw.filter((s): s is TestStep => !!s && typeof s === 'object') : [];
}

export function newHookStep(): TestStep {
    return {
        id: typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `step-${Date.now()}`,
        type: 'http-request',
        value: '',
        params: { method: 'POST' },
    };
}

/** The settings blob to save: empty hook lists and a blank seed are removed
 *  (absent = inherit/none), every other key is kept as it was. */
export function withHooks(
    settings: SuiteHookSettings | null | undefined,
    setup: TestStep[],
    teardown: TestStep[],
    seed: string,
): SuiteHookSettings {
    const next: SuiteHookSettings = { ...(settings || {}) };
    if (setup.length) next.setup_steps = setup; else delete next.setup_steps;
    if (teardown.length) next.teardown_steps = teardown; else delete next.teardown_steps;
    const trimmed = seed.trim();
    if (trimmed) next.data_seed = trimmed; else delete next.data_seed;
    return next;
}

/** Index of the first step with no action chosen, or -1. */
export function firstUnchosen(steps: TestStep[]): number {
    return steps.findIndex((s) => !s.type);
}

export function moveItem<T>(list: T[], index: number, direction: 'up' | 'down'): T[] {
    const target = direction === 'up' ? index - 1 : index + 1;
    if (target < 0 || target >= list.length) return list;
    const next = [...list];
    [next[index], next[target]] = [next[target], next[index]];
    return next;
}

// ---- run page -------------------------------------------------------------

export interface HookStepOutcome { index: number; type: string; status: string; error?: string; duration_ms?: number }
export interface HookPhase {
    suite_id: number; suite_name?: string; phase: 'setup' | 'teardown'; status: string;
    error?: string; duration_ms?: number; steps: HookStepOutcome[];
}
export interface CleanupOutcome {
    method: string; url: string; status: string; http_status?: number; error?: string;
    origin: 'setup' | 'case'; suite_id?: number;
}
export interface HookOutcomes {
    setup: HookPhase[];
    teardown: HookPhase[];
    cleanup: CleanupOutcome[];
    warnings: string[];
}

type Payload = Record<string, unknown> | null | undefined;

export function hookOutcomesOf(payload: Payload): HookOutcomes | null {
    if (!payload || typeof payload !== 'object') return null;
    const hooks = (payload.hooks || {}) as { setup?: HookPhase[]; teardown?: HookPhase[] };
    const out: HookOutcomes = {
        setup: Array.isArray(hooks.setup) ? hooks.setup : [],
        teardown: Array.isArray(hooks.teardown) ? hooks.teardown : [],
        cleanup: Array.isArray(payload.cleanup) ? (payload.cleanup as CleanupOutcome[]) : [],
        warnings: Array.isArray(payload.hook_warnings) ? (payload.hook_warnings as string[]) : [],
    };
    return out.setup.length || out.teardown.length || out.cleanup.length ? out : null;
}

/** Results whose teardown/cleanup reported a problem (run-level banner). */
export function resultsWithHookWarnings(results: Array<{ result_payload?: Payload }> | undefined): number {
    return (results || []).filter((r) => (hookOutcomesOf(r.result_payload)?.warnings.length || 0) > 0).length;
}

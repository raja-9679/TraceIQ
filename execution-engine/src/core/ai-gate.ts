// Per-run AI gate (WP14b, decision W2).
//
// The backend resolves the `ai.use` policy at dispatch — the workspace AI
// opt-out, the plan's monthly token quota and the agent session's token
// budget — and puts the answer on the job as `settings.ai_allowed`. A worker
// never decides this itself. While a job of a run with `ai_allowed: false`
// is executing, every LLM call made for that run (selector heal, runtime heal,
// mobile heal, failure analysis) is skipped: `provider.complete` checks the
// gate before reaching any SDK, so a code path added later cannot forget.
//
// Absent flag (an older backend) = allowed, i.e. exactly the old behaviour.

const blockedRuns = new Map<number, number>();   // runId -> active jobs holding the block

/** What the job says. Only an explicit `false` blocks. */
export function jobAllowsAi(settings: any): boolean {
    return !(settings && settings.ai_allowed === false);
}

/** May an LLM be called on behalf of this run right now? */
export function aiAllowedForRun(runId?: number | null): boolean {
    if (runId === undefined || runId === null) return true;
    return !blockedRuns.has(runId);
}

/**
 * Run `fn` with the job's AI gate applied to its run. Reference-counted so
 * two concurrent jobs of one run cannot lift each other's block early.
 */
export async function withAiGate<T>(runId: number | undefined, settings: any, fn: () => Promise<T>): Promise<T> {
    if (runId === undefined || runId === null || jobAllowsAi(settings)) return fn();
    blockedRuns.set(runId, (blockedRuns.get(runId) ?? 0) + 1);
    console.log(`[AI] Run ${runId}: AI is not allowed for this run (workspace policy); LLM calls skipped`);
    try {
        return await fn();
    } finally {
        const left = (blockedRuns.get(runId) ?? 1) - 1;
        if (left <= 0) blockedRuns.delete(runId);
        else blockedRuns.set(runId, left);
    }
}

/** Test hook. */
export function _resetAiGate(): void {
    blockedRuns.clear();
}

/**
 * Evidence a worker attaches to each test result for backend failure
 * analysis and proactive selector heal. Pure helpers, kept out of worker.ts
 * (which starts a worker on import) so they can be unit-tested.
 */
import { RedactionPolicy, redactText } from './redact';

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
        .filter(l => l.test === testName && (l.type === 'error' || l.type === 'warning'))
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

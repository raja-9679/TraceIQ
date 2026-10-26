/**
 * `run-test` steps ("Run another test"). The backend replaces each one with
 * the called case's steps at dispatch (app/services/case_calls.py), so a
 * worker only ever sees one when the call could not be expanded — missing
 * case, loop, wrong kind of worker — and then it must FAIL the case with the
 * backend's reason, never skip it.
 *
 * Inlined steps carry `from` (the test they came from); it is copied into
 * step evidence so the run page can group them.
 */

export interface StepOrigin {
    case_id: number;
    case_name: string;
    call_id: string;
    depth: number;
}

export function runTestStepError(step: any): Error {
    const reason = typeof step?.params?.error === 'string' && step.params.error
        ? step.params.error
        : 'this step was not expanded by the backend (is the backend older than the worker?)';
    return new Error(`Run Test: ${reason}`);
}

/** The step's `from`, shape-checked; undefined when absent or malformed. */
export function stepOrigin(step: any): StepOrigin | undefined {
    const f = step?.from;
    if (!f || typeof f !== 'object' || !Number.isInteger(f.case_id) || f.case_id <= 0) return undefined;
    return {
        case_id: f.case_id,
        case_name: String(f.case_name ?? `#${f.case_id}`).slice(0, 200),
        call_id: String(f.call_id ?? '').slice(0, 100),
        depth: Number.isInteger(f.depth) && f.depth > 0 && f.depth <= 10 ? f.depth : 1,
    };
}

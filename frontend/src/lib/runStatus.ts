// Run / result statuses (backend app/services/run_status.py, WP15f).
//
// Two questions with different answers:
//   - is it over?           TERMINAL  — stop polling, show the final state
//   - did it give a verdict? VERDICT  — counts in pass rates
// `cancelled` and `timed_out` are over but carry no verdict: a run someone
// stopped, or one that hit the platform's time limit, says nothing about the
// application, so they never count as a pass or a failure.

export type RunStatus = 'pending' | 'running' | 'passed' | 'failed' | 'error' | 'cancelled' | 'timed_out';

export type ErrorKind = 'test' | 'setup' | 'infrastructure' | 'timeout' | 'cancelled' | 'not_executed';

const norm = (s: string | null | undefined) => (s ?? '').toLowerCase();

export const LIVE = new Set<string>(['pending', 'running']);
export const VERDICT = new Set<string>(['passed', 'failed', 'error']);
export const NO_VERDICT = new Set<string>(['cancelled', 'timed_out']);
export const FAILING = new Set<string>(['failed', 'error']);

export const isLive = (s: string | null | undefined) => LIVE.has(norm(s));
export const isTerminal = (s: string | null | undefined) => VERDICT.has(norm(s)) || NO_VERDICT.has(norm(s));
export const hasVerdict = (s: string | null | undefined) => VERDICT.has(norm(s));
export const isNoVerdict = (s: string | null | undefined) => NO_VERDICT.has(norm(s));
export const isFailing = (s: string | null | undefined) => FAILING.has(norm(s));

export const STATUS_LABEL: Record<RunStatus, string> = {
    pending: 'Queued',
    running: 'Running',
    passed: 'Passed',
    failed: 'Failed',
    error: 'Error',
    cancelled: 'Cancelled',
    timed_out: 'Timed out',
};

export const statusLabel = (s: string | null | undefined): string =>
    STATUS_LABEL[norm(s) as RunStatus] ?? (s || 'Unknown');

// What each error kind means, in the words a reader needs to decide whose
// problem it is.
export const ERROR_KIND_LABEL: Record<ErrorKind, { label: string; hint: string }> = {
    test: { label: 'Test failure', hint: 'A step or assertion failed against the application.' },
    setup: { label: 'Setup failed', hint: 'Suite setup failed, so the case never ran.' },
    infrastructure: { label: 'Infrastructure', hint: 'The browser or worker failed — not a verdict on the application.' },
    timeout: { label: 'Time limit', hint: "The platform's time limit stopped it — not a verdict on the application." },
    cancelled: { label: 'Cancelled', hint: 'Someone cancelled the run before this case finished.' },
    not_executed: { label: 'Not executed', hint: 'This case is not run by workers (for example a manual case).' },
};

export const errorKindLook = (k: string | null | undefined) =>
    ERROR_KIND_LABEL[norm(k) as ErrorKind] ?? null;

/** Pass rate over runs that produced a verdict; null when none did. */
export function passRate(statuses: (string | null | undefined)[]): { rate: number | null; passed: number; counted: number; ignored: number } {
    const counted = statuses.filter(hasVerdict);
    const passed = counted.filter((s) => norm(s) === 'passed').length;
    return {
        rate: counted.length ? Math.round((passed / counted.length) * 100) : null,
        passed,
        counted: counted.length,
        ignored: statuses.filter(isNoVerdict).length,
    };
}

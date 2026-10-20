/**
 * Stopping a job part-way (WP15f): cancellation and the platform time limit.
 *
 * Neither can interrupt Playwright mid-call, so both work at STEP
 * boundaries: before every step the executor calls `check(job)`, which throws
 * a `JobAbort` if the run was cancelled or this job ran out of time. The
 * existing finally blocks then run as for any failure — suite teardown and
 * cleanups included (WP5 contract), browser closed, artifacts uploaded.
 *
 * Cancellation is a Redis key the backend sets (`run:cancelled:<run id>`,
 * POST /api/runs/{id}/cancel). The worker only READS it, at most once per
 * CANCEL_CACHE_MS per run, so a 200-step case costs a handful of GETs.
 *
 * Results are reported with status `cancelled` / `timed_out` and an
 * `error_kind`; an older backend maps unknown statuses to `error`, which is
 * what these jobs were before.
 */

export type ErrorKind = 'test' | 'setup' | 'infrastructure' | 'timeout' | 'cancelled' | 'not_executed';
export type AbortStatus = 'cancelled' | 'timed_out';

export const CANCEL_KEY_PREFIX = 'run:cancelled:';
const CANCEL_CACHE_MS = 1000;

export function cancelKey(runId: number | string): string {
    return `${CANCEL_KEY_PREFIX}${runId}`;
}

export class JobAbort extends Error {
    readonly status: AbortStatus;
    readonly errorKind: ErrorKind;
    constructor(status: AbortStatus, message: string) {
        super(message);
        this.name = 'JobAbort';
        this.status = status;
        this.errorKind = status === 'cancelled' ? 'cancelled' : 'timeout';
    }
}

/** Thrown by the job wall-clock race in the worker loop. */
export class JobTimeout extends Error {
    constructor(message: string) {
        super(message);
        this.name = 'JobTimeout';
    }
}

export function isJobAbort(err: unknown): err is JobAbort {
    return err instanceof JobAbort;
}

export const CANCELLED_MESSAGE = 'Run cancelled — remaining steps were not executed.';

export function timeoutMessage(limitMs: number): string {
    return `Job exceeded MAX_JOB_DURATION_MS (${limitMs}ms) — the platform's time limit, not a test failure.`;
}

type Getter = (key: string) => Promise<string | null>;

export class RunControl {
    private readonly get: Getter;
    private readonly now: () => number;
    private readonly timedOut = new Set<string>();
    private readonly cache = new Map<string, { at: number; cancelled: boolean }>();

    constructor(get: Getter, now: () => number = Date.now) {
        this.get = get;
        this.now = now;
    }

    /** The worker loop gave up on this job; its next step boundary stops it. */
    markTimedOut(jobId: string): void {
        this.timedOut.add(jobId);
    }

    /** Forget a job once it has fully wound down. */
    release(jobId: string): void {
        this.timedOut.delete(jobId);
    }

    async isRunCancelled(runId: number | string): Promise<boolean> {
        const key = String(runId);
        const hit = this.cache.get(key);
        if (hit && this.now() - hit.at < CANCEL_CACHE_MS) return hit.cancelled;
        let cancelled = false;
        try {
            cancelled = !!(await this.get(cancelKey(runId)));
        } catch {
            // Redis hiccup: keep running. A missed cancel is caught at the
            // next step; a false cancel would throw away a healthy run.
            cancelled = hit?.cancelled ?? false;
        }
        // Once cancelled, always cancelled — no need to ask again.
        this.cache.set(key, { at: cancelled ? Number.POSITIVE_INFINITY : this.now(), cancelled });
        if (this.cache.size > 1000) this.cache.delete(this.cache.keys().next().value as string);
        return cancelled;
    }

    /** Throws JobAbort when the job must stop before its next step. */
    async check(job: { job_id: string; run_id: number }): Promise<void> {
        if (this.timedOut.has(job.job_id)) {
            throw new JobAbort('timed_out', 'Stopped: the job hit the platform time limit.');
        }
        if (await this.isRunCancelled(job.run_id)) {
            throw new JobAbort('cancelled', CANCELLED_MESSAGE);
        }
    }
}

/**
 * The result reported for a job that never got to run its cases, or whose
 * results were lost to the time limit: one result for a single-test job, one
 * per case for a continuous job (so run progress still completes).
 */
export function abortedJobResult(job: any, status: AbortStatus, message: string, durationMs = 0): any {
    const errorKind: ErrorKind = status === 'cancelled' ? 'cancelled' : 'timeout';
    const base = {
        job_id: job.job_id,
        run_id: job.run_id,
        status,
        error_kind: errorKind,
        duration_ms: durationMs,
        error: message,
        artifacts: { screenshots: [] },
        network_events: [],
        completed_at: new Date().toISOString(),
    };
    const cases: any[] = Array.isArray(job.test_cases) && job.test_cases.length ? job.test_cases : [];
    if (cases.length) {
        return {
            ...base,
            test_results: cases.map((tc: any) => ({
                test_case_id: tc.id,
                test_name: tc.name,
                status,
                error_kind: errorKind,
                duration_ms: 0,
                error: message,
            })),
        };
    }
    return {
        ...base,
        test_case_id: job.test_case_id ?? job.test_case?.id,
        test_name: job.test_case?.name,
    };
}

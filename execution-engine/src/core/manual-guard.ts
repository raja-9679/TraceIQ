/**
 * Manual test cases (executor=manual, `manual-step` steps) are executed by a
 * person from a manual run in TraceIQ. The backend never dispatches them
 * (app/services/manual_testing.py exclude_manual); this is the belt to that
 * brace for a job that reaches a worker anyway — a replayed dead letter, an
 * older backend. Without it the case would launch a browser and die on
 * "Unknown step type manual-step", which reads as a product bug. Instead it
 * is reported as not executed, with no browser started.
 */
import type { JobResult, TestCase, TestCaseResult, TestJob } from './job-queue';

export const MANUAL_CASE_ERROR =
    'Manual test case — a person runs it from a manual run in TraceIQ; workers never execute it. ' +
    'Nothing was executed.';

export function isManualCase(tc: Partial<TestCase> | null | undefined): boolean {
    if (!tc) return false;
    if ((tc as any).executor === 'manual') return true;
    return Array.isArray(tc.steps) && tc.steps.some((s: any) => s?.type === 'manual-step');
}

export function manualCaseResult(tc: Partial<TestCase>): TestCaseResult {
    return {
        test_case_id: tc.id as number,
        test_name: tc.name || 'manual case',
        status: 'error',
        duration_ms: 0,
        error: MANUAL_CASE_ERROR,
    };
}

/** Result for a single-case job carrying a manual case. */
export function manualJobResult(job: TestJob): JobResult {
    return {
        job_id: job.job_id,
        run_id: job.run_id,
        test_case_id: job.test_case_id,
        test_name: job.test_case?.name,
        status: 'error',
        duration_ms: 0,
        error: MANUAL_CASE_ERROR,
        artifacts: { screenshots: [] },
        network_events: [],
        completed_at: new Date().toISOString(),
    };
}

/** Split a continuous job's cases into the ones a worker can run and the
 * results to report for the manual ones. */
export function splitManualCases(cases: TestCase[]): { automated: TestCase[]; manual: TestCaseResult[] } {
    const automated: TestCase[] = [];
    const manual: TestCaseResult[] = [];
    for (const tc of cases || []) {
        if (isManualCase(tc)) manual.push(manualCaseResult(tc));
        else automated.push(tc);
    }
    return { automated, manual };
}

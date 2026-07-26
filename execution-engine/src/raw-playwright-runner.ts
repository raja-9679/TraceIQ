/**
 * Raw Playwright executor (PLATFORM_VISION.md §4, item 5).
 *
 * Runs an uploaded Playwright spec verbatim via `playwright test --reporter=json`
 * (the @playwright/test runner) instead of the TraceIQ step interpreter, and
 * maps the JSON report to TraceIQ results at spec/test granularity.
 *
 * SECURITY: this executes arbitrary user-supplied code. It must only run on a
 * sandboxed, network-restricted worker image and is gated by the
 * RAW_PLAYWRIGHT_ENABLED env var (checked by the caller). Requires
 * @playwright/test + installed browsers in the image.
 */
import { execFile } from 'child_process';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

export type RawStatus = 'passed' | 'failed' | 'error';

export interface RawTestResult {
    title: string;
    status: RawStatus;
    duration_ms: number;
    error?: string;
}

export interface RawPlaywrightResult {
    status: RawStatus;
    duration_ms: number;
    error?: string;
    tests: RawTestResult[];
}

/** Recursively collect every spec across the (possibly nested) suite tree. */
function collectSpecs(node: any, out: RawTestResult[]): void {
    if (!node) return;
    for (const spec of node.specs || []) {
        const firstTest = (spec.tests || [])[0];
        const firstResult = firstTest ? (firstTest.results || [])[0] : undefined;
        const rawStatus = firstResult?.status; // passed|failed|timedOut|skipped|interrupted
        let status: RawStatus = spec.ok ? 'passed' : 'failed';
        if (!spec.ok && (rawStatus === 'timedOut' || rawStatus === 'interrupted')) {
            status = 'error';
        }
        out.push({
            title: spec.title || '(unnamed test)',
            status,
            duration_ms: Math.round(firstResult?.duration || 0),
            error: firstResult?.error?.message || undefined,
        });
    }
    for (const child of node.suites || []) collectSpecs(child, out);
}

/** Parse Playwright's JSON reporter output into a TraceIQ-shaped result. */
export function parsePlaywrightJson(raw: string): RawPlaywrightResult {
    let report: any;
    try {
        report = JSON.parse(raw);
    } catch (e: any) {
        return { status: 'error', duration_ms: 0, tests: [], error: `Could not parse Playwright JSON report: ${e.message}` };
    }
    const tests: RawTestResult[] = [];
    for (const suite of report.suites || []) collectSpecs(suite, tests);

    // Config-level errors (e.g. syntax error in the spec) surface in `errors`.
    const configErrors: string[] = (report.errors || [])
        .map((e: any) => e?.message || String(e))
        .filter(Boolean);

    const durationTotal = tests.reduce((a, t) => a + t.duration_ms, 0);

    if (tests.length === 0) {
        // No tests ran — treat config errors as a hard error, else empty pass.
        if (configErrors.length) {
            return { status: 'error', duration_ms: durationTotal, tests: [], error: configErrors.join('\n') };
        }
        return { status: 'error', duration_ms: durationTotal, tests: [], error: 'No tests were discovered in the spec.' };
    }

    const anyError = tests.some(t => t.status === 'error') || configErrors.length > 0;
    const anyFailed = tests.some(t => t.status === 'failed');
    const status: RawStatus = anyError ? 'error' : (anyFailed ? 'failed' : 'passed');
    const firstProblem = tests.find(t => t.status !== 'passed');
    return {
        status,
        duration_ms: durationTotal,
        tests,
        error: configErrors.join('\n') || firstProblem?.error || undefined,
    };
}

interface RunOptions {
    baseUrl?: string;
    timeoutMs?: number;
}

/**
 * Write the spec to an isolated temp dir and run it via `playwright test`.
 * Resolves even when tests fail (exit code 1) — only infrastructure failures
 * (runner missing, spawn error) produce an `error` status without a report.
 */
export function runRawPlaywright(script: string, opts: RunOptions = {}): Promise<RawPlaywrightResult> {
    return new Promise((resolve) => {
        let workDir: string;
        try {
            workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'tiq-raw-pw-'));
        } catch (e: any) {
            resolve({ status: 'error', duration_ms: 0, tests: [], error: `Could not create work dir: ${e.message}` });
            return;
        }
        const specPath = path.join(workDir, 'imported.spec.ts');
        try {
            fs.writeFileSync(specPath, script, 'utf8');
        } catch (e: any) {
            resolve({ status: 'error', duration_ms: 0, tests: [], error: `Could not write spec: ${e.message}` });
            return;
        }

        const env = { ...process.env };
        if (opts.baseUrl) env.PLAYWRIGHT_TEST_BASE_URL = opts.baseUrl;
        // Force JSON to stdout regardless of any inherited config.
        delete env.PLAYWRIGHT_JSON_OUTPUT_NAME;

        const args = ['playwright', 'test', 'imported.spec.ts', '--reporter=json', '--workers=1'];
        const child = execFile('npx', args, {
            cwd: workDir,
            env,
            timeout: opts.timeoutMs || 300000,
            maxBuffer: 64 * 1024 * 1024,
        }, (err, stdout, stderr) => {
            // Cleanup temp dir best-effort.
            try { fs.rmSync(workDir, { recursive: true, force: true }); } catch { /* ignore */ }

            const out = (stdout || '').trim();
            if (out.startsWith('{')) {
                resolve(parsePlaywrightJson(out));
                return;
            }
            // No JSON on stdout → the runner itself failed to start.
            const detail = (stderr || '').trim() || (err && err.message) || 'unknown error';
            resolve({ status: 'error', duration_ms: 0, tests: [], error: `Playwright runner failed: ${detail.slice(0, 2000)}` });
        });
        // Guard against execFile never invoking the callback (it always does,
        // but keep the child reference to avoid GC surprises).
        void child;
    });
}

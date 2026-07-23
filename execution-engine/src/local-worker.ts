// TraceIQ local worker — the localhost-testing bridge.
//
// Runs on a developer's machine next to their dev server, so runs created
// with `local_worker_id` can exercise http://localhost:* — something the
// server-side workers can never reach. Talks ONLY to the public REST API:
//
//   TRACEIQ_URL=https://traceiq.example.com \
//   TRACEIQ_API_KEY=tiq_... \
//   TRACEIQ_WORKER_ID=my-laptop \
//   npm run worker:local
//
// Trigger runs against it:
//   POST /api/runs?suite_id=N  body: {"local_worker_id": "my-laptop"}
//
// Reuses the full TestExecutor step engine (same behavior as server
// workers). v1 limits: single-test jobs only (use SEPARATE/PARALLEL mode;
// continuous sub-suite jobs are reported as errors), artifacts stay local
// (no MinIO from outside the cluster), results feed the identical
// aggregation pipeline via POST /api/jobs/result.

import { chromium, firefox, webkit, Browser, Page, FrameLocator } from 'playwright';
import { TestExecutor } from './core/test-executor';
import { collectWebVitals } from './web-vitals';

const BASE_URL = (process.env.TRACEIQ_URL || process.env.BACKEND_URL || 'http://localhost:8000').replace(/\/$/, '');
const API_KEY = process.env.TRACEIQ_API_KEY || '';
const WORKER_ID = process.env.TRACEIQ_WORKER_ID || 'local';
const POLL_INTERVAL_MS = parseInt(process.env.TRACEIQ_POLL_INTERVAL_MS || '2000', 10);

if (!API_KEY) {
    console.error('[LocalWorker] TRACEIQ_API_KEY is required (mint one at /api-keys)');
    process.exit(1);
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function api(path: string, init: RequestInit = {}): Promise<globalThis.Response> {
    return fetch(`${BASE_URL}/api${path}`, {
        ...init,
        headers: {
            'X-API-Key': API_KEY,
            'Content-Type': 'application/json',
            ...(init.headers || {}),
        },
    });
}

async function pollJob(): Promise<any | null> {
    const res = await api(`/jobs/poll?worker_id=${encodeURIComponent(WORKER_ID)}`);
    if (res.status === 204) return null;
    if (!res.ok) throw new Error(`poll returned ${res.status}: ${(await res.text()).slice(0, 200)}`);
    return res.json();
}

async function submitResult(result: any): Promise<void> {
    const res = await api('/jobs/result', { method: 'POST', body: JSON.stringify(result) });
    if (!res.ok) {
        console.error(`[LocalWorker] result submit failed (${res.status}): ${(await res.text()).slice(0, 200)}`);
    }
}

function browserFor(name: string) {
    if (name === 'firefox') return firefox;
    if (name === 'webkit') return webkit;
    return chromium;
}

async function executeJob(job: any): Promise<any> {
    const started = Date.now();
    const base = {
        job_id: job.job_id,
        run_id: job.run_id,
        test_case_id: job.test_case_id,
        test_name: job.test_case?.name,
        artifacts: { screenshots: [] },
        network_events: [],
        completed_at: new Date().toISOString(),
    };

    if (job.execution_mode === 'continuous' || job.test_cases) {
        return { ...base, status: 'error', duration_ms: 0,
            test_name: job.unit_name || 'continuous job',
            error: 'Local worker v1 runs single-test jobs only — use SEPARATE or PARALLEL execution mode.' };
    }
    const tc = job.test_case;
    if (!tc?.steps) {
        return { ...base, status: 'error', duration_ms: 0, error: 'Job has no test_case/steps.' };
    }

    let browser: Browser | null = null;
    let status: 'passed' | 'failed' = 'passed';
    let errorMessage: string | undefined;
    let responseData: any = undefined;
    let lastStepResult: any = null;
    let webVitals: any = null;

    try {
        browser = await browserFor(job.browser || 'chromium').launch({ headless: process.env.TRACEIQ_HEADED !== 'true' });
        const context = await browser.newContext(
            job.settings?.storage_state && tc.use_auth_session !== false && !tc.is_auth_setup
                ? { storageState: job.settings.storage_state } : {});
        let page: Page = await context.newPage();
        page.setDefaultTimeout(parseInt(process.env.DEFAULT_TIMEOUT || '30000', 10));

        const contextData = {
            id: tc.id, name: tc.name,
            browser: job.browser || 'chromium', device: job.device || null,
            variables: {}, data: tc.data_row || {},
        };

        let currentContext: Page | FrameLocator = page;
        for (const step of tc.steps) {
            if (step.type === 'switch-frame') {
                const sel = step.selector || step.value;
                currentContext = (sel === 'main' || sel === 'top') ? page
                    : sel ? currentContext.frameLocator(sel) : currentContext;
                continue;
            }
            const stepResponse = await TestExecutor.executeStep(
                page, currentContext, step, job.settings || {}, contextData);
            if (stepResponse?.__switchToPage) {
                page = stepResponse.__switchToPage;
                currentContext = page;
            } else if (stepResponse && ['http-request', 'graphql', 'feed-check', 'amp-validate'].includes(step.type)) {
                lastStepResult = stepResponse;
            }
        }
        if (lastStepResult) {
            responseData = {
                status: lastStepResult.status, headers: lastStepResult.headers,
                body: lastStepResult.body, request: lastStepResult.request,
            };
        }
        if (!page.isClosed()) webVitals = await collectWebVitals(page);
    } catch (err: any) {
        status = 'failed';
        errorMessage = err.message;
        if (err.stepResult) {
            responseData = {
                status: err.stepResult.status, headers: err.stepResult.headers,
                body: err.stepResult.body, request: err.stepResult.request,
            };
        }
    } finally {
        try { await browser?.close(); } catch { /* ignore */ }
    }

    return {
        ...base,
        status,
        duration_ms: Date.now() - started,
        error: errorMessage,
        response_data: responseData,
        ...(webVitals ? { web_vitals: webVitals } : {}),
    };
}

async function main() {
    console.log(`[LocalWorker] "${WORKER_ID}" polling ${BASE_URL} every ${POLL_INTERVAL_MS}ms`);
    console.log('[LocalWorker] Trigger runs with body {"local_worker_id": "' + WORKER_ID + '"} on POST /api/runs');
    let failures = 0;
    for (;;) {
        try {
            const job = await pollJob();
            failures = 0;
            if (!job) { await sleep(POLL_INTERVAL_MS); continue; }
            console.log(`[LocalWorker] job ${job.job_id} (run ${job.run_id}): ${job.test_case?.name || job.unit_name}`);
            const result = await executeJob(job);
            await submitResult(result);
            console.log(`[LocalWorker] job ${job.job_id} → ${result.status}${result.error ? `: ${result.error.slice(0, 120)}` : ''}`);
        } catch (err: any) {
            failures++;
            console.error(`[LocalWorker] ${err.message || err}`);
            // Back off on repeated failures (server down, bad key) instead of hammering.
            await sleep(Math.min(POLL_INTERVAL_MS * 2 ** Math.min(failures, 5), 60_000));
        }
    }
}

main();

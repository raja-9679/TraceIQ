/**
 * Execution Worker - Processes test jobs with complete isolation
 * 
 * This worker supports two modes:
 * 1. Single test case jobs (original SEPARATE mode)
 * 2. Multi-test continuous jobs (hybrid mode - sub-suite execution)
 * 
 * For single test jobs:
 *   - Launches isolated browser context
 *   - Executes single test case
 *   - Uploads artifacts and publishes result
 * 
 * For continuous jobs:
 *   - Launches shared browser context
 *   - Executes multiple tests sequentially
 *   - Uploads combined artifacts and publishes individual results
 */

import { Browser, BrowserContext, Page, FrameLocator, devices } from 'playwright';
import * as fs from 'fs';
import * as path from 'path';
import { JobQueue, TestJob, JobResult, TestCase, TestCaseResult, getJobQueue } from './core/job-queue';
import { BrowserManager } from './core/browser-manager';
import { NetworkInterceptor } from './core/network-interceptor';
import { TestExecutor } from './core/test-executor';
import { AIEngine } from './ai';
import { provider as llmProvider } from './llm-provider';
import { collectWebVitals, WebVitals } from './web-vitals';
import {
    ArtifactStore, CaptureLevel, PutResult, artifactKeys, normalizeCaptureLevel,
} from './core/artifact-store';

// Errors that look like a selector no longer matching the page — the only
// failures worth an LLM heal attempt.
const SELECTOR_FAILURE_RE = /waiting for locator|not found|no element|Timeout \d+ms exceeded|failed to find element|strict mode violation/i;


// Configuration
const ARTIFACTS_BASE_DIR = process.env.ARTIFACTS_DIR || '/tmp/artifacts';
const IDLE_TIMEOUT_MS = parseInt(process.env.WORKER_IDLE_TIMEOUT || '60000');
const MAX_JOBS_BEFORE_RESTART = parseInt(process.env.MAX_JOBS_BEFORE_RESTART || '50');
// Maximum wall-clock time a single job may run before it is aborted (default 10 min)
const MAX_JOB_DURATION_MS = parseInt(process.env.MAX_JOB_DURATION_MS || '600000');
const MAX_CONSOLE_LOG_ENTRIES = parseInt(process.env.MAX_CONSOLE_LOG_ENTRIES || '5000');

class ExecutionWorker {
    private jobQueue: JobQueue;
    private browserManager: BrowserManager;
    private isShuttingDown: boolean = false;
    private jobsProcessed: number = 0;
    private idleTimer: NodeJS.Timeout | null = null;
    private aiEngine: AIEngine = new AIEngine();

    /**
     * Reactive selector heal: on a selector-looking failure, ask the LLM for
     * a replacement selector against the live DOM and record a suggestion.
     * The backend persists it as a pending SelectorHealProposal — the test
     * itself is never mutated here.
     */
    private async maybeProposeHeal(
        page: Page | null,
        step: any,
        testCaseId: number | undefined,
        runId: number,
        errorMessage: string,
        sink: any[]
    ): Promise<void> {
        try {
            if (llmProvider.name === 'null') return;
            if (!step?.selector || !testCaseId) return;
            if (!SELECTOR_FAILURE_RE.test(errorMessage || '')) return;
            if (!page || page.isClosed()) return;

            const dom = await page.content();
            const healed = (await this.aiEngine.healSelector(step.selector, dom, runId) || '').trim();
            if (!healed || healed === step.selector) return;

            const matches = await page.locator(healed).count().catch(() => 0);
            sink.push({
                test_case_id: testCaseId,
                step_id: step.id || '',
                old_selector: step.selector,
                new_selector: healed,
                matches,
                intent: step.intent || null,
            });
            console.log(`[Worker] Heal suggestion: "${step.selector}" -> "${healed}" (${matches} match(es) in current DOM)`);
        } catch (healErr: any) {
            console.warn(`[Worker] Heal attempt failed: ${healErr.message}`);
        }
    }

    /**
     * Runtime self-heal (opt-in via RUNTIME_HEAL_ENABLED): on a selector-looking
     * failure, ask the LLM for a replacement selector and, when it UNIQUELY
     * matches the live DOM, retry the step with it so the run recovers instead
     * of failing on a brittle selector. Always records the old→new suggestion
     * (via `sink`) so the fix can be made durable by a human. Returns true iff
     * the step passed on retry.
     */
    private async tryRuntimeHeal(
        page: Page | null,
        currentContext: Page | FrameLocator,
        step: any,
        testCaseId: number | undefined,
        runId: number,
        errorMessage: string,
        sink: any[],
        settings: any,
        contextData: any,
    ): Promise<boolean> {
        try {
            if (process.env.RUNTIME_HEAL_ENABLED !== 'true') return false;
            if (llmProvider.name === 'null') return false;
            if (!step?.selector || !page || page.isClosed()) return false;
            if (!SELECTOR_FAILURE_RE.test(errorMessage || '')) return false;

            const dom = await page.content();
            const healed = (await this.aiEngine.healSelector(step.selector, dom, runId) || '').trim();
            if (!healed || healed === step.selector) return false;

            const matches = await page.locator(healed).count().catch(() => 0);
            const record = () => {
                if (testCaseId) sink.push({
                    test_case_id: testCaseId, step_id: step.id || '',
                    old_selector: step.selector, new_selector: healed,
                    matches, intent: step.intent || null,
                });
            };
            // Only auto-apply an unambiguous match; otherwise just suggest.
            if (matches !== 1) {
                if (matches > 0) record();
                return false;
            }

            await TestExecutor.executeStep(page, currentContext, { ...step, selector: healed }, settings, contextData);
            record();
            console.log(`[Worker] Runtime heal APPLIED: "${step.selector}" -> "${healed}" (step recovered)`);
            return true;
        } catch (e: any) {
            console.warn(`[Worker] Runtime heal retry failed: ${e.message}`);
            return false;
        }
    }

    constructor() {
        this.jobQueue = getJobQueue();
        this.browserManager = new BrowserManager();
    }

    /**
     * Start the worker loop
     */
    async start(): Promise<void> {
        console.log('[Worker] Starting execution worker...');

        // Initialize job queue
        await this.jobQueue.initialize();

        // Setup graceful shutdown
        this.setupShutdownHandlers();

        // Start processing loop
        await this.processLoop();
    }

    /**
     * Main processing loop
     */
    private async processLoop(): Promise<void> {
        console.log('[Worker] Entering job processing loop');

        while (!this.isShuttingDown) {
            // Periodic memory cleanup (without restarting)
            if (this.jobsProcessed > 0 && this.jobsProcessed % MAX_JOBS_BEFORE_RESTART === 0) {
                const memBefore = process.memoryUsage();
                console.log(`[Worker] Memory cleanup after ${this.jobsProcessed} jobs (heap: ${Math.round(memBefore.heapUsed / 1024 / 1024)}MB)`);

                // Force garbage collection if available (run with --expose-gc)
                if (global.gc) {
                    global.gc();
                    const memAfter = process.memoryUsage();
                    console.log(`[Worker] GC complete (heap: ${Math.round(memAfter.heapUsed / 1024 / 1024)}MB, freed: ${Math.round((memBefore.heapUsed - memAfter.heapUsed) / 1024 / 1024)}MB)`);
                }
            }

            // Reset idle timer
            this.resetIdleTimer();

            // Check dead-letter queue every 100 iterations
            if (this.jobsProcessed % 100 === 0) {
                await this.jobQueue.checkDeadLetterQueue();
            }

            try {
                // Try to claim a job
                const claimed = await this.jobQueue.claimJob();

                if (!claimed) {
                    // No job available, continue waiting
                    continue;
                }

                const { streamId, job } = claimed;

                // Cancel idle timer while processing
                this.cancelIdleTimer();

                // Mode-2 discovery jobs are not test runs: crawl and stash the
                // result under its own Redis key, bypassing run-progress.
                if ((job as any).job_type === 'discovery') {
                    await this.handleDiscoveryJob(streamId, job as any);
                    this.jobsProcessed++;
                    continue;
                }

                // Process the job with a hard wall-clock timeout.
                const timeout = new Promise<never>((_, reject) =>
                    setTimeout(() => reject(new Error(`Job ${job.job_id} exceeded MAX_JOB_DURATION_MS (${MAX_JOB_DURATION_MS}ms)`)), MAX_JOB_DURATION_MS)
                );
                const result = await Promise.race([this.executeJob(job), timeout]);

                // Complete the job
                await this.jobQueue.completeJob(streamId, result);

                this.jobsProcessed++;
                console.log(`[Worker] Completed job ${job.job_id} (total: ${this.jobsProcessed})`);

            } catch (err: any) {
                console.error('[Worker] Error in processing loop:', err);
                // Small delay before retrying
                await this.sleep(1000);
            }
        }

        console.log('[Worker] Exiting processing loop');
        await this.shutdown();
    }

    /**
     * Handle a Mode-2 discovery (URL-only crawl) job.
     */
    private async handleDiscoveryJob(streamId: string, job: any): Promise<void> {
        const discoveryId = job.discovery_id || job.job_id;
        console.log(`[Worker] Discovery job ${discoveryId}: crawling ${job.base_url}`);
        let result: any;
        try {
            const { crawlSurface } = require('./discovery');
            const browser = await this.browserManager.start(job.browser || 'chromium');
            try {
                result = await crawlSurface(browser, {
                    base_url: job.base_url,
                    max_pages: job.max_pages,
                    storage_state: job.settings?.storage_state,
                });
            } finally {
                await this.browserManager.stop();
            }
        } catch (err: any) {
            result = { status: 'error', base_url: job.base_url, pages: [], pages_visited: 0, pages_skipped: 0, error: err.message };
        }
        await this.jobQueue.completeDiscoveryJob(streamId, job.job_id, discoveryId, result);
        console.log(`[Worker] Discovery job ${discoveryId} done: ${result.pages_visited} page(s)`);
    }

    /**
     * Execute a test job - routes to appropriate handler based on job type
     */
    private async executeJob(job: TestJob): Promise<JobResult> {
        // Raw Playwright: run the uploaded spec verbatim, not the interpreter.
        if (job.test_case?.executor === 'raw_playwright') {
            console.log(`[Worker] Executing raw_playwright job ${job.job_id}`);
            return this.executeRawPlaywrightJob(job);
        }

        // Load testing: generate + run a k6 script (no browser involved).
        if (job.test_case?.executor === 'load') {
            console.log(`[Worker] Executing load job ${job.job_id}`);
            return this.executeLoadJob(job);
        }

        // Check if this is a multi-test continuous job
        if (job.execution_mode === 'continuous' && job.test_cases && job.test_cases.length > 0) {
            console.log(`[Worker] Executing continuous job ${job.job_id} with ${job.test_cases.length} tests`);
            return this.executeContinuousJob(job);
        }

        // Single test case job (original behavior)
        console.log(`[Worker] Executing single test job ${job.job_id}`);
        return this.executeSingleTestJobWithRetry(job);
    }

    /**
     * Run an uploaded Playwright spec (executor=raw_playwright). Gated by
     * RAW_PLAYWRIGHT_ENABLED because it executes arbitrary user code — only
     * enable on a sandboxed, network-restricted worker image with
     * @playwright/test + browsers installed. Results are reported at spec/test
     * granularity via `test_results` (same shape as continuous jobs).
     */
    private async executeRawPlaywrightJob(job: TestJob): Promise<JobResult> {
        const start = Date.now();
        const tc = job.test_case;
        const base: Omit<JobResult, 'status' | 'duration_ms'> = {
            job_id: job.job_id,
            run_id: job.run_id,
            test_case_id: job.test_case_id,
            test_name: tc?.name,
            artifacts: { screenshots: [] },
            network_events: [],
            completed_at: new Date().toISOString(),
        };

        if (process.env.RAW_PLAYWRIGHT_ENABLED !== 'true') {
            return { ...base, status: 'error', duration_ms: 0,
                error: 'raw_playwright execution is disabled on this worker (set RAW_PLAYWRIGHT_ENABLED=true on a sandboxed worker image).' };
        }
        if (!tc?.raw_script) {
            return { ...base, status: 'error', duration_ms: 0, error: 'Case has no raw_script.' };
        }

        const { runRawPlaywright } = await import('./raw-playwright-runner');
        const baseUrl = job.settings?.environment?.base_url;
        const res = await runRawPlaywright(tc.raw_script, { baseUrl, timeoutMs: MAX_JOB_DURATION_MS });

        const test_results: TestCaseResult[] = res.tests.map((t) => ({
            test_case_id: job.test_case_id as number,
            test_name: t.title,
            status: t.status,
            duration_ms: t.duration_ms,
            error: t.error,
        }));

        return {
            ...base,
            status: res.status,
            duration_ms: res.duration_ms || (Date.now() - start),
            error: res.error,
            test_results,
        };
    }

    /**
     * Run a k6 load test (executor=load). The case's first `load-test` step is
     * the declarative spec; the k6 script is generated, never user-supplied.
     * The generated script + k6 summary are uploaded as run artifacts.
     */
    private async executeLoadJob(job: TestJob): Promise<JobResult> {
        const tc = job.test_case;
        const base = {
            job_id: job.job_id,
            run_id: job.run_id,
            test_case_id: job.test_case_id,
            test_name: tc?.name,
            network_events: [],
            completed_at: new Date().toISOString(),
        };

        const loadStep = (tc?.steps || []).find((s: any) => s.type === 'load-test');
        if (!loadStep) {
            return { ...base, status: 'error', duration_ms: 0,
                artifacts: { screenshots: [], uploadedLocalPaths: [] } as any,
                error: 'Load case has no load-test step.' };
        }

        const artifactsDir = path.join(ARTIFACTS_BASE_DIR, job.job_id);
        fs.mkdirSync(artifactsDir, { recursive: true });

        const { runLoadTest } = await import('./load-runner');
        const spec = { target_url: loadStep.value || loadStep.selector, ...(loadStep.params || {}) };
        const outcome = await runLoadTest(spec, job.settings, artifactsDir);

        const artifacts = await this.uploadArtifacts(
            job.run_id, job.job_id, artifactsDir, null, null, [], []);
        this.cleanupUploadedArtifacts(artifactsDir, artifacts.uploadedLocalPaths);

        return {
            ...base,
            status: outcome.status,
            duration_ms: outcome.duration_ms,
            error: outcome.error,
            artifacts,
            ...(outcome.payload ? { result_kind: 'load', result_payload: outcome.payload } : {}),
        };
    }

    /**
     * Run a single-test job, retrying the whole case on failure/error when the
     * suite's auto_retry policy is enabled. Uses exponential backoff between
     * attempts. The returned result is the last attempt's, with retry_count set
     * to the number of retries performed (0 = passed first try).
     */
    private async executeSingleTestJobWithRetry(job: TestJob): Promise<JobResult> {
        const autoRetry = job.settings?.auto_retry === true;
        const maxRetries = autoRetry ? Math.max(0, job.settings?.max_retries ?? 2) : 0;
        const backoffBase = job.settings?.retry_backoff_ms ?? 1000;

        let attempt = 0;
        let result = await this.executeSingleTestJob(job);
        while (result.status !== 'passed' && attempt < maxRetries) {
            attempt++;
            const delay = backoffBase * Math.pow(2, attempt - 1);
            console.log(`[Worker] Test "${result.test_name}" ${result.status}; retry ${attempt}/${maxRetries} after ${delay}ms`);
            await new Promise(res => setTimeout(res, delay));
            result = await this.executeSingleTestJob(job);
        }
        result.retry_count = attempt;
        if (attempt > 0) {
            console.log(`[Worker] Test "${result.test_name}" final status ${result.status} after ${attempt} retry(ies)`);
        }
        return result;
    }

    /**
     * Execute a single test job with complete isolation
     */
    private async executeSingleTestJob(job: TestJob): Promise<JobResult> {
        // Validate that job has test_case (required for single test jobs)
        if (!job.test_case || !job.test_case_id) {
            throw new Error(`Invalid single test job ${job.job_id}: missing test_case or test_case_id`);
        }

        const testCase = job.test_case;
        const testCaseId = job.test_case_id;

        const startTime = Date.now();
        const artifactsDir = path.join(ARTIFACTS_BASE_DIR, job.job_id);

        // Create isolated artifacts directory
        fs.mkdirSync(artifactsDir, { recursive: true });

        let browser: Browser | null = null;
        let context: BrowserContext | null = null;
        let page: Page | null = null;

        const networkEvents: any[] = [];
        const consoleLogs: any[] = [];
        const screenshots: string[] = [];

        let status: 'passed' | 'failed' | 'error' = 'passed';
        let errorMessage: string | undefined;
        let responseData: any = undefined;
        let videoPath: string | null = null;
        let tracePath: string | null = null;
        let lastStepResult: any = null;
        let capturedAuthState: any = null;
        let currentStep: any = null;
        let webVitals: WebVitals | null = null;
        const healSuggestions: any[] = [];
        let healedStepCount = 0;

        try {
            // Launch browser
            browser = await this.browserManager.start(job.browser);

            // Prepare context options
            const contextOptions: any = {
                recordVideo: {
                    dir: artifactsDir,
                    size: { width: 1280, height: 720 }
                }
            };

            // Opt-in HAR capture: suite setting (dispatched per job) or a
            // worker-wide env default. Written on context.close(), picked up
            // by uploadArtifacts' directory scan.
            if (job.settings?.har_capture || process.env.HAR_CAPTURE_ENABLED === 'true') {
                contextOptions.recordHar = {
                    path: path.join(artifactsDir, `network-${job.job_id}.har`),
                    content: 'embed',
                };
                console.log(`[Worker] HAR capture enabled for job ${job.job_id}`);
            }

            // Apply device emulation if specified
            let emulatedAs: string | null = null;
            if (job.device) {
                const deviceConfig = this.getDeviceConfig(job.device, job.browser);
                if (deviceConfig) {
                    Object.assign(contextOptions, deviceConfig.options);
                    emulatedAs = deviceConfig.emulatedAs;
                }
            }

            // Start from the project's stored auth session (storageState) unless
            // this case is the auth-setup case itself or has opted out.
            if (job.settings?.storage_state && testCase.use_auth_session !== false && !testCase.is_auth_setup) {
                contextOptions.storageState = job.settings.storage_state;
                console.log(`[Worker] Using stored auth session for "${testCase.name}"`);
            }

            // Create isolated context
            context = await browser.newContext(contextOptions);
            await this.browserManager.injectInitScripts(context, job.browser, job.device || null, emulatedAs);

            // Start tracing
            await context.tracing.start({
                screenshots: true,
                snapshots: true,
                sources: true
            });

            // Setup network interception
            const requestStartTimes = new Map<string, number>();
            const contextData = {
                id: testCaseId,
                name: testCase.name,
                browser: job.browser || 'chromium',
                device: job.device || null,
                variables: {},
                // Data-driven row for this expansion; steps read {{data.KEY}}
                data: (testCase as any).data_row || {}
            };
            const sourceDomain = { value: null as string | null };

            await NetworkInterceptor.setupNetworkListeners(
                context,
                requestStartTimes,
                networkEvents,
                contextData
            );
            await NetworkInterceptor.setupRouteInterception(
                context,
                job.settings,
                sourceDomain
            );

            // Create page
            page = await context.newPage();
            page.setDefaultTimeout(parseInt(process.env.DEFAULT_TIMEOUT || '30000'));

            // Log console messages
            page.on('console', msg => {
                console.log(`  [Browser] [${testCase.name}]: ${msg.text()}`);
                if (consoleLogs.length < MAX_CONSOLE_LOG_ENTRIES) {
                    consoleLogs.push({ ts: new Date().toISOString(), type: msg.type(), text: msg.text(), test: testCase.name });
                }
            });

            // Initialize page
            try {
                await page.goto('about:blank', { waitUntil: 'domcontentloaded', timeout: 5000 });
                await page.evaluate((name) => {
                    (window as any).__TRACEIQ_TEST_NAME__ = name;
                    (window as any).__TRACEIQ_JOB_ID__ = name;
                }, testCase.name);
            } catch (e) {
                // Ignore navigation errors on about:blank
            }

            // Execute test steps
            let currentContext: Page | FrameLocator = page;

            for (const step of testCase.steps) {
                currentStep = step;
                try {
                    if (step.type === 'switch-frame') {
                        currentContext = await this.handleFrameSwitch(
                            page,
                            currentContext,
                            step
                        );
                    } else {
                        const stepResponse = await TestExecutor.executeStep(
                            page,
                            currentContext,
                            step,
                            job.settings,
                            contextData
                        );
                        if (stepResponse?.__switchToPage) {
                            page = stepResponse.__switchToPage as Page;
                            currentContext = page;
                        } else if (stepResponse && (step.type === 'http-request' || step.type === 'graphql' || step.type === 'feed-check' || step.type === 'amp-validate')) {
                            lastStepResult = stepResponse;
                        }
                    }
                } catch (stepErr: any) {
                    if (stepErr.stepResult) {
                        lastStepResult = stepErr.stepResult;
                    }
                    // Runtime self-heal: try to recover this step with an
                    // LLM-healed selector before failing the whole test.
                    const recovered = await this.tryRuntimeHeal(
                        page, currentContext, step, testCaseId, job.run_id,
                        stepErr.message, healSuggestions, job.settings, contextData);
                    if (recovered) {
                        healedStepCount++;
                        continue;
                    }
                    throw stepErr;
                }
            }

            if (healedStepCount > 0) {
                console.log(`[Worker] Test "${testCase.name}" passed with ${healedStepCount} runtime-healed step(s); heal proposals recorded.`);
            }

            // Capture response data if available
            if (lastStepResult) {
                responseData = {
                    status: lastStepResult.status,
                    headers: lastStepResult.headers,
                    body: lastStepResult.body,
                    request: lastStepResult.request
                };
            }

            // Auth-setup case succeeded: capture the logged-in storageState so
            // the backend can persist it for the project's later runs.
            if (testCase.is_auth_setup && context) {
                try {
                    capturedAuthState = await (page && !page.isClosed() ? page.context() : context).storageState();
                    console.log(`[Worker] Captured auth session state from "${testCase.name}"`);
                } catch (authErr: any) {
                    console.warn(`[Worker] Failed to capture auth state: ${authErr.message}`);
                }
            }

        } catch (err: any) {
            status = 'failed';
            errorMessage = err.message;
            console.error(`[Worker] Test failed: ${err.message}`);

            await this.maybeProposeHeal(
                page, currentStep, testCaseId, job.run_id, err.message, healSuggestions);

            // Capture response data from failed step if available
            if (err.stepResult) {
                responseData = {
                    status: err.stepResult.status,
                    headers: err.stepResult.headers,
                    body: err.stepResult.body,
                    request: err.stepResult.request
                };
            } else if (lastStepResult) {
                // Use last successful step result if available
                responseData = {
                    status: lastStepResult.status,
                    headers: lastStepResult.headers,
                    body: lastStepResult.body,
                    request: lastStepResult.request
                };
            }

            // Take screenshot on failure
            if (page && !page.isClosed()) {
                try {
                    const screenshotPath = path.join(artifactsDir, 'failure.png');
                    await page.screenshot({ path: screenshotPath, fullPage: true });
                } catch (screenshotErr) {
                    console.warn('[Worker] Failed to capture failure screenshot');
                }
            }
        } finally {
            // Web vitals for the page's final document — pass or fail, the
            // perf data is real as long as the page is still open.
            if (page && !page.isClosed()) {
                webVitals = await collectWebVitals(page);
            }

            // Stop tracing and save
            if (context) {
                try {
                    tracePath = path.join(artifactsDir, 'trace.zip');
                    await context.tracing.stop({ path: tracePath });
                } catch (traceErr) {
                    console.warn('[Worker] Failed to save trace');
                    tracePath = null;
                }
            }

            // Get video path
            if (page) {
                try {
                    const video = page.video();
                    if (video) {
                        videoPath = await video.path();
                    }
                } catch (videoErr) {
                    console.warn('[Worker] Failed to get video path');
                }
            }

            // Close context and browser
            if (context) {
                try {
                    await context.close();
                } catch (e) {
                    // Ignore close errors
                }
            }

            await this.browserManager.stop();
        }

        const duration = Date.now() - startTime;

        // Upload artifacts to MinIO, then remove only the files that uploaded.
        const artifacts = await this.uploadArtifacts(
            job.run_id,
            job.job_id,
            artifactsDir,
            videoPath,
            tracePath,
            consoleLogs,
            networkEvents
        );
        this.cleanupUploadedArtifacts(artifactsDir, artifacts.uploadedLocalPaths);

        return {
            job_id: job.job_id,
            run_id: job.run_id,
            test_case_id: testCaseId,
            test_name: testCase.name,
            status,
            duration_ms: duration,
            error: errorMessage,
            artifacts,
            response_data: responseData,
            network_events: networkEvents,
            ...(webVitals ? { web_vitals: webVitals } : {}),
            ...(capturedAuthState && status === 'passed' ? { auth_state: capturedAuthState } : {}),
            ...(healSuggestions.length ? { heal_suggestions: healSuggestions } : {}),
            completed_at: new Date().toISOString()
        };
    }

    /**
     * Execute a continuous job with multiple test cases in shared browser
     * This is used for sub-suite execution in hybrid mode
     */
    private async executeContinuousJob(job: TestJob): Promise<JobResult> {
        const startTime = Date.now();
        const artifactsDir = path.join(ARTIFACTS_BASE_DIR, job.job_id);

        // Create isolated artifacts directory
        fs.mkdirSync(artifactsDir, { recursive: true });

        let browser: Browser | null = null;
        let sharedContext: BrowserContext | null = null;

        const networkEvents: any[] = [];
        const consoleLogs: any[] = [];
        const testResults: TestCaseResult[] = [];
        let overallStatus: 'passed' | 'failed' | 'error' = 'passed';
        let overallError: string | undefined;
        let videoPath: string | null = null;
        let tracePath: string | null = null;
        let capturedAuthState: any = null;
        let capturedAuthCaseId: number | undefined;
        const healSuggestions: any[] = [];

        try {
            // Launch browser
            browser = await this.browserManager.start(job.browser);

            // Prepare context options with video recording
            const contextOptions: any = {
                recordVideo: {
                    dir: artifactsDir,
                    size: { width: 1280, height: 720 }
                }
            };

            // Opt-in HAR capture (shared context — one archive for the job).
            if (job.settings?.har_capture || process.env.HAR_CAPTURE_ENABLED === 'true') {
                contextOptions.recordHar = {
                    path: path.join(artifactsDir, `network-${job.job_id}.har`),
                    content: 'embed',
                };
                console.log(`[Worker] HAR capture enabled for continuous job ${job.job_id}`);
            }

            // Apply device emulation if specified
            let emulatedAs: string | null = null;
            if (job.device) {
                const deviceConfig = this.getDeviceConfig(job.device, job.browser);
                if (deviceConfig) {
                    Object.assign(contextOptions, deviceConfig.options);
                    emulatedAs = deviceConfig.emulatedAs;
                }
            }

            // Start from the project's stored auth session unless this job
            // includes the auth-setup case itself (it must exercise the real
            // login) — shared context, so the choice is job-level.
            if (job.settings?.storage_state && !job.test_cases?.some((c: any) => c.is_auth_setup)) {
                contextOptions.storageState = job.settings.storage_state;
                console.log(`[Worker] Using stored auth session for continuous job "${job.unit_name}"`);
            }

            // Create shared context for all tests
            sharedContext = await browser.newContext(contextOptions);
            await this.browserManager.injectInitScripts(sharedContext, job.browser, job.device || null, emulatedAs);

            // Start tracing for entire suite
            await sharedContext.tracing.start({
                screenshots: true,
                snapshots: true,
                sources: true
            });

            // Setup shared network listeners
            const sharedRequestStartTimes = new Map<string, number>();
            const sharedContextData = {
                id: job.unit_id || 0,
                name: job.unit_name || 'continuous-job',
                variables: {},
                // Per-case data-driven row; reassigned as each case starts.
                data: {} as Record<string, any>
            };
            const sourceDomain = { value: null as string | null };

            await NetworkInterceptor.setupNetworkListeners(
                sharedContext,
                sharedRequestStartTimes,
                networkEvents,
                sharedContextData
            );
            await NetworkInterceptor.setupRouteInterception(
                sharedContext,
                job.settings,
                sourceDomain
            );

            // Create shared page
            let page = await sharedContext.newPage();
            page.setDefaultTimeout(parseInt(process.env.DEFAULT_TIMEOUT || '30000'));

            // Log console messages
            page.on('console', msg => {
                console.log(`  [Browser] [${job.unit_name}]: ${msg.text()}`);
                if (consoleLogs.length < MAX_CONSOLE_LOG_ENTRIES) {
                    consoleLogs.push({ ts: new Date().toISOString(), type: msg.type(), text: msg.text(), test: sharedContextData.name });
                }
            });

            // Execute each test case sequentially
            for (const testCase of job.test_cases!) {
                const caseStartTime = Date.now();
                let caseStatus: 'passed' | 'failed' | 'error' = 'passed';
                let caseError: string | undefined;
                let responseData: any = undefined;
                let lastStepResult: any = null;
                let currentStep: any = null;
                sharedContextData.data = (testCase as any).data_row || {};

                // Snapshot network events length before this test so we can
                // slice out only the events generated by this test case.
                const netStartIdx = networkEvents.length;

                // Update context data for this test case
                sharedContextData.id = testCase.id;
                sharedContextData.name = testCase.name;

                console.log(`[Worker] Executing test case: ${testCase.name}`);

                // Check if page is still valid, create new one if closed
                if (page.isClosed()) {
                    console.log(`[Worker] Page was closed, creating new page for: ${testCase.name}`);
                    try {
                        page = await sharedContext.newPage();
                        page.setDefaultTimeout(parseInt(process.env.DEFAULT_TIMEOUT || '30000'));
                        page.on('console', msg => {
                            console.log(`  [Browser] [${job.unit_name}]: ${msg.text()}`);
                            if (consoleLogs.length < MAX_CONSOLE_LOG_ENTRIES) {
                                consoleLogs.push({ ts: new Date().toISOString(), type: msg.type(), text: msg.text(), test: sharedContextData.name });
                            }
                        });
                    } catch (pageErr: any) {
                        console.error(`[Worker] Failed to create new page: ${pageErr.message}`);
                        // Record error and continue to next test
                        testResults.push({
                            test_case_id: testCase.id,
                            test_name: testCase.name,
                            status: 'error',
                            duration_ms: Date.now() - caseStartTime,
                            error: `Failed to create page: ${pageErr.message}`,
                            response_data: undefined
                        });
                        overallStatus = 'failed';
                        continue;
                    }
                }

                try {
                    // Initialize page for this test
                    try {
                        await page.evaluate((name) => {
                            (window as any).__TRACEIQ_TEST_NAME__ = name;
                        }, testCase.name);
                    } catch (e) {
                        // Ignore if page is in unexpected state
                    }

                    // Execute test steps
                    let currentContext: Page | FrameLocator = page;

                    for (const step of testCase.steps) {
                        currentStep = step;
                        try {
                            if (step.type === 'switch-frame') {
                                currentContext = await this.handleFrameSwitch(
                                    page,
                                    currentContext,
                                    step
                                );
                            } else {
                                const stepResponse = await TestExecutor.executeStep(
                                    page,
                                    currentContext,
                                    step,
                                    job.settings,
                                    sharedContextData
                                );
                                if (stepResponse?.__switchToPage) {
                                    page = stepResponse.__switchToPage;
                                    currentContext = page;
                                } else if (stepResponse && (step.type === 'http-request' || step.type === 'graphql' || step.type === 'feed-check' || step.type === 'amp-validate')) {
                                    lastStepResult = stepResponse;
                                }
                            }
                        } catch (stepErr: any) {
                            if (stepErr.stepResult) {
                                lastStepResult = stepErr.stepResult;
                            }
                            throw stepErr;
                        }
                    }

                    // Capture response data if available
                    if (lastStepResult) {
                        responseData = {
                            status: lastStepResult.status,
                            headers: lastStepResult.headers,
                            body: lastStepResult.body,
                            request: lastStepResult.request
                        };
                    }

                    // Auth-setup case passed inside a continuous job: capture
                    // the logged-in storageState for the backend to persist.
                    if ((testCase as any).is_auth_setup && sharedContext) {
                        try {
                            capturedAuthState = await sharedContext.storageState();
                            capturedAuthCaseId = testCase.id;
                            console.log(`[Worker] Captured auth session state from "${testCase.name}"`);
                        } catch (authErr: any) {
                            console.warn(`[Worker] Failed to capture auth state: ${authErr.message}`);
                        }
                    }

                } catch (err: any) {
                    caseStatus = 'failed';
                    caseError = err.message;
                    overallStatus = 'failed';
                    console.error(`[Worker] Test case failed: ${testCase.name} - ${err.message}`);

                    await this.maybeProposeHeal(
                        page, currentStep, testCase.id, job.run_id, err.message, healSuggestions);

                    // Capture response data from failed step if available
                    if (err.stepResult) {
                        responseData = {
                            status: err.stepResult.status,
                            headers: err.stepResult.headers,
                            body: err.stepResult.body,
                            request: err.stepResult.request
                        };
                    } else if (lastStepResult) {
                        responseData = {
                            status: lastStepResult.status,
                            headers: lastStepResult.headers,
                            body: lastStepResult.body,
                            request: lastStepResult.request
                        };
                    }

                    // Take screenshot on failure
                    if (page && !page.isClosed()) {
                        try {
                            const screenshotPath = path.join(artifactsDir, `failure-${testCase.id}.png`);
                            await page.screenshot({ path: screenshotPath, fullPage: true });
                        } catch (screenshotErr) {
                            console.warn('[Worker] Failed to capture failure screenshot');
                        }
                    }
                }

                const caseDuration = Date.now() - caseStartTime;

                const caseVitals = (page && !page.isClosed())
                    ? await collectWebVitals(page) : null;

                // Record test result, including only the network events for
                // this test case (sliced from the shared accumulator).
                testResults.push({
                    test_case_id: testCase.id,
                    test_name: testCase.name,
                    status: caseStatus,
                    duration_ms: caseDuration,
                    error: caseError,
                    response_data: responseData,
                    network_events: networkEvents.slice(netStartIdx),
                    ...(caseVitals ? { web_vitals: caseVitals } : {})
                });

                console.log(`[Worker] Completed test case: ${testCase.name} (${caseStatus}, ${caseDuration}ms)`);
            }

        } catch (err: any) {
            overallStatus = 'error';
            overallError = err.message;
            console.error(`[Worker] Continuous job failed: ${err.message}`);
        } finally {
            // Stop tracing and save
            if (sharedContext) {
                try {
                    tracePath = path.join(artifactsDir, 'trace.zip');
                    await sharedContext.tracing.stop({ path: tracePath });
                } catch (traceErr) {
                    console.warn('[Worker] Failed to save trace');
                    tracePath = null;
                }

                // Get video path before closing context
                const pages = sharedContext.pages();
                if (pages.length > 0) {
                    try {
                        const video = pages[0].video();
                        if (video) {
                            videoPath = await video.path();
                        }
                    } catch (videoErr) {
                        console.warn('[Worker] Failed to get video path');
                    }
                }

                // Close shared context
                try {
                    await sharedContext.close();
                } catch (e) {
                    // Ignore close errors
                }
            }

            await this.browserManager.stop();
        }

        const duration = Date.now() - startTime;

        // Upload artifacts to MinIO, then remove only the files that uploaded.
        const artifacts = await this.uploadArtifacts(
            job.run_id,
            job.job_id,
            artifactsDir,
            videoPath,
            tracePath,
            consoleLogs,
            networkEvents
        );
        this.cleanupUploadedArtifacts(artifactsDir, artifacts.uploadedLocalPaths);

        // Determine first failure for backward compatibility
        const firstFailure = testResults.find(r => r.status !== 'passed');

        return {
            job_id: job.job_id,
            run_id: job.run_id,
            // For continuous jobs, report first test as primary (for backward compat)
            test_case_id: testResults[0]?.test_case_id,
            test_name: job.unit_name || testResults[0]?.test_name,
            status: overallStatus,
            duration_ms: duration,
            error: overallError || firstFailure?.error,
            artifacts,
            // Include all test results for proper aggregation
            test_results: testResults,
            network_events: networkEvents,
            ...(capturedAuthState ? { auth_state: capturedAuthState, auth_case_id: capturedAuthCaseId } : {}),
            ...(healSuggestions.length ? { heal_suggestions: healSuggestions } : {}),
            completed_at: new Date().toISOString()
        };
    }

    /**
     * Handle frame switching
     */
    private async handleFrameSwitch(
        page: Page,
        currentContext: Page | FrameLocator,
        step: any
    ): Promise<Page | FrameLocator> {
        const frameSelector = step.selector || step.value;

        if (frameSelector === 'main' || frameSelector === 'top') {
            return page;
        }

        if (frameSelector) {
            if (step.options?.strict_lifecycle) {
                const frameElement = currentContext.locator(frameSelector).first();
                await frameElement.waitFor({ state: 'attached', timeout: 30000 });
                const elementHandle = await frameElement.elementHandle();
                const contentFrame = await elementHandle?.contentFrame();
                if (contentFrame) {
                    await contentFrame.waitForLoadState('domcontentloaded', { timeout: 30000 });
                }
            }
            return currentContext.frameLocator(frameSelector);
        }

        return currentContext;
    }

    /**
     * Get device configuration for emulation
     */
    private getDeviceConfig(device: string, browserType: string): { options: any; emulatedAs: string | null } | null {
        if (device === 'Mobile (Generic)') {
            return {
                options: {
                    viewport: { width: 375, height: 667 },
                    deviceScaleFactor: 2,
                    isMobile: browserType !== 'firefox',
                    hasTouch: true
                },
                emulatedAs: null
            };
        }

        const descriptor = devices[device as keyof typeof devices];
        if (!descriptor) return null;

        const options: any = {
            viewport: descriptor.viewport,
            deviceScaleFactor: descriptor.deviceScaleFactor,
            hasTouch: descriptor.hasTouch,
            isMobile: browserType !== 'firefox'
        };

        // Handle cross-browser device emulation
        if (descriptor.defaultBrowserType && descriptor.defaultBrowserType !== browserType) {
            const isIOS = descriptor.defaultBrowserType === 'webkit' ||
                device.includes('iPhone') ||
                device.includes('iPad');

            if (isIOS && browserType === 'chromium') {
                options.userAgent = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/120.0.6099.119 Mobile/15E148 Safari/604.1';
            } else if (browserType === 'firefox') {
                options.userAgent = 'Mozilla/5.0 (Android 14; Mobile; rv:120.0) Gecko/120.0 Firefox/120.0';
            }
        }

        return { options, emulatedAs: descriptor.defaultBrowserType || null };
    }

    /**
     * Upload artifacts to MinIO.
     * Returns uploaded keys and a list of local paths that were successfully
     * uploaded so callers can safely delete only those files.
     */
    private async uploadArtifacts(
        runId: number,
        jobId: string,
        artifactsDir: string,
        videoPath: string | null,
        tracePath: string | null,
        consoleLogs?: any[],
        networkEvents?: any[],
        captureLevel: CaptureLevel = 'full'
    ): Promise<{ video?: string; trace?: string; har?: string; screenshots: string[]; console_log?: string; network_log?: string; uploadedLocalPaths: string[] }> {
        const result: { video?: string; trace?: string; har?: string; screenshots: string[]; console_log?: string; network_log?: string; uploadedLocalPaths: string[] } = {
            screenshots: [],
            uploadedLocalPaths: []
        };

        // One store per job: it carries the job's capture level, and every
        // write below is gated on it. Nothing in this method talks to MinIO
        // directly any more.
        const store = new ArtifactStore(captureLevel);

        // A suppressed artifact still needs its local file cleaned up —
        // otherwise a `capture_level: none` worker slowly fills its disk.
        const consumed = (localPath: string, put: PutResult, assign?: (key: string) => void) => {
            if (put.key) {
                result.uploadedLocalPaths.push(localPath);
                assign?.(put.key);
            } else if (put.suppressed) {
                result.uploadedLocalPaths.push(localPath);
            }
        };

        try {
            if (videoPath) {
                const put = await store.putFile('video', artifactKeys.video(runId, jobId), videoPath);
                consumed(videoPath, put, key => { result.video = key; });
            }

            if (tracePath) {
                const put = await store.putFile('trace', artifactKeys.trace(runId, jobId), tracePath);
                consumed(tracePath, put, key => { result.trace = key; });
            }

            if (fs.existsSync(artifactsDir)) {
                const files = fs.readdirSync(artifactsDir);
                for (const file of files.filter(f => f.endsWith('.png'))) {
                    const localPath = path.join(artifactsDir, file);
                    const put = await store.putFile(
                        'screenshot', artifactKeys.screenshot(runId, jobId, file), localPath, 'image/png');
                    consumed(localPath, put, key => { result.screenshots.push(key); });
                }

                // HAR network archive (written by recordHar on context.close()
                // when har_capture is enabled — one per job).
                const harFile = files.find(f => f.endsWith('.har'));
                if (harFile) {
                    const localPath = path.join(artifactsDir, harFile);
                    const put = await store.putFile('har', artifactKeys.har(runId, jobId), localPath);
                    consumed(localPath, put, key => { result.har = key; });
                }
            }

            if (consoleLogs && consoleLogs.length) {
                const body = Buffer.from(JSON.stringify(consoleLogs, null, 1));
                const put = await store.putBuffer(
                    'console_log', artifactKeys.consoleLog(runId, jobId), body, 'application/json');
                if (put.key) result.console_log = put.key;
            }
            if (networkEvents && networkEvents.length) {
                const body = Buffer.from(JSON.stringify(networkEvents, null, 1));
                const put = await store.putBuffer(
                    'network_log', artifactKeys.networkLog(runId, jobId), body, 'application/json');
                if (put.key) result.network_log = put.key;
            }
        } catch (err) {
            console.error('[Worker] Error uploading artifacts:', err);
        }

        return result;
    }

    /**
     * Remove local artifact files that were confirmed uploaded to MinIO.
     * Files that failed to upload are left in place so they can be retried.
     */
    private cleanupUploadedArtifacts(artifactsDir: string, uploadedPaths: string[]): void {
        for (const localPath of uploadedPaths) {
            try {
                if (fs.existsSync(localPath)) {
                    fs.rmSync(localPath, { force: true });
                }
            } catch (err) {
                console.warn(`[Worker] Failed to remove uploaded artifact: ${localPath}`);
            }
        }

        // Remove the artifacts dir only if it is now empty
        try {
            if (fs.existsSync(artifactsDir)) {
                const remaining = fs.readdirSync(artifactsDir);
                if (remaining.length === 0) {
                    fs.rmdirSync(artifactsDir);
                } else {
                    console.warn(`[Worker] ${remaining.length} artifact(s) not uploaded, keeping dir: ${artifactsDir}`);
                }
            }
        } catch (err) {
            console.warn('[Worker] Failed to remove artifacts directory');
        }
    }

    /**
     * Setup idle timer for auto-shutdown
     */
    private resetIdleTimer(): void {
        this.cancelIdleTimer();

        if (IDLE_TIMEOUT_MS > 0) {
            this.idleTimer = setTimeout(() => {
                console.log('[Worker] Idle timeout reached, shutting down');
                this.isShuttingDown = true;
            }, IDLE_TIMEOUT_MS);
        }
    }

    private cancelIdleTimer(): void {
        if (this.idleTimer) {
            clearTimeout(this.idleTimer);
            this.idleTimer = null;
        }
    }

    /**
     * Setup graceful shutdown handlers
     */
    private setupShutdownHandlers(): void {
        const shutdown = async (signal: string) => {
            console.log(`[Worker] Received ${signal}, initiating graceful shutdown`);
            this.isShuttingDown = true;
        };

        process.on('SIGTERM', () => shutdown('SIGTERM'));
        process.on('SIGINT', () => shutdown('SIGINT'));
    }

    /**
     * Graceful shutdown
     */
    private async shutdown(): Promise<void> {
        console.log('[Worker] Shutting down...');
        this.cancelIdleTimer();
        await this.browserManager.stop();
        await this.jobQueue.shutdown();
        console.log('[Worker] Shutdown complete');
    }

    private sleep(ms: number): Promise<void> {
        return new Promise(resolve => setTimeout(resolve, ms));
    }
}

// Start worker
const worker = new ExecutionWorker();
worker.start().catch(err => {
    console.error('[Worker] Fatal error:', err);
    process.exit(1);
});

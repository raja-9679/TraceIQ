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
import * as Minio from 'minio';
import * as fs from 'fs';
import * as path from 'path';
import { JobQueue, TestJob, JobResult, TestCase, TestCaseResult, getJobQueue } from './core/job-queue';
import { BrowserManager } from './core/browser-manager';
import { NetworkInterceptor } from './core/network-interceptor';
import { TestExecutor } from './core/test-executor';

const MinioClient = (Minio as any).Client || Minio;

// Configuration
const ARTIFACTS_BASE_DIR = process.env.ARTIFACTS_DIR || '/tmp/artifacts';
const BUCKET_NAME = process.env.MINIO_BUCKET_NAME || 'test-artifacts';
const IDLE_TIMEOUT_MS = parseInt(process.env.WORKER_IDLE_TIMEOUT || '60000');
const MAX_JOBS_BEFORE_RESTART = parseInt(process.env.MAX_JOBS_BEFORE_RESTART || '50');

class ExecutionWorker {
    private jobQueue: JobQueue;
    private browserManager: BrowserManager;
    private minioClient: any;
    private isShuttingDown: boolean = false;
    private jobsProcessed: number = 0;
    private idleTimer: NodeJS.Timeout | null = null;

    constructor() {
        this.jobQueue = getJobQueue();
        this.browserManager = new BrowserManager();
        this.minioClient = new MinioClient({
            endPoint: process.env.MINIO_ENDPOINT || 'localhost',
            port: parseInt(process.env.MINIO_PORT || '9000'),
            useSSL: process.env.MINIO_USE_SSL === 'true',
            accessKey: process.env.MINIO_ACCESS_KEY || 'minioadmin',
            secretKey: process.env.MINIO_SECRET_KEY || 'minioadmin'
        });
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

                // Process the job
                const result = await this.executeJob(job);

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
     * Execute a test job - routes to appropriate handler based on job type
     */
    private async executeJob(job: TestJob): Promise<JobResult> {
        // Check if this is a multi-test continuous job
        if (job.execution_mode === 'continuous' && job.test_cases && job.test_cases.length > 0) {
            console.log(`[Worker] Executing continuous job ${job.job_id} with ${job.test_cases.length} tests`);
            return this.executeContinuousJob(job);
        }

        // Single test case job (original behavior)
        console.log(`[Worker] Executing single test job ${job.job_id}`);
        return this.executeSingleTestJob(job);
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
        const screenshots: string[] = [];

        let status: 'passed' | 'failed' | 'error' = 'passed';
        let errorMessage: string | undefined;
        let responseData: any = undefined;
        let videoPath: string | null = null;
        let tracePath: string | null = null;
        let lastStepResult: any = null;

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

            // Apply device emulation if specified
            let emulatedAs: string | null = null;
            if (job.device) {
                const deviceConfig = this.getDeviceConfig(job.device, job.browser);
                if (deviceConfig) {
                    Object.assign(contextOptions, deviceConfig.options);
                    emulatedAs = deviceConfig.emulatedAs;
                }
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
                variables: {}
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
                        if (stepResponse && (step.type === 'http-request' || step.type === 'feed-check')) {
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

        } catch (err: any) {
            status = 'failed';
            errorMessage = err.message;
            console.error(`[Worker] Test failed: ${err.message}`);

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

        // Upload artifacts to MinIO
        const artifacts = await this.uploadArtifacts(
            job.run_id,
            job.job_id,
            artifactsDir,
            videoPath,
            tracePath
        );

        // Cleanup local artifacts
        try {
            fs.rmSync(artifactsDir, { recursive: true, force: true });
        } catch (cleanupErr) {
            console.warn('[Worker] Failed to cleanup artifacts directory');
        }

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
        const testResults: TestCaseResult[] = [];
        let overallStatus: 'passed' | 'failed' | 'error' = 'passed';
        let overallError: string | undefined;
        let videoPath: string | null = null;
        let tracePath: string | null = null;

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

            // Apply device emulation if specified
            let emulatedAs: string | null = null;
            if (job.device) {
                const deviceConfig = this.getDeviceConfig(job.device, job.browser);
                if (deviceConfig) {
                    Object.assign(contextOptions, deviceConfig.options);
                    emulatedAs = deviceConfig.emulatedAs;
                }
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
                variables: {}
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
            });

            // Execute each test case sequentially
            for (const testCase of job.test_cases!) {
                const caseStartTime = Date.now();
                let caseStatus: 'passed' | 'failed' | 'error' = 'passed';
                let caseError: string | undefined;
                let responseData: any = undefined;
                let lastStepResult: any = null;

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
                                if (stepResponse && (step.type === 'http-request' || step.type === 'feed-check')) {
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

                } catch (err: any) {
                    caseStatus = 'failed';
                    caseError = err.message;
                    overallStatus = 'failed';
                    console.error(`[Worker] Test case failed: ${testCase.name} - ${err.message}`);

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

                // Record test result
                testResults.push({
                    test_case_id: testCase.id,
                    test_name: testCase.name,
                    status: caseStatus,
                    duration_ms: caseDuration,
                    error: caseError,
                    response_data: responseData
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

        // Upload artifacts to MinIO
        const artifacts = await this.uploadArtifacts(
            job.run_id,
            job.job_id,
            artifactsDir,
            videoPath,
            tracePath
        );

        // Cleanup local artifacts
        try {
            fs.rmSync(artifactsDir, { recursive: true, force: true });
        } catch (cleanupErr) {
            console.warn('[Worker] Failed to cleanup artifacts directory');
        }

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
     * Upload artifacts to MinIO
     */
    private async uploadArtifacts(
        runId: number,
        jobId: string,
        artifactsDir: string,
        videoPath: string | null,
        tracePath: string | null
    ): Promise<{ video?: string; trace?: string; screenshots: string[] }> {
        const result: { video?: string; trace?: string; screenshots: string[] } = {
            screenshots: []
        };

        try {
            // Ensure bucket exists
            const bucketExists = await this.minioClient.bucketExists(BUCKET_NAME);
            if (!bucketExists) {
                await this.minioClient.makeBucket(BUCKET_NAME);
            }

            // Upload video
            if (videoPath && fs.existsSync(videoPath)) {
                const videoKey = `runs/${runId}/videos/${jobId}.webm`;
                await this.minioClient.fPutObject(BUCKET_NAME, videoKey, videoPath);
                result.video = videoKey;
                console.log(`[Worker] Uploaded video: ${videoKey}`);
            }

            // Upload trace
            if (tracePath && fs.existsSync(tracePath)) {
                const traceKey = `runs/${runId}/traces/${jobId}.zip`;
                await this.minioClient.fPutObject(BUCKET_NAME, traceKey, tracePath);
                result.trace = traceKey;
                console.log(`[Worker] Uploaded trace: ${traceKey}`);
            }

            // Upload screenshots
            if (fs.existsSync(artifactsDir)) {
                const files = fs.readdirSync(artifactsDir);
                for (const file of files.filter(f => f.endsWith('.png'))) {
                    const screenshotKey = `runs/${runId}/screenshots/${jobId}-${file}`;
                    await this.minioClient.fPutObject(
                        BUCKET_NAME,
                        screenshotKey,
                        path.join(artifactsDir, file)
                    );
                    result.screenshots.push(screenshotKey);
                }
            }
        } catch (err) {
            console.error('[Worker] Error uploading artifacts:', err);
        }

        return result;
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

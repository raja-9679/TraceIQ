import { Browser, BrowserContext, devices, Page, FrameLocator } from 'playwright';
import * as Minio from 'minio';
import * as fs from 'fs';
import * as path from 'path';
import { BrowserManager } from './core/browser-manager';
import { NetworkInterceptor } from './core/network-interceptor';
import { TestExecutor } from './core/test-executor';

const MinioClient = (Minio as any).Client || Minio;

const minioClient = new MinioClient({
    endPoint: process.env.MINIO_ENDPOINT || 'localhost',
    port: parseInt(process.env.MINIO_PORT || '9000'),
    useSSL: false,
    accessKey: process.env.MINIO_ACCESS_KEY || 'minioadmin',
    secretKey: process.env.MINIO_SECRET_KEY || 'minioadmin'
});

const BUCKET_NAME = process.env.MINIO_BUCKET_NAME || 'test-artifacts';

export class PlaywrightRunner {
    private browserManager = new BrowserManager();

    async start(browserType: string = 'chromium') {
        return this.browserManager.start(browserType);
    }

    async stop() {
        return this.browserManager.stop();
    }

    async runTest(runId: number, testCases: any[], browserType: string = 'chromium', globalSettings: any = {}, device?: string, executionMode: string = 'continuous', callbackUrl?: string, webhookSecret?: string): Promise<any> {
        const browser = await this.start(browserType);
        const artifactsDir = process.env.ARTIFACTS_DIR ? path.join(process.env.ARTIFACTS_DIR, String(runId)) : `/tmp/artifacts/${runId}`;
        fs.mkdirSync(artifactsDir, { recursive: true });

        // Global lists (thread-safe in JS event loop)
        const executionLog: any[] = [];
        const testResults: any[] = [];
        const networkEvents: any[] = [];
        const screenshots: string[] = [];
        let videoKey: string | null = null;
        let traceKey: string | null = null;

        let status = 'passed';
        let error: string | null = null;
        const startTime = Date.now();

        // Prepare shared context if needed (continuous mode)
        let sharedContext: BrowserContext | null = null;
        let contextOptions: any = {
            recordVideo: { dir: artifactsDir, size: { width: 1280, height: 720 } }
        };

        // Device logic
        let emulatedAs: string | null = null;
        if (device) {
            let descriptor: any = null;
            if (device === 'Mobile (Generic)') {
                descriptor = { viewport: { width: 375, height: 667 }, deviceScaleFactor: 2, isMobile: browserType !== 'firefox', hasTouch: true };
            } else if (devices[device as keyof typeof devices]) {
                const d = devices[device as keyof typeof devices];
                if (d.defaultBrowserType && d.defaultBrowserType !== browserType) {
                    descriptor = { viewport: d.viewport, deviceScaleFactor: d.deviceScaleFactor, hasTouch: d.hasTouch, isMobile: browserType !== 'firefox' };
                    const isIOS = d.defaultBrowserType === 'webkit' || (device && (device.includes('iPhone') || device.includes('iPad')));
                    if (isIOS) {
                        descriptor.userAgent = browserType === 'chromium' ? 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/120.0.6099.119 Mobile/15E148 Safari/604.1' : 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) FxiOS/120.0 Mobile/15E148 Safari/605.1.15';
                    } else if (browserType === 'firefox') {
                        descriptor.userAgent = 'Mozilla/5.0 (Android 14; Mobile; rv:120.0) Gecko/120.0 Firefox/120.0';
                    }
                } else {
                    descriptor = { ...d };
                    emulatedAs = descriptor.defaultBrowserType;
                    if (browserType === 'firefox') delete descriptor.isMobile;
                }
            }
            if (descriptor) contextOptions = { ...contextOptions, ...descriptor };
        }

        try {
            if (!testCases || testCases.length === 0) throw new Error("No test cases provided");

            // Helper function for single case
            const runSingleCase = async (testCase: any, useSharedContext: boolean) => {
                const caseStartTime = Date.now();
                let caseStatus = 'passed';
                let caseError = null;
                let lastStepResult: any = null;
                let context: BrowserContext;
                let page: Page;
                let closeContext = false;

                // Setup Context
                if (useSharedContext && sharedContext) {
                    context = sharedContext;
                    const pages = context.pages();
                    page = (pages.length > 0 && !pages[0].isClosed()) ? pages[0] : await context.newPage();
                } else {
                    context = await browser.newContext(contextOptions);
                    await this.browserManager.injectInitScripts(context, browserType, device || null, emulatedAs || null);
                    await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
                    page = await context.newPage();
                    closeContext = true; // Close explicit context after use
                }

                // Setup Listeners
                const localRequestStartTimes = new Map<string, number>();
                const localContextData = { id: testCase.id, name: testCase.name, variables: {} as Record<string, any> };
                const localSourceDomain = { value: null as string | null };

                // We push to global networkEvents. Ideally we filter by test case, but for now flat list.
                await NetworkInterceptor.setupNetworkListeners(context, localRequestStartTimes, networkEvents, localContextData);

                let caseSettings = {
                    headers: globalSettings?.headers || {},
                    params: globalSettings?.params || {},
                    allowed_domains: globalSettings?.allowed_domains || [],
                    domain_settings: globalSettings?.domain_settings || {}
                };
                if (testCase.settings) {
                    caseSettings.headers = testCase.settings.headers || {};
                    caseSettings.params = testCase.settings.params || {};
                    caseSettings.allowed_domains = testCase.settings.allowed_domains || [];
                    caseSettings.domain_settings = testCase.settings.domain_settings || {};
                }

                await NetworkInterceptor.setupRouteInterception(context, caseSettings, localSourceDomain);
                page.on('console', msg => console.log(`  [Browser-Console] [${testCase.name}]: ${msg.text()}`));

                try {
                    const defaultTimeout = parseInt(process.env.DEFAULT_TIMEOUT || '30000');
                    page.setDefaultTimeout(defaultTimeout);
                    try {
                        await page.goto('about:blank', { waitUntil: 'domcontentloaded', timeout: 5000 });
                        await page.evaluate((tn) => { (window as any).__TRACEIQ_TEST_NAME__ = tn; }, testCase.name);
                    } catch (e) { }

                    let currentContext: Page | FrameLocator = page;
                    for (const step of testCase.steps) {
                        try {
                            if (step.type === 'switch-frame') {
                                const frameSelector = step.selector || step.value;
                                if (frameSelector === 'main' || frameSelector === 'top') {
                                    currentContext = page;
                                } else if (frameSelector) {
                                    if (step.options?.strict_lifecycle) {
                                        const frameElement = currentContext.locator(frameSelector).first();
                                        await frameElement.waitFor({ state: 'attached', timeout: 30000 });
                                        const elementHandle = await frameElement.elementHandle();
                                        const contentFrame = await elementHandle?.contentFrame();
                                        if (contentFrame) await contentFrame.waitForLoadState('domcontentloaded', { timeout: 30000 });
                                    }
                                    currentContext = currentContext.frameLocator(frameSelector);
                                }
                            } else {
                                const stepResponse = await TestExecutor.executeStep(page, currentContext, step, caseSettings, localContextData);
                                if (stepResponse && (step.type === 'http-request' || step.type === 'feed-check')) {
                                    lastStepResult = stepResponse;
                                }
                            }
                        } catch (stepErr: any) {
                            if (stepErr.stepResult) lastStepResult = stepErr.stepResult;
                            throw stepErr;
                        }
                    }

                } catch (e: any) {
                    caseStatus = 'failed';
                    caseError = e.message;
                } finally {
                    const caseEndTime = Date.now();
                    executionLog.push({ testCaseId: testCase.id, testCaseName: testCase.name, startTime: caseStartTime, endTime: caseEndTime, status: caseStatus, error: caseError });

                    // Capture video path if available
                    let caseVideo = null;
                    const v = page.video();
                    if (v) {
                        const vp = await v.path().catch(() => null);
                        if (vp) caseVideo = vp; // Save absolute path, we'll process later
                    }

                    testResults.push({
                        test_case_id: testCase.id,
                        test_name: testCase.name, status: caseStatus, duration_ms: caseEndTime - caseStartTime, error: caseError,
                        response_status: lastStepResult?.status, response_headers: lastStepResult?.headers, response_body: lastStepResult?.body,
                        request_headers: lastStepResult?.request?.headers, request_body: lastStepResult?.request?.body,
                        video_path: caseVideo // Temporary field for internal use
                    });

                    if (closeContext) {
                        // Stop tracing before closing
                        try {
                            const traceName = `trace-${testCase.id}.zip`;
                            await context.tracing.stop({ path: path.join(artifactsDir, traceName) });
                        } catch (tracingErr: any) {
                            console.warn(`Failed to stop tracing for test case ${testCase.id}: ${tracingErr.message}`);
                        }

                        try {
                            await context.close();
                        } catch (closeErr: any) {
                            console.warn(`Failed to close context for test case ${testCase.id}: ${closeErr.message}`);
                        }
                    }
                }
            }; // End runSingleCase

            if (executionMode === 'parallel') {
                console.log("Running test cases in PARALLEL");
                // Run all in parallel, each gets its own context
                await Promise.all(testCases.map(tc => runSingleCase(tc, false)));
            } else {
                console.log("Running test cases CONTINUOUSLY (Sequential)");
                // Create shared context once
                sharedContext = await browser.newContext(contextOptions);
                await this.browserManager.injectInitScripts(sharedContext, browserType, device || null, emulatedAs || null);
                await sharedContext.tracing.start({ screenshots: true, snapshots: true, sources: true });

                await NetworkInterceptor.setupNetworkListeners(sharedContext, new Map(), networkEvents, { id: 0, name: 'shared' }); // simplified

                for (const testCase of testCases) {
                    // Check if case overrides to separate
                    if (testCase.executionMode === 'separate') {
                        await runSingleCase(testCase, false);
                    } else {
                        await runSingleCase(testCase, true);
                    }
                }

                // Stop shared trace
                try {
                    await sharedContext.tracing.stop({ path: path.join(artifactsDir, 'trace.zip') });
                } catch (tracingErr: any) {
                    console.warn(`Failed to stop shared context tracing: ${tracingErr.message}`);
                }

                try {
                    await sharedContext.close();
                } catch (closeErr: any) {
                    console.warn(`Failed to close shared context: ${closeErr.message}`);
                }
            }

        } catch (e: any) {
            status = 'failed';
            error = e.message;
        } finally {
            const duration = Date.now() - startTime;
            await this.stop(); // Close browser

            try {
                if (fs.existsSync(artifactsDir)) {
                    const files = fs.readdirSync(artifactsDir);

                    // Process Screenshots
                    for (const file of files.filter(f => f.endsWith('.png'))) {
                        const key = `runs/${runId}/screenshots/${file}`;
                        await minioClient.fPutObject(BUCKET_NAME, key, path.join(artifactsDir, file));
                        screenshots.push(key);
                    }

                    // Process Videos
                    // If parallel, we might have multiple videos.
                    // We need to map them back to results if possible, or just upload them.
                    // If sequential, usually one video.
                    // We try to use the 'video_path' we captured in testResults to rename/upload clearly.

                    for (const res of testResults) {
                        if (res.video_path && fs.existsSync(res.video_path)) {
                            const ext = path.extname(res.video_path);
                            // Unique video name per case
                            const vKey = `runs/${runId}/videos/${res.test_case_id}${ext}`;
                            await minioClient.fPutObject(BUCKET_NAME, vKey, res.video_path);
                            res.video = vKey; // Update result with public key
                            delete res.video_path; // Remove local path
                            // Set main videoKey to first one if null
                            if (!videoKey) videoKey = vKey;
                        }
                    }

                    // Fallback for any leftover videos (e.g. shared context video)
                    const remainingVideos = fs.readdirSync(artifactsDir).filter(f => f.endsWith('.webm'));
                    for (const vFile of remainingVideos) {
                        const vPath = path.join(artifactsDir, vFile);
                        // Check if already uploaded (by size/name? hard to know, Playwright uses random names)
                        // Simple: Just upload as run video if we don't have one
                        if (!videoKey) {
                            videoKey = `runs/${runId}/video.webm`;
                            await minioClient.fPutObject(BUCKET_NAME, videoKey, vPath);
                        }
                    }

                    // Process Traces
                    // Parallel: trace-ID.zip. Sequential: trace.zip
                    // Upload all zips
                    const traceFiles = files.filter(f => f.endsWith('.zip'));
                    for (const tFile of traceFiles) {
                        const tKey = `runs/${runId}/traces/${tFile}`;
                        await minioClient.fPutObject(BUCKET_NAME, tKey, path.join(artifactsDir, tFile));
                        // Link to specific result?
                        if (tFile === 'trace.zip') traceKey = tKey;
                        else {
                            // try to parse ID
                            const match = tFile.match(/trace-(\d+)\.zip/);
                            if (match) {
                                const tcId = parseInt(match[1]);
                                const r = testResults.find(tr => tr.test_case_id === tcId);
                                if (r) r.trace = tKey;
                            }
                        }
                    }

                    fs.rmSync(artifactsDir, { recursive: true, force: true });
                }
            } catch (cleanupError) {
                console.error("Error during artifact cleanup:", cleanupError);
            }

            const finalResult = {
                status, duration_ms: duration, error, trace: traceKey, video: videoKey, screenshots: screenshots,
                network_events: networkEvents, execution_log: executionLog, results: testResults
            };

            if (callbackUrl) {
                try {
                    console.log(`Sending callback to ${callbackUrl}`);
                    await fetch(callbackUrl, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            ...(webhookSecret ? { 'X-TraceIQ-Secret': webhookSecret } : {})
                        },
                        body: JSON.stringify(finalResult)
                    });
                    console.log('Callback sent successfully');
                } catch (cbError) {
                    console.error('Failed to send callback:', cbError);
                }
            }
            return finalResult;
        }
    }
}


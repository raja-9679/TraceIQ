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

    async runTest(runId: number, testCases: any[], browserType: string = 'chromium', globalSettings: any = {}, device?: string): Promise<any> {
        const browser = await this.start(browserType);
        const artifactsDir = process.env.ARTIFACTS_DIR ? path.join(process.env.ARTIFACTS_DIR, String(runId)) : `/tmp/artifacts/${runId}`;
        fs.mkdirSync(artifactsDir, { recursive: true });

        let contextOptions: any = {
            recordVideo: { dir: artifactsDir, size: { width: 1280, height: 720 } }
        };

        let emulatedAs: string | null = null;
        if (device) {
            let descriptor: any = null;
            if (device === 'Mobile (Generic)') {
                descriptor = {
                    viewport: { width: 375, height: 667 },
                    deviceScaleFactor: 2,
                    isMobile: browserType !== 'firefox',
                    hasTouch: true,
                };
            } else if (devices[device as keyof typeof devices]) {
                const deviceDescriptor = devices[device as keyof typeof devices];
                if (deviceDescriptor.defaultBrowserType && deviceDescriptor.defaultBrowserType !== browserType) {
                    descriptor = {
                        viewport: deviceDescriptor.viewport,
                        deviceScaleFactor: deviceDescriptor.deviceScaleFactor,
                        hasTouch: deviceDescriptor.hasTouch,
                        isMobile: browserType !== 'firefox',
                    };
                    const isIOS = deviceDescriptor.defaultBrowserType === 'webkit' || (device && device.includes('iPhone')) || (device && device.includes('iPad'));
                    if (isIOS) {
                        if (browserType === 'chromium') {
                            descriptor.userAgent = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/120.0.6099.119 Mobile/15E148 Safari/604.1';
                        } else if (browserType === 'firefox') {
                            descriptor.userAgent = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) FxiOS/120.0 Mobile/15E148 Safari/605.1.15';
                        }
                    } else if (browserType === 'firefox') {
                        descriptor.userAgent = 'Mozilla/5.0 (Android 14; Mobile; rv:120.0) Gecko/120.0 Firefox/120.0';
                    }
                } else {
                    descriptor = { ...deviceDescriptor };
                    emulatedAs = descriptor.defaultBrowserType;
                    if (browserType === 'firefox') delete descriptor.isMobile;
                }
            }
            if (descriptor) {
                contextOptions = { ...contextOptions, ...descriptor };
            }
        }

        const sharedContext = await browser.newContext(contextOptions);
        const requestStartTimes = new Map<string, number>();
        const networkEvents: any[] = [];
        const testCaseContext = { id: null as number | null, name: null as string | null };
        const sourceDomain = { value: null as string | null };

        await NetworkInterceptor.setupNetworkListeners(sharedContext, requestStartTimes, networkEvents, testCaseContext);

        let currentSettings = {
            headers: globalSettings?.headers || {},
            params: globalSettings?.params || {},
            allowed_domains: globalSettings?.allowed_domains || [],
            domain_settings: globalSettings?.domain_settings || {}
        };

        await NetworkInterceptor.setupRouteInterception(sharedContext, currentSettings, sourceDomain);
        await this.browserManager.injectInitScripts(sharedContext, browserType, device || null, emulatedAs || null);

        const tracePath = path.join(artifactsDir, 'trace.zip');
        await sharedContext.tracing.start({ screenshots: true, snapshots: true, sources: true });

        let page = await sharedContext.newPage();
        const startTime = Date.now();
        let status = 'passed';
        let error: string | null = null;
        let executionLog: any[] = [];
        let testResults: any[] = [];

        try {
            if (!testCases || testCases.length === 0) throw new Error("No test cases provided");

            for (const testCase of testCases) {
                const caseStartTime = Date.now();
                let caseStatus = 'passed';
                let caseError = null;
                let lastStepResult: any = null;
                let tempContext: BrowserContext | null = null;

                try {
                    testCaseContext.id = testCase.id;
                    testCaseContext.name = testCase.name;

                    if (testCase.settings) {
                        currentSettings.headers = testCase.settings.headers || {};
                        currentSettings.params = testCase.settings.params || {};
                        currentSettings.allowed_domains = testCase.settings.allowed_domains || [];
                        currentSettings.domain_settings = testCase.settings.domain_settings || {};
                    }

                    sourceDomain.value = null;
                    const executionMode = testCase.executionMode || 'continuous';

                    if (executionMode === 'separate') {
                        tempContext = await browser.newContext(contextOptions);
                        await this.browserManager.injectInitScripts(tempContext, browserType, device || null, emulatedAs || null);
                        await tempContext.tracing.start({ screenshots: true, snapshots: true, sources: true });
                        page = await tempContext.newPage();
                        await NetworkInterceptor.setupNetworkListeners(tempContext, requestStartTimes, networkEvents, testCaseContext);
                        await NetworkInterceptor.setupRouteInterception(tempContext, currentSettings, sourceDomain);
                    } else {
                        const pages = sharedContext.pages();
                        page = (pages.length > 0 && !pages[0].isClosed()) ? pages[0] : await sharedContext.newPage();
                        await NetworkInterceptor.setupRouteInterception(sharedContext, currentSettings, sourceDomain);
                    }

                    try {
                        const defaultTimeout = parseInt(process.env.DEFAULT_TIMEOUT || '30000');
                        page.setDefaultTimeout(defaultTimeout);
                        await page.goto('about:blank', { waitUntil: 'domcontentloaded', timeout: 5000 });
                        await page.evaluate((tn) => { (window as any).__TRACEIQ_TEST_NAME__ = tn; }, testCase.name);
                    } catch (e) { }

                    let currentContext: Page | FrameLocator = page;

                    for (const step of testCase.steps) {
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
                            const stepResponse = await TestExecutor.executeStep(page, currentContext, step, currentSettings, testCaseContext);
                            if (stepResponse && (step.type === 'http-request' || step.type === 'feed-check')) {
                                lastStepResult = stepResponse;
                            }
                        }
                    }
                } catch (e: any) {
                    caseStatus = 'failed';
                    caseError = e.message;
                    if (e.stepResult) {
                        lastStepResult = e.stepResult;
                    }
                } finally {
                    const caseEndTime = Date.now();
                    executionLog.push({ testCaseId: testCase.id, testCaseName: testCase.name, startTime: caseStartTime, endTime: caseEndTime, status: caseStatus, error: caseError });
                    testResults.push({
                        test_case_id: testCase.id,
                        test_name: testCase.name, status: caseStatus, duration_ms: caseEndTime - caseStartTime, error: caseError,
                        response_status: lastStepResult?.status, response_headers: lastStepResult?.headers, response_body: lastStepResult?.body,
                        request_headers: lastStepResult?.request?.headers, request_body: lastStepResult?.request?.body, request_url: lastStepResult?.request?.url,
                        request_method: lastStepResult?.request?.method, request_params: lastStepResult?.request?.params
                    });
                    if (tempContext) await tempContext.close();
                }
            }
        } catch (e: any) {
            status = 'failed';
            error = e.message;
        } finally {
            const duration = Date.now() - startTime;
            await sharedContext.tracing.stop({ path: tracePath });
            await sharedContext.close();

            try {
                if (fs.existsSync(artifactsDir)) {
                    const traceKey = `runs/${runId}/trace.zip`;
                    if (fs.existsSync(tracePath)) {
                        await minioClient.fPutObject(BUCKET_NAME, traceKey, tracePath);
                    }

                    const files = fs.readdirSync(artifactsDir);
                    const screenshots: string[] = [];
                    for (const file of files.filter(f => f.endsWith('.png'))) {
                        const key = `runs/${runId}/screenshots/${file}`;
                        await minioClient.fPutObject(BUCKET_NAME, key, path.join(artifactsDir, file));
                        screenshots.push(key);
                    }

                    let videoKey = null;
                    const videoFile = files.find(f => f.endsWith('.webm'));
                    if (videoFile) {
                        videoKey = `runs/${runId}/video.webm`;
                        await minioClient.fPutObject(BUCKET_NAME, videoKey, path.join(artifactsDir, videoFile));
                    }

                    fs.rmSync(artifactsDir, { recursive: true, force: true });
                }
            } catch (cleanupError) {
                console.error("Error during artifact cleanup:", cleanupError);
            }

            return {
                status, duration_ms: duration, error, trace: null, video: null, screenshots: [],
                network_events: networkEvents, execution_log: executionLog, results: testResults
            };
        }
    }
}

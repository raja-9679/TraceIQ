import { chromium, Browser, BrowserContext, Page, FrameLocator, Locator } from 'playwright';
import * as Minio from 'minio'; // Try standard import, but fallback if needed
import * as fs from 'fs';
import * as path from 'path';

// Fix for Minio import consistency
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
    private browser: Browser | null = null;

    async start() {
        this.browser = await chromium.launch({
            headless: true,
            args: ['--no-sandbox', '--disable-setuid-sandbox']
        });
    }

    async stop() {
        if (this.browser) {
            await this.browser.close();
        }
    }

    async runTest(runId: number, testCases: any[], globalSettings: any = {}): Promise<any> {
        if (!this.browser) await this.start();

        const artifactsDir = `/tmp/artifacts/${runId}`;
        fs.mkdirSync(artifactsDir, { recursive: true });

        const context = await this.browser!.newContext({
            recordVideo: { dir: artifactsDir, size: { width: 1280, height: 720 } }
        });

        let currentTestCaseId: number | null = null;
        let currentTestCaseName: string | null = null;

        let responseStatus: number | null = null;
        let requestHeaders: any = null;
        let responseHeaders: any = null;
        let networkEvents: any[] = [];
        const requestStartTimes = new Map<string, number>();

        // Listen for network events
        context.on('request', request => {
            requestStartTimes.set(request.url(), Date.now());
        });

        context.on('response', async response => {
            try {
                const request = response.request();
                const url = response.url();
                const startTime = requestStartTimes.get(url) || Date.now();
                const endTime = Date.now();
                const duration = endTime - startTime;

                // Clean up map
                requestStartTimes.delete(url);

                const headers = await response.allHeaders();
                const reqHeaders = await request.allHeaders();

                networkEvents.push({
                    testCaseId: currentTestCaseId, // Tag event with current test case
                    testCaseName: currentTestCaseName, // Tag event with current test case name
                    url: url,
                    method: request.method(),
                    status: response.status(),
                    startTime,
                    endTime,
                    duration,
                    requestHeaders: reqHeaders,
                    responseHeaders: headers
                });

                // Keep legacy fields for backward compatibility with existing UI logic
                // (Capture first successful navigation)
                if (!responseStatus && response.status() < 400 && request.resourceType() === 'document') {
                    responseStatus = response.status();
                    requestHeaders = reqHeaders;
                    responseHeaders = headers;
                }
            } catch (e) {
                console.error('Error capturing network event:', e);
            }
        });

        // Apply global headers and params
        // We now use route interception to apply headers selectively based on domain
        // These serve as defaults, but can be overridden per test case
        let currentSettings = {
            headers: globalSettings?.headers || {},
            params: globalSettings?.params || {},
            allowed_domains: globalSettings?.allowed_domains || [],
            domain_settings: globalSettings?.domain_settings || {}
        };

        let sourceDomain: string | null = null;

        // Helper to get domain from URL
        const getDomain = (url: string) => {
            try {
                return new URL(url).hostname;
            } catch {
                return '';
            }
        };

        // Intercept all requests to apply headers and params
        await context.route('**/*', async route => {
            const request = route.request();
            const urlStr = request.url();
            const hostname = getDomain(urlStr);

            // Use current dynamic settings
            const { headers: globalHeaders, params: globalParams, allowed_domains: allowedDomains, domain_settings: domainSettings } = currentSettings;

            // Determine source domain from the first navigation if not set
            if (!sourceDomain && request.isNavigationRequest()) {
                sourceDomain = hostname;
                console.log(`Inferred source domain: ${sourceDomain}`);
            }

            // DEBUG: Log matching logic for fetch/xhr
            if (request.resourceType() === 'fetch' || request.resourceType() === 'xhr') {
                console.log(`[Network] Checking headers for ${urlStr}`);
                console.log(`  Hostname: ${hostname}`);
                console.log(`  SourceDomain: ${sourceDomain}`);
                console.log(`  AllowedDomains: ${JSON.stringify(allowedDomains)}`);
                console.log(`  DomainSettings keys: ${JSON.stringify(Object.keys(domainSettings))}`);
            }

            const headers = { ...request.headers() };
            let modifiedHeaders = false;
            let modifiedUrl = false;
            let newUrl = urlStr;

            // Helper to append params
            const appendParams = (targetUrl: string, params: any) => {
                try {
                    const urlObj = new URL(targetUrl);
                    for (const [key, value] of Object.entries(params)) {
                        const strValue = String(value);
                        // Only append if this specific key-value pair doesn't already exist
                        // We check if the value is already in the list of values for this key
                        const existingValues = urlObj.searchParams.getAll(key);
                        if (!existingValues.includes(strValue)) {
                            urlObj.searchParams.append(key, strValue);
                        }
                    }
                    return urlObj.toString();
                } catch (e) {
                    return targetUrl;
                }
            };

            // 1. Check for domain-specific settings first (highest priority)
            if (domainSettings[hostname]) {
                const specificHeaders = domainSettings[hostname].headers;
                const specificParams = domainSettings[hostname].params; // Assuming params can be domain-specific too

                if (specificHeaders) {
                    Object.assign(headers, specificHeaders);
                    modifiedHeaders = true;
                    console.log(`Applied specific headers for ${hostname}`);
                }
                if (specificParams) {
                    newUrl = appendParams(newUrl, specificParams);
                    if (newUrl !== urlStr) {
                        modifiedUrl = true;
                        console.log(`Applied specific params for ${hostname}`);
                    }
                }
            }
            // 2. Check if it matches source domain or allowed domains
            // Normalize allowed domains to objects
            const normalizedAllowedDomains = allowedDomains.map((d: any) => {
                if (typeof d === 'string') return { domain: d, headers: true, params: false }; // Default legacy behavior: headers only
                return { domain: d.domain, headers: d.headers !== false, params: d.params === true };
            });

            const matchedAllowedDomain = normalizedAllowedDomains.find((d: any) =>
                hostname === d.domain || hostname.endsWith(`.${d.domain}`)
            );

            const isSourceDomain = sourceDomain && (hostname === sourceDomain || hostname.endsWith(`.${sourceDomain}`));

            if (isSourceDomain || matchedAllowedDomain) {
                const allowHeaders = isSourceDomain || matchedAllowedDomain?.headers;
                const allowParams = isSourceDomain || matchedAllowedDomain?.params;

                // Apply Global Headers
                if (allowHeaders && Object.keys(globalHeaders).length > 0) {
                    Object.assign(headers, globalHeaders);
                    modifiedHeaders = true;
                }

                // Apply Source Domain specific settings to Allowed Domains and Subdomains
                // (If we are not strictly on the source domain, which was handled in Step 1)
                if (sourceDomain && hostname !== sourceDomain && domainSettings[sourceDomain]) {
                    const sourceHeaders = domainSettings[sourceDomain].headers;
                    const sourceParams = domainSettings[sourceDomain].params;

                    if (allowHeaders && sourceHeaders) {
                        Object.assign(headers, sourceHeaders);
                        modifiedHeaders = true;
                        console.log(`Applied source domain (${sourceDomain}) headers to ${hostname}`);
                    }

                    if (allowParams && sourceParams) {
                        newUrl = appendParams(newUrl, sourceParams);
                        if (newUrl !== urlStr) {
                            modifiedUrl = true;
                            console.log(`Applied source domain (${sourceDomain}) params to ${hostname}`);
                        }
                    }
                }

                // Apply Global Params
                if (allowParams && Object.keys(globalParams).length > 0) {
                    newUrl = appendParams(newUrl, globalParams);
                    if (newUrl !== urlStr) {
                        modifiedUrl = true;
                        console.log(`Applied global params for ${hostname}`);
                    }
                }
            }

            if (modifiedHeaders || modifiedUrl) {
                // If URL changed, we must pass it. If only headers changed, we pass headers.
                const continueOptions: any = { headers };
                if (modifiedUrl) {
                    continueOptions.url = newUrl;
                }
                await route.continue(continueOptions);
            } else {
                await route.continue();
            }
        });

        // Inject mouse cursor visualization
        await context.addInitScript(() => {
            const box = document.createElement('div');
            box.classList.add('selenium-mouse-helper');
            const styleElement = document.createElement('style');
            styleElement.innerHTML = `
                .selenium-mouse-helper {
                    pointer-events: none;
                    position: absolute;
                    top: 0;
                    left: 0;
                    width: 20px;
                    height: 20px;
                    border: 1px solid white;
                    border-radius: 50%;
                    background: rgba(255, 0, 0, 0.7);
                    margin: -10px 0 0 -10px;
                    padding: 0;
                    transition: background .2s, border-radius .2s, border-color .2s;
                    box-shadow: 0 0 4px rgba(0,0,0,0.8);
                    z-index: 100000;
                }
                .selenium-mouse-helper.button-pressed {
                    background: rgba(255, 0, 0, 1);
                    transform: scale(0.9);
                }
            `;
            document.head.appendChild(styleElement);
            document.body.appendChild(box);

            document.addEventListener('mousemove', event => {
                box.style.left = event.pageX + 'px';
                box.style.top = event.pageY + 'px';
            }, true);

            document.addEventListener('mousedown', event => {
                box.classList.add('button-pressed');
            }, true);

            document.addEventListener('mouseup', event => {
                box.classList.remove('button-pressed');
            }, true);
        });

        const tracePath = path.join(artifactsDir, 'trace.zip');
        await context.tracing.start({ screenshots: true, snapshots: true, sources: true });

        const page = await context.newPage();
        const startTime = Date.now();
        let status = 'passed';
        let error: string | null = null;

        let executionLog: any[] = []; // Track execution times for each test case

        try {
            console.log(`Running test suite for runId: ${runId} with ${testCases?.length || 0} cases`);

            if (!testCases || testCases.length === 0) {
                throw new Error("No test cases provided");
            }

            for (const testCase of testCases) {
                const caseStartTime = Date.now();
                let caseStatus = 'passed';
                let caseError = null;

                currentTestCaseId = testCase.id; // Set current test case ID
                currentTestCaseName = testCase.name;
                console.log(`Executing Test Case: ${testCase.name} (ID: ${testCase.id})`);

                // Update settings for this test case if provided
                if (testCase.settings) {
                    currentSettings = {
                        headers: testCase.settings.headers || {},
                        params: testCase.settings.params || {},
                        allowed_domains: testCase.settings.allowed_domains || [],
                        domain_settings: testCase.settings.domain_settings || {}
                    };
                    console.log(`Updated settings for test case ${testCase.name}`);
                } else {
                    // Fallback to global settings if not provided (shouldn't happen with new worker logic)
                    currentSettings = {
                        headers: globalSettings?.headers || {},
                        params: globalSettings?.params || {},
                        allowed_domains: globalSettings?.allowed_domains || [],
                        domain_settings: globalSettings?.domain_settings || {}
                    };
                }

                // Reset source domain for each test case to ensure isolation in continuous mode
                sourceDomain = null;

                // Reset page state to prevent navigation interruptions from previous test cases
                try {
                    console.log('  Resetting page state (about:blank)...');
                    await page.goto('about:blank', { waitUntil: 'domcontentloaded', timeout: 5000 });
                } catch (e) {
                    console.warn(`  Warning: Failed to reset page state: ${e}`);
                }

                // Track current context (Page or Frame)
                let currentContext: Page | FrameLocator = page;

                try {
                    for (const step of testCase.steps) {
                        if (step.type === 'switch-frame') {
                            const frameSelector = step.selector || step.value;
                            if (frameSelector === 'main' || frameSelector === 'top') {
                                console.log('  Step: switch-frame to main page');
                                currentContext = page;
                            } else if (frameSelector) {
                                console.log(`  Step: switch-frame ${frameSelector}`);

                                // Enhanced Lifecycle Management for Cross-Origin Stability
                                if (step.options?.strict_lifecycle) {
                                    console.log('    [Strict Lifecycle] 1. Waiting for iframe attachment...');

                                    // 1. Wait for Attachment (Antigravity: Attachment confirms existence)
                                    // We locate the iframe element itself (not its content yet)
                                    // Note: We use 'attached' state, ignoring visibility (opacity:0 iframes are valid)
                                    const frameElement = currentContext.locator(frameSelector).first();
                                    await frameElement.waitFor({ state: 'attached', timeout: 30000 });

                                    // 2. Sync Load State (Antigravity: Load state confirms readiness)
                                    // For cross-origin iframes, existence attached != Ready to accept commands.
                                    // We must acquire the underlying Frame object to check its network idle/load state.
                                    const elementHandle = await frameElement.elementHandle();
                                    const contentFrame = await elementHandle?.contentFrame();

                                    if (contentFrame) {
                                        console.log('    [Strict Lifecycle] 2. Waiting for frame load state (domcontentloaded)...');
                                        // This is critical: waits for the SUB-RESOURCE (the iframe src) to finish loading
                                        try {
                                            await contentFrame.waitForLoadState('domcontentloaded', { timeout: 30000 });
                                        } catch (e) {
                                            console.warn(`    [Strict Lifecycle] Warning: Frame load wait warning: ${e}`);
                                        }
                                    } else {
                                        console.warn('    [Strict Lifecycle] Warning: Could not access content frame (detached or strict CSP?)');
                                    }
                                }

                                // 3. Set Context (Antigravity: Interaction via frameLocator)
                                // We switch to the isolated context for subsequent steps
                                currentContext = currentContext.frameLocator(frameSelector);
                            }
                        } else {
                            const stepResponse = await this.executeStep(page, currentContext, step, globalSettings);
                            // Legacy capture logic moved to event listener, but we keep this just in case
                            // actually we can remove the legacy capture from here since the event listener handles it better
                        }
                    }
                } catch (e: any) {
                    caseStatus = 'failed';
                    caseError = e.message;
                    throw e; // Re-throw to fail the run? Or continue? 
                    // For continuous mode, we might want to continue? 
                    // But the outer catch block catches it and sets global status to failed.
                    // Let's re-throw for now to maintain existing behavior, but log the case result first.
                } finally {
                    const caseEndTime = Date.now();
                    executionLog.push({
                        testCaseId: testCase.id,
                        testCaseName: testCase.name,
                        startTime: caseStartTime,
                        endTime: caseEndTime,
                        status: caseStatus,
                        error: caseError
                    });
                }
            }

        } catch (e: any) {
            status = 'failed';
            error = e.message;
            console.error(`Test failed: ${e}`);
        } finally {
            const duration = Date.now() - startTime;
            await context.tracing.stop({ path: tracePath });
            await context.close(); // Saves video

            // Upload artifacts
            const traceKey = `runs/${runId}/trace.zip`;
            await minioClient.fPutObject(BUCKET_NAME, traceKey, tracePath);

            let videoKey = null;
            const files = fs.readdirSync(artifactsDir);
            const videoFile = files.find(f => f.endsWith('.webm'));
            if (videoFile) {
                const videoPath = path.join(artifactsDir, videoFile);
                videoKey = `runs/${runId}/video.webm`;
                await minioClient.fPutObject(BUCKET_NAME, videoKey, videoPath);
            }

            // Cleanup
            fs.rmSync(artifactsDir, { recursive: true, force: true });

            return {
                status,
                duration_ms: duration,
                error,
                trace: traceKey,
                video: videoKey,
                response_status: responseStatus,
                request_headers: requestHeaders,
                response_headers: responseHeaders,
                network_events: networkEvents,
                execution_log: executionLog
            };
        }
    }

    private async executeStep(page: Page, context: Page | FrameLocator, step: any, globalSettings: any = {}) {
        console.log(`  Step: ${step.type} ${step.selector || ''} ${step.value || ''}`);

        // Helper to simulate mouse movement (only works reliably on main Page for now)
        const moveMouseTo = async (locator: Locator) => {
            try {
                await locator.hover();
            } catch (e) {
                // ignore
            }
        };

        const getLocator = (selector: string) => {
            return context.locator(selector).first();
        }

        switch (step.type) {
            case 'goto':
                let url = step.value || step.selector || 'about:blank';

                // Append global params if present
                if (globalSettings?.params && Object.keys(globalSettings.params).length > 0) {
                    try {
                        const urlObj = new URL(url);
                        for (const [key, value] of Object.entries(globalSettings.params)) {
                            urlObj.searchParams.append(key, String(value));
                        }
                        url = urlObj.toString();
                        console.log(`  Modified URL with params: ${url}`);
                    } catch (e) {
                        console.warn(`  Could not append params to URL ${url}: ${e}`);
                    }
                }

                // goto is always on page
                // Retry logic for goto
                let attempts = 0;
                const maxAttempts = 3;
                while (attempts < maxAttempts) {
                    try {
                        return await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 80000 });
                    } catch (e: any) {
                        attempts++;
                        console.warn(`  goto failed (attempt ${attempts}/${maxAttempts}): ${e.message}`);
                        if (attempts >= maxAttempts) throw e;
                        await page.waitForTimeout(1000);
                    }
                }
                break;
            case 'click':
                const clickSelector = step.selector || step.value;
                if (clickSelector) {
                    const locator = getLocator(clickSelector);
                    await moveMouseTo(locator);
                    await locator.click();
                }
                break;
            case 'fill':
                if (step.selector) {
                    const locator = getLocator(step.selector);
                    await moveMouseTo(locator);
                    await locator.fill(step.value || '');
                }
                break;
            case 'check':
                const checkSelector = step.selector || step.value;
                if (checkSelector) {
                    const locator = getLocator(checkSelector);
                    await moveMouseTo(locator);
                    await locator.check();
                }
                break;
            case 'expect-visible':
                const visibleSelector = step.selector || step.value;
                if (visibleSelector) {
                    console.log(`Waiting for selector: ${visibleSelector} to be visible...`);
                    // Smart wait: use Page.waitForSelector if available to find ANY visible element
                    // Check if 'waitForSelector' exists on the context object (it exists on Page, not FrameLocator)
                    if ('waitForSelector' in context) {
                        await (context as Page).waitForSelector(visibleSelector, { state: 'visible', timeout: 80000 });
                    } else {
                        // FrameLocator doesn't have waitForSelector, so we rely on locator.waitFor
                        await getLocator(visibleSelector).waitFor({ state: 'visible', timeout: 50000 });
                    }
                }
                break;
            case 'expect-hidden':
                const hiddenSelector = step.selector || step.value;
                if (hiddenSelector) {
                    console.log(`Waiting for selector: ${hiddenSelector} to be hidden...`);
                    // Use waitForSelector if available (Page), otherwise locator
                    if ('waitForSelector' in context) {
                        await (context as Page).waitForSelector(hiddenSelector, { state: 'hidden', timeout: 50000 });
                    } else {
                        await getLocator(hiddenSelector).waitFor({ state: 'hidden', timeout: 50000 });
                    }
                }
                break;
            case 'expect-text':
                if (step.selector && step.value) {
                    const locator = getLocator(step.selector);
                    await locator.waitFor({ state: 'visible', timeout: 50000 });
                    const text = await locator.textContent();
                    if (!text?.includes(step.value)) {
                        throw new Error(`Expected text "${step.value}" not found in element "${step.selector}"`);
                    }
                }
                break;
            case 'expect-url':
                const expectedUrl = step.value || step.selector;
                if (expectedUrl) {
                    await page.waitForURL(expectedUrl, { timeout: 15000 });
                }
                break;
            case 'hover':
                const hoverSelector = step.selector || step.value;
                if (hoverSelector) {
                    const locator = getLocator(hoverSelector);
                    await locator.hover();
                }
                break;
            case 'select-option':
                if (step.selector && step.value) {
                    const locator = getLocator(step.selector);
                    await moveMouseTo(locator);
                    await locator.selectOption(step.value);
                }
                break;
            case 'press-key':
                const key = step.value || step.selector;
                if (key) {
                    await page.keyboard.press(key);
                }
                break;
            case 'screenshot':
                const screenshotName = step.value || `screenshot-${Date.now()}`;
                const videoPath = await page.video()?.path();
                const screenshotPath = path.join(videoPath ? path.dirname(videoPath) : '/tmp', `${screenshotName}.png`);
                await page.screenshot({ path: screenshotPath, fullPage: true });
                console.log(`Screenshot saved to: ${screenshotPath}`);
                break;
            case 'scroll-to':
                const scrollSelector = step.selector || step.value;
                if (scrollSelector) {
                    const locator = getLocator(scrollSelector);
                    await locator.scrollIntoViewIfNeeded();
                }
                break;
            case 'wait-timeout':
                const timeout = parseInt(step.value || step.selector || '1000');
                await page.waitForTimeout(timeout);
                break;
            default:
                console.warn(`Unknown step type: ${step.type}`);
        }
    }
}

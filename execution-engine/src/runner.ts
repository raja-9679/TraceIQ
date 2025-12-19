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

    async runTest(runId: number, testCases: any[]): Promise<any> {
        if (!this.browser) await this.start();

        const artifactsDir = `/tmp/artifacts/${runId}`;
        fs.mkdirSync(artifactsDir, { recursive: true });

        const context = await this.browser!.newContext({
            recordVideo: { dir: artifactsDir, size: { width: 1280, height: 720 } }
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
        let error = null;

        let responseStatus = null;
        let requestHeaders = null;
        let responseHeaders = null;

        try {
            console.log(`Running test suite for runId: ${runId} with ${testCases?.length || 0} cases`);

            if (!testCases || testCases.length === 0) {
                throw new Error("No test cases provided");
            }

            for (const testCase of testCases) {
                console.log(`Executing Test Case: ${testCase.name}`);

                // Track current context (Page or Frame)
                let currentContext: Page | FrameLocator = page;

                for (const step of testCase.steps) {
                    if (step.type === 'switch-frame') {
                        const frameSelector = step.selector || step.value;
                        if (frameSelector === 'main' || frameSelector === 'top') {
                            console.log('  Step: switch-frame to main page');
                            currentContext = page;
                        } else if (frameSelector) {
                            console.log(`  Step: switch-frame ${frameSelector}`);
                            // Switch context to frame
                            currentContext = page.frameLocator(frameSelector);
                        }
                    } else {
                        await this.executeStep(page, currentContext, step);
                    }
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
                response_headers: responseHeaders
            };
        }
    }

    private async executeStep(page: Page, context: Page | FrameLocator, step: any) {
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
                const url = step.value || step.selector || 'about:blank';
                // goto is always on page
                await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 80000 });
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
                        await (context as Page).waitForSelector(visibleSelector, { state: 'visible', timeout: 50000 });
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

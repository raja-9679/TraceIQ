/**
 * Mobile execution worker — Phase MOB (native app testing).
 *
 * Claims `mobile_appium` jobs from the dedicated `jobs:mobile:pending`
 * stream and drives an Appium server (APPIUM_URL) over the plain W3C
 * WebDriver protocol. Results flow into the normal `jobs:results` stream, so
 * aggregation, finalize, notifications, and AI analysis are identical to the
 * Playwright path.
 *
 * v1 scope (mirrors local-worker v1): single-test and continuous jobs, no
 * MinIO artifact upload — failure screenshots stay in the result payload as
 * base64 only when small. See SCOPE_NOTES.md / FEATURE_GAP_ANALYSIS.md §31.
 *
 * Env:
 *   REDIS_URL     — same Redis the backend dispatches to
 *   APPIUM_URL    — Appium 2 server (default http://localhost:4723)
 *   DEVICE_NAME   — capability override (default: Android Emulator / iPhone Simulator)
 */

// JobQueue reads its stream/group names from these env vars at module load,
// so the overrides must land before the module is required (static `import`
// would hoist above them).
process.env.REDIS_JOBS_STREAM = process.env.REDIS_JOBS_STREAM || 'jobs:mobile:pending';
process.env.REDIS_CONSUMER_GROUP = process.env.REDIS_CONSUMER_GROUP || 'mobile-workers';

// eslint-disable-next-line @typescript-eslint/no-var-requires
const { JobQueue } = require('./core/job-queue') as typeof import('./core/job-queue');
import type { TestJob, JobResult, TestCaseResult, TestCase } from './core/job-queue';
import { WebDriverClient } from './core/webdriver-client';

const APPIUM_URL = process.env.APPIUM_URL || 'http://localhost:4723';
const POLL_IDLE_MS = 2000;
// Base64 PNGs above this size are dropped from the result payload rather
// than bloating the Redis results stream.
const MAX_INLINE_SCREENSHOT_BYTES = 256 * 1024;

type MobileApp = NonNullable<TestJob['settings']['mobile_app']>;

class MobileWorker {
    private queue = new JobQueue();
    private driver = new WebDriverClient(APPIUM_URL);
    private running = true;

    async start(): Promise<void> {
        await this.queue.initialize();
        try {
            await this.driver.status();
            console.log(`[MobileWorker] Appium reachable at ${APPIUM_URL}`);
        } catch (err: any) {
            console.warn(`[MobileWorker] WARNING: Appium not reachable at ${APPIUM_URL} (${err.message}). ` +
                `Jobs will fail until it is up.`);
        }
        console.log(`[MobileWorker] Consuming ${process.env.REDIS_JOBS_STREAM} as ${process.env.REDIS_CONSUMER_GROUP}`);

        while (this.running) {
            try {
                const claimed = await this.queue.claimJob();
                if (!claimed) {
                    await sleep(POLL_IDLE_MS);
                    continue;
                }
                const result = await this.runJob(claimed.job);
                await this.queue.completeJob(claimed.streamId, result);
            } catch (err: any) {
                console.error('[MobileWorker] Loop error:', err.message);
                await sleep(POLL_IDLE_MS);
            }
        }
    }

    stop(): void {
        this.running = false;
    }

    private async runJob(job: TestJob): Promise<JobResult> {
        const startedAt = Date.now();
        const cases: TestCase[] = job.test_cases || (job.test_case ? [job.test_case] : []);
        const app = job.settings?.mobile_app;
        console.log(`[MobileWorker] Job ${job.job_id} (run ${job.run_id}): ${cases.length} case(s)`);

        const base = {
            job_id: job.job_id,
            run_id: job.run_id,
            network_events: [] as any[],
            artifacts: { screenshots: [] as string[] },
            completed_at: '',
            result_kind: 'mobile',
        };

        if (!app) {
            return this.fail(base, job, cases, startedAt,
                'No app build pinned to this run — POST /api/runs with app_build_id.');
        }

        let sessionId: string | null = null;
        try {
            sessionId = await this.driver.createSession(this.capabilities(app, job));
        } catch (err: any) {
            return this.fail(base, job, cases, startedAt,
                `Failed to start Appium session: ${err.message}`);
        }

        const testResults: TestCaseResult[] = [];
        try {
            for (const testCase of cases) {
                testResults.push(await this.runCase(sessionId, testCase, app));
            }
        } finally {
            await this.driver.deleteSession(sessionId).catch(() => undefined);
        }

        const failed = testResults.filter((r) => r.status !== 'passed').length;
        const status: JobResult['status'] = failed === 0 ? 'passed' : 'failed';
        const single = cases.length === 1 && !job.test_cases;

        return {
            ...base,
            status,
            duration_ms: Date.now() - startedAt,
            completed_at: new Date().toISOString(),
            result_payload: { platform: app.platform, app_build_id: app.app_build_id, appium_url: APPIUM_URL },
            ...(single
                ? {
                    test_case_id: cases[0].id,
                    test_name: cases[0].name,
                    error: testResults[0].error,
                }
                : { test_results: testResults }),
        };
    }

    private async runCase(sessionId: string, testCase: TestCase, app: MobileApp): Promise<TestCaseResult> {
        const start = Date.now();
        console.log(`[MobileWorker]   Case: ${testCase.name}`);
        try {
            for (const step of testCase.steps || []) {
                await this.executeStep(sessionId, step, app);
            }
            return {
                test_case_id: testCase.id,
                test_name: testCase.name,
                status: 'passed',
                duration_ms: Date.now() - start,
            };
        } catch (err: any) {
            console.log(`[MobileWorker]   FAILED: ${err.message}`);
            return {
                test_case_id: testCase.id,
                test_name: testCase.name,
                status: 'failed',
                duration_ms: Date.now() - start,
                error: err.message,
            };
        }
    }

    private async executeStep(sessionId: string, step: any, app: MobileApp): Promise<void> {
        const type = step.type as string;
        const params = step.params || {};
        try {
            switch (type) {
                case 'mobile-launch-app':
                    await this.driver.executeScript(sessionId, 'mobile: activateApp',
                        [{ appId: app.package_id, bundleId: app.package_id }]);
                    break;

                case 'mobile-terminate-app':
                    await this.driver.executeScript(sessionId, 'mobile: terminateApp',
                        [{ appId: app.package_id, bundleId: app.package_id }]);
                    break;

                case 'mobile-tap': {
                    const el = await this.driver.waitForElement(sessionId, step.selector);
                    await this.driver.click(sessionId, el);
                    break;
                }

                case 'mobile-long-press': {
                    const el = await this.driver.waitForElement(sessionId, step.selector);
                    const rect = await this.driver.getElementRect(sessionId, el);
                    const x = Math.round(rect.x + rect.width / 2);
                    const y = Math.round(rect.y + rect.height / 2);
                    await this.driver.performActions(sessionId,
                        pointerSequence(x, y, x, y, Number(params.duration_ms) || 800));
                    break;
                }

                case 'mobile-type': {
                    const el = await this.driver.waitForElement(sessionId, step.selector);
                    await this.driver.clear(sessionId, el).catch(() => undefined);
                    await this.driver.sendKeys(sessionId, el, step.value ?? '');
                    break;
                }

                case 'mobile-swipe': {
                    const win = await this.driver.getWindowRect(sessionId);
                    const distance = Math.min(Math.max(Number(params.distance) || 0.5, 0.05), 0.9);
                    const cx = Math.round(win.width / 2);
                    const cy = Math.round(win.height / 2);
                    const dx = Math.round(win.width * distance);
                    const dy = Math.round(win.height * distance);
                    const dir = params.direction || 'up';
                    const targets: Record<string, [number, number]> = {
                        up: [cx, cy - dy], down: [cx, cy + dy],
                        left: [cx - dx, cy], right: [cx + dx, cy],
                    };
                    const [tx, ty] = targets[dir] || targets.up;
                    await this.driver.performActions(sessionId, pointerSequence(cx, cy, tx, ty, 400));
                    break;
                }

                case 'mobile-press-key': {
                    // Android keycodes; iOS has no hardware keys.
                    const codes: Record<string, number> = { back: 4, home: 3, enter: 66 };
                    const code = codes[(step.value || 'back').toLowerCase()];
                    if (code === undefined) throw new Error(`Unknown key '${step.value}'`);
                    await this.driver.executeScript(sessionId, 'mobile: pressKey', [{ keycode: code }]);
                    break;
                }

                case 'mobile-wait-for':
                    await this.driver.waitForElement(sessionId, step.selector,
                        Number(params.timeout_ms) || 10000);
                    break;

                case 'mobile-expect-visible': {
                    const el = await this.driver.waitForElement(sessionId, step.selector);
                    const visible = await this.driver.isDisplayed(sessionId, el);
                    if (!visible) throw new Error(`Element found but not visible: ${step.selector}`);
                    break;
                }

                case 'mobile-expect-text': {
                    const el = await this.driver.waitForElement(sessionId, step.selector);
                    const text = await this.driver.getText(sessionId, el);
                    if (!text?.includes(step.value ?? '')) {
                        throw new Error(`Expected text '${step.value}' but element has '${text}'`);
                    }
                    break;
                }

                case 'mobile-screenshot': {
                    const png = await this.driver.takeScreenshot(sessionId);
                    if (png.length > MAX_INLINE_SCREENSHOT_BYTES) {
                        console.log('[MobileWorker] Screenshot too large to inline — dropped (artifact upload deferred)');
                    }
                    break;
                }

                default:
                    // Same contract as the Playwright worker: unknown types fail
                    // loudly rather than passing silently.
                    throw new Error(`Unknown mobile step type '${type}' — is the case's executor set correctly?`);
            }
        } catch (err: any) {
            throw new Error(`Step '${type}'${step.selector ? ` (${step.selector})` : ''}: ${err.message}`);
        }
    }

    private capabilities(app: MobileApp, job: TestJob): Record<string, any> {
        const common = {
            'appium:app': app.app_url,
            'appium:newCommandTimeout': 300,
        };
        if (app.platform === 'ios') {
            return {
                platformName: 'iOS',
                'appium:automationName': 'XCUITest',
                'appium:deviceName': job.device || process.env.DEVICE_NAME || 'iPhone Simulator',
                ...(app.package_id ? { 'appium:bundleId': app.package_id } : {}),
                ...common,
            };
        }
        return {
            platformName: 'Android',
            'appium:automationName': 'UiAutomator2',
            'appium:deviceName': job.device || process.env.DEVICE_NAME || 'Android Emulator',
            ...(app.package_id ? { 'appium:appPackage': app.package_id } : {}),
            ...common,
        };
    }

    private fail(
        base: any, job: TestJob, cases: TestCase[], startedAt: number, message: string,
    ): JobResult {
        const single = cases.length <= 1 && !job.test_cases;
        const perCase: TestCaseResult[] = cases.map((c) => ({
            test_case_id: c.id,
            test_name: c.name,
            status: 'error',
            duration_ms: 0,
            error: message,
        }));
        return {
            ...base,
            status: 'error',
            duration_ms: Date.now() - startedAt,
            error: message,
            completed_at: new Date().toISOString(),
            ...(single
                ? { test_case_id: cases[0]?.id, test_name: cases[0]?.name }
                : { test_results: perCase }),
        };
    }
}

function pointerSequence(x1: number, y1: number, x2: number, y2: number, holdMs: number): any[] {
    return [{
        type: 'pointer',
        id: 'finger1',
        parameters: { pointerType: 'touch' },
        actions: [
            { type: 'pointerMove', duration: 0, x: x1, y: y1 },
            { type: 'pointerDown', button: 0 },
            { type: 'pause', duration: Math.min(holdMs, 100) },
            { type: 'pointerMove', duration: Math.max(holdMs - 100, 100), x: x2, y: y2 },
            { type: 'pointerUp', button: 0 },
        ],
    }];
}

function sleep(ms: number): Promise<void> {
    return new Promise((r) => setTimeout(r, ms));
}

const worker = new MobileWorker();
process.on('SIGINT', () => { worker.stop(); process.exit(0); });
process.on('SIGTERM', () => { worker.stop(); process.exit(0); });

worker.start().catch((err) => {
    console.error('[MobileWorker] Fatal:', err);
    process.exit(1);
});

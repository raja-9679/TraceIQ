/**
 * Mobile execution worker — Phase MOB (native app testing).
 *
 * Claims `mobile_appium` jobs from the dedicated `jobs:mobile:pending`
 * stream and drives an Appium server (APPIUM_URL) over the plain W3C
 * WebDriver protocol. Results flow into the normal `jobs:results` stream, so
 * aggregation, finalize, notifications, and AI analysis are identical to the
 * Playwright path.
 *
 * Artifacts: screenshots (explicit `mobile-screenshot` steps, automatic
 * on-failure captures, visual-match candidates/diffs) and a best-effort MP4
 * screen recording per job upload to MinIO under the same `runs/{run_id}/…`
 * key layout the Playwright worker uses. Steps interpolate `{{env.X}}`,
 * `{{secret.X}}`, `{{data.X}}`, `{{fake.KIND}}`, and `{{name}}` runtime
 * variables fed by `mobile-extract-value`. `mobile-expect-visual-match`
 * reuses the pixelmatch pipeline against baselines keyed browser='mobile'.
 *
 * MOB-5 selector heal: locator-shaped failures ask the LLM for a replacement
 * against the Appium XML page source; suggestions ride `heal_suggestions` in
 * the job result (persisted as pending SelectorHealProposal rows by the
 * backend, exactly like web). RUNTIME_HEAL_ENABLED=true additionally retries
 * the step in place when the healed locator matches exactly one element.
 *
 *
 * MOB-4 device clouds: MOBILE_DEVICE_PROVIDER = local (default) |
 * browserstack | saucelabs | lambdatest routes sessions to a cloud hub —
 * binaries are pushed to the cloud's app storage automatically (cached per
 * build). iOS requires a cloud provider. See src/device-cloud.ts.
 *
 * Env:
 *   REDIS_URL                — same Redis the backend dispatches to
 *   APPIUM_URL               — local Appium 2 server (default http://localhost:4723)
 *   MOBILE_DEVICE_PROVIDER   — local | browserstack | saucelabs | lambdatest
 *   BROWSERSTACK_USERNAME/ACCESS_KEY, SAUCE_USERNAME/ACCESS_KEY (+SAUCE_REGION),
 *   LT_USERNAME/LT_ACCESS_KEY — cloud credentials per provider
 *   MOBILE_DEVICE_NAME       — device capability (e.g. "Google Pixel 8", "iPhone 15")
 *   MOBILE_PLATFORM_VERSION  — OS version capability (e.g. "14")
 *   MINIO_*                  — artifact store (same vars as the Playwright worker)
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
import { resolveTemplates, TemplateContext } from './core/interpolate';
import { AIEngine } from './ai';
import { provider as llmProvider } from './llm-provider';
import { pickDeviceProvider, DeviceCloudProvider } from './device-cloud';
import * as Minio from 'minio';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

const MinioClient = (Minio as any).Client || Minio;
const BUCKET_NAME = process.env.MINIO_BUCKET_NAME || 'test-artifacts';
const POLL_IDLE_MS = 2000;
// Locator-shaped failures eligible for AI heal (mirrors the web worker's
// SELECTOR_FAILURE_RE, matching this worker's own error wording).
const MOBILE_SELECTOR_FAILURE_RE = /element not found|no such element|timed out waiting for/i;

type MobileApp = NonNullable<TestJob['settings']['mobile_app']>;

/** Screenshot captured during a job, uploaded to MinIO after the last case. */
interface PendingScreenshot {
    label: string;
    png: Buffer;
}

/** Per-case execution context threaded through steps (ids for baseline
 * lookups, the shared runtime-variables map fed by mobile-extract-value). */
interface StepExecMeta {
    testCaseId?: number;
    device?: string | null;
    runId: number;
    variables: Record<string, any>;
}

class MobileWorker {
    private queue = new JobQueue();
    // MOB-4: local Appium or a device cloud (MOBILE_DEVICE_PROVIDER) — same
    // WebDriver protocol either way. Misconfiguration throws at startup.
    private provider: DeviceCloudProvider = pickDeviceProvider();
    private driver = new WebDriverClient(this.provider.webdriverUrl, this.provider.authHeader);
    private aiEngine = new AIEngine();
    private running = true;
    private minio = new MinioClient({
        endPoint: process.env.MINIO_ENDPOINT || 'localhost',
        port: parseInt(process.env.MINIO_PORT || '9000'),
        useSSL: process.env.MINIO_USE_SSL === 'true',
        accessKey: process.env.MINIO_ACCESS_KEY || 'minioadmin',
        secretKey: process.env.MINIO_SECRET_KEY || 'minioadmin',
    });

    async start(): Promise<void> {
        await this.queue.initialize();
        try {
            await this.driver.status();
            console.log(`[MobileWorker] WebDriver hub reachable: ${this.provider.name} (${this.provider.webdriverUrl})`);
        } catch (err: any) {
            console.warn(`[MobileWorker] WARNING: ${this.provider.name} hub not reachable at ` +
                `${this.provider.webdriverUrl} (${err.message}). Jobs will fail until it is up.`);
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
            artifacts: { screenshots: [] as string[] } as { video?: string; screenshots: string[] },
            completed_at: '',
            result_kind: 'mobile',
        };

        if (!app) {
            return this.fail(base, job, cases, startedAt,
                'No app build pinned to this run — POST /api/runs with app_build_id.');
        }

        // MOB-4: deliver the binary to wherever the session runs — the MinIO
        // URL directly for local Appium, or the cloud's app storage
        // (bs://… / storage:… / lt://…) for a device-cloud provider.
        let appTarget: string;
        try {
            appTarget = await this.provider.resolveApp(app);
        } catch (err: any) {
            return this.fail(base, job, cases, startedAt,
                `Failed to deliver app build to ${this.provider.name}: ${err.message}`);
        }

        let sessionId: string | null = null;
        try {
            const caps = this.provider.decorateCapabilities(
                this.capabilities(app, job, appTarget), app);
            sessionId = await this.driver.createSession(caps);
        } catch (err: any) {
            return this.fail(base, job, cases, startedAt,
                `Failed to start Appium session: ${err.message}`);
        }

        const shots: PendingScreenshot[] = [];
        const heals: any[] = [];
        const testResults: TestCaseResult[] = [];
        let videoBuf: Buffer | null = null;

        // Screen recording is best-effort: emulators and clouds support it,
        // some targets don't — never fail the job over it.
        let recording = false;
        try {
            await this.driver.startScreenRecording(sessionId);
            recording = true;
        } catch (err: any) {
            console.log(`[MobileWorker] Screen recording unavailable: ${err.message}`);
        }

        try {
            for (const testCase of cases) {
                testResults.push(await this.runCase(sessionId, testCase, app, job, shots, heals));
            }
        } finally {
            if (recording) {
                try {
                    const b64 = await this.driver.stopScreenRecording(sessionId);
                    if (b64) videoBuf = Buffer.from(b64, 'base64');
                } catch (err: any) {
                    console.log(`[MobileWorker] Failed to stop screen recording: ${err.message}`);
                }
            }
            await this.driver.deleteSession(sessionId).catch(() => undefined);
            this.aiEngine.clearRunState(job.run_id);
        }

        base.artifacts.screenshots = await this.uploadScreenshots(job, shots);
        const videoKey = await this.uploadVideo(job, videoBuf);
        if (videoKey) base.artifacts.video = videoKey;

        const failed = testResults.filter((r) => r.status !== 'passed').length;
        const status: JobResult['status'] = failed === 0 ? 'passed' : 'failed';
        const single = cases.length === 1 && !job.test_cases;

        return {
            ...base,
            status,
            duration_ms: Date.now() - startedAt,
            completed_at: new Date().toISOString(),
            result_payload: {
                platform: app.platform,
                app_build_id: app.app_build_id,
                device_provider: this.provider.name,
                appium_url: this.provider.webdriverUrl,
            },
            ...(heals.length ? { heal_suggestions: heals } : {}),
            ...(single
                ? {
                    test_case_id: cases[0].id,
                    test_name: cases[0].name,
                    error: testResults[0].error,
                }
                : { test_results: testResults }),
        };
    }

    private async runCase(
        sessionId: string, testCase: TestCase, app: MobileApp, job: TestJob,
        shots: PendingScreenshot[], heals: any[],
    ): Promise<TestCaseResult> {
        const start = Date.now();
        console.log(`[MobileWorker]   Case: ${testCase.name}`);
        // {{env.X}} / {{secret.X}} from job settings; {{data.X}} from this
        // case's dataset row; bare {{name}} from mobile-extract-value steps
        // earlier in the same case (the map is shared by reference).
        const variables: Record<string, any> = {};
        const ctx: TemplateContext = {
            envVars: job.settings?.environment?.variables,
            secrets: job.settings?.secrets,
            dataRow: testCase.data_row,
            variables,
        };
        const meta: StepExecMeta = {
            testCaseId: testCase.id,
            device: job.device || null,
            runId: job.run_id,
            variables,
        };
        try {
            for (const step of testCase.steps || []) {
                try {
                    await this.executeStep(sessionId, step, app, ctx, shots, meta);
                } catch (stepErr: any) {
                    const recovered = await this.tryHeal(
                        sessionId, step, testCase, job, app, ctx, shots, heals, stepErr, meta);
                    if (!recovered) throw stepErr;
                }
            }
            return {
                test_case_id: testCase.id,
                test_name: testCase.name,
                status: 'passed',
                duration_ms: Date.now() - start,
            };
        } catch (err: any) {
            console.log(`[MobileWorker]   FAILED: ${err.message}`);
            // Failure screenshot — same convention as the web worker's
            // failure.png. Best-effort: the session may already be dead.
            try {
                const png = await this.driver.takeScreenshot(sessionId);
                shots.push({ label: `failure-${testCase.id}`, png: Buffer.from(png, 'base64') });
            } catch { /* session gone — nothing to capture */ }
            return {
                test_case_id: testCase.id,
                test_name: testCase.name,
                status: 'failed',
                duration_ms: Date.now() - start,
                error: err.message,
            };
        }
    }

    private async executeStep(
        sessionId: string, step: any, app: MobileApp, ctx: TemplateContext,
        shots: PendingScreenshot[], meta: StepExecMeta,
    ): Promise<void> {
        const type = step.type as string;
        const selector = resolveTemplates(step.selector, ctx);
        const value = resolveTemplates(step.value, ctx);
        const params = resolveTemplates(step.params || {}, ctx);
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
                    const el = await this.driver.waitForElement(sessionId, selector);
                    await this.driver.click(sessionId, el);
                    break;
                }

                case 'mobile-long-press': {
                    const el = await this.driver.waitForElement(sessionId, selector);
                    const rect = await this.driver.getElementRect(sessionId, el);
                    const x = Math.round(rect.x + rect.width / 2);
                    const y = Math.round(rect.y + rect.height / 2);
                    await this.driver.performActions(sessionId,
                        pointerSequence(x, y, x, y, Number(params.duration_ms) || 800));
                    break;
                }

                case 'mobile-type': {
                    const el = await this.driver.waitForElement(sessionId, selector);
                    await this.driver.clear(sessionId, el).catch(() => undefined);
                    await this.driver.sendKeys(sessionId, el, value ?? '');
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
                    const code = codes[(value || 'back').toLowerCase()];
                    if (code === undefined) throw new Error(`Unknown key '${value}'`);
                    await this.driver.executeScript(sessionId, 'mobile: pressKey', [{ keycode: code }]);
                    break;
                }

                case 'mobile-wait-for':
                    await this.driver.waitForElement(sessionId, selector,
                        Number(params.timeout_ms) || 10000);
                    break;

                case 'mobile-expect-visible': {
                    const el = await this.driver.waitForElement(sessionId, selector);
                    const visible = await this.driver.isDisplayed(sessionId, el);
                    if (!visible) throw new Error(`Element found but not visible: ${selector}`);
                    break;
                }

                case 'mobile-expect-text': {
                    const el = await this.driver.waitForElement(sessionId, selector);
                    const text = await this.driver.getText(sessionId, el);
                    if (!text?.includes(value ?? '')) {
                        throw new Error(`Expected text '${value}' but element has '${text}'`);
                    }
                    break;
                }

                case 'mobile-screenshot': {
                    const png = await this.driver.takeScreenshot(sessionId);
                    shots.push({ label: value || 'screenshot', png: Buffer.from(png, 'base64') });
                    break;
                }

                case 'mobile-extract-value': {
                    // Element text → {{varName}} for later steps in this case
                    // (mirrors the web extract-value contract: value = name).
                    const varName = value || params.variableName;
                    if (!varName) throw new Error("mobile-extract-value needs a variable name in 'value'");
                    const el = await this.driver.waitForElement(sessionId, selector);
                    const text = await this.driver.getText(sessionId, el);
                    meta.variables[varName] = text;
                    console.log(`[MobileWorker] Extracted "${text}" -> {{${varName}}}`);
                    break;
                }

                case 'mobile-expect-visual-match': {
                    // Same workflow as the web expect-visual-match, on the
                    // device screen: capture → resolve baseline (browser key
                    // 'mobile') → pixelmatch → fail over tolerance. The
                    // candidate always becomes an artifact so the promote
                    // workflow can pin it as the baseline; degraded modes
                    // (no baseline / diff lib missing) are capture-only.
                    const stepId = step.id || `visual-${Date.now()}`;
                    const png = await this.driver.takeScreenshot(sessionId);
                    const candidate = Buffer.from(png, 'base64');
                    shots.push({ label: `visual-${stepId}`, png: candidate });
                    const candidatePath = path.join(os.tmpdir(), `mobile-visual-${stepId}-${Date.now()}.png`);
                    try {
                        fs.writeFileSync(candidatePath, candidate);
                        // eslint-disable-next-line @typescript-eslint/no-var-requires
                        const { compareScreenshots } = require('./visual-diff');
                        // eslint-disable-next-line @typescript-eslint/no-var-requires
                        const { resolveBaseline, fetchImageBytes } = require('./baseline-client');
                        const baseline = await resolveBaseline({
                            testCaseId: meta.testCaseId,
                            stepId,
                            browser: 'mobile',
                            device: meta.device || app.platform,
                        });
                        if (!baseline) {
                            console.log(`[MobileWorker] visual-match: no baseline for step ${stepId} — capture-only`);
                            break;
                        }
                        const baselineBytes = await fetchImageBytes(baseline.image_url);
                        const result = await compareScreenshots({
                            candidatePath,
                            baselineBytes,
                            tolerance: baseline.tolerance ?? 0.01,
                            maskRegions: baseline.mask_regions || [],
                        });
                        if (result.diffImagePath && fs.existsSync(result.diffImagePath)) {
                            shots.push({ label: `visual-diff-${stepId}`, png: fs.readFileSync(result.diffImagePath) });
                            fs.unlinkSync(result.diffImagePath);
                        }
                        console.log(`[MobileWorker] visual-match step=${stepId} diffRatio=${result.diffRatio.toFixed(4)} passed=${result.passed}`);
                        if (!result.passed) {
                            throw new Error(`Visual regression: diffRatio=${result.diffRatio.toFixed(4)} > tolerance=${baseline.tolerance ?? 0.01}`);
                        }
                    } catch (err: any) {
                        if (err?.message?.startsWith('Visual regression')) throw err;
                        console.log(`[MobileWorker] visual-match degraded to capture-only: ${err?.message}`);
                    } finally {
                        try { fs.unlinkSync(candidatePath); } catch { /* already gone */ }
                    }
                    break;
                }

                default:
                    // Same contract as the Playwright worker: unknown types fail
                    // loudly rather than passing silently.
                    throw new Error(`Unknown mobile step type '${type}' — is the case's executor set correctly?`);
            }
        } catch (err: any) {
            throw new Error(`Step '${type}'${selector ? ` (${selector})` : ''}: ${err.message}`);
        }
    }

    /**
     * MOB-5 selector heal — the mobile analogue of the web worker's
     * maybeProposeHeal + tryRuntimeHeal, on Appium XML page source instead
     * of DOM. Always records an old→new suggestion (the backend persists it
     * as a pending SelectorHealProposal; accepting rewrites the stored
     * step). With RUNTIME_HEAL_ENABLED=true and the healed locator matching
     * EXACTLY one element, the step is retried in place so the run recovers.
     * Returns true iff the step passed on retry.
     */
    private async tryHeal(
        sessionId: string, step: any, testCase: TestCase, job: TestJob, app: MobileApp,
        ctx: TemplateContext, shots: PendingScreenshot[], heals: any[], err: Error,
        meta: StepExecMeta,
    ): Promise<boolean> {
        try {
            if (llmProvider.name === 'null') return false;
            if (!step?.selector || !testCase.id) return false;
            if (!MOBILE_SELECTOR_FAILURE_RE.test(err.message || '')) return false;

            const source = await this.driver.getPageSource(sessionId);
            const healed = (await this.aiEngine.healMobileLocator(step.selector, source, job.run_id) || '').trim();
            if (!healed || healed === step.selector) return false;

            const matches = (await this.driver.findElements(sessionId, healed).catch(() => [])).length;
            heals.push({
                test_case_id: testCase.id,
                step_id: step.id || '',
                old_selector: step.selector,
                new_selector: healed,
                matches,
                intent: step.intent || null,
            });
            console.log(`[MobileWorker] Heal suggestion: "${step.selector}" -> "${healed}" (${matches} match(es))`);

            // Only auto-apply an unambiguous match; otherwise just suggest.
            if (process.env.RUNTIME_HEAL_ENABLED !== 'true' || matches !== 1) return false;

            await this.executeStep(sessionId, { ...step, selector: healed }, app, ctx, shots, meta);
            console.log(`[MobileWorker] Runtime heal APPLIED: "${step.selector}" -> "${healed}" (step recovered)`);
            return true;
        } catch (healErr: any) {
            console.warn(`[MobileWorker] Heal attempt failed: ${healErr.message}`);
            return false;
        }
    }

    /**
     * Upload captured screenshots to MinIO under the same key layout the
     * Playwright worker uses (`runs/{run_id}/screenshots/…`) and return the
     * object keys. Best-effort: an unreachable MinIO fails the artifacts,
     * never the job.
     */
    private async uploadScreenshots(job: TestJob, shots: PendingScreenshot[]): Promise<string[]> {
        if (shots.length === 0) return [];
        const keys: string[] = [];
        try {
            const exists = await this.minio.bucketExists(BUCKET_NAME);
            if (!exists) await this.minio.makeBucket(BUCKET_NAME);
            for (let i = 0; i < shots.length; i++) {
                const label = (shots[i].label || 'screenshot').replace(/[^a-zA-Z0-9_-]+/g, '_').slice(0, 60);
                const key = `runs/${job.run_id}/screenshots/${job.job_id}-${i}-${label}.png`;
                await this.minio.putObject(BUCKET_NAME, key, shots[i].png, shots[i].png.length, {
                    'Content-Type': 'image/png',
                });
                keys.push(key);
            }
            console.log(`[MobileWorker] Uploaded ${keys.length} screenshot(s) for job ${job.job_id}`);
        } catch (err: any) {
            console.error(`[MobileWorker] Screenshot upload failed (job continues): ${err.message}`);
        }
        return keys;
    }

    /** Upload the job's screen recording (MP4) — same key layout as web video. */
    private async uploadVideo(job: TestJob, videoBuf: Buffer | null): Promise<string | null> {
        if (!videoBuf || videoBuf.length === 0) return null;
        try {
            const exists = await this.minio.bucketExists(BUCKET_NAME);
            if (!exists) await this.minio.makeBucket(BUCKET_NAME);
            const key = `runs/${job.run_id}/videos/${job.job_id}.mp4`;
            await this.minio.putObject(BUCKET_NAME, key, videoBuf, videoBuf.length, {
                'Content-Type': 'video/mp4',
            });
            console.log(`[MobileWorker] Uploaded video: ${key} (${(videoBuf.length / 1e6).toFixed(1)} MB)`);
            return key;
        } catch (err: any) {
            console.error(`[MobileWorker] Video upload failed (job continues): ${err.message}`);
            return null;
        }
    }

    private capabilities(app: MobileApp, job: TestJob, appTarget: string): Record<string, any> {
        const common = {
            'appium:app': appTarget,
            'appium:newCommandTimeout': 300,
            ...(process.env.MOBILE_PLATFORM_VERSION
                ? { 'appium:platformVersion': process.env.MOBILE_PLATFORM_VERSION } : {}),
            // Read by device-cloud decorators for session/build naming.
            'traceiq:sessionName': `TraceIQ run ${job.run_id}`,
        };
        if (app.platform === 'ios') {
            return {
                platformName: 'iOS',
                'appium:automationName': 'XCUITest',
                'appium:deviceName': job.device || process.env.MOBILE_DEVICE_NAME || process.env.DEVICE_NAME || 'iPhone Simulator',
                ...(app.package_id ? { 'appium:bundleId': app.package_id } : {}),
                ...common,
            };
        }
        return {
            platformName: 'Android',
            'appium:automationName': 'UiAutomator2',
            'appium:deviceName': job.device || process.env.MOBILE_DEVICE_NAME || process.env.DEVICE_NAME || 'Android Emulator',
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

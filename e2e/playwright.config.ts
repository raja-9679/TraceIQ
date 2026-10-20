import { defineConfig, devices } from '@playwright/test';

// TraceIQ's own E2E suite (WP15c). It drives a real compose stack, so the
// timeouts are generous (a run needs Celery, Redis, a worker and a browser)
// but always bounded. No test sleeps: every wait is on a condition.

const baseURL = process.env.TRACEIQ_E2E_BASE_URL || 'http://localhost:8080';
const isCI = !!process.env.CI;
export default defineConfig({
    testDir: './tests',
    outputDir: './test-results',
    timeout: 120_000,
    expect: { timeout: 15_000 },
    fullyParallel: false,
    forbidOnly: isCI,
    // One retry in CI only; a retry that passes is reported as "flaky" in the
    // HTML report, which is the signal we want to keep, not hide.
    retries: isCI ? 1 : 0,
    workers: Number(process.env.TRACEIQ_E2E_WORKERS || 3),
    reporter: [
        ['list'],
        ['html', { open: 'never', outputFolder: 'playwright-report' }],
        ['junit', { outputFile: 'test-results/junit.xml' }],
    ],
    use: {
        baseURL,
        trace: 'retain-on-failure',
        screenshot: 'only-on-failure',
        video: 'off',
        actionTimeout: 15_000,
        navigationTimeout: 30_000,
        viewport: { width: 1440, height: 900 },
    },
    projects: [
        // Registers the first user (instance admin on a fresh stack), proves
        // password login works, and saves the session for everything else.
        { name: 'setup', testMatch: /auth\.setup\.ts/ },
        {
            name: 'public',
            testMatch: /public\.spec\.ts/,
            use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
        },
        {
            name: 'app',
            testMatch: /(journey|navigation)\.spec\.ts/,
            dependencies: ['setup'],
            use: {
                ...devices['Desktop Chrome'],
                viewport: { width: 1440, height: 900 },
                storageState: '.auth/user.json',
            },
        },
    ],
});

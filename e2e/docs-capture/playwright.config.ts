import { defineConfig, devices } from '@playwright/test';

// Screenshots + screen recordings for the user guide at /docs/
// (frontend/public/docs). Not a test suite: each "test" visits one page of
// the app, annotates it and saves pictures into the guide's media folder.
// Run with e2e/docs-capture/run.sh — see the README next to this file.

const baseURL = process.env.TRACEIQ_DOCS_BASE_URL || 'http://frontend';

export default defineConfig({
    testDir: '.',
    outputDir: '../test-results/docs-capture',
    timeout: 180_000,
    expect: { timeout: 20_000 },
    fullyParallel: false,
    workers: 1,
    retries: 0,
    reporter: [['list']],
    use: {
        baseURL,
        actionTimeout: 15_000,
        navigationTimeout: 30_000,
        // 1440x900 matches the e2e suite; at this width the app shows its full
        // sidebar, which is what most readers will see.
        viewport: { width: 1440, height: 900 },
        deviceScaleFactor: 1,
        video: 'off',
        trace: 'off',
    },
    projects: [
        { name: 'session', testMatch: /session\.setup\.ts/ },
        {
            name: 'pages',
            testMatch: /pages\/.*\.spec\.ts/,
            dependencies: ['session'],
            use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 }, storageState: '.auth/docs-user.json' },
        },
    ],
});

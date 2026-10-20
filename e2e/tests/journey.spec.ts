import { expect, test, type Page } from '@playwright/test';
import { api } from './support/account';
import { settle, watch, type Problems } from './support/health';

// (2)–(4): the core loop, in order — create a project, a suite and a case
// through the UI, run it, see the result with its steps, then cancel a run.
// The case browses e2e-target, a static page served by the stack itself.

const TARGET_URL = process.env.TRACEIQ_E2E_TARGET_URL || 'http://e2e-target/';
const TARGET_TITLE = process.env.TRACEIQ_E2E_TARGET_TITLE || 'TraceIQ E2E target';
// A run needs Celery dispatch, a worker claim, a browser launch and the
// results drain (beat, every 2s). Generous, but bounded.
const RUN_TIMEOUT = 180_000;

test.describe.configure({ mode: 'serial' });

const tag = Date.now().toString(36);
const projectName = `E2E Journey ${tag}`;
const suiteName = `Smoke ${tag}`;
const caseName = `Target page has its title ${tag}`;
let suiteId: number;
let projectId: number;

let page: Page;
let problems: Problems;

test.beforeAll(async ({ browser, baseURL }) => {
    // One page for the whole journey: the steps build on each other the way a
    // person's session would (active project in localStorage, etc.).
    const ctx = await browser.newContext({ storageState: '.auth/user.json' });
    page = await ctx.newPage();
    problems = watch(page, baseURL!);
});

test.afterAll(async () => {
    await page.context().close();
});

test.afterEach(async ({}, info) => {
    if (problems.clientErrors.length) {
        await info.attach('api-4xx.txt', { body: problems.clientErrors.join('\n'), contentType: 'text/plain' });
    }
    expect(problems.errors, 'page errors / console errors / 5xx during this step').toEqual([]);
});

test('create a project from the Workspace page and switch to it', async () => {
    await page.goto('/workspace');
    await page.getByRole('tab', { name: /Projects/ }).click();
    await page.getByRole('button', { name: 'New project' }).click();
    const dialog = page.getByRole('dialog', { name: 'New project' });
    await dialog.getByLabel('Name').fill(projectName);
    await dialog.getByLabel('Description').fill('Created by the TraceIQ E2E suite.');
    await dialog.getByRole('button', { name: 'Create project' }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByText(projectName).first()).toBeVisible();

    // Switch the sidebar's project picker to it.
    await page.getByRole('button', { name: /^Project: / }).click();
    await page.getByRole('menuitem', { name: projectName }).click();
    await expect(page.getByRole('button', { name: `Project: ${projectName}` })).toBeVisible();
    projectId = Number(await page.evaluate(() => localStorage.getItem('activeProjectId')));
    expect(projectId).toBeGreaterThan(0);
    await settle(problems);
});

test('create a suite', async () => {
    await page.goto('/suites');
    await page.getByRole('button', { name: /New suite/ }).first().click();
    await page.getByPlaceholder('e.g., Core API Regression').fill(suiteName);
    await page.getByRole('button', { name: /^Create/ }).last().click();
    // Creating opens the suite (or lists it); either way we end on its page.
    const link = page.getByRole('link', { name: suiteName }).or(page.getByRole('heading', { name: suiteName }));
    await expect(link.first()).toBeVisible();
    if (!/\/suites\/\d+/.test(page.url())) await page.getByRole('link', { name: suiteName }).first().click();
    await expect(page).toHaveURL(/\/suites\/\d+/);
    suiteId = Number(page.url().match(/\/suites\/(\d+)/)![1]);
    await expect(page.getByRole('heading', { level: 1, name: suiteName })).toBeVisible();
    await settle(problems);
});

test('create a case in the builder: go to the target, expect its title', async () => {
    await page.getByRole('button', { name: 'New test' }).first().click();
    await expect(page).toHaveURL(new RegExp(`/suites/${suiteId}/builder`));
    await page.getByPlaceholder('Enter Test Case Name...').fill(caseName);

    // The add-step bar: "Navigate" adds a goto step, "Assert" an assertion
    // whose type is then switched to Expect Title.
    await page.getByRole('button', { name: 'Navigate', exact: true }).click();
    await page.getByPlaceholder('https://example.com').fill(TARGET_URL);
    await page.getByRole('button', { name: 'Assert', exact: true }).click();
    await page.getByRole('combobox', { name: 'Step action: Expect Visible' }).click();
    await page.getByRole('option', { name: 'Expect Title' }).click();
    await page.getByPlaceholder('Text the page title should contain').fill(TARGET_TITLE);

    await page.getByRole('button', { name: 'Save Test' }).click();
    await expect(page).toHaveURL(new RegExp(`/suites/${suiteId}$`));
    await expect(page.getByRole('heading', { name: caseName })).toBeVisible();
    await settle(problems);
});

test('run the suite and see the passing result with its steps', async () => {
    await page.goto(`/suites/${suiteId}`);
    await page.getByRole('button', { name: 'Run suite' }).click();
    await expect(page).toHaveURL(/\/runs$/);
    // Newest run of this suite: open it from the runs list.
    await page.getByRole('link', { name: new RegExp(suiteName) }).first().click();
    await expect(page).toHaveURL(/\/runs\/\d+/);

    await expect(page.getByText('Passed', { exact: true }).first()).toBeVisible({ timeout: RUN_TIMEOUT });
    await expect(page.getByText(caseName).first()).toBeVisible();
    // The result's step evidence lists both steps the case was built from.
    await expect(page.getByText(/step evidence \(2\)/i)).toBeVisible();
    await expect(page.getByText('goto', { exact: true })).toBeVisible();
    await expect(page.getByText('expect-title', { exact: true })).toBeVisible();
    await settle(problems);
});

test('cancel a running run and see it shown as Cancelled', async () => {
    // A slow case, created through the API: the builder was exercised above,
    // and this one only has to stay busy long enough to be cancelled.
    // wait-timeout steps are short so the worker reaches a step boundary (where
    // it checks for cancellation) quickly.
    const steps = [
        { id: 's1', type: 'goto', value: TARGET_URL },
        ...Array.from({ length: 24 }, (_, i) => ({ id: `w${i}`, type: 'wait-timeout', value: '5000' })),
    ];
    const slowSuite = await api<{ id: number }>(page, 'POST', '/suites', {
        name: `Slow ${tag}`, project_id: projectId, description: 'Cancelled by the E2E suite.',
    });
    await api(page, 'POST', `/suites/${slowSuite.id}/cases`, { name: `Slow case ${tag}`, steps });

    await page.goto(`/suites/${slowSuite.id}`);
    await page.getByRole('button', { name: 'Run suite' }).click();
    await expect(page).toHaveURL(/\/runs$/);
    await page.getByRole('link', { name: new RegExp(`Slow ${tag}`) }).first().click();
    await expect(page).toHaveURL(/\/runs\/\d+/);

    await expect(page.getByText('Running', { exact: true }).first()).toBeVisible({ timeout: RUN_TIMEOUT });
    await page.getByRole('button', { name: 'Cancel run' }).click();
    const confirm = page.getByRole('alertdialog');
    await confirm.getByRole('button', { name: 'Cancel run' }).click();
    await expect(confirm).toBeHidden();

    await expect(page.getByText('Cancelled', { exact: true }).first()).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole('button', { name: 'Cancel run' })).toBeHidden();
    // And the server agrees: the run is over, without a verdict.
    const runId = Number(page.url().match(/\/runs\/(\d+)/)![1]);
    const run = await api<{ status: string }>(page, 'GET', `/runs/${runId}`);
    expect(run.status.toLowerCase()).toBe('cancelled');
    await settle(problems);
});

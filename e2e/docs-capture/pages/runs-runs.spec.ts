import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/runs/runs.html
// Nothing here changes data: filters and tabs only change what is shown. No
// delete, cancel, re-run, compare, share, ticket or analyse button is pressed.

const DIR = 'runs/runs';

/** Opens the newest failed run of the demo web suite from the Runs list. */
async function openFailedWebRun(page: Page) {
    const main = page.locator('main');
    await main.getByRole('group', { name: 'Filter by status' }).getByRole('button', { name: 'Failed' }).click();
    await settle(page);
    const row = main.locator('li').filter({ hasText: 'Web smoke (demo)' }).first();
    await expect(row).toBeVisible();
    return row;
}

test('Runs › Runs', async ({ page, browser }) => {
    await page.goto('/runs');
    await expect(page.getByRole('heading', { name: 'Test runs', level: 1 })).toBeVisible();
    const main = page.locator('main');
    await expect(main.getByText('Total runs', { exact: true })).toBeVisible();
    await settle(page);

    // 1. The list as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    const search = main.getByPlaceholder('Search suite or test case');
    const status = main.getByRole('group', { name: 'Filter by status' });
    const browsers = main.getByRole('group', { name: 'Filter by browser' });
    const tiles = main.getByText('Total runs', { exact: true }).locator('xpath=ancestor::div[contains(@class,"grid")][1]');
    const list = main.locator('ul').filter({ has: page.locator('li') }).first();
    const firstRow = list.locator('li').first();
    const more = main.getByRole('button', { name: 'More' });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: search },
        { n: 2, target: status },
        { n: 3, target: browsers, side: 'right' },
        { n: 4, target: tiles },
        { n: 5, target: firstRow, inset: true },
        { n: 6, target: more, side: 'right' },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups of the list.
    await status.screenshot({ path: out(`${DIR}/status-filter.png`) });
    await tiles.screenshot({ path: out(`${DIR}/tiles.png`) });
    await firstRow.screenshot({ path: out(`${DIR}/run-row.png`) });
    const pager = main.getByRole('navigation', { name: 'Pagination' });
    await pager.scrollIntoViewIfNeeded();
    await pager.screenshot({ path: out(`${DIR}/pagination.png`) });
    await page.evaluate(() => window.scrollTo(0, 0));

    // 4. One run's page: the newest failed run of the web suite.
    const row = await openFailedWebRun(page);
    await row.screenshot({ path: out(`${DIR}/run-row-failed.png`) });
    await row.click();
    await page.waitForURL(/\/runs\/\d+$/);
    await expect(main.getByRole('tablist', { name: 'Run sections' })).toBeVisible();
    await expect(main.getByText('Tests in this run', { exact: true })).toBeVisible();
    await settle(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.screenshot({ path: out(`${DIR}/run.png`) });

    const header = main.getByRole('tablist', { name: 'Run sections' }).locator('xpath=ancestor::div[contains(@class,"rounded-3xl")][1]');
    const title = header.locator('h1').locator('xpath=ancestor::div[contains(@class,"flex-col")][1]');
    const actions = header.getByRole('button', { name: /Compare deployment/ }).locator('xpath=..');
    const tabs = main.getByRole('tablist', { name: 'Run sections' });
    const runTiles = main.getByText('Pass rate', { exact: true }).locator('xpath=ancestor::div[contains(@class,"grid")][1]');
    const ai = main.getByText('TraceIQ thinks').locator('xpath=ancestor::div[contains(@class,"rounded-3xl")][1]');
    const testsList = main.getByText('Tests in this run', { exact: true }).locator('xpath=ancestor::div[contains(@class,"rounded-3xl")][1]');
    const testDetail = main.locator('[id^="result-"]').first();
    await page.setViewportSize({ width: 1440, height: 1000 });
    await settle(page);
    const clean2 = await annotate(page, [
        { n: 7, target: title },
        { n: 8, target: actions, side: 'right' },
        { n: 9, target: tabs },
        { n: 10, target: runTiles },
        { n: 11, target: ai },
        { n: 12, target: testsList },
        { n: 13, target: testDetail, side: 'right' },
    ]);
    await page.screenshot({ path: out(`${DIR}/run-annotated.png`) });
    await clean2();
    await title.screenshot({ path: out(`${DIR}/run-title.png`) });
    await actions.screenshot({ path: out(`${DIR}/run-actions.png`) });
    await runTiles.screenshot({ path: out(`${DIR}/run-tiles.png`) });
    await ai.screenshot({ path: out(`${DIR}/run-ai.png`) });
    await testsList.screenshot({ path: out(`${DIR}/run-tests.png`) });
    await testDetail.screenshot({ path: out(`${DIR}/run-test-detail.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });

    // 5. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/runs');
        await expect(p.getByRole('heading', { name: 'Test runs', level: 1 })).toBeVisible();
        const m = p.locator('main');
        await expect(m.getByText('Total runs', { exact: true })).toBeVisible();
        await settle(p);
        await caption(p, 'Runs lists every test run in all your projects, newest first.', 2000);
        await caption(p, 'Each row is one run: its suite, number, start time, result and duration.');
        await pointAt(p, m.locator('ul li').first(), 2600);
        await caption(p, 'Search by suite or test name, or filter by result and browser.');
        await pointAt(p, m.getByPlaceholder('Search suite or test case'), 1400);
        const st = m.getByRole('group', { name: 'Filter by status' });
        await pointAt(p, st.getByRole('button', { name: 'Failed' }), 900);
        const r = await openFailedWebRun(p);
        await beat(p, 800);
        await caption(p, 'Here only failed runs are shown. Click one to open it.');
        await pointAt(p, r, 2200);
        await caption(p, '');
        await r.click();
        await p.waitForURL(/\/runs\/\d+$/);
        await expect(m.getByText('Tests in this run', { exact: true })).toBeVisible();
        await settle(p);
        await caption(p, 'The run page. At the top: what ran, the result, and when.', 2200);
        await caption(p, 'Duration, the number of tests, and the share that passed.');
        await pointAt(p, m.getByText('Pass rate', { exact: true }), 2400);
        await caption(p, 'For a failed run, AI analysis can suggest a likely cause, when it is set up.');
        await pointAt(p, m.getByText('TraceIQ thinks'), 2600);
        const tl = m.getByText('Tests in this run', { exact: true });
        await tl.scrollIntoViewIfNeeded();
        await caption(p, 'Every test in the run. Failed ones come first, and the first is already open.');
        await pointAt(p, m.getByRole('button', { pressed: true }), 2600);
        await caption(p, 'On the right: the selected test, with its error message.');
        await pointAt(p, m.getByText('Error Details'), 2600);
        await p.evaluate(() => window.scrollTo(0, 0));
        await caption(p, 'The tabs at the top show recordings and network traffic, when the run captured them.');
        const tabs = m.getByRole('tablist', { name: 'Run sections' });
        await pointAt(p, tabs.getByRole('tab', { name: 'Traces' }), 1400);
        await pointAt(p, tabs.getByRole('tab', { name: 'Network' }), 1600);
        await caption(p, '');
    });
});

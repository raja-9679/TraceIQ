import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/reports/costs.html
// Nothing here changes data: only the page's own pickers (window, workspace,
// project) are used. The budget field is looked at, never saved.

const DIR = 'reports/costs';

/** The page opens on the account's first workspace; the pictures need the demo one. */
async function pickDemo(p: Page) {
    const main = p.locator('main');
    await main.getByRole('combobox', { name: 'Workspace' }).click();
    await p.getByRole('option', { name: 'Demo workspace' }).click();
    await settle(p);
    // Drop the focus ring the picker keeps after choosing.
    await p.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
    await expect(main.getByText('Demo — Todo web app').first()).toBeVisible();
}

test('Reports › Costs', async ({ page, browser }) => {
    await page.goto('/costs');
    await expect(page.getByRole('heading', { name: 'Costs', level: 1 })).toBeVisible();
    await settle(page);
    await pickDemo(page);
    const main = page.locator('main');

    // 1. The page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    const pickers = main.getByRole('combobox', { name: 'Time window' }).locator('xpath=..');
    const warn = main.getByText(/No currency is set/);
    const tiles = main.getByText('Total', { exact: true }).locator('xpath=ancestor::div[contains(@class,"grid")][1]');
    const trend = main.locator('section').filter({ has: page.getByRole('heading', { name: /per day$/, level: 2 }) });
    const models = main.locator('section[aria-label="By model"]').locator('xpath=..');
    const byProject = main.locator('section[aria-label="By project"]');
    const topRuns = main.locator('section[aria-label="Most expensive runs"]');
    await page.setViewportSize({ width: 1440, height: 1900 });
    await settle(page);
    let clean = await annotate(page, [
        { n: 1, target: pickers, side: 'right' },
        { n: 2, target: warn },
        { n: 3, target: tiles },
        { n: 4, target: trend },
        { n: 5, target: models },
        { n: 6, target: byProject },
        { n: 7, target: topRuns },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups used next to each explanation.
    await tiles.screenshot({ path: out(`${DIR}/tiles.png`) });
    await trend.screenshot({ path: out(`${DIR}/trend.png`) });
    await byProject.screenshot({ path: out(`${DIR}/by-project.png`) });
    await topRuns.screenshot({ path: out(`${DIR}/top-runs.png`) });

    // 4. One project picked: the monthly budget card appears at the bottom.
    await byProject.getByRole('button', { name: 'Demo — Todo web app' }).click();
    await settle(page);
    const budget = main.getByText('Monthly budget', { exact: true }).locator('xpath=ancestor::section[1]');
    await expect(budget).toBeVisible();
    clean = await annotate(page, [{ n: 8, target: budget }]);
    await budget.scrollIntoViewIfNeeded();
    await page.screenshot({ path: out(`${DIR}/project-annotated.png`) });
    await clean();
    await budget.screenshot({ path: out(`${DIR}/budget.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });

    // 5. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/costs');
        await expect(p.getByRole('heading', { name: 'Costs', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        await caption(p, 'Costs shows what testing costs: AI use, and the time test machines spend running tests.', 2000);
        await caption(p, 'First choose the workspace. Here, the demo workspace.');
        const ws = m.getByRole('combobox', { name: 'Workspace' });
        await pointAt(p, ws, 600);
        await ws.click();
        await beat(p, 700);
        const opt = p.getByRole('option', { name: 'Demo workspace' });
        await pointAt(p, opt, 600);
        await opt.click();
        await settle(p);
        await caption(p, 'You can also choose how far back to look, and one project or all of them.');
        await pointAt(p, m.getByRole('combobox', { name: 'Time window' }), 1200);
        await pointAt(p, m.getByRole('combobox', { name: 'Project' }), 1200);
        await caption(p, 'No currency has been set on this server, so the page shows usage without money.');
        await pointAt(p, m.getByText(/No currency is set/), 2400);
        await caption(p, 'The tiles split the total into AI tokens and browser-minutes.');
        await pointAt(p, m.getByText('AI (LLM tokens)', { exact: true }), 1400);
        await pointAt(p, m.getByText('Workers (browser-minutes)', { exact: true }), 1600);
        const tr = m.locator('section').filter({ has: p.getByRole('heading', { name: /per day$/, level: 2 }) });
        await tr.scrollIntoViewIfNeeded();
        await caption(p, 'The chart shows each day. Point at a day to see its numbers.');
        await pointAt(p, tr.locator('[role="img"] > div').last(), 2400);
        const bp = m.locator('section[aria-label="By project"]');
        await bp.scrollIntoViewIfNeeded();
        await caption(p, 'By project compares this window and this month with each project\'s budget.');
        await pointAt(p, bp.getByText('Budget', { exact: true }), 2200);
        const tops = m.locator('section[aria-label="Most expensive runs"]');
        await tops.scrollIntoViewIfNeeded();
        await caption(p, 'Most expensive runs lists the ten runs that used the most. Click one to open it.', {
            say: 'Most expensive runs lists the ten runs that used the most. Click one to open it.',
        });
        await pointAt(p, tops.locator('a').first(), 2400);
        await bp.scrollIntoViewIfNeeded();
        await caption(p, 'Click a project name to look at that project alone.');
        const proj = bp.getByRole('button', { name: 'Demo — Todo web app' });
        await pointAt(p, proj, 900);
        await proj.click();
        await settle(p);
        const b = m.getByText('Monthly budget', { exact: true });
        await b.scrollIntoViewIfNeeded();
        await caption(p, 'Its monthly budget is at the bottom. Project admins can set it.');
        await pointAt(p, b, 2400);
        await caption(p, '');
    });
});

import { expect, test } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/reports/portfolio.html
// Nothing here changes data: the workspace picker and the column headings only
// change what is shown. "Open summary" switches the selected project, which
// lives in the browser (localStorage) of the recording only.

const DIR = 'reports/portfolio';

// The docs account also has a workspace of its own, listed first: pick the demo.
async function openDemo(p: import('@playwright/test').Page) {
    await p.goto('/reports/portfolio');
    await expect(p.getByRole('heading', { name: 'Portfolio', level: 1 })).toBeVisible();
    const m = p.locator('main');
    await m.getByLabel('Workspace').selectOption({ label: 'Demo workspace' });
    await expect(m.getByRole('region', { name: 'Projects' }).getByText('Demo — Public API')).toBeVisible();
    await settle(p);
    return m;
}

test('Reports › Portfolio', async ({ page, browser }) => {
    const main = await openDemo(page);

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered (the guide explains each number).
    const table = main.getByRole('region', { name: 'Projects' });
    const picker = main.getByLabel('Workspace').locator('xpath=ancestor::label[1]');
    const head = table.locator('thead tr');
    const row = table.locator('tbody tr').first();
    const open = row.getByRole('button', { name: 'Open summary' });
    const clean = await annotate(page, [
        { n: 1, target: picker, side: 'right' },
        { n: 2, target: head, side: 'right' },
        { n: 3, target: table.locator('tbody') },
        { n: 4, target: open, side: 'right' },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups used next to each explanation.
    await picker.screenshot({ path: out(`${DIR}/workspace.png`) });
    await table.screenshot({ path: out(`${DIR}/table.png`) });
    await head.screenshot({ path: out(`${DIR}/headings.png`) });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        const m = await openDemo(p);
        const t = m.getByRole('region', { name: 'Projects' });
        await caption(p, 'Portfolio puts every project of a workspace side by side, one row each.', 2200);
        await caption(p, 'Pick the workspace here, if you belong to more than one.');
        await pointAt(p, m.getByLabel('Workspace'), 2200);
        await caption(p, 'The pass rate over the last 7 days and the last 30 days.', { say: 'The pass rate over the last seven days and the last thirty days.' });
        await pointAt(p, t.locator('tbody tr').first().locator('td').nth(1), 1400);
        await pointAt(p, t.locator('tbody tr').first().locator('td').nth(2), 1400);
        await caption(p, 'The newest run with a result, and whether the latest version is ready to release.');
        await pointAt(p, t.locator('tbody tr').first().locator('td').nth(4), 1600);
        await pointAt(p, t.locator('tbody tr').first().locator('td').nth(5), 1600);
        await caption(p, 'Flaky tests, and security findings that are still open.');
        await pointAt(p, t.locator('tbody tr').nth(1).locator('td').nth(6), 1400);
        await pointAt(p, t.locator('tbody tr').nth(1).locator('td').nth(7), 1400);
        await caption(p, 'The worst 7-day pass rate comes first. Click a heading to sort by that column.', {
            say: 'The worst seven-day pass rate comes first. Click a heading to sort by that column.',
        });
        const runsHead = t.getByRole('button', { name: 'Runs, 7 days' });
        await pointAt(p, runsHead, 900);
        await runsHead.click();
        await beat(p, 1600);
        await caption(p, 'Click it again to turn the order round.');
        await runsHead.click();
        await beat(p, 1800);
        await caption(p, 'Open summary shows the executive summary of that project.');
        const btn = t.locator('tbody tr').first().getByRole('button', { name: 'Open summary' });
        await pointAt(p, btn, 2000);
        await caption(p, '');
        await btn.click();
        await p.waitForURL(/\/reports\/summary/);
        await expect(p.getByRole('heading', { name: 'Executive summary', level: 1 })).toBeVisible();
        await settle(p);
        await caption(p, 'The sidebar now has that project selected, too.', 2200);
        await caption(p, '');
    });
});

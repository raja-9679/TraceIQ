import { expect, test } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/today/overview.html

const DIR = 'today/overview';

test('Today › Overview', async ({ page, browser }) => {
    await page.goto('/overview');
    await expect(page.getByRole('heading', { name: 'Overview', level: 1 })).toBeVisible();
    await settle(page);

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. The same page with its parts numbered (the guide explains each number).
    const main = page.locator('main');
    const tiles = main.getByText('Total runs', { exact: true }).locator('xpath=ancestor::div[contains(@class,"grid")][1]');
    const trend = main.locator('section[aria-labelledby="trend-h"]');
    const recent = main.locator('section[aria-labelledby="recent-h"]');
    const allRuns = main.getByRole('link', { name: 'All runs' });
    const clean = await annotate(page, [
        { n: 1, target: tiles },
        { n: 2, target: trend },
        { n: 3, target: recent, side: 'right' },
        { n: 4, target: allRuns },
    ]);
    // A taller window instead of fullPage: the sidebar is viewport-high, so a
    // full-page shot would show it ending halfway down.
    await page.setViewportSize({ width: 1440, height: 1240 });
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();
    await page.setViewportSize({ width: 1440, height: 900 });

    // 3. Close-ups used next to each explanation.
    await tiles.screenshot({ path: out(`${DIR}/tiles.png`) });
    await trend.screenshot({ path: out(`${DIR}/trend.png`) });
    // Hovering a bar shows that day's numbers.
    const bar = trend.locator('[title*="pass rate"]').last();
    await bar.hover();
    await recent.locator('li').first().screenshot({ path: out(`${DIR}/recent-run-row.png`) });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/overview');
        await expect(p.getByRole('heading', { name: 'Overview', level: 1 })).toBeVisible();
        await settle(p);
        await caption(p, 'Overview is the first page you see after signing in.', 2600);
        const m = p.locator('main');
        await caption(p, 'Total runs: every test run you can see, in all your projects.');
        await pointAt(p, m.getByText('Total runs', { exact: true }), 2400);
        await caption(p, 'Passed, Failed and Running count only the 50 most recent runs.');
        for (const label of ['Passed', 'Failed', 'Running']) {
            await pointAt(p, m.getByText(label, { exact: true }), 1100);
        }
        await beat(p, 900);
        const tr = m.locator('section[aria-labelledby="trend-h"]');
        const bars = tr.locator('[title]');
        const n = await bars.count();
        await caption(p, 'One bar per day: the share of runs that passed in the selected project.');
        await pointAt(p, bars.nth(Math.max(0, n - 6)), 1600);
        await caption(p, 'Green = 90% or more passed. Yellow = 60–89%. Orange = under 60%.');
        await pointAt(p, bars.nth(Math.max(0, n - 3)), 1400);
        await pointAt(p, bars.nth(n - 1), 1800);
        const rc = m.locator('section[aria-labelledby="recent-h"]');
        await rc.scrollIntoViewIfNeeded();
        await caption(p, 'Recent runs: the 10 newest. Click one to open its full result.');
        const row = rc.locator('li a').first();
        await pointAt(p, row, 2200);
        // The app changes page without a reload, so the caption would linger.
        await caption(p, '');
        await row.click();
        await p.waitForURL(/\/runs\/\d+/);
        await settle(p);
        await caption(p, 'This is the run page: every test, step, screenshot and log of that run.', 3000);
        await p.goBack();
        await settle(p);
        await caption(p, 'All runs opens the complete list, with search and filters.');
        await pointAt(p, m.getByRole('link', { name: 'All runs' }), 2400);
        await caption(p, '');
    });
});

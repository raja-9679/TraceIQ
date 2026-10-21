import { expect, test } from '@playwright/test';
import { annotate, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/reports/flake-trends.html
// Read-only page: the two window menus only change what is shown.

const DIR = 'reports/flake-trends';

test('Reports › Flake trends', async ({ page, browser }) => {
    await page.goto('/reports/flake-trends');
    await expect(page.getByRole('heading', { name: 'Flake trends', level: 1 })).toBeVisible();
    const main = page.locator('main');
    await expect(main.getByRole('region', { name: 'By failing step type' })).toBeVisible();
    await settle(page);

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered (the guide explains each number).
    const toolbar = main.getByLabel('Trend').locator('xpath=ancestor::label[1]/..');
    const overTime = main.getByRole('region', { name: 'Flaky tests over time' });
    const flakiest = main.getByRole('region', { name: 'Flakiest tests: score history' });
    const summary = main.getByText(/^Patterns, last \d+ days$/).locator('xpath=ancestor::div[contains(@class,"rounded-2xl")][1]');
    const cards = main.getByRole('region', { name: 'By browser' }).locator('xpath=..');
    await page.setViewportSize({ width: 1440, height: 1100 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: toolbar },
        { n: 2, target: overTime },
        { n: 3, target: flakiest },
        { n: 4, target: summary },
        { n: 5, target: cards },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups used next to each explanation.
    await toolbar.screenshot({ path: out(`${DIR}/toolbar.png`) });
    await overTime.screenshot({ path: out(`${DIR}/over-time.png`) });
    await flakiest.screenshot({ path: out(`${DIR}/flakiest.png`) });
    await summary.screenshot({ path: out(`${DIR}/summary.png`) });
    await cards.screenshot({ path: out(`${DIR}/patterns.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/reports/flake-trends');
        await expect(p.getByRole('heading', { name: 'Flake trends', level: 1 })).toBeVisible();
        const m = p.locator('main');
        await expect(m.getByRole('region', { name: 'By failing step type' })).toBeVisible();
        await settle(p);
        await caption(p, 'Flake trends shows whether flaky tests are becoming more or less common, and where they cluster.', 2000);
        const ot = m.getByRole('region', { name: 'Flaky tests over time' });
        await caption(p, 'The yellow area is how many tests counted as flaky each day.');
        await pointAt(p, ot.locator('svg'), 2400);
        await caption(p, 'The dark line is the share of that day’s results that were flaky.');
        await pointAt(p, ot.getByText(/^Latest:/), 2400);
        const fl = m.getByRole('region', { name: 'Flakiest tests: score history' });
        await fl.scrollIntoViewIfNeeded();
        await caption(p, 'The flakiest tests, with how their flakiness score has moved.');
        await pointAt(p, fl.locator('li').first(), 2600);
        const by = m.getByRole('region', { name: 'By browser' });
        await by.scrollIntoViewIfNeeded();
        await caption(p, 'Patterns: is flakiness worse in one browser, one environment, or at one time of day?');
        await pointAt(p, by, 1400);
        await pointAt(p, m.getByRole('region', { name: 'By environment' }), 1200);
        await pointAt(p, m.getByRole('region', { name: 'By time of day (UTC)' }), 1400);
        await caption(p, 'And which kind of step was failing when a test went wrong.');
        await pointAt(p, m.getByRole('region', { name: 'By failing step type' }), 2400);
        await p.evaluate(() => window.scrollTo(0, 0));
        await caption(p, 'The trend can cover up to 180 days; the patterns up to 90.', {
            say: 'The trend can cover up to a hundred and eighty days; the patterns up to ninety.',
        });
        await pointAt(p, m.getByLabel('Trend'), 1400);
        await pointAt(p, m.getByLabel('Patterns'), 1400);
        await m.getByLabel('Patterns').selectOption('7');
        await settle(p);
        await caption(p, 'A shorter pattern window shows what is happening this week.');
        await pointAt(p, m.getByText(/^Patterns, last \d+ days$/), 2400);
        await caption(p, '');
    });
});

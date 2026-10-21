import { expect, test } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/reports/delivery.html
// Nothing here changes data: the window / environment menus only change what
// is shown, and "Band thresholds" is a fold-out. The curl example is shown,
// never run.

const DIR = 'reports/delivery';

test('Reports › Delivery (DORA)', async ({ page, browser }) => {
    await page.goto('/reports/delivery');
    await expect(page.getByRole('heading', { name: 'Delivery (DORA)', level: 1 })).toBeVisible();
    const main = page.locator('main');
    await expect(main.getByRole('region', { name: 'Recent deployments' })).toBeVisible();
    await settle(page);

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered (the guide explains each number).
    const toolbar = main.getByLabel('Window').locator('xpath=ancestor::label[1]/..');
    const tiles = main.getByRole('region', { name: 'Deployment frequency' }).locator('xpath=..');
    const inputs = main.getByText('Inputs used', { exact: true }).locator('xpath=ancestor::div[contains(@class,"rounded-2xl")][1]');
    const deployments = main.getByRole('region', { name: 'Recent deployments' });
    const howTo = main.getByText('How to send deployments', { exact: true }).locator('xpath=ancestor::div[contains(@class,"rounded-2xl")][1]');
    await page.setViewportSize({ width: 1440, height: 1560 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: toolbar },
        { n: 2, target: tiles },
        { n: 3, target: inputs },
        { n: 4, target: deployments },
        { n: 5, target: howTo },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups used next to each explanation.
    await toolbar.screenshot({ path: out(`${DIR}/toolbar.png`) });
    await tiles.screenshot({ path: out(`${DIR}/tiles.png`) });
    const freq = main.getByRole('region', { name: 'Deployment frequency' });
    await freq.getByText('Band thresholds').click();
    await expect(freq.getByText('Elite:')).toBeVisible();
    await freq.screenshot({ path: out(`${DIR}/thresholds.png`) });
    await freq.getByText('Band thresholds').click();
    await inputs.screenshot({ path: out(`${DIR}/inputs.png`) });
    await deployments.screenshot({ path: out(`${DIR}/deployments.png`) });
    await howTo.screenshot({ path: out(`${DIR}/how-to-send.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/reports/delivery');
        await expect(p.getByRole('heading', { name: 'Delivery (DORA)', level: 1 })).toBeVisible();
        const m = p.locator('main');
        await expect(m.getByRole('region', { name: 'Recent deployments' })).toBeVisible();
        await settle(p);
        await caption(p, 'Delivery measures how your team ships, from the deployments your build pipeline reports.', 2000);
        const f = m.getByRole('region', { name: 'Deployment frequency' });
        await caption(p, 'Deployment frequency: how often you deploy.');
        await pointAt(p, f.locator('span').nth(2), 2200);
        await caption(p, 'Each figure gets a band, from Low to Elite, using the published DORA research thresholds.', {
            say: 'Each figure gets a band, from Low to Elite, using the published Dora research thresholds.',
        });
        await pointAt(p, f.getByText('High', { exact: true }), 1200);
        await f.getByText('Band thresholds').click();
        await beat(p, 2400);
        await f.getByText('Band thresholds').click();
        await caption(p, 'Lead time: how long a change takes to reach production.');
        await pointAt(p, m.getByRole('region', { name: 'Lead time for changes' }), 2400);
        await caption(p, 'Change failure rate: the share of deployments that went wrong.');
        await pointAt(p, m.getByRole('region', { name: 'Change failure rate' }), 2400);
        await caption(p, 'Time to restore: how long incidents took to fix.');
        await pointAt(p, m.getByRole('region', { name: 'Time to restore' }), 2200);
        await caption(p, 'A dash means there was nothing to measure. The line below it says what is missing.');
        await pointAt(p, m.getByRole('region', { name: 'Lead time for changes' }).getByText(/No deployment in this window/), 2600);
        const d = m.getByRole('region', { name: 'Recent deployments' });
        await d.scrollIntoViewIfNeeded();
        await caption(p, 'Recent deployments, newest first, and whether a failure followed each one.');
        await pointAt(p, d.locator('tbody tr').nth(2), 2600);
        await caption(p, 'Choose a longer window, or one environment, at the top.');
        await p.evaluate(() => window.scrollTo(0, 0));
        await pointAt(p, m.getByLabel('Window'), 1000);
        await m.getByLabel('Window').selectOption('90');
        await settle(p);
        await pointAt(p, m.getByLabel('Environment'), 1800);
        await m.getByText('How to send deployments', { exact: true }).scrollIntoViewIfNeeded();
        await caption(p, 'At the bottom: the call your pipeline makes after each deployment.');
        await pointAt(p, m.locator('pre'), 2600);
        await caption(p, '');
    });
});

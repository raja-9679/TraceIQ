import { expect, test } from '@playwright/test';
import { annotate, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/reports/dependencies.html
// Read-only page: the window menu and the list only change what is shown.
// The demo's web project loads Google Fonts and Stripe's script; Stripe's
// script often answered 5xx on runs that failed.

const DIR = 'reports/dependencies';

test('Reports › Dependencies', async ({ page, browser }) => {
    await page.goto('/reports/dependencies');
    await expect(page.getByRole('heading', { name: 'Third-party dependencies', level: 1 })).toBeVisible();
    const main = page.locator('main');
    const detail = main.getByRole('article', { name: /^Vendor / });
    await expect(detail).toBeVisible();
    await settle(page);

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered (the guide explains each number).
    const windowPick = main.getByLabel('Window').locator('xpath=ancestor::label[1]');
    const list = main.getByRole('heading', { name: 'Returned errors' }).locator('xpath=ancestor::section[1]');
    const clean = await annotate(page, [
        { n: 1, target: windowPick },
        { n: 2, target: list, side: 'left' },
        { n: 3, target: detail.locator('h2'), side: 'right' },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await windowPick.screenshot({ path: out(`${DIR}/window.png`) });
    await list.screenshot({ path: out(`${DIR}/list.png`) });
    await page.setViewportSize({ width: 1440, height: 1400 });
    await settle(page);
    await detail.screenshot({ path: out(`${DIR}/detail.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/reports/dependencies');
        await expect(p.getByRole('heading', { name: 'Third-party dependencies', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        const d = m.getByRole('article', { name: /^Vendor / });
        await caption(p, 'Dependencies lists the outside services your app loads while the tests run.', 2000);
        await caption(p, 'For example analytics, payments, fonts or a content delivery network.');
        await pointAt(p, m.locator('h1'), 2200);
        await caption(p, 'Services that returned errors come first, with their error rate on the right.');
        await pointAt(p, m.getByRole('heading', { name: 'Returned errors' }), 2400);
        await caption(p, 'The selected one is shown in detail: requests, error rate and how slow it was.');
        await pointAt(p, d.locator('h2'), 2400);
        const fit = d.getByText('Does it line up with failing tests?');
        await fit.scrollIntoViewIfNeeded();
        await caption(p, 'And whether its errors came with failing tests more often than with passing ones.');
        await pointAt(p, fit, 2600);
        await caption(p, 'The window menu picks how many days to cover, up to 180.', {
            say: 'The window menu picks how many days to cover, up to a hundred and eighty.',
        });
        await p.evaluate(() => window.scrollTo(0, 0));
        await pointAt(p, m.getByLabel('Window'), 1200);
        await m.getByLabel('Window').selectOption('90');
        await settle(p);
        await caption(p, '');
    });
});

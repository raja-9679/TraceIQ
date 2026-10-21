import { expect, test } from '@playwright/test';
import { annotate, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/reports/dependencies.html
// Read-only page: the window menu only changes what is shown.
// The demo workspace records no third-party traffic, so these pictures show
// the page's empty state (see the guide page and the writer's report).

const DIR = 'reports/dependencies';

test('Reports › Dependencies', async ({ page, browser }) => {
    await page.goto('/reports/dependencies');
    await expect(page.getByRole('heading', { name: 'Third-party dependencies', level: 1 })).toBeVisible();
    const main = page.locator('main');
    await settle(page);
    const empty = main.getByText('No third-party requests', { exact: true });
    await expect(empty).toBeVisible();

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered (the guide explains each number).
    const windowPick = main.getByLabel('Window').locator('xpath=ancestor::label[1]');
    const emptyBox = empty.locator('xpath=ancestor::div[contains(@class,"rounded")][1]');
    const clean = await annotate(page, [
        { n: 1, target: windowPick },
        { n: 2, target: emptyBox },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await windowPick.screenshot({ path: out(`${DIR}/window.png`) });
    await emptyBox.screenshot({ path: out(`${DIR}/empty.png`) });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/reports/dependencies');
        await expect(p.getByRole('heading', { name: 'Third-party dependencies', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        await caption(p, 'Dependencies lists the outside services your app loads while the tests run.', 2000);
        await caption(p, 'For example analytics, payments, fonts or a content delivery network.');
        await pointAt(p, m.locator('h1'), 2200);
        await caption(p, 'For each one: how many requests failed, how slow it was, and whether that lines up with failing tests.');
        await pointAt(p, m.getByText('No third-party requests', { exact: true }), 2600);
        await caption(p, 'This demo project only calls its own site, so there is nothing to show yet.');
        await pointAt(p, m.getByText(/Runs are rolled up hourly/), 2600);
        await caption(p, 'TraceIQ adds up each run’s network traffic once an hour, so new runs appear within the hour.');
        await caption(p, 'The window menu picks how many days to cover, up to 180.', {
            say: 'The window menu picks how many days to cover, up to a hundred and eighty.',
        });
        await pointAt(p, m.getByLabel('Window'), 1200);
        await m.getByLabel('Window').selectOption('90');
        await settle(p);
        await caption(p, 'Pick a project whose tests load outside services to see its list.');
        await pointAt(p, p.getByRole('button', { name: /^Project: / }).first(), 2400);
        await caption(p, '');
    });
});

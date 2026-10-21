import { expect, test } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/tests/analytics.html
// Nothing here changes data: the time window is switched (a view setting),
// and the "New digest" form is opened and closed with Cancel. "Send now" is
// never pressed.

const DIR = 'tests/analytics';

test('Tests › Analytics', async ({ page, browser }) => {
    await page.goto('/analytics');
    await expect(page.getByRole('heading', { name: 'Analytics', level: 1 })).toBeVisible();
    await settle(page);
    const main = page.locator('main');
    await expect(main.getByText('Pass rate', { exact: true })).toBeVisible();
    await expect(main.getByRole('heading', { name: 'Per-test effectiveness' })).toBeVisible();
    await settle(page);

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered (a taller window: the page is long).
    const windowSel = main.getByRole('group', { name: 'Time window' });
    const tiles = main.getByText('Pass rate', { exact: true }).locator('xpath=ancestor::div[contains(@class,"grid")][1]');
    const lists = main.getByRole('heading', { name: 'Top failing' }).locator('xpath=ancestor::div[contains(@class,"grid")][1]');
    const table = main.getByRole('heading', { name: 'Per-test effectiveness' }).locator('xpath=ancestor::section[1]');
    const digest = main.getByText('Quality digest', { exact: true }).locator('xpath=ancestor::section[1]');
    await page.setViewportSize({ width: 1440, height: 1500 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: windowSel, side: 'right' },
        { n: 2, target: tiles },
        { n: 3, target: lists },
        { n: 4, target: table },
        { n: 5, target: digest },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await tiles.screenshot({ path: out(`${DIR}/tiles.png`) });
    await lists.screenshot({ path: out(`${DIR}/lists.png`) });
    await table.screenshot({ path: out(`${DIR}/table.png`) });
    await digest.screenshot({ path: out(`${DIR}/digest.png`) });
    await digest.getByRole('button', { name: 'New digest' }).click();
    await expect(digest.getByRole('button', { name: 'Create digest' })).toBeVisible();
    await beat(page, 400);
    await digest.screenshot({ path: out(`${DIR}/digest-form.png`) });
    await digest.getByRole('button', { name: 'Cancel' }).click();
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/analytics');
        await expect(p.getByRole('heading', { name: 'Analytics', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        await caption(p, 'Analytics shows how one project’s tests have done over a period of time.', 2000);
        await caption(p, 'Choose the last 7, 30 or 90 days here.');
        await pointAt(p, m.getByRole('group', { name: 'Time window' }), 2000);
        await caption(p, 'Pass rate is the share of finished runs that passed in that period.');
        await pointAt(p, m.getByText('Pass rate', { exact: true }), 2000);
        await caption(p, 'Open failure groups counts the problems still waiting on the Failure triage page.');
        await pointAt(p, m.getByText('Open failure groups', { exact: true }), 2400);
        await caption(p, 'Below: the tests that fail most, the slowest, and the flakiest.');
        const tf = m.getByRole('heading', { name: 'Top failing' });
        await tf.scrollIntoViewIfNeeded();
        await pointAt(p, tf, 1200);
        await pointAt(p, m.getByRole('heading', { name: 'Slowest' }), 1000);
        await pointAt(p, m.getByRole('heading', { name: 'Flakiest' }), 1200);
        await caption(p, 'The table gives every test’s runs, failures, failure rate and average time.');
        const tb = m.getByRole('heading', { name: 'Per-test effectiveness' });
        await tb.scrollIntoViewIfNeeded();
        await pointAt(p, tb, 2400);
        await caption(p, 'Switching to 7 days recounts everything for the last week only.', 600);
        await m.getByRole('group', { name: 'Time window' }).scrollIntoViewIfNeeded();
        await m.getByRole('group', { name: 'Time window' }).getByRole('button', { name: '7 days' }).click();
        await settle(p);
        await beat(p, 1600);
        await caption(p, 'At the bottom, a quality digest can send this summary by email on a timer.');
        const dg = m.getByText('Quality digest', { exact: true });
        await dg.scrollIntoViewIfNeeded();
        await pointAt(p, dg, 2400);
        await caption(p, '');
    });
});

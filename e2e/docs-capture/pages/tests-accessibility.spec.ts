import { expect, test } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/tests/accessibility.html
// The demo's web project has a month of axe results (one check per run of its
// accessibility case) and a waiver covering its one open problem. Nothing here
// changes data: the New waiver form is opened and closed with Cancel.

const DIR = 'tests/accessibility';

test('Tests › Accessibility', async ({ page, browser }) => {
    await page.goto('/accessibility');
    await expect(page.getByRole('heading', { name: 'Accessibility', level: 1 })).toBeVisible();
    await settle(page);
    const main = page.locator('main');
    const open = main.getByRole('region', { name: 'Open violations' });
    await expect(open.locator('table')).toBeVisible();
    const waivers = main.getByRole('region', { name: 'Waivers' });

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered, on a window tall enough to hold them all.
    await page.setViewportSize({ width: 1440, height: 1160 });
    await settle(page);
    const windowSel = main.getByLabel('Window');
    const tiles = main.getByText('open in the latest scans').first().locator('xpath=ancestor::div[contains(@class,"grid")][1]');
    const clean = await annotate(page, [
        { n: 1, target: windowSel },
        { n: 2, target: tiles },
        { n: 3, target: open },
        { n: 4, target: waivers },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await tiles.screenshot({ path: out(`${DIR}/tiles.png`) });
    await main.getByRole('region', { name: 'Violations by impact' }).screenshot({ path: out(`${DIR}/trend.png`) });
    await open.screenshot({ path: out(`${DIR}/open-violations.png`) });
    await waivers.screenshot({ path: out(`${DIR}/waivers.png`) });

    // The waiver form (it opens above the violations table), opened and closed again.
    await waivers.getByRole('button', { name: 'New waiver' }).click();
    const form = main.getByRole('form', { name: 'New waiver' });
    await expect(form).toBeVisible();
    await beat(page, 400);
    await form.screenshot({ path: out(`${DIR}/waiver-form.png`) });
    await form.getByRole('button', { name: 'Cancel' }).click();
    await expect(form).toBeHidden();
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/accessibility');
        await expect(p.getByRole('heading', { name: 'Accessibility', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        await caption(p, 'Accessibility collects every problem the accessibility checks in your tests have found.', 2000);
        await caption(p, 'A problem is something that makes the page hard to use, for example with a screen reader.');
        await beat(p, 1200);
        await caption(p, 'The tiles count the open problems in the latest scans, by how serious they are.');
        await pointAt(p, m.getByText('open in the latest scans').first(), 2200);
        await caption(p, 'The window chooses how many days of scans to look at.');
        await pointAt(p, m.getByLabel('Window'), 2000);
        const o = m.getByRole('region', { name: 'Open violations' });
        await o.scrollIntoViewIfNeeded();
        await caption(p, 'Open violations lists each problem: its rule, the element, and the test that found it.');
        await pointAt(p, o.locator('tbody tr').first(), 2400);
        await caption(p, 'This one is waived: set aside for a limited time, so it doesn’t block a release.');
        await pointAt(p, o.getByText('waived', { exact: true }), 2200);
        const w = m.getByRole('region', { name: 'Waivers' });
        await caption(p, 'A waiver lasts up to 90 days. New waiver adds one.', {
            say: 'A waiver lasts up to ninety days. New waiver adds one.',
        });
        await pointAt(p, w.getByRole('button', { name: 'New waiver' }), 900);
        await w.getByRole('button', { name: 'New waiver' }).click();
        await beat(p, 900);
        const f = m.getByRole('form', { name: 'New waiver' });
        await caption(p, 'It names the rule, which elements it covers, a reason, how long, and an owner.');
        await pointAt(p, f.getByText('Rule', { exact: true }), 1000);
        await pointAt(p, f.getByText(/Selector pattern/), 1000);
        await pointAt(p, f.getByText('Reason', { exact: true }), 1000);
        await pointAt(p, f.getByText(/Length/), 1000);
        await caption(p, 'Cancel closes the form without adding anything.');
        await pointAt(p, f.getByRole('button', { name: 'Cancel' }), 1200);
        await f.getByRole('button', { name: 'Cancel' }).click();
        await beat(p, 600);
        await caption(p, '');
    });
});

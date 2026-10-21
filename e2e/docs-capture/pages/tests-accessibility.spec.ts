import { expect, test } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/tests/accessibility.html
// The demo has no stored accessibility scans yet, so the page shows its
// empty state. Nothing here changes data: the "New waiver" form is opened
// and closed with Cancel, without typing.

const DIR = 'tests/accessibility';

test('Tests › Accessibility', async ({ page, browser }) => {
    await page.goto('/accessibility');
    await expect(page.getByRole('heading', { name: 'Accessibility', level: 1 })).toBeVisible();
    await settle(page);
    const main = page.locator('main');
    await expect(main.getByText('No accessibility scans yet')).toBeVisible();
    const waivers = main.getByRole('region', { name: 'Waivers' });

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    const windowSel = main.getByLabel('Window');
    const empty = main.getByText('No accessibility scans yet').locator('xpath=ancestor::div[contains(@class,"rounded")][1]');
    const clean = await annotate(page, [
        { n: 1, target: windowSel },
        { n: 2, target: empty },
        { n: 3, target: waivers },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. The waiver form, opened and closed again.
    await waivers.getByRole('button', { name: 'New waiver' }).click();
    const form = main.getByRole('form', { name: 'New waiver' });
    await expect(form).toBeVisible();
    await beat(page, 400);
    await waivers.screenshot({ path: out(`${DIR}/waiver-form.png`) });
    await form.getByRole('button', { name: 'Cancel' }).click();
    await expect(form).toBeHidden();

    // 4. A short walkthrough video.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/accessibility');
        await expect(p.getByRole('heading', { name: 'Accessibility', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        const w = m.getByRole('region', { name: 'Waivers' });
        await caption(p, 'Accessibility collects every problem the accessibility checks in your tests have found.', 2000);
        await caption(p, 'A problem is something that makes the page hard to use, for example with a screen reader.');
        await beat(p, 1200);
        await caption(p, 'Results appear once a test has a check-accessibility step and has run.', {
            say: 'Results appear once a test has a check accessibility step, and has run.',
        });
        await pointAt(p, m.getByText('No accessibility scans yet'), 2400);
        await caption(p, 'The window chooses how many days of scans to look at.');
        await pointAt(p, m.getByLabel('Window'), 2000);
        await caption(p, 'A waiver sets a known problem aside for up to 90 days, so it doesn’t block a release.', {
            say: 'A waiver sets a known problem aside for up to ninety days, so that it doesn’t block a release.',
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

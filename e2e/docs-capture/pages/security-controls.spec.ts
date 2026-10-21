import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/security/controls.html
// The demo workspace has no controls. Nothing here changes data: "Load SOC 2
// starter set" is never pressed, and the New control form is filled in the
// browser and closed with Cancel ("Create control" is never pressed).

const DIR = 'security/controls';

async function open(p: Page) {
    await p.goto('/compliance/controls');
    await expect(p.getByRole('heading', { name: 'Controls', level: 1 })).toBeVisible();
    await settle(p);
    await expect(p.getByText('No controls yet', { exact: true })).toBeVisible();
}

test('Security › Controls', async ({ page, browser }) => {
    await open(page);
    const main = page.locator('main');
    await expect(main.getByLabel('Workspace')).toHaveValue(/\d+/);

    // 1. The page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });
    const actions = main.getByRole('button', { name: 'New control' }).locator('xpath=..');
    await actions.screenshot({ path: out(`${DIR}/actions.png`) });

    // 2. The New control form, numbered (not saved).
    await main.getByRole('button', { name: 'New control' }).click();
    const form = main.locator('section').filter({ has: page.getByRole('heading', { name: 'New control' }) }).first();
    await expect(form.getByLabel('Reference')).toBeVisible();
    await form.getByLabel('Reference').fill('CC6.2');
    await form.getByLabel('Title').fill('Access is reviewed every quarter');
    await form.getByLabel(/Access review signed off within N days/).check();
    await form.getByLabel(/No open security findings past their SLA/).check();
    const fields = form.getByLabel('Framework').locator('xpath=ancestor::div[contains(@class,"divide-y")][1]');
    const sources = form.getByText('Automated evidence sources', { exact: true }).locator('xpath=..');
    await page.setViewportSize({ width: 1440, height: 1700 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: main.getByLabel('Workspace').locator('xpath=..') },
        { n: 2, target: main.getByRole('button', { name: 'Load SOC 2 starter set' }) },
        { n: 3, target: main.getByRole('button', { name: 'New control' }), side: 'right' },
        { n: 4, target: fields },
        { n: 5, target: sources },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();
    await fields.screenshot({ path: out(`${DIR}/fields.png`) });
    await sources.screenshot({ path: out(`${DIR}/sources.png`) });
    await form.getByRole('button', { name: 'Cancel' }).click();
    await page.setViewportSize({ width: 1440, height: 900 });

    // 3. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await open(p);
        const m = p.locator('main');
        await caption(p, 'A control is one promise your organisation makes about security, for example "access is reviewed every quarter".', 2000);
        await caption(p, 'This page lists your controls and checks, from TraceIQ\'s own records, whether each promise is being kept.');
        await beat(p, 1200);
        await caption(p, 'The demo workspace has no controls yet. An admin can load a ready-made set for SOC 2, a common security standard.');
        await pointAt(p, m.getByRole('button', { name: 'Load SOC 2 starter set' }), 2400);
        await caption(p, 'Or write your own with New control.');
        const nb = m.getByRole('button', { name: 'New control' });
        await pointAt(p, nb, 600);
        await nb.click();
        const f = m.locator('section').filter({ has: p.getByRole('heading', { name: 'New control' }) }).first();
        await expect(f.getByLabel('Reference')).toBeVisible();
        await caption(p, 'A control has a standard, a reference in it, a title, and an owner.');
        await f.getByLabel('Reference').pressSequentially('CC6.2', { delay: 50 });
        await f.getByLabel('Title').pressSequentially('Access is reviewed every quarter', { delay: 30 });
        await pointAt(p, f.getByLabel('Owner'), 1200);
        await caption(p, 'Freshness says how old the newest proof may be before the control counts as stale.');
        await pointAt(p, f.getByLabel('Freshness SLA (days)'), 2200);
        const box = f.getByLabel(/Access review signed off within N days/);
        await caption(p, 'Automated sources are checks TraceIQ makes for you, such as a recent access review.');
        await box.scrollIntoViewIfNeeded();
        await pointAt(p, box, 600);
        await box.check();
        await beat(p, 1600);
        await caption(p, 'Once saved, people can add their own evidence, and admins can approve a time-limited exception.');
        await pointAt(p, f.getByRole('button', { name: 'Create control' }), 2400);
        await caption(p, '');
        await f.getByRole('button', { name: 'Cancel' }).click();
    });
});

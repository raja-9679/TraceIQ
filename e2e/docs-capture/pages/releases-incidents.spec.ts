import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/releases/incidents.html
// Nothing here changes data: the New incident form is opened, typed into and
// cancelled (Record incident is never pressed); the webhook panel is opened
// and closed (no secret is created or rotated). The Project menu is not used.

const DIR = 'releases/incidents';

const open = async (p: Page) => {
    await p.goto('/incidents');
    await expect(p.getByRole('heading', { name: 'Incidents', level: 1 })).toBeVisible();
    await expect(p.locator('main').getByText(/Escaped defects \(180d\)/)).toBeVisible();
    await settle(p);
};

test('Releases › Incidents', async ({ page, browser }) => {
    await open(page);
    const main = page.locator('main');
    const tiles = main.getByText(/Escaped defects \(180d\)/).locator('xpath=ancestor::div[contains(@class,"grid")][1]');
    const bar = main.getByRole('group', { name: 'Filter incidents' }).locator('xpath=..');
    const list = bar.locator('xpath=following-sibling::div[1]');
    const picker = main.getByRole('combobox', { name: 'Project' }).locator('xpath=..');

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    const clean = await annotate(page, [
        { n: 1, target: picker },
        { n: 2, target: tiles },
        { n: 3, target: bar },
        { n: 4, target: list },
        { n: 5, target: main.getByRole('button', { name: 'Webhook setup' }) },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await tiles.screenshot({ path: out(`${DIR}/tiles.png`) });
    await bar.screenshot({ path: out(`${DIR}/toolbar.png`) });
    await list.screenshot({ path: out(`${DIR}/empty.png`) });

    // The New incident form (opened and cancelled) and the webhook panel.
    await page.setViewportSize({ width: 1440, height: 1100 });
    await main.getByRole('button', { name: 'New incident' }).click();
    const form = main.getByRole('heading', { name: 'New incident' }).locator('xpath=..');
    await expect(form).toBeVisible();
    await form.screenshot({ path: out(`${DIR}/new-incident.png`) });
    await form.getByRole('button', { name: 'Cancel' }).click();
    await main.getByRole('button', { name: 'Webhook setup' }).click();
    const hook = main.getByRole('heading', { name: 'Inbound webhook' }).locator('xpath=..');
    await expect(hook).toBeVisible();
    await settle(page);
    await hook.screenshot({ path: out(`${DIR}/webhook.png`) });
    await main.getByRole('button', { name: 'Hide webhook setup' }).click();
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await open(p);
        const m = p.locator('main');
        await caption(p, 'Incidents records problems that happened in production, for the selected project.');
        await pointAt(p, m.getByRole('heading', { name: 'Incidents', level: 1 }), 2200);
        await caption(p, 'The tiles count escaped defects: bugs your tests let through, over the last 180 days.', {
            say: 'The tiles count escaped defects: bugs your tests let through, over the last hundred and eighty days.',
        });
        await pointAt(p, m.getByText(/Escaped defects \(180d\)/), 1600);
        await pointAt(p, m.getByText('No test existed', { exact: true }), 1400);
        await caption(p, 'The buttons show all incidents, only open ones, or only escaped defects.');
        await pointAt(p, m.getByRole('group', { name: 'Filter incidents' }), 2400);
        await caption(p, 'New incident records one by hand. The demo project has none yet.');
        await pointAt(p, m.getByRole('button', { name: 'New incident' }), 800);
        await m.getByRole('button', { name: 'New incident' }).click();
        const f = m.getByRole('heading', { name: 'New incident' }).locator('xpath=..');
        const title = f.getByLabel('Title');
        await title.click();
        await title.pressSequentially('Checkout returns 500 for guest users', { delay: 60 });
        await caption(p, 'Give it a title, a severity and the service. TraceIQ can then suggest a test that would catch it.');
        await pointAt(p, f.getByLabel('Severity'), 1400);
        await pointAt(p, f.getByText('Propose a regression test'), 2200);
        await caption(p, 'Cancel closes the form without saving anything.');
        await pointAt(p, f.getByRole('button', { name: 'Cancel' }), 800);
        await f.getByRole('button', { name: 'Cancel' }).click();
        await beat(p, 800);
        await caption(p, 'Webhook setup lets your paging tool send incidents here by itself.');
        await pointAt(p, m.getByRole('button', { name: 'Webhook setup' }), 800);
        await m.getByRole('button', { name: 'Webhook setup' }).click();
        await settle(p);
        await pointAt(p, m.getByRole('heading', { name: 'Inbound webhook' }), 2400);
        await m.getByRole('button', { name: 'Hide webhook setup' }).click();
        await caption(p, '');
    });
});

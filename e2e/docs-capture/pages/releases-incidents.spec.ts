import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/releases/incidents.html
// Nothing here changes data: incidents are selected in the list (that only
// changes which one the detail card shows), the New incident form is opened,
// typed into and cancelled (Record incident is never pressed); the webhook
// panel is opened and closed (no secret is created or rotated). The tabs only
// filter the list. The page follows the sidebar's project (the demo's web project).

const DIR = 'releases/incidents';

const open = async (p: Page) => {
    await p.goto('/incidents');
    await expect(p.getByRole('heading', { name: 'Incidents', level: 1 })).toBeVisible();
    await expect(p.locator('main').getByText(/Escaped defects \(180d\)/)).toBeVisible();
    await expect(p.locator('main').getByRole('article', { name: /^Incident / })).toBeVisible();
    await settle(p);
};
// The left-hand list: the section holding the status groups' rows.
const listOf = (p: Page) => p.locator('main section').filter({ has: p.locator('button[aria-pressed]') }).first();

test('Releases › Incidents', async ({ page, browser }) => {
    await open(page);
    const main = page.locator('main');
    const header = main.getByRole('heading', { name: 'Incidents', level: 1 }).locator('xpath=ancestor::div[contains(@class,"rounded-3xl")][1]');
    const actions = main.getByRole('button', { name: 'Webhook setup' }).locator('xpath=..');
    const tabs = main.getByRole('tablist', { name: 'Filter incidents' });
    const tiles = main.getByText(/Escaped defects \(180d\)/).locator('xpath=ancestor::div[contains(@class,"grid")][1]');
    const list = listOf(page);
    const detail = main.getByRole('article', { name: /^Incident / });
    const months = main.getByRole('heading', { name: 'Escaped defects by month' }).locator('xpath=ancestor::div[contains(@class,"rounded-3xl")][1]');

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered. Tall enough to show the monthly table at the bottom.
    await page.setViewportSize({ width: 1440, height: 1250 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: actions },
        { n: 2, target: tabs },
        { n: 3, target: tiles },
        { n: 4, target: list, side: 'left' },
        { n: 5, target: detail },
        { n: 6, target: months },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await header.screenshot({ path: out(`${DIR}/toolbar.png`) });
    await tiles.screenshot({ path: out(`${DIR}/tiles.png`) });
    await list.screenshot({ path: out(`${DIR}/list.png`) });
    await months.screenshot({ path: out(`${DIR}/months.png`) });

    // The escaped defect, selected (a click only changes the detail card).
    await list.getByRole('button', { name: /Todos disappear after adding a second item/ }).click();
    await expect(detail.getByText('Should have been caught by')).toBeVisible();
    await settle(page);
    await detail.screenshot({ path: out(`${DIR}/detail.png`) });

    // The New incident form (opened and cancelled) and the webhook panel.
    await page.setViewportSize({ width: 1440, height: 1100 });
    await main.getByRole('button', { name: 'New incident' }).click();
    const form = main.getByRole('region', { name: 'New incident' });
    await expect(form).toBeVisible();
    await form.screenshot({ path: out(`${DIR}/new-incident.png`) });
    await form.getByRole('button', { name: 'Cancel' }).click();
    await main.getByRole('button', { name: 'Webhook setup' }).click();
    const hook = main.getByRole('region', { name: 'Inbound webhook' });
    await expect(hook).toBeVisible();
    await settle(page);
    await hook.screenshot({ path: out(`${DIR}/webhook.png`) });
    await main.getByRole('button', { name: 'Hide webhook setup' }).click();
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await open(p);
        const m = p.locator('main');
        const l = listOf(p);
        const d = m.getByRole('article', { name: /^Incident / });
        await caption(p, 'Incidents records problems that happened in production, for the project chosen in the sidebar.');
        await pointAt(p, m.getByRole('heading', { name: 'Incidents', level: 1 }), 2200);
        await caption(p, 'The tiles count escaped defects: bugs your tests let through, over the last 180 days.', {
            say: 'The tiles count escaped defects: bugs your tests let through, over the last hundred and eighty days.',
        });
        await pointAt(p, m.getByText(/Escaped defects \(180d\)/), 1600);
        await pointAt(p, m.getByText('No test existed', { exact: true }).first(), 1400);
        await caption(p, 'The tabs show all incidents, only open ones, or only escaped defects.');
        await pointAt(p, m.getByRole('tablist', { name: 'Filter incidents' }), 2400);
        await caption(p, 'The list is grouped by status. Recurrence and Escaped labels sit on the right of a row.');
        await pointAt(p, l.getByText(/#\d/).first(), 1600);
        await pointAt(p, l.getByText('Escaped', { exact: true }).first(), 1600);
        await caption(p, 'Click an incident to see it on the right, where its status is changed and escaped defects are marked.');
        const esc = l.getByRole('button', { name: /Todos disappear after adding a second item/ });
        await pointAt(p, esc, 800);
        await esc.click();
        await settle(p);
        await pointAt(p, d.getByLabel('Status'), 1400);
        await pointAt(p, d.getByText('Escaped defect', { exact: true }), 1800);
        await caption(p, 'New incident records one by hand.');
        await pointAt(p, m.getByRole('button', { name: 'New incident' }), 800);
        await m.getByRole('button', { name: 'New incident' }).click();
        const f = m.getByRole('region', { name: 'New incident' });
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
        await pointAt(p, m.getByRole('region', { name: 'Inbound webhook' }), 2400);
        await m.getByRole('button', { name: 'Hide webhook setup' }).click();
        await caption(p, '');
    });
});

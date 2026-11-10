import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/security/script-inventory.html
// The demo's web project (the sidebar's active project) monitors one page
// ("Checkout page (demo)", two of its four scripts unapproved). Nothing here
// changes data: the "Monitor a page" form is filled in the browser but "Save
// page" is never pressed; "Check now", "Delete page" and the approve controls
// are never used, and the sidebar's project switcher is never touched.

const DIR = 'security/script-inventory';
const PAGE = 'Checkout page (demo)';

async function open(p: Page) {
    await p.goto('/script-inventory');
    await expect(p.getByRole('heading', { name: 'Script inventory', level: 1 })).toBeVisible();
    // With nothing in the address, the first monitored page opens by itself.
    await expect(p.locator('main').getByRole('heading', { name: PAGE, level: 2 })).toBeVisible();
    await settle(p);
}

test('Security › Script inventory', async ({ page, browser }) => {
    await open(page);
    const main = page.locator('main');
    const nav = main.getByRole('navigation', { name: 'Monitored pages' });

    // 1. The page as a reader first meets it: the demo page open on Scripts.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. The whole page, numbered.
    const card = main.getByRole('article', { name: `Monitored page ${PAGE}` });
    const title = card.getByRole('heading', { name: PAGE, level: 2 }).locator('xpath=ancestor::div[contains(@class,"flex-col")][1]');
    const actions = card.getByRole('button', { name: 'Check now' }).locator('xpath=..');
    const tabs = card.getByRole('tablist', { name: 'Page views' });
    const table = card.locator('table').locator('xpath=..');
    await page.setViewportSize({ width: 1440, height: 1300 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: main.getByRole('button', { name: 'Monitor a page' }) },
        { n: 2, target: nav },
        { n: 3, target: title, side: 'right' },
        { n: 4, target: actions },
        { n: 5, target: tabs, side: 'right' },
        { n: 6, target: table, side: 'right' },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();
    await card.screenshot({ path: out(`${DIR}/monitored-page.png`) });
    await tabs.getByRole('tab', { name: 'Change history' }).click();
    await settle(page);
    await card.screenshot({ path: out(`${DIR}/history.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });
    await settle(page);

    // 3. The form behind "Monitor a page" (not saved).
    await main.getByRole('button', { name: 'Monitor a page' }).click();
    const form = main.getByRole('article', { name: 'Monitor a page' }).locator('form');
    await expect(form).toBeVisible();
    await form.getByLabel('Name').fill('Checkout');
    await form.getByLabel(/^URL/).fill('https://shop.example.com/checkout');
    await form.getByLabel('Defacement checks').check();
    const fields = form.getByLabel('Name').locator('xpath=ancestor::div[contains(@class,"grid")][1]');
    const toggles = form.getByLabel('Scheduled checks').locator('xpath=ancestor::div[1]');
    const deface = form.locator('fieldset');
    await fields.screenshot({ path: out(`${DIR}/fields.png`) });
    await toggles.screenshot({ path: out(`${DIR}/toggles.png`) });
    await deface.screenshot({ path: out(`${DIR}/defacement.png`) });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await open(p);
        const m = p.locator('main');
        await caption(p, 'Script inventory keeps a list of every script that runs on your most sensitive pages, such as checkout.', 2000);
        await caption(p, 'A script added or changed by an attacker can steal card details, so each one must be approved.');
        await beat(p, 1000);
        await caption(p, 'The list on the left shows each watched page: here a checkout page with two unapproved scripts.');
        await pointAt(p, m.getByRole('navigation', { name: 'Monitored pages' }).getByText(PAGE), 1800);
        const c = m.getByRole('article', { name: `Monitored page ${PAGE}` });
        await caption(p, 'On the right, every script on that page, with its fingerprint and whether it is approved.');
        await pointAt(p, c.locator('table'), 2400);
        await caption(p, 'Someone approves each script with a written reason.');
        await pointAt(p, c.getByRole('button', { name: 'Approve…' }).first(), 1800);
        await caption(p, 'Change history shows what changed and when.');
        const h = c.getByRole('tab', { name: 'Change history' });
        await pointAt(p, h, 600);
        await h.click();
        await settle(p);
        await beat(p, 1400);
        await caption(p, 'Monitor a page adds another page to watch.');
        const nb = m.getByRole('button', { name: 'Monitor a page' });
        await pointAt(p, nb, 600);
        await nb.click();
        const f = m.getByRole('article', { name: 'Monitor a page' }).locator('form');
        await expect(f).toBeVisible();
        await caption(p, 'Give the page a name and its address. The site must be on the project\'s list of sites that may be tested.');
        await f.getByLabel('Name').pressSequentially('Checkout', { delay: 40 });
        await f.getByLabel(/^URL/).pressSequentially('https://shop.example.com/checkout', { delay: 25 });
        await caption(p, 'The schedule says how often to look. Card-industry rules ask for at least once a week.');
        await pointAt(p, f.getByLabel(/Check schedule/), 2200);
        await caption(p, 'Script inventory records the scripts. Defacement checks also watch the page\'s text.');
        await pointAt(p, f.getByLabel('Script inventory'), 1200);
        const d = f.getByLabel('Defacement checks');
        await pointAt(p, d, 600);
        await d.check();
        await caption(p, 'You can be warned when the text changes a lot, or when words that should never appear show up.');
        await pointAt(p, f.locator('fieldset'), 2400);
        await caption(p, 'Save page keeps it; the first check then records every script on it.');
        await pointAt(p, f.getByRole('button', { name: 'Save page' }), 2200);
        await caption(p, '');
    });
});

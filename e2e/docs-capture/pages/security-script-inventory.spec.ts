import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/security/script-inventory.html
// The demo's web project monitors one page ("Checkout page (demo)", two of
// its four scripts unapproved). Nothing here changes data: the "Monitor a
// page" form is filled in the browser but "Save page" is never pressed,
// "Check now", "Delete page" and the approve controls are never used, and the
// page's Project menu is never touched.

const DIR = 'security/script-inventory';

async function open(p: Page) {
    await p.goto('/script-inventory');
    await expect(p.getByRole('heading', { name: 'Script Inventory', level: 1 })).toBeVisible();
    await settle(p);
    await expect(p.getByText('Checkout page (demo)')).toBeVisible();
}

test('Security › Script inventory', async ({ page, browser }) => {
    await open(page);
    const main = page.locator('main');

    // 1. The page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. The form behind "Monitor a page", numbered (not saved).
    const nav = main.getByRole('navigation', { name: 'Monitored pages' });
    await nav.getByRole('button', { name: 'Monitor a page' }).click();
    const form = main.locator('form').filter({ has: page.getByRole('heading', { name: 'Monitor a page' }) });
    await expect(form).toBeVisible();
    await form.getByLabel('Name').fill('Checkout');
    await form.getByLabel(/^URL/).fill('https://shop.example.com/checkout');
    await form.getByLabel('Defacement checks').check();
    const fields = form.getByLabel('Name').locator('xpath=ancestor::div[contains(@class,"grid")][1]');
    const toggles = form.getByLabel('Scheduled checks').locator('xpath=ancestor::div[1]');
    const deface = form.locator('fieldset');
    await page.setViewportSize({ width: 1440, height: 1000 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: nav.getByRole('button', { name: 'Monitor a page' }) },
        { n: 2, target: fields, side: 'right' },
        { n: 3, target: toggles },
        { n: 4, target: deface },
        { n: 5, target: form.getByRole('button', { name: 'Save page' }) },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();
    await fields.screenshot({ path: out(`${DIR}/fields.png`) });
    await toggles.screenshot({ path: out(`${DIR}/toggles.png`) });
    await deface.screenshot({ path: out(`${DIR}/defacement.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });

    // 3. The demo's monitored page: its scripts, then its change history.
    await nav.getByText('Checkout page (demo)').click();
    const title = main.getByRole('heading', { name: 'Checkout page (demo)', level: 2 });
    await expect(title).toBeVisible();
    await settle(page);
    const view = title.locator('xpath=ancestor::div[.//*[@role="tablist"]][1]');
    await page.setViewportSize({ width: 1440, height: 1300 });
    await settle(page);
    await view.screenshot({ path: out(`${DIR}/monitored-page.png`) });
    await main.getByRole('tab', { name: 'Change history' }).click();
    await settle(page);
    await view.screenshot({ path: out(`${DIR}/history.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await open(p);
        const m = p.locator('main');
        await caption(p, 'Script inventory keeps a list of every script that runs on your most sensitive pages, such as checkout.', 2000);
        await caption(p, 'A script added or changed by an attacker can steal card details, so each one must be approved.');
        await beat(p, 1400);
        await caption(p, 'The list shows each watched page: here a checkout page with two unapproved scripts. Monitor a page adds another.');
        const nb = m.getByRole('button', { name: 'Monitor a page' });
        await pointAt(p, nb, 600);
        await nb.click();
        const f = m.locator('form').filter({ has: p.getByRole('heading', { name: 'Monitor a page' }) });
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
        await caption(p, 'Once saved and checked, every script is listed with its fingerprint, and someone approves it with a reason.');
        await pointAt(p, f.getByRole('button', { name: 'Save page' }), 2200);
        await caption(p, '');
    });
});

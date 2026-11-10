import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/security/estate.html
// The demo's web project (the sidebar's active project) has one estate
// ("example.com estate (demo)", two domains, scanned). Nothing here changes
// data: the New estate form is opened but "Save estate" is never pressed;
// "Scan now", "Settings", "Delete estate", "Remove …" and "Import domains" are
// never pressed, and the sidebar's project switcher is never touched.

const DIR = 'security/estate';
const ESTATE = 'example.com estate (demo)';

async function open(p: Page) {
    await p.goto('/estate');
    await expect(p.getByRole('heading', { name: 'Estate', level: 1 })).toBeVisible();
    // With nothing in the address, the first estate opens by itself.
    await expect(p.locator('main').getByRole('heading', { name: ESTATE, level: 2 })).toBeVisible();
    await settle(p);
}

test('Security › Estate', async ({ page, browser }) => {
    await open(page);
    const main = page.locator('main');
    const nav = main.getByRole('navigation', { name: 'Estates' });
    const tabs = main.getByRole('tablist', { name: 'Estate views' });

    // 1. The page as a reader first meets it: the first estate open.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. One domain opened, the whole page numbered.
    const card = main.getByRole('article', { name: `Estate ${ESTATE}` });
    await card.locator('table tbody tr').first().getByRole('button').first().click();
    await expect(main.getByText('How the score is made')).toBeVisible();
    await settle(page);
    await page.setViewportSize({ width: 1440, height: 2300 });
    await settle(page);
    const top = card.getByRole('heading', { name: ESTATE, level: 2 }).locator('xpath=ancestor::div[contains(@class,"justify-between")][1]');
    const domains = card.locator('table').locator('xpath=ancestor::div[contains(@class,"flex-col")][1]');
    const importForm = card.locator('form');
    const drill = main.getByRole('article', { name: /^Checks for / });
    const history = main.getByRole('heading', { name: 'Scan history' }).locator('xpath=ancestor::div[contains(@class,"rounded-3xl")][1]');
    const clean = await annotate(page, [
        { n: 1, target: main.getByRole('button', { name: 'New estate' }) },
        { n: 2, target: tabs },
        { n: 3, target: nav },
        { n: 4, target: top, side: 'right' },
        { n: 5, target: domains, side: 'right' },
        { n: 6, target: importForm, side: 'right' },
        { n: 7, target: drill, side: 'right' },
        { n: 8, target: history, side: 'right' },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();
    await card.screenshot({ path: out(`${DIR}/estate.png`) });
    await drill.screenshot({ path: out(`${DIR}/domain.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });
    await settle(page);

    // 3. The New estate form (not saved).
    await main.getByRole('button', { name: 'New estate' }).click();
    const form = main.getByRole('article', { name: 'New estate' }).locator('form');
    await expect(form).toBeVisible();
    await form.getByLabel('Name').fill('Public websites');
    await main.getByRole('article', { name: 'New estate' }).screenshot({ path: out(`${DIR}/new-estate.png`) });

    // 4. The certificate calendar.
    await tabs.getByRole('tab', { name: 'Certificate calendar' }).click();
    await settle(page);
    await expect(main.locator('table')).toBeVisible();
    await main.getByRole('article', { name: 'Certificate calendar' }).screenshot({ path: out(`${DIR}/certificates.png`) });

    // 5. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await open(p);
        const m = p.locator('main');
        const n = m.getByRole('navigation', { name: 'Estates' });
        await caption(p, 'Your estate is every web address your organisation owns, as the outside world sees it.', 2000);
        await caption(p, 'TraceIQ checks each one and gives it a score out of one hundred, worst first.');
        await beat(p, 1200);
        await caption(p, 'An estate is a named group of domains. The number next to it is its worst domain\'s score.');
        await pointAt(p, n.getByText(ESTATE), 1800);
        await caption(p, 'The estate opens on the right, with its domains, lowest score first.');
        const c = m.getByRole('article', { name: `Estate ${ESTATE}` });
        await pointAt(p, c.locator('table'), 2000);
        await caption(p, 'Click a domain to see every check and how its score is made.');
        const d = c.locator('table tbody tr').first().getByRole('button').first();
        await pointAt(p, d, 600);
        await d.click();
        const drill = m.getByRole('article', { name: /^Checks for / });
        await expect(drill).toBeVisible();
        await drill.scrollIntoViewIfNeeded();
        await pointAt(p, drill.getByText('How the score is made'), 2400);
        await caption(p, 'New estate starts one: a name, how often to check, and who to warn.');
        await p.mouse.wheel(0, -4000);
        const nb = m.getByRole('button', { name: 'New estate' });
        await pointAt(p, nb, 600);
        await nb.click();
        const f = m.getByRole('article', { name: 'New estate' }).locator('form');
        await expect(f).toBeVisible();
        await f.getByLabel('Name').pressSequentially('Public websites', { delay: 40 });
        await pointAt(p, f.getByLabel(/Scan schedule/), 1400);
        await caption(p, 'Certificate alerts warn you a set number of days before a site\'s certificate runs out.');
        await pointAt(p, f.getByLabel(/Certificate alerts/), 2200);
        await caption(p, 'TraceIQ can also find more of your sub-domains in public certificate records.');
        await pointAt(p, f.getByText(/Discover subdomains/), 2200);
        await caption(p, 'After saving, you add the domains, and each one is only checked if it is on the project\'s list of sites that may be tested.');
        await pointAt(p, f.getByRole('button', { name: 'Save estate' }), 2200);
        await caption(p, 'The Certificate calendar tab lists every certificate in the workspace, the soonest to run out first.');
        const cb = m.getByRole('tablist', { name: 'Estate views' }).getByRole('tab', { name: 'Certificate calendar' });
        await pointAt(p, cb, 600);
        await cb.click();
        await settle(p);
        await pointAt(p, m.locator('table'), 2200);
        await caption(p, '');
    });
});

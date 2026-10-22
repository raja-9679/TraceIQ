import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/security/estate.html
// The demo's web project has one estate ("example.com estate (demo)", two
// domains, scanned). Nothing here changes data: the New estate form is opened
// but "Save estate" is never pressed, "Scan now", "Settings", "Delete estate",
// "Remove" and "Import domains" are never pressed, and the page's Project menu
// is never touched (it switches the active project).

const DIR = 'security/estate';

async function open(p: Page) {
    await p.goto('/estate');
    await expect(p.getByRole('heading', { name: 'Estate', level: 1 })).toBeVisible();
    await settle(p);
    await expect(p.getByText('example.com estate (demo)')).toBeVisible();
}

test('Security › Estate', async ({ page, browser }) => {
    await open(page);
    const main = page.locator('main');

    // 1. The page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. The New estate form, numbered (not saved).
    const nav = main.getByRole('navigation', { name: 'Estates' });
    await nav.getByRole('button', { name: 'New estate' }).click();
    const form = main.locator('form').filter({ has: page.getByRole('heading', { name: 'New estate' }) });
    await expect(form).toBeVisible();
    await form.getByLabel('Name').fill('Public websites');
    const picker = main.getByLabel('Project');
    const buttons = nav.getByRole('button', { name: 'New estate' }).locator('xpath=..');
    const clean = await annotate(page, [
        { n: 1, target: nav.getByRole('button', { name: 'New estate' }) },
        { n: 2, target: nav.getByRole('button', { name: 'Certificate calendar' }) },
        { n: 3, target: picker, side: 'right' },
        { n: 4, target: form },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();
    await form.screenshot({ path: out(`${DIR}/new-estate.png`) });
    await expect(buttons).toBeVisible();

    // 3. Inside the demo estate: the domain table and one domain's checks.
    await nav.getByText('example.com estate (demo)').click();
    await expect(main.getByRole('heading', { name: 'example.com estate (demo)', level: 2 })).toBeVisible();
    await settle(page);
    const firstDomain = main.locator('table tbody tr').first().getByRole('button').first();
    await firstDomain.click();
    await expect(main.getByRole('heading', { name: 'How the score is made' })).toBeVisible();
    await settle(page);
    await page.setViewportSize({ width: 1440, height: 1500 });
    await settle(page);
    await main.getByRole('heading', { name: 'example.com estate (demo)', level: 2 })
        .locator('xpath=ancestor::div[contains(@class,"space-y-5")][1]')
        .screenshot({ path: out(`${DIR}/estate.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. The certificate calendar.
    await nav.getByRole('button', { name: 'Certificate calendar' }).click();
    await settle(page);
    await expect(main.locator('table')).toBeVisible();
    await main.locator('table').locator('xpath=..').screenshot({ path: out(`${DIR}/certificates.png`) });

    // 5. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await open(p);
        const m = p.locator('main');
        const n = m.getByRole('navigation', { name: 'Estates' });
        await caption(p, 'Your estate is every web address your organisation owns, as the outside world sees it.', 2000);
        await caption(p, 'TraceIQ checks each one and gives it a score out of one hundred, worst first.');
        await beat(p, 1600);
        await caption(p, 'An estate is a named group of domains. The number is its worst domain\'s score.');
        await pointAt(p, n.getByText('example.com estate (demo)'), 1800);
        await caption(p, 'New estate starts one: a name, how often to check, and who to warn.');
        const nb = n.getByRole('button', { name: 'New estate' });
        await pointAt(p, nb, 600);
        await nb.click();
        const f = m.locator('form').filter({ has: p.getByRole('heading', { name: 'New estate' }) });
        await expect(f).toBeVisible();
        await f.getByLabel('Name').pressSequentially('Public websites', { delay: 40 });
        await pointAt(p, f.getByLabel(/Scan schedule/), 1400);
        await caption(p, 'Certificate alerts warn you a set number of days before a site\'s certificate runs out.');
        await pointAt(p, f.getByLabel(/Certificate alerts/), 2200);
        await caption(p, 'TraceIQ can also find more of your sub-domains in public certificate records.');
        await pointAt(p, f.getByText(/Discover subdomains/), 2200);
        await caption(p, 'After saving, you add the domains, and each one is only checked if it is on the project\'s list of sites that may be tested.');
        await pointAt(p, f.getByRole('button', { name: 'Save estate' }), 2200);
        await caption(p, 'Certificate calendar lists every certificate in the workspace, the soonest to run out first.');
        const cb = n.getByRole('button', { name: 'Certificate calendar' });
        await pointAt(p, cb, 600);
        await cb.click();
        await settle(p);
        await pointAt(p, m.locator('table'), 2200);
        await caption(p, '');
    });
});

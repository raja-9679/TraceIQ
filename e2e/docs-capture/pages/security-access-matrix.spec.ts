import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/security/access-matrix.html
// The demo has no matrices, so the spec opens "New matrix" and fills in a
// draft in the browser only. Nothing here changes data: "Create matrix" is
// never pressed (the draft is thrown away when the page is left), and the
// page's own Project menu is never touched (it switches the active project).

const DIR = 'security/access-matrix';

async function open(p: Page) {
    await p.goto('/access-matrix');
    await expect(p.getByRole('heading', { name: 'Access Matrix', level: 1 })).toBeVisible();
    await settle(p);
}

/** Fills the new-matrix form with an example (local state only). */
async function draft(p: Page, slow = false) {
    const m = p.locator('main');
    const pause = (ms: number) => (slow ? beat(p, ms) : Promise.resolve());
    await m.getByLabel('Name', { exact: true }).fill('Todo items: who may see them');
    await m.getByLabel('Base URL (for relative paths)').fill('https://app.example.com');
    await pause(400);
    await m.getByLabel('alice (todolite)').check();
    await m.getByLabel('bob (todolite)').check();
    await m.getByLabel(/Anonymous column/).check();
    await pause(400);
    const add = m.getByRole('button', { name: 'Add row' });
    await add.click();
    await add.click();
    const rows = m.locator('table tbody tr');
    await rows.nth(0).getByLabel('Label').fill('My todo list');
    await rows.nth(0).getByLabel('Target').selectOption('page');
    await rows.nth(0).getByLabel('Page URL').fill('/todos');
    await rows.nth(1).getByLabel('Label').fill('Admin user list');
    await rows.nth(1).getByLabel('URL or path').fill('/api/admin/users');
    // Click cycles Skip → Allow → Deny.
    const cell = (r: number, who: string) => rows.nth(r).getByRole('button', { name: new RegExp(`as ${who}`) });
    await cell(0, 'alice').click();
    await cell(0, 'bob').click();
    await cell(0, 'Anonymous').click();
    await cell(0, 'Anonymous').click();
    for (const who of ['alice', 'bob', 'Anonymous']) {
        await cell(1, who).click();
        await cell(1, who).click();
    }
    await pause(300);
}

test('Security › Access matrix', async ({ page, browser }) => {
    await open(page);
    const main = page.locator('main');
    await expect(main.getByText('No matrices in this project yet.')).toBeVisible();

    // 1. The page as a reader first meets it (the demo has no matrices yet).
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. A draft matrix in the editor, numbered.
    await main.getByRole('button', { name: 'New matrix' }).click();
    await expect(main.getByLabel('Name', { exact: true })).toBeVisible();
    await settle(page);
    await draft(page);
    const list = main.getByRole('navigation', { name: 'Access matrices' });
    const fields = main.getByLabel('Name', { exact: true }).locator('xpath=ancestor::div[contains(@class,"grid")][1]');
    const identities = main.locator('fieldset');
    const grid = main.locator('table').locator('xpath=..');
    const prereq = main.locator('section[aria-labelledby="am-prereq"]');
    const create = main.getByRole('button', { name: 'Create matrix' });
    await page.setViewportSize({ width: 1440, height: 1560 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: list.getByRole('button', { name: 'New matrix' }) },
        { n: 2, target: fields, side: 'right' },
        { n: 3, target: identities, side: 'right' },
        { n: 4, target: grid },
        { n: 5, target: prereq },
        { n: 6, target: create },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups used next to each explanation.
    await fields.screenshot({ path: out(`${DIR}/fields.png`) });
    await identities.screenshot({ path: out(`${DIR}/identities.png`) });
    await grid.screenshot({ path: out(`${DIR}/grid.png`) });
    await prereq.screenshot({ path: out(`${DIR}/prerequisites.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await open(p);
        const m = p.locator('main');
        await caption(p, 'An access matrix checks that each kind of user can reach only what they should.', 2000);
        await caption(p, 'The demo project has no matrix yet. New matrix starts one.');
        const nb = m.getByRole('button', { name: 'New matrix' });
        await pointAt(p, nb, 1000);
        await nb.click();
        await expect(m.getByLabel('Name', { exact: true })).toBeVisible();
        await settle(p);
        await caption(p, 'Give it a name, and the address of the app being checked.');
        await m.getByLabel('Name', { exact: true }).pressSequentially('Todo items: who may see them', { delay: 30 });
        await m.getByLabel('Base URL (for relative paths)').pressSequentially('https://app.example.com', { delay: 30 });
        await caption(p, 'The columns are identities: saved logins called personas, plus Anonymous, someone not logged in.');
        await pointAt(p, m.locator('fieldset'), 600);
        await m.getByLabel('alice (todolite)').check();
        await beat(p, 400);
        await m.getByLabel('bob (todolite)').check();
        await beat(p, 400);
        await m.getByLabel(/Anonymous column/).check();
        await beat(p, 600);
        await caption(p, 'Each row is something to try: a page, a request, or one of your tests.');
        const add = m.getByRole('button', { name: 'Add row' });
        await pointAt(p, add, 600);
        await add.click();
        const row = m.locator('table tbody tr').first();
        await row.getByLabel('Label').pressSequentially('Admin user list', { delay: 30 });
        await row.getByLabel('URL or path').pressSequentially('/api/admin/users', { delay: 30 });
        await caption(p, 'Click a cell to say what should happen: Allow, Deny, or Skip.');
        const cell = (who: string) => row.getByRole('button', { name: new RegExp(`as ${who}`) });
        await pointAt(p, cell('alice'), 600);
        await cell('alice').click();
        await beat(p, 700);
        await cell('alice').click();
        await beat(p, 500);
        for (const who of ['bob', 'Anonymous']) {
            await cell(who).click();
            await cell(who).click();
        }
        await beat(p, 600);
        await caption(p, 'Here nobody should reach the admin list, so every cell says Deny.');
        await pointAt(p, cell('Anonymous'), 2000);
        const pre = m.locator('section[aria-labelledby="am-prereq"]');
        await caption(p, 'Before it can run, the app\'s address must be on the project\'s list of sites that may be tested.');
        await pre.scrollIntoViewIfNeeded();
        await pointAt(p, pre.getByText(/Not on the authorized domain list/), 2600);
        await caption(p, 'Create matrix saves it. Then Runs and results shows, cell by cell, what really happened.');
        await pointAt(p, m.getByRole('button', { name: 'Create matrix' }), 2400);
        await caption(p, '');
    });
});

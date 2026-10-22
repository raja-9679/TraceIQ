import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/security/access-matrix.html
// The demo's web project has one saved matrix ("Todo app roles (demo)") with
// one finished run. Nothing here changes data: "Save changes", "Run matrix"
// and "Delete matrix" are never pressed, and the page's own Project menu is
// never touched (it switches the active project).

const DIR = 'security/access-matrix';
const MATRIX = 'Todo app roles (demo)';

async function open(p: Page) {
    await p.goto('/access-matrix');
    await expect(p.getByRole('heading', { name: 'Access Matrix', level: 1 })).toBeVisible();
    await settle(p);
}

async function openMatrix(p: Page) {
    const list = p.locator('main').getByRole('navigation', { name: 'Access matrices' });
    await list.getByText(MATRIX, { exact: true }).click();
    await expect(p.locator('main').getByLabel('Name', { exact: true })).toHaveValue(MATRIX);
    await settle(p);
}

test('Security › Access matrix', async ({ page, browser }) => {
    await open(page);
    const main = page.locator('main');
    const list = main.getByRole('navigation', { name: 'Access matrices' });
    await expect(list.getByText(MATRIX)).toBeVisible();

    // 1. The page as a reader first meets it: the list, nothing opened yet.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. The demo matrix opened on Edit grid, numbered.
    await openMatrix(page);
    const fields = main.getByLabel('Name', { exact: true }).locator('xpath=ancestor::div[contains(@class,"grid")][1]');
    const identities = main.locator('fieldset');
    const grid = main.locator('table').locator('xpath=..');
    const prereq = main.locator('section[aria-labelledby="am-prereq"]');
    const save = main.getByRole('button', { name: 'Save changes' });
    await page.setViewportSize({ width: 1440, height: 1960 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: list.getByRole('button', { name: 'New matrix' }) },
        { n: 2, target: fields, side: 'right' },
        { n: 3, target: identities, side: 'right' },
        { n: 4, target: grid },
        { n: 5, target: prereq },
        { n: 6, target: save },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups used next to each explanation.
    await fields.screenshot({ path: out(`${DIR}/fields.png`) });
    await identities.screenshot({ path: out(`${DIR}/identities.png`) });
    await grid.screenshot({ path: out(`${DIR}/grid.png`) });
    await prereq.screenshot({ path: out(`${DIR}/prerequisites.png`) });

    // 4. Runs & results: the demo run, its findings, and one cell's evidence.
    await main.getByRole('tab', { name: 'Runs & results' }).click();
    const results = main.locator('section[aria-labelledby="am-results"]');
    await expect(results.getByText('Results — run #', { exact: false })).toBeVisible();
    await settle(page);
    await main.getByRole('heading', { name: 'Run history' }).locator('xpath=ancestor::div[contains(@class,"grid")][1]')
        .screenshot({ path: out(`${DIR}/results.png`) });
    const findings = main.locator('section[aria-labelledby="am-findings"]');
    await expect(findings.getByRole('button', { name: /View evidence in run/ }).first()).toBeVisible();
    await findings.screenshot({ path: out(`${DIR}/findings.png`) });
    await results.getByRole('button', { name: /Admin panel as bob.*Show evidence/ }).click();
    const sheet = page.getByRole('dialog');
    await expect(sheet).toBeVisible();
    await settle(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await settle(page);
    await page.screenshot({ path: out(`${DIR}/evidence.png`) });

    // 5. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await open(p);
        const m = p.locator('main');
        await caption(p, 'An access matrix checks that each kind of user can reach only what they should.', 2000);
        await caption(p, 'The list on the left holds the project\'s matrices. Open one to see its grid.');
        await pointAt(p, m.getByRole('navigation', { name: 'Access matrices' }).getByText(MATRIX), 1200);
        await openMatrix(p);
        await caption(p, 'The columns are identities: saved logins called personas, plus Anonymous, someone not logged in.');
        await pointAt(p, m.locator('fieldset'), 2200);
        const g = m.locator('table');
        await g.scrollIntoViewIfNeeded();
        await caption(p, 'Each row is something to try: a page, a request, or one of your tests.');
        await pointAt(p, g.locator('tbody tr').first(), 2000);
        await caption(p, 'Each cell says what should happen: Allow, Deny, or Skip. A click changes it.');
        await pointAt(p, g.getByRole('button', { name: /Expectation for/ }).first(), 2200);
        await caption(p, 'Here only alice, the admin, should reach the admin panel.');
        await pointAt(p, g.locator('tbody tr').nth(1), 2200);
        await caption(p, '');
        await m.getByRole('tab', { name: 'Runs & results' }).click();
        await settle(p);
        await caption(p, 'Runs and results shows what really happened, cell by cell.');
        const r = m.locator('section[aria-labelledby="am-results"]');
        await pointAt(p, r.locator('table'), 2200);
        await caption(p, 'Red means access was granted that should have been denied. bob, a plain user, reached the admin panel.');
        const bad = r.getByRole('button', { name: /Admin panel as bob/ });
        await pointAt(p, bad, 2600);
        await caption(p, 'Click a cell to see its evidence.');
        await bad.click();
        await beat(p, 2200);
        await p.keyboard.press('Escape');
        await caption(p, 'Each such hole becomes a security finding, listed below the results.');
        const f = m.locator('section[aria-labelledby="am-findings"]');
        await f.scrollIntoViewIfNeeded();
        await pointAt(p, f, 2400);
        await caption(p, '');
    });
});

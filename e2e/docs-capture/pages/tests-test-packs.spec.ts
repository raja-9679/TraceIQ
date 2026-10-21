import { expect, test } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/tests/test-packs.html
// Nothing here changes data: a pack's case list is expanded, and its import
// dialog is opened and closed with Cancel without typing; "Import pack" is
// never pressed.

const DIR = 'tests/test-packs';
const PACK = 'SCIM 2.0 provisioning';

test('Tests › Test packs', async ({ page, browser }) => {
    await page.goto('/test-packs');
    await expect(page.getByRole('heading', { name: 'Test packs', level: 1 })).toBeVisible();
    await settle(page);
    const main = page.locator('main');
    const card = main.getByRole('article', { name: PACK });
    await expect(card).toBeVisible();

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. One pack's parts, numbered.
    const titleRow = card.getByRole('heading', { name: PACK }).locator('xpath=..');
    const counts = card.getByText(/cases · \d+ variables/);
    const show = card.getByRole('button', { name: 'Show cases' });
    const importBtn = card.getByRole('button', { name: 'Import into a project' });
    const clean = await annotate(page, [
        { n: 1, target: titleRow, side: 'right' },
        { n: 2, target: counts, side: 'right' },
        { n: 3, target: show },
        { n: 4, target: importBtn },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups: the card with its cases open, and the import dialog.
    await show.click();
    await expect(card.getByRole('button', { name: 'Hide cases' })).toBeVisible();
    await beat(page, 300);
    await card.screenshot({ path: out(`${DIR}/pack-cases.png`) });
    await card.getByRole('button', { name: 'Hide cases' }).click();

    await importBtn.click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByText('Variables', { exact: true })).toBeVisible();
    await settle(page);
    await dialog.screenshot({ path: out(`${DIR}/import-dialog.png`) });
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();

    // 4. A short walkthrough video.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/test-packs');
        await expect(p.getByRole('heading', { name: 'Test packs', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        const c = m.getByRole('article', { name: PACK });
        await caption(p, 'Test packs are ready-made suites for common integrations, written by the TraceIQ team.', 2000);
        await caption(p, 'Each card is one pack: its name, its version, and what it checks.');
        await pointAt(p, c.getByRole('heading', { name: PACK }), 1400);
        await pointAt(p, c.getByText(/RFC 7643/), 1600);
        await caption(p, 'Show cases lists the tests inside, and the kinds of steps each one uses.');
        await pointAt(p, c.getByRole('button', { name: 'Show cases' }), 900);
        await c.getByRole('button', { name: 'Show cases' }).click();
        await beat(p, 2200);
        await c.getByRole('button', { name: 'Hide cases' }).click();
        await caption(p, 'Import into a project copies the pack into one of your projects as a new suite.');
        await pointAt(p, c.getByRole('button', { name: 'Import into a project' }), 900);
        await c.getByRole('button', { name: 'Import into a project' }).click();
        await settle(p);
        const d = p.getByRole('dialog');
        await caption(p, 'You choose the project, the environment, and fill in the pack’s values, such as your server’s address.');
        await pointAt(p, d.getByLabel('Project'), 1200);
        await pointAt(p, d.getByLabel('Environment for the variables'), 1200);
        await pointAt(p, d.getByLabel(/SCIM_BASE_URL/), 1400);
        await caption(p, 'Values marked Secret are stored encrypted, and are never shown again.');
        await pointAt(p, d.getByLabel(/SCIM_TOKEN/), 2200);
        await caption(p, 'Importing runs nothing. Cancel closes the dialog without importing.');
        await pointAt(p, d.getByRole('button', { name: 'Cancel' }), 1400);
        await d.getByRole('button', { name: 'Cancel' }).click();
        await beat(p, 600);
        await caption(p, '');
    });
});

import { expect, test } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/tests/import-playwright.html
// Nothing here changes data: the sample script is put in the text box and
// a suite and name are filled for the picture, but "Import case" is never
// pressed, and the page keeps nothing once left.

const DIR = 'tests/import-playwright';
const SUITE = 'Web smoke (demo)';

test('Tests › Import Playwright', async ({ page, browser }) => {
    await page.goto('/import-playwright');
    await expect(page.getByRole('heading', { name: 'Import a Playwright spec', level: 1 })).toBeVisible();
    await settle(page);
    const main = page.locator('main');

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Filled in (never submitted), with its parts numbered.
    await main.getByLabel('Target suite').click();
    await page.getByRole('option', { name: SUITE }).click();
    await main.getByLabel('Case name').fill('homepage-title.spec');
    await main.getByRole('button', { name: 'Insert sample' }).click();
    await beat(page, 300);
    const note = main.getByText(/Imported specs can't use/).locator('xpath=ancestor::div[1]');
    const top = main.getByLabel('Target suite').locator('xpath=ancestor::div[contains(@class,"grid")][1]');
    const script = main.getByLabel('Playwright spec');
    const importBtn = main.getByRole('button', { name: 'Import case' });
    const clean = await annotate(page, [
        { n: 1, target: note },
        { n: 2, target: top },
        { n: 3, target: script },
        { n: 4, target: importBtn, side: 'right' },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();
    await note.screenshot({ path: out(`${DIR}/note.png`) });
    await script.locator('xpath=ancestor::div[contains(@class,"rounded-3xl")][1]').screenshot({ path: out(`${DIR}/form.png`) });

    // 3. A short walkthrough video.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/import-playwright');
        await expect(p.getByRole('heading', { name: 'Import a Playwright spec', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        await caption(p, 'Import Playwright brings in a test that a developer has already written as Playwright code.', 2000);
        await caption(p, 'The note explains that such a test runs exactly as written, on a specially prepared test machine.');
        await pointAt(p, m.getByText(/Imported specs can't use/), 2600);
        await caption(p, 'First choose the suite it goes into, and give it a name.');
        await m.getByLabel('Target suite').click();
        await beat(p, 700);
        await p.getByRole('option', { name: SUITE }).click();
        await m.getByLabel('Case name').click();
        await m.getByLabel('Case name').pressSequentially('homepage-title.spec', { delay: 40 });
        await caption(p, 'Then paste the code. Insert sample shows what a small one looks like.');
        await pointAt(p, m.getByRole('button', { name: 'Insert sample' }), 900);
        await m.getByRole('button', { name: 'Insert sample' }).click();
        await beat(p, 1400);
        await caption(p, 'Import case stores it as a new test in that suite. It does not run it.');
        await pointAt(p, m.getByRole('button', { name: 'Import case' }), 2400);
        await caption(p, 'Nothing is saved here until you press Import case.', 1600);
        await caption(p, '');
    });
});

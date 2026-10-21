import { expect, test } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/reports/journey-coverage.html
// Nothing here changes data: tabs are switched, but no file is chosen, nothing
// is uploaded and the path rules are never saved ("Add rule" only adds an
// unsaved row, which "Discard changes" removes again).
// The demo has no traffic import, so the Coverage tab shows its empty state.

const DIR = 'reports/journey-coverage';

test('Reports › Journey coverage', async ({ page, browser }) => {
    await page.goto('/journey-coverage');
    await expect(page.getByRole('heading', { name: 'Journey coverage', level: 1 })).toBeVisible();
    const main = page.locator('main');
    const empty = main.getByText('No traffic imported yet', { exact: true });
    await expect(empty).toBeVisible();
    await settle(page);

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered (the guide explains each number).
    const tabs = main.getByRole('tablist', { name: 'Journey coverage views' });
    const emptyBox = empty.locator('xpath=ancestor::div[contains(@class,"rounded")][1]');
    const clean = await annotate(page, [
        { n: 1, target: tabs },
        { n: 2, target: emptyBox },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups: the empty Coverage tab, then the two other tabs.
    await tabs.screenshot({ path: out(`${DIR}/tabs.png`) });
    await emptyBox.screenshot({ path: out(`${DIR}/empty.png`) });
    await tabs.getByRole('tab', { name: 'Imports' }).click();
    const importCard = main.getByText('Import traffic data', { exact: true }).locator('xpath=ancestor::section[1]');
    await expect(importCard).toBeVisible();
    await settle(page);
    await importCard.screenshot({ path: out(`${DIR}/import-form.png`) });
    await tabs.getByRole('tab', { name: 'Path rules' }).click();
    const rules = main.getByText('Path rules', { exact: true }).last().locator('xpath=ancestor::section[1]');
    await expect(rules.getByRole('button', { name: 'Add rule' })).toBeVisible();
    await settle(page);
    // An unsaved example row, so the picture shows what a rule looks like.
    await rules.getByRole('button', { name: 'Add rule' }).click();
    await rules.getByPlaceholder('/product/{id}').fill('/product/{id}');
    await rules.screenshot({ path: out(`${DIR}/rules.png`) });
    await rules.getByRole('button', { name: 'Discard changes' }).click();

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/journey-coverage');
        await expect(p.getByRole('heading', { name: 'Journey coverage', level: 1 })).toBeVisible();
        const m = p.locator('main');
        await expect(m.getByText('No traffic imported yet', { exact: true })).toBeVisible();
        await settle(p);
        await caption(p, 'Journey coverage compares the pages real visitors use with the pages your tests visit.', 2000);
        await caption(p, 'It needs a page-view export from your analytics tool. The demo has none yet.');
        await pointAt(p, m.getByText('No traffic imported yet', { exact: true }), 2600);
        const t = m.getByRole('tablist', { name: 'Journey coverage views' });
        await caption(p, 'Imports is where an editor uploads that export, as a CSV or JSON file.');
        await pointAt(p, t.getByRole('tab', { name: 'Imports' }), 900);
        await t.getByRole('tab', { name: 'Imports' }).click();
        await settle(p);
        await pointAt(p, m.getByLabel('File'), 1600);
        await caption(p, 'Each row needs a page address and a number of views. Journey and step are optional.');
        await pointAt(p, m.getByText('Import traffic data', { exact: true }), 2600);
        await caption(p, 'Import history keeps the 20 newest uploads. The newest one drives the coverage view.', {
            say: 'Import history keeps the twenty newest uploads. The newest one drives the coverage view.',
        });
        await pointAt(p, m.getByText('Import history', { exact: true }), 2400);
        await caption(p, 'Path rules fold similar addresses into one page, such as every product page.');
        await t.getByRole('tab', { name: 'Path rules' }).click();
        await settle(p);
        const r = m.getByText('Path rules', { exact: true }).last().locator('xpath=ancestor::section[1]');
        await pointAt(p, r.getByRole('button', { name: 'Add rule' }), 800);
        await r.getByRole('button', { name: 'Add rule' }).click();
        await r.getByPlaceholder('/product/{id}').pressSequentially('/product/{id}', { delay: 60 });
        await beat(p, 1200);
        await caption(p, 'Rules apply to old imports too, so nothing needs uploading again. This example is discarded, not saved.');
        await pointAt(p, r.getByRole('button', { name: 'Discard changes' }), 1200);
        await r.getByRole('button', { name: 'Discard changes' }).click();
        await beat(p, 800);
        await caption(p, 'Back on Coverage, the result is the share of all page views that some test visits.');
        await t.getByRole('tab', { name: 'Coverage' }).click();
        await settle(p);
        await pointAt(p, t.getByRole('tab', { name: 'Coverage' }), 2400);
        await caption(p, '');
    });
});

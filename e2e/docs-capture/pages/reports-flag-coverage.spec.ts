import { expect, test } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/reports/flag-coverage.html
// Nothing here changes data: the catalogue's "Add a flag" form is typed into
// for the picture and the video, but "Add flag" is never pressed (the form
// does not save on its own) and the typing is lost when the page changes.

const DIR = 'reports/flag-coverage';

test('Reports › Flag coverage', async ({ page, browser }) => {
    await page.goto('/flag-coverage');
    await expect(page.getByRole('heading', { name: 'Flag coverage', level: 1 })).toBeVisible();
    await settle(page);
    const main = page.locator('main');

    // 1. The page as a reader first meets it (Coverage tab).
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Coverage tab, numbered.
    const tabs = main.getByRole('tablist', { name: 'Flag coverage views' });
    const windowPick = main.getByLabel('Window').locator('xpath=..');
    const tiles = main.getByText('Runs with flags', { exact: true }).locator('xpath=ancestor::div[contains(@class,"flex-wrap")][1]');
    const empty = main.getByText('No flags yet', { exact: true }).locator('xpath=..');
    let clean = await annotate(page, [
        { n: 1, target: tabs },
        { n: 2, target: windowPick },
        { n: 3, target: tiles },
        { n: 4, target: empty },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();
    await tiles.screenshot({ path: out(`${DIR}/tiles.png`) });

    // 3. Catalogue tab, with an example typed into the form (not added).
    await tabs.getByRole('tab', { name: 'Flag catalogue' }).click();
    await settle(page);
    const addCard = main.locator('section').filter({ has: page.getByRole('heading', { name: 'Add a flag', level: 2 }) });
    const catCard = main.locator('section').filter({ has: page.getByRole('heading', { name: 'Catalogue', level: 2 }) });
    await expect(addCard).toBeVisible();
    await addCard.getByLabel('Name').fill('checkout_v2');
    await addCard.getByLabel('Possible values').fill('true, false');
    await addCard.getByLabel('Description').fill('New checkout page, being rolled out');
    clean = await annotate(page, [
        { n: 5, target: addCard },
        { n: 6, target: catCard },
    ]);
    await page.screenshot({ path: out(`${DIR}/catalogue-annotated.png`) });
    await clean();
    await addCard.screenshot({ path: out(`${DIR}/add-flag.png`) });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/flag-coverage');
        await expect(p.getByRole('heading', { name: 'Flag coverage', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        await caption(p, 'Flag coverage shows which feature flags and variants your test runs have tried.', 2200);
        await caption(p, 'A run says which flags it ran with when it is started from CI or the API.', {
            say: 'A run says which flags it ran with when it is started from C I or the A P I.',
        });
        await pointAt(p, m.getByText(/Runs declare the flags/), 2200);
        await caption(p, 'Choose how far back to look: from 7 to 180 days.', {
            say: 'Choose how far back to look: from seven to a hundred and eighty days.',
        });
        await pointAt(p, m.getByLabel('Window'), 1800);
        await caption(p, 'The tiles count the runs that sent flags, and the values no run has tried yet.');
        await pointAt(p, m.getByText('Runs with flags', { exact: true }), 1200);
        await pointAt(p, m.getByText('Catalogue values never tested', { exact: true }), 1600);
        await caption(p, 'Below them, one card per flag: each value, how many runs used it, and how they went.');
        await pointAt(p, m.getByText('No flags yet', { exact: true }), 2400);
        await caption(p, 'The Flag catalogue tab lists the flags you have and every value they can take.');
        const tab = m.getByRole('tab', { name: 'Flag catalogue' });
        await pointAt(p, tab, 900);
        await tab.click();
        await settle(p);
        await beat(p, 600);
        const add = m.locator('section').filter({ has: p.getByRole('heading', { name: 'Add a flag', level: 2 }) });
        await caption(p, 'Type its name exactly as runs send it, and its possible values.');
        await add.getByLabel('Name').pressSequentially('checkout_v2', { delay: 60 });
        await add.getByLabel('Possible values').pressSequentially('true, false', { delay: 60 });
        await beat(p, 600);
        await caption(p, 'Add flag would save it. Editors can do this; here we leave it unsaved.');
        await pointAt(p, add.getByRole('button', { name: 'Add flag' }), 2200);
        await caption(p, 'Once it is in the catalogue, values that no run has tried are flagged on the Coverage tab.');
        await pointAt(p, m.getByRole('tab', { name: 'Coverage' }), 2400);
        await caption(p, '');
    });
});

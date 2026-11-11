import { expect, test } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/releases/test-cycles.html
// Nothing here changes data: dialogs are opened to show them and closed with
// Cancel / Done; no run is started, nothing is assigned and no sign-off is
// recorded. The cycle shown is the demo's "Release 2.4.0 regression".

const DIR = 'releases/test-cycles';
const CYCLE = 'Release 2.4.0 regression';

test('Releases › Test cycles', async ({ page, browser }) => {
    // The list of cycles.
    await page.goto('/test-cycles');
    await expect(page.getByRole('heading', { name: 'Test cycles', level: 1 })).toBeVisible();
    const card = page.locator('main').getByRole('link', { name: new RegExp(CYCLE) });
    await expect(card).toBeVisible();
    await settle(page);
    await page.screenshot({ path: out(`${DIR}/list.png`) });

    // One cycle.
    await card.click();
    await expect(page.getByRole('heading', { name: CYCLE, level: 1 })).toBeVisible();
    const main = page.locator('main');
    const progress = main.locator('section[aria-labelledby="cycle-progress"]');
    const cases = main.locator('section[aria-labelledby="cycle-cases"]');
    const runs = main.locator('section[aria-labelledby="cycle-runs"]');
    const signoff = main.locator('section[aria-labelledby="cycle-signoff"]');
    await expect(signoff).toBeVisible();
    await settle(page);
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // Its parts, numbered (the guide explains each number).
    await page.setViewportSize({ width: 1440, height: 2300 });
    await settle(page);
    const header = main.locator('h1').locator('xpath=ancestor::div[contains(@class,"rounded-3xl")][1]');
    const clean = await annotate(page, [
        { n: 1, target: header },
        { n: 2, target: progress },
        { n: 3, target: cases },
        { n: 4, target: runs },
        { n: 5, target: signoff },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // Close-ups used next to each explanation.
    await header.screenshot({ path: out(`${DIR}/header.png`) });
    await progress.screenshot({ path: out(`${DIR}/progress.png`) });
    await cases.screenshot({ path: out(`${DIR}/tests.png`) });
    await runs.screenshot({ path: out(`${DIR}/runs.png`) });
    await signoff.screenshot({ path: out(`${DIR}/signoff.png`) });

    // Two tests ticked: the row of actions that appears.
    await cases.getByRole('checkbox', { name: 'Select Declined card keeps the free plan' }).check();
    await cases.getByRole('checkbox', { name: 'Select Upgrade to Pro with a test card' }).check();
    await expect(cases.getByRole('group', { name: 'Selected tests' })).toBeVisible();
    await cases.screenshot({ path: out(`${DIR}/selected.png`) });
    await cases.getByRole('button', { name: 'Clear', exact: true }).click();

    await page.setViewportSize({ width: 1440, height: 900 });
    await page.evaluate(() => window.scrollTo(0, 0));

    // The dialogs (closed again without starting anything).
    await main.getByRole('button', { name: 'Test manually' }).click();
    const manual = page.getByRole('dialog');
    await expect(manual.getByRole('heading', { name: 'Test manually' })).toBeVisible();
    await manual.screenshot({ path: out(`${DIR}/manual-dialog.png`) });
    await manual.getByRole('button', { name: 'Cancel' }).click();

    await main.getByRole('button', { name: 'Run automated tests' }).click();
    const auto = page.getByRole('dialog');
    await expect(auto.getByRole('heading', { name: 'Run automated tests' })).toBeVisible();
    await auto.getByRole('radio', { name: /Every test/ }).check();
    await auto.screenshot({ path: out(`${DIR}/run-dialog.png`) });
    await auto.getByRole('button', { name: 'Cancel' }).click();

    await main.getByRole('button', { name: 'Add tests' }).click();
    const add = page.getByRole('dialog');
    await add.getByLabel('Single tests').fill('todo');
    await expect(add.getByRole('list')).toBeVisible();
    await settle(page);
    await add.screenshot({ path: out(`${DIR}/add-dialog.png`) });
    await add.getByRole('button', { name: 'Done' }).click();

    // A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/test-cycles');
        await expect(p.getByRole('heading', { name: 'Test cycles', level: 1 })).toBeVisible();
        const m = p.locator('main');
        await settle(p);
        await caption(p, 'A test cycle is one round of testing for a release.');
        await pointAt(p, m.getByRole('heading', { name: 'Test cycles', level: 1 }), 2000);
        const c = m.getByRole('link', { name: new RegExp(CYCLE) });
        await caption(p, 'Each card shows how far a cycle is: how many tests were run, and how many passed.');
        await pointAt(p, c, 2400);
        await c.click();
        await expect(p.getByRole('heading', { name: CYCLE, level: 1 })).toBeVisible();
        await settle(p);
        const pr = m.locator('section[aria-labelledby="cycle-progress"]');
        const cs = m.locator('section[aria-labelledby="cycle-cases"]');
        const rn = m.locator('section[aria-labelledby="cycle-runs"]');
        const so = m.locator('section[aria-labelledby="cycle-signoff"]');
        await caption(p, 'Progress counts every test in the cycle, and shows the same for each person.');
        await pointAt(p, pr.getByRole('img').first(), 2600);
        await cs.scrollIntoViewIfNeeded();
        await caption(p, 'The list holds manual and automated tests together, with who is testing each one.');
        await pointAt(p, cs.getByRole('heading').first(), 2400);
        await caption(p, 'Show only what is still to do. A failed test stays here until a later run passes.');
        await cs.getByLabel('State').selectOption('remaining');
        await beat(p, 2200);
        await cs.getByLabel('State').selectOption('all');
        await rn.scrollIntoViewIfNeeded();
        await caption(p, 'Every run made for the cycle is listed, by hand or automated.');
        await pointAt(p, rn.getByRole('heading').first(), 2200);
        await so.scrollIntoViewIfNeeded();
        await caption(p, 'When testing is done, a person approves or rejects the release here.');
        await pointAt(p, so.getByRole('heading').first(), 2600);
        await caption(p, '');
    });
});

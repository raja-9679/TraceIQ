import { expect, test } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/today/flaky-tests.html
// Nothing here changes data: the owner menu saves on pick, so it is only
// pointed at; the quarantine dialog is opened and cancelled, never submitted.

const DIR = 'today/flaky-tests';

test('Today › Flaky tests', async ({ page, browser }) => {
    await page.goto('/flaky-tests');
    await expect(page.getByRole('heading', { name: 'Flaky tests', level: 1 })).toBeVisible();
    const main = page.locator('main');
    const detail = main.getByRole('article', { name: 'Selected flaky test' });
    await expect(detail.getByText('Flake score', { exact: true })).toBeVisible();
    await settle(page);

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered (the guide explains each number).
    const subtitle = main.getByText(/pass and fail under the same conditions ·/);
    const list = main.locator('section').filter({ has: page.getByRole('button', { pressed: true }) }).first();
    const score = detail.getByText('Flake score', { exact: true }).locator('xpath=ancestor::div[contains(@class,"flex-col")][1]');
    const facts = detail.getByText('Runs sampled', { exact: true }).locator('xpath=ancestor::div[contains(@class,"grid")][1]');
    const owner = detail.getByText('Owner', { exact: true }).locator('xpath=ancestor::div[contains(@class,"flex-col")][1]');
    const meaning = detail.getByText('What this means', { exact: true }).locator('xpath=ancestor::div[contains(@class,"rounded-2xl")][1]');
    const lastFailure = detail.getByText('Last failure', { exact: true }).locator('xpath=ancestor::div[contains(@class,"flex-col")][1]');
    const actions = detail.getByRole('button', { name: /Quarantine/ }).locator('xpath=ancestor::div[contains(@class,"border-t")][1]');
    await page.setViewportSize({ width: 1440, height: 1000 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: subtitle, side: 'right' },
        { n: 2, target: list, side: 'left' },
        { n: 3, target: score, side: 'right' },
        { n: 4, target: facts, side: 'right' },
        { n: 5, target: owner, side: 'right' },
        { n: 6, target: meaning, side: 'right' },
        { n: 7, target: actions, side: 'right' },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups used next to each explanation.
    await list.screenshot({ path: out(`${DIR}/list.png`) });
    await score.screenshot({ path: out(`${DIR}/score.png`) });
    await facts.screenshot({ path: out(`${DIR}/facts.png`) });
    await owner.screenshot({ path: out(`${DIR}/owner.png`) });
    await meaning.screenshot({ path: out(`${DIR}/meaning.png`) });
    await lastFailure.screenshot({ path: out(`${DIR}/last-failure.png`) });
    await actions.screenshot({ path: out(`${DIR}/actions.png`) });
    // The quarantine dialog, opened and cancelled (nothing is saved).
    await detail.getByRole('button', { name: 'Quarantine' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByLabel('Reason')).toBeVisible();
    await beat(page, 400);
    await dialog.screenshot({ path: out(`${DIR}/quarantine-dialog.png`) });
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/flaky-tests');
        await expect(p.getByRole('heading', { name: 'Flaky tests', level: 1 })).toBeVisible();
        const m = p.locator('main');
        const d = m.getByRole('article', { name: 'Selected flaky test' });
        await expect(d.getByText('Flake score', { exact: true })).toBeVisible();
        await settle(p);
        await caption(p, 'A flaky test passes and fails without anything changing. This page lists them.', 2200);
        await caption(p, 'Each row shows a test, its recent failures, its owner, and its flake score.');
        await pointAt(p, m.getByRole('button', { pressed: true }), 2600);
        await caption(p, 'The flake score: how often the result flipped between pass and fail, recently.');
        await pointAt(p, d.getByRole('img', { name: /Flake score/ }), 2600);
        await caption(p, 'How many results the score is based on, and how many failed in the last 30 days.', {
            say: 'How many results the score is based on, and how many failed in the last thirty days.',
        });
        await pointAt(p, d.getByText('Runs sampled', { exact: true }), 1400);
        await pointAt(p, d.getByText('Recent failures', { exact: true }), 1400);
        await caption(p, 'The owner is the person who will fix it.');
        await pointAt(p, d.getByRole('combobox', { name: /Owner of/ }), 2200);
        const lf = d.getByText('Last failure', { exact: true });
        await lf.scrollIntoViewIfNeeded();
        await caption(p, 'What this means explains the score in plain words. Below it is the last error.');
        await pointAt(p, d.getByText('What this means', { exact: true }), 1600);
        await pointAt(p, lf, 1600);
        const q = d.getByRole('button', { name: 'Quarantine' });
        await q.scrollIntoViewIfNeeded();
        await caption(p, 'Quarantine takes it out of runs for a while, so it stops blocking your results.');
        await pointAt(p, q, 1000);
        await q.click();
        const dlg = p.getByRole('dialog');
        await expect(dlg.getByLabel('Reason')).toBeVisible();
        await caption(p, 'You give a reason and a number of days. It comes back on its own when they are up.');
        await pointAt(p, dlg.getByLabel('Reason'), 1400);
        await pointAt(p, dlg.getByLabel('Days'), 1600);
        await caption(p, 'We cancel here, so nothing changes.');
        await pointAt(p, dlg.getByRole('button', { name: 'Cancel' }), 900);
        await dlg.getByRole('button', { name: 'Cancel' }).click();
        await expect(dlg).toBeHidden();
        await caption(p, '');
    });
});

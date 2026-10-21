import { expect, test } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/today/visual-review.html
// Nothing here changes data: only tabs and the Show / Sort menus are used.
// The demo workspace has no visual comparisons or baselines (no run there
// ever took a screenshot), so every picture shows an empty state.

const DIR = 'today/visual-review';

test('Today › Visual review', async ({ page, browser }) => {
    await page.goto('/visual-review');
    await expect(page.getByRole('heading', { name: 'Visual review', level: 1 })).toBeVisible();
    const main = page.locator('main');
    await expect(main.getByText('No visual comparisons yet')).toBeVisible();
    await settle(page);

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered (the guide explains each number).
    const tabs = main.getByRole('tablist', { name: 'Visual review views' });
    const controls = main.getByText('Sort by', { exact: true }).locator('xpath=ancestor::div[contains(@class,"rounded-3xl")][1]');
    const empty = main.getByText('No visual comparisons yet').locator('xpath=..');
    const clean = await annotate(page, [
        { n: 1, target: tabs },
        { n: 2, target: controls },
        { n: 3, target: empty },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups used next to each explanation.
    await tabs.screenshot({ path: out(`${DIR}/tabs.png`) });
    await controls.screenshot({ path: out(`${DIR}/controls.png`) });
    // The Show menu, opened and closed again.
    await main.getByLabel('Show').click();
    const menu = page.getByRole('listbox');
    await expect(menu.getByRole('option', { name: 'Not yet decided' })).toBeVisible();
    await beat(page, 300);
    await page.screenshot({ path: out(`${DIR}/show-menu.png`), clip: await clipAround(page, controls, 220) });
    await page.keyboard.press('Escape');
    await expect(menu).toBeHidden();
    // The second tab.
    await tabs.getByRole('tab', { name: 'Pinned baselines' }).click();
    await expect(main.getByText('No visual baselines in this project')).toBeVisible();
    await settle(page);
    await page.screenshot({ path: out(`${DIR}/baselines.png`) });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/visual-review');
        await expect(p.getByRole('heading', { name: 'Visual review', level: 1 })).toBeVisible();
        const m = p.locator('main');
        await expect(m.getByText('No visual comparisons yet')).toBeVisible();
        await settle(p);
        await caption(p, 'Visual review is where you look at screenshots that changed.', 2000);
        await caption(p, 'A test can compare a screenshot of a page with an approved one, called the baseline.');
        await pointAt(p, m.getByRole('heading', { name: 'Visual review', level: 1 }), 2400);
        await caption(p, 'Changes by run shows one run at a time. Pick the run here.');
        await pointAt(p, m.getByLabel('Run'), 2400);
        await caption(p, 'Show chooses what to list: changes, only the ones nobody decided yet, or everything.');
        await m.getByLabel('Show').click();
        const lb = p.getByRole('listbox');
        await expect(lb).toBeVisible();
        await pointAt(p, lb.getByRole('option', { name: 'Not yet decided' }), 2400);
        await p.keyboard.press('Escape');
        await caption(p, 'Sort puts the biggest change first, or sorts by test name.');
        await pointAt(p, m.getByLabel('Sort by'), 2200);
        await caption(p, 'No test in the demo takes screenshots, so there is nothing to review yet.');
        await pointAt(p, m.getByText('No visual comparisons yet'), 2400);
        const t = m.getByRole('tablist', { name: 'Visual review views' });
        await caption(p, 'Pinned baselines lists every approved screenshot in the project.');
        await pointAt(p, t.getByRole('tab', { name: 'Pinned baselines' }), 900);
        await t.getByRole('tab', { name: 'Pinned baselines' }).click();
        await settle(p);
        await pointAt(p, m.getByText('No visual baselines in this project'), 2000);
        await caption(p, 'For each one you can accept the newest screenshot, keep the old one, or roll back.');
        await beat(p, 1200);
        await caption(p, '');
    });
});

/** A screenshot clip covering `el` and `below` pixels under it (for an open menu). */
async function clipAround(page: import('@playwright/test').Page, el: import('@playwright/test').Locator, below: number) {
    const box = (await el.boundingBox())!;
    return { x: box.x - 8, y: box.y - 8, width: box.width + 16, height: box.height + below };
}

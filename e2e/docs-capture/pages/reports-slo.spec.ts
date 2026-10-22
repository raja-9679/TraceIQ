import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/reports/slo.html
// Nothing here changes data: "Edit targets" is opened and closed again
// ("Close targets"), never saved, and the window menu only changes the view.
// The demo's monitor belongs to the "Demo — Public API" project, so the
// project is switched in the sidebar first (a browser-side setting).

const DIR = 'reports/slo';
const MONITOR = /^SLO for /;

async function pickApiProject(p: Page) {
    await p.getByRole('button', { name: /^Project: / }).first().click();
    await p.getByRole('menuitem', { name: /Demo — Public API/ }).click();
    // The menu hands focus back to the switcher once it has closed, and the
    // switcher's focus ring would show in every picture.
    await p.waitForTimeout(400);
    await p.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
    await p.mouse.move(800, 860);
}

test('Reports › SLOs', async ({ page, browser }) => {
    await page.goto('/reports/slo');
    await expect(page.getByRole('heading', { name: 'SLO reports', level: 1 })).toBeVisible();
    await pickApiProject(page);
    const main = page.locator('main');
    const card = main.getByRole('region', { name: MONITOR }).first();
    await expect(card.getByText('Daily compliance')).toBeVisible();
    await settle(page);

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered (the guide explains each number).
    const windowPick = main.getByLabel('Window').locator('xpath=ancestor::label[1]');
    const head = card.locator('xpath=./div[1]');
    const tiles = card.getByRole('region', { name: 'Compliance' }).locator('xpath=..');
    const daily = card.getByText('Daily compliance').locator('xpath=ancestor::div[contains(@class,"flex-col")][1]');
    const how = main.getByText('How this is measured', { exact: true }).locator('xpath=ancestor::div[contains(@class,"rounded-2xl")][1]');
    await page.setViewportSize({ width: 1440, height: 1000 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: windowPick },
        { n: 2, target: head, side: 'right' },
        { n: 3, target: tiles },
        { n: 4, target: daily, side: 'right' },
        { n: 5, target: how },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups used next to each explanation.
    await windowPick.screenshot({ path: out(`${DIR}/window.png`) });
    await head.screenshot({ path: out(`${DIR}/monitor-head.png`) });
    await tiles.screenshot({ path: out(`${DIR}/tiles.png`) });
    await daily.screenshot({ path: out(`${DIR}/daily.png`) });
    await card.getByRole('button', { name: 'Edit targets' }).click();
    const form = card.locator('section').filter({ hasText: 'Save targets' }).last();
    await expect(form).toBeVisible();
    await form.screenshot({ path: out(`${DIR}/targets.png`) });
    await card.getByRole('button', { name: 'Close targets' }).click();
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/reports/slo');
        await expect(p.getByRole('heading', { name: 'SLO reports', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        await caption(p, 'SLOs show whether your monitors meet the availability you promised.', 2000);
        await caption(p, 'Monitors belong to a project, so pick the project in the sidebar.');
        await pointAt(p, p.getByRole('button', { name: /^Project: / }).first(), 1000);
        await pickApiProject(p);
        const c = m.getByRole('region', { name: MONITOR }).first();
        await expect(c.getByText('Daily compliance')).toBeVisible();
        await settle(p);
        await beat(p, 800);
        await caption(p, 'One card per monitor: its target, and whether it is meeting it.');
        await pointAt(p, c.locator('h2'), 1300);
        await pointAt(p, c.locator('xpath=./div[1]/div[2]').locator('span').first(), 1600);
        await caption(p, 'Compliance: the share of checks that passed in the window.');
        await pointAt(p, c.getByRole('region', { name: 'Compliance' }), 2400);
        await caption(p, 'The error budget is how many failed checks the target allows. Below zero means it is overspent.');
        await pointAt(p, c.getByRole('region', { name: 'Error budget left' }), 2800);
        await caption(p, 'Burn rate: how fast the budget is being used. Above 1×, it runs out before the window ends.', {
            say: 'Burn rate: how fast the budget is being used. Above one times, it runs out before the window ends.',
        });
        await pointAt(p, c.getByRole('region', { name: 'Burn rate' }), 2800);
        await caption(p, 'Latency: how long the slowest checks took, against an optional target.');
        await pointAt(p, c.getByRole('region', { name: 'Latency p95' }), 2400);
        await c.getByText('Daily compliance').scrollIntoViewIfNeeded();
        await caption(p, 'One bar per day. Green days met the target; orange days did not.');
        await pointAt(p, c.getByText('Daily compliance'), 2400);
        await p.evaluate(() => window.scrollTo(0, 0));
        await caption(p, 'Edit targets sets the goal for this monitor. Only editors can save it.');
        await pointAt(p, c.getByRole('button', { name: 'Edit targets' }), 900);
        await c.getByRole('button', { name: 'Edit targets' }).click();
        await c.getByRole('button', { name: 'Close targets' }).scrollIntoViewIfNeeded();
        await c.getByText('Save targets').scrollIntoViewIfNeeded();
        await beat(p, 2400);
        await c.getByRole('button', { name: 'Close targets' }).click();
        await p.evaluate(() => window.scrollTo(0, 0));
        await caption(p, 'The window menu shows the last 7, 30 or 90 days for every monitor at once.', {
            say: 'The window menu shows the last seven, thirty or ninety days for every monitor at once.',
        });
        await pointAt(p, m.getByLabel('Window'), 2600);
        await caption(p, '');
    });
});

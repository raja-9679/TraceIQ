import { expect, test, type Locator } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/tests/suites.html
// Nothing here changes data: the "New suite" panel and the "More" / "Add"
// menus are opened and closed with Escape, and the step editor is left with
// its Back button without touching a field (an untouched editor keeps no
// draft and asks nothing).

const DIR = 'tests/suites';
const SUITE = 'Web smoke (demo)';

/** A picture of an open menu together with the button that opened it. */
// The anchor's box is taken before the menu opens: an open menu hides the
// rest of the page from the accessibility tree, so role locators stop matching.
async function menuShot(menu: Locator, b: { x: number; y: number; width: number; height: number }, path: string) {
    const a = (await menu.boundingBox())!;
    const x = Math.max(0, Math.min(a.x, b.x) - 16);
    const y = Math.max(0, Math.min(a.y, b.y) - 16);
    return {
        path,
        clip: { x, y, width: Math.max(a.x + a.width, b.x + b.width) - x + 16, height: Math.max(a.y + a.height, b.y + b.height) - y + 16 },
    };
}

test('Tests › Suites', async ({ page, browser }) => {
    await page.goto('/suites');
    await expect(page.getByRole('heading', { name: 'Tests', level: 1 })).toBeVisible();
    await settle(page);
    const main = page.locator('main');
    await expect(main.getByText(SUITE, { exact: true })).toBeVisible();

    // 1. The suite list as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    const subtitle = main.getByText(/suites? · \d+ tests?/);
    const actions = main.getByRole('button', { name: 'New suite' }).locator('xpath=..');
    const toolbar = main.getByRole('group', { name: 'Layout' }).locator('xpath=..');
    const row = main.locator('li').filter({ hasText: SUITE }).first();
    const clean = await annotate(page, [
        { n: 1, target: subtitle, side: 'right' },
        { n: 2, target: actions, side: 'right' },
        { n: 3, target: toolbar },
        { n: 4, target: row },
    ]);
    await page.setViewportSize({ width: 1440, height: 560 });
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();
    await page.setViewportSize({ width: 1440, height: 900 });

    await row.screenshot({ path: out(`${DIR}/suite-row.png`) });

    // The "New suite" panel, opened and closed again without saving.
    await main.getByRole('button', { name: 'New suite' }).click();
    const sheet = page.getByRole('dialog');
    await expect(sheet.getByText('How tests run')).toBeVisible();
    await beat(page, 600);
    await sheet.screenshot({ path: out(`${DIR}/new-suite.png`) });
    await page.keyboard.press('Escape');
    await expect(sheet).toBeHidden();

    // 2. A suite's own page.
    await row.getByRole('link').first().click();
    await page.waitForURL(/\/suites\/\d+$/);
    await expect(page.getByRole('heading', { name: SUITE, level: 1 })).toBeVisible();
    await settle(page);
    const top = main.getByRole('heading', { name: SUITE, level: 1 }).locator('xpath=ancestor::div[contains(@class,"rounded-3xl")][1]');
    const runBar = main.getByRole('button', { name: 'Run suite' }).locator('xpath=..');
    const tabs = main.getByRole('tablist', { name: 'Suite sections' });
    const list = main.locator('section').filter({ has: page.getByRole('button', { pressed: true }) }).first();
    const detail = main.getByRole('article', { name: 'Selected test' });
    await expect(detail.getByText('Steps', { exact: true })).toBeVisible();
    const clean2 = await annotate(page, [
        { n: 5, target: runBar },
        { n: 6, target: tabs },
        { n: 7, target: list, side: 'left' },
        { n: 8, target: detail, side: 'right' },
    ]);
    await page.screenshot({ path: out(`${DIR}/suite-annotated.png`) });
    await clean2();

    await top.screenshot({ path: out(`${DIR}/suite-header.png`) });
    await list.screenshot({ path: out(`${DIR}/test-list.png`) });
    // The demo suite also holds a sub-module, listed above its own tests.
    await main.getByRole('region', { name: 'Sub-modules' }).screenshot({ path: out(`${DIR}/sub-modules.png`) });
    // A quarantined test with a {{secret}} in its steps.
    await list.getByRole('button', { name: /Add a todo/ }).click();
    await expect(detail.getByRole('heading', { name: 'Add a todo' })).toBeVisible();
    await detail.screenshot({ path: out(`${DIR}/test-steps.png`) });

    // The "More" and "Add" menus, opened and closed.
    const moreBtn = main.getByRole('button', { name: 'More suite actions' });
    const moreBox = (await moreBtn.boundingBox())!;
    await moreBtn.click();
    const more = page.getByRole('menu');
    await expect(more).toBeVisible();
    await beat(page, 400);
    await page.screenshot(await menuShot(more, moreBox, out(`${DIR}/more-menu.png`)));
    await page.keyboard.press('Escape');
    await expect(more).toBeHidden();
    const addBtn = main.getByRole('button', { name: 'Add or import' });
    const addBox = (await addBtn.boundingBox())!;
    await addBtn.click();
    const add = page.getByRole('menu');
    await expect(add).toBeVisible();
    await beat(page, 400);
    await page.screenshot(await menuShot(add, addBox, out(`${DIR}/add-menu.png`)));
    await page.keyboard.press('Escape');
    await expect(add).toBeHidden();

    // The Settings tab: just its first cards.
    await tabs.getByRole('tab', { name: 'Settings' }).click();
    await expect(main.getByText('Execution Mode', { exact: true })).toBeVisible();
    await settle(page);
    await beat(page, 400);
    await page.screenshot({ path: out(`${DIR}/settings.png`) });
    await tabs.getByRole('tab', { name: 'Tests' }).click();

    // 3. The step editor, opened and left without changing anything.
    await list.getByRole('button', { name: /Add a todo/ }).click();
    await detail.getByRole('button', { name: 'Edit steps' }).click();
    await page.waitForURL(/\/cases\/\d+\/edit$/);
    await expect(page.getByText('Execution Timeline')).toBeVisible();
    await settle(page);
    await beat(page, 600);
    await page.screenshot({ path: out(`${DIR}/editor.png`) });
    await page.getByRole('button', { name: 'Back' }).click();
    await page.waitForURL(/\/suites\/\d+$/);

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/suites');
        await expect(p.getByRole('heading', { name: 'Tests', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        await caption(p, 'Suites is where your tests are kept, grouped into suites.', 2000);
        const r = m.locator('li').filter({ hasText: SUITE }).first();
        await caption(p, 'Each row is one suite: its name, how its tests run, and how many tests it holds.');
        await pointAt(p, r.getByText(SUITE, { exact: true }), 1400);
        await pointAt(p, r.getByText(/tests ·/), 1400);
        await caption(p, 'The play button runs every test in the suite.');
        await pointAt(p, r.getByRole('button', { name: `Run ${SUITE}` }), 2200);
        await caption(p, '');
        await r.getByRole('link').first().click();
        await p.waitForURL(/\/suites\/\d+$/);
        await settle(p);
        await caption(p, "This is the suite's own page. Sub-modules come first, then the suite's own tests.", 1600);
        const l = m.locator('section').filter({ has: p.getByRole('button', { pressed: true }) }).first();
        const d = m.getByRole('article', { name: 'Selected test' });
        await caption(p, 'Click a test to read its steps on the right, in plain words.');
        await pointAt(p, l.getByRole('button', { name: /Add a todo/ }), 900);
        await l.getByRole('button', { name: /Add a todo/ }).click();
        await beat(p, 900);
        await pointAt(p, d.locator('ol li').nth(4), 1600);
        await caption(p, 'Quarantined means the test is flaky and set aside: suite runs skip it for now.');
        await pointAt(p, l.getByText('Quarantined', { exact: true }), 2200);
        await caption(p, 'Run suite runs every test here. Run now runs only the test you picked.');
        await pointAt(p, m.getByRole('button', { name: 'Run suite' }), 1600);
        await pointAt(p, d.getByRole('button', { name: 'Run now' }), 1600);
        await caption(p, 'Edit steps opens the step editor.');
        await pointAt(p, d.getByRole('button', { name: 'Edit steps' }), 1400);
        await caption(p, '');
        await d.getByRole('button', { name: 'Edit steps' }).click();
        await p.waitForURL(/\/cases\/\d+\/edit$/);
        await settle(p);
        await caption(p, 'Each step is one action, such as opening a page, clicking or typing.');
        await pointAt(p, p.getByText('Navigate', { exact: true }).first(), 1600);
        await caption(p, 'Next to the action: where on the page it happens, and what to type.');
        await pointAt(p, p.locator('input[value="#login-username"]'), 1600);
        await caption(p, 'The dark bar at the bottom adds a step. Nothing is saved until you press Update Test.');
        await pointAt(p, p.getByRole('button', { name: 'Assert' }), 1600);
        await pointAt(p, p.getByRole('button', { name: 'Update Test' }), 1600);
        await caption(p, '');
        await p.getByRole('button', { name: 'Back' }).click();
        await p.waitForURL(/\/suites\/\d+$/);
        await settle(p);
        await caption(p, 'Back on the suite page, nothing has changed.', 1400);
        await caption(p, '');
    });
});

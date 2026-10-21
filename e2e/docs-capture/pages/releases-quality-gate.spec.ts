import { expect, test } from '@playwright/test';
import { annotate, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/releases/quality-gate.html
// Nothing here changes data: "Gate rules" only shows and hides the rules
// panel (no Save button is pressed), and "Re-evaluate" is pointed at, never
// pressed.

const DIR = 'releases/quality-gate';

test('Releases › Quality gate', async ({ page, browser }) => {
    await page.goto('/quality');
    await expect(page.getByRole('heading', { name: 'Releases', level: 1 })).toBeVisible();
    const main = page.locator('main');
    const detail = main.getByRole('article', { name: 'Selected check' });
    await expect(detail).toBeVisible();
    await settle(page);

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered (the guide explains each number).
    const header = main.getByRole('heading', { name: 'Releases', level: 1 }).locator('xpath=ancestor::div[contains(@class,"rounded-3xl")][1]');
    const list = main.locator('section').filter({ hasText: 'Passing' }).filter({ has: page.getByRole('button') }).first();
    const health = main.locator('section[aria-labelledby="health-h"]');
    const tiles = health.getByText('Flaky tests', { exact: true }).locator('xpath=ancestor::div[contains(@class,"grid")][1]');
    const charts = health.getByText('Daily pass rate', { exact: true }).locator('xpath=ancestor::div[contains(@class,"grid")][1]');
    await page.setViewportSize({ width: 1440, height: 1100 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: header },
        { n: 2, target: list, side: 'left' },
        { n: 3, target: detail },
        { n: 4, target: tiles },
        { n: 5, target: charts },
        { n: 6, target: main.getByRole('button', { name: 'Gate rules' }) },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups used next to each explanation.
    await header.screenshot({ path: out(`${DIR}/header.png`) });
    await list.screenshot({ path: out(`${DIR}/checks.png`) });
    await detail.screenshot({ path: out(`${DIR}/detail.png`) });
    await health.screenshot({ path: out(`${DIR}/health.png`) });

    // The rules panel (opened, photographed, closed again; nothing saved).
    await main.getByRole('button', { name: 'Gate rules' }).click();
    const rules = main.getByRole('heading', { name: 'Gate rules', level: 2 }).locator('xpath=ancestor::section[1]');
    const ci = main.getByRole('heading', { name: 'CI reporting', level: 2 }).locator('xpath=ancestor::section[1]');
    const history = main.getByRole('heading', { name: 'Rule history', level: 2 }).locator('xpath=ancestor::section[1]');
    await expect(rules).toBeVisible();
    await settle(page);
    await page.setViewportSize({ width: 1440, height: 1300 });
    await rules.screenshot({ path: out(`${DIR}/rules.png`) });
    // The CI card is stretched to the rules card's height; keep its content only.
    await ci.scrollIntoViewIfNeeded();
    const ciBox = (await ci.boundingBox())!;
    const ciSave = (await ci.getByRole('button', { name: 'Save CI settings' }).boundingBox())!;
    await page.screenshot({
        path: out(`${DIR}/ci.png`),
        clip: { x: ciBox.x, y: ciBox.y, width: ciBox.width, height: ciSave.y + ciSave.height + 24 - ciBox.y },
    });
    await history.screenshot({ path: out(`${DIR}/history.png`) });
    await main.getByRole('button', { name: 'Gate rules' }).click();
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/quality');
        await expect(p.getByRole('heading', { name: 'Releases', level: 1 })).toBeVisible();
        const m = p.locator('main');
        const d = m.getByRole('article', { name: 'Selected check' });
        await expect(d).toBeVisible();
        await settle(p);
        await caption(p, 'The quality gate says whether the selected project is ready to ship.');
        await pointAt(p, m.getByText(/Ready to ship|Not ready/).first(), 2000);
        await caption(p, 'The line below names the run that was judged, and the version of the rules.');
        await pointAt(p, m.getByText(/Gate evaluated on/), 2200);
        await caption(p, 'Every check is listed on the left. Blocking checks come first, then passing ones.');
        await pointAt(p, m.getByText('Passing', { exact: true }).first(), 2200);
        const row = m.getByRole('button', { name: /High-severity security findings/ });
        await caption(p, 'Click a check to see what it measured, and the rule it has to meet.');
        await pointAt(p, row, 1000);
        await row.click();
        await settle(p);
        await pointAt(p, d.getByText('Now', { exact: true }), 1200);
        await pointAt(p, d.getByText('Rule', { exact: true }), 1200);
        await caption(p, 'Evidence links take you to the page where you can look into it.');
        await pointAt(p, d.getByText('Evidence', { exact: true }), 2200);
        const rulesBtn = m.getByRole('button', { name: 'Gate rules' });
        await caption(p, 'Gate rules shows what a release must pass. Editors can change the rules.');
        await pointAt(p, rulesBtn, 900);
        await rulesBtn.click();
        await settle(p);
        await pointAt(p, m.getByText('Minimum pass rate (%)'), 1800);
        const hist = m.getByRole('heading', { name: 'Rule history', level: 2 });
        await hist.scrollIntoViewIfNeeded();
        await caption(p, 'Rule history keeps every version of the rules that has judged a release.');
        await pointAt(p, hist, 2400);
        await caption(p, '');
        await m.getByRole('button', { name: 'Gate rules' }).scrollIntoViewIfNeeded();
        await m.getByRole('button', { name: 'Gate rules' }).click();
        await settle(p);
        const h = m.locator('section[aria-labelledby="health-h"]');
        await h.scrollIntoViewIfNeeded();
        await caption(p, 'Project health gives the wider picture, here over the last 7 days.', {
            say: 'Project health gives the wider picture, here over the last seven days.',
        });
        await pointAt(p, h.getByText('Pass rate', { exact: true }), 1400);
        await pointAt(p, h.getByText('Flaky tests', { exact: true }), 1200);
        await caption(p, 'You can switch to 14 or 30 days.', { say: 'You can switch to fourteen or thirty days.' });
        await pointAt(p, h.getByLabel('Time window'), 2000);
        await caption(p, 'Each bar is the pass rate of one day.');
        await pointAt(p, h.locator('[title*="% of"]').last(), 2200);
        await m.getByRole('button', { name: 'Re-evaluate' }).scrollIntoViewIfNeeded();
        await caption(p, 'Re-evaluate checks the gate again against the newest results.');
        await pointAt(p, m.getByRole('button', { name: 'Re-evaluate' }), 2400);
        await caption(p, '');
    });
});

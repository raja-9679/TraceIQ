import { expect, test, type Page } from '@playwright/test';
import { annotate, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/releases/requirements.html
// Nothing here changes data: requirements are selected in the list (that only
// changes which one the detail card shows), the Import panel is opened and
// closed. Sync all, Re-sync, Remove and Generate tests are never pressed.
// The page follows the sidebar's project (the demo's web project).

const DIR = 'releases/requirements';

const open = async (p: Page) => {
    await p.goto('/requirements');
    await expect(p.getByRole('heading', { name: 'Requirements', level: 1 })).toBeVisible();
    await expect(p.locator('main').getByRole('article', { name: /^Requirement / })).toBeVisible();
    await settle(p);
};
// The left-hand list: the section holding the coverage groups' rows.
const listOf = (p: Page) => p.locator('main section').filter({ has: p.locator('button[aria-pressed]') }).first();

test('Releases › Requirements', async ({ page, browser }) => {
    await open(page);
    const main = page.locator('main');
    const header = main.getByRole('heading', { name: 'Requirements', level: 1 }).locator('xpath=ancestor::div[contains(@class,"rounded-3xl")][1]');
    const actions = main.getByRole('button', { name: /Sync all/ }).locator('xpath=..');
    const tiles = main.getByText('Not covered', { exact: true }).first().locator('xpath=ancestor::div[contains(@class,"grid")][1]');
    const list = listOf(page);
    const detail = main.getByRole('article', { name: /^Requirement / });

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered. The page opens with the first ticket (by key) selected.
    await page.setViewportSize({ width: 1440, height: 1200 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: actions },
        { n: 2, target: tiles },
        { n: 3, target: list, side: 'left' },
        { n: 4, target: detail },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await header.screenshot({ path: out(`${DIR}/toolbar.png`) });
    await tiles.screenshot({ path: out(`${DIR}/tiles.png`) });
    await list.screenshot({ path: out(`${DIR}/list.png`) });
    await detail.screenshot({ path: out(`${DIR}/detail.png`) });

    // The import panel (opened and closed; nothing searched or imported).
    await main.getByRole('button', { name: 'Import', exact: true }).click();
    await settle(page);
    const panel = header.locator('xpath=following-sibling::section[1]');
    await expect(panel).toBeVisible();
    await panel.screenshot({ path: out(`${DIR}/import.png`) });
    await main.getByRole('button', { name: 'Close import' }).click();
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await open(p);
        const m = p.locator('main');
        const l = listOf(p);
        const rows = l.locator('button[aria-pressed]');
        const d = m.getByRole('article', { name: /^Requirement / });
        await caption(p, 'Requirements lists the tickets imported from Jira or Linear, and how well they are tested.');
        await pointAt(p, m.getByRole('heading', { name: 'Requirements', level: 1 }), 2200);
        await caption(p, 'The four counts sort them: covered, failing, not run yet, and not covered.');
        for (const t of ['Covered', 'Failing', 'Not run yet', 'Not covered']) {
            await pointAt(p, m.getByText(t, { exact: true }).first(), 900);
        }
        await caption(p, 'The list on the left groups the tickets the same way, failing ones first.');
        await pointAt(p, rows.first(), 2400);
        await caption(p, 'Click a ticket to see it on the right: its acceptance criteria and the tests that cover it.');
        await pointAt(p, rows.first(), 800);
        await rows.first().click();
        await settle(p);
        await pointAt(p, d.getByText('Acceptance criteria', { exact: true }), 1600);
        await pointAt(p, d.getByText('Covered by', { exact: true }), 1600);
        await caption(p, 'Generate tests turns each criterion into a suggested test, for a person to review.');
        await pointAt(p, d.getByRole('button', { name: /Generate tests/ }), 2600);
        await caption(p, 'Import, at the top, brings in more tickets, from a search or by their keys.');
        await pointAt(p, m.getByRole('button', { name: 'Import', exact: true }), 800);
        await m.getByRole('button', { name: 'Import', exact: true }).click();
        await settle(p);
        await pointAt(p, m.getByRole('heading', { name: 'Requirements', level: 1 })
            .locator('xpath=ancestor::div[contains(@class,"rounded-3xl")][1]/following-sibling::section[1]'), 2400);
        await m.getByRole('button', { name: 'Close import' }).click();
        await caption(p, 'Sync all fetches the latest title, status and criteria of every ticket.');
        await pointAt(p, m.getByRole('button', { name: /Sync all/ }), 2400);
        await caption(p, '');
    });
});

import { expect, test, type Page } from '@playwright/test';
import { annotate, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/releases/requirements.html
// Nothing here changes data: a requirement is expanded and collapsed, the
// Import panel is opened and closed. Sync all, Re-sync, Remove and
// Generate tests are never pressed.

const DIR = 'releases/requirements';

const open = async (p: Page) => {
    await p.goto('/requirements');
    await expect(p.getByRole('heading', { name: 'Requirements', level: 1 })).toBeVisible();
    await expect(p.locator('main').getByText('Imported requirements')).toBeVisible();
    await settle(p);
};

test('Releases › Requirements', async ({ page, browser }) => {
    await open(page);
    const main = page.locator('main');
    const tiles = main.getByText('Not covered', { exact: true }).first().locator('xpath=ancestor::div[contains(@class,"grid")][1]');
    const bar = main.getByRole('button', { name: /Sync all/ }).locator('xpath=..');
    const rows = main.locator('button[aria-expanded]').filter({ hasText: 'linked test' });
    const first = rows.first();

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered. The first requirement is opened for part 4.
    await first.click();
    await expect(main.getByText('Acceptance criteria', { exact: true })).toBeVisible();
    await settle(page);
    const expanded = first.locator('xpath=..');
    const detail = expanded.locator('xpath=./div[contains(@class,"border-t")]');
    await page.setViewportSize({ width: 1440, height: 1100 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: tiles },
        { n: 2, target: bar },
        { n: 3, target: first },
        { n: 4, target: detail },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await tiles.screenshot({ path: out(`${DIR}/tiles.png`) });
    await bar.screenshot({ path: out(`${DIR}/toolbar.png`) });
    await first.screenshot({ path: out(`${DIR}/row.png`) });
    await detail.screenshot({ path: out(`${DIR}/detail.png`) });
    await first.click(); // collapse again

    // The import panel (opened and closed; nothing searched or imported).
    await main.getByRole('button', { name: 'Import', exact: true }).click();
    await settle(page);
    const panel = main.getByRole('button', { name: 'Close import' }).locator('xpath=../following-sibling::div[1]');
    await expect(panel).toBeVisible();
    await panel.screenshot({ path: out(`${DIR}/import.png`) });
    await main.getByRole('button', { name: 'Close import' }).click();
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await open(p);
        const m = p.locator('main');
        const r = m.locator('button[aria-expanded]').filter({ hasText: 'linked test' });
        await caption(p, 'Requirements lists the tickets imported from Jira or Linear, and how well they are tested.');
        await pointAt(p, m.getByRole('heading', { name: 'Requirements', level: 1 }), 2200);
        await caption(p, 'The four counts sort them: covered, failing, not run yet, and not covered.');
        for (const t of ['Covered', 'Failing', 'Not run yet', 'Not covered']) {
            await pointAt(p, m.getByText(t, { exact: true }).first(), 900);
        }
        await caption(p, 'Each row is one ticket, with its coverage on the right.');
        await pointAt(p, r.first(), 2000);
        await caption(p, 'Click a ticket to see its acceptance criteria and the tests that cover it.');
        await pointAt(p, r.first(), 800);
        await r.first().click();
        await settle(p);
        await pointAt(p, m.getByText('Acceptance criteria', { exact: true }), 1400);
        await pointAt(p, m.getByText('Covered by', { exact: true }), 1600);
        await caption(p, 'Generate tests turns each criterion into a suggested test, for a person to review.');
        await pointAt(p, m.getByRole('button', { name: /Generate tests/ }), 2600);
        await r.first().click();
        await caption(p, 'Import brings in more tickets, from a search or by their keys.');
        await pointAt(p, m.getByRole('button', { name: 'Import', exact: true }), 800);
        await m.getByRole('button', { name: 'Import', exact: true }).click();
        await settle(p);
        await pointAt(p, m.getByRole('button', { name: 'Close import' }).locator('xpath=../following-sibling::div[1]'), 2400);
        await m.getByRole('button', { name: 'Close import' }).click();
        await caption(p, 'Sync all fetches the latest title, status and criteria of every ticket.');
        await pointAt(p, m.getByRole('button', { name: /Sync all/ }), 2400);
        await caption(p, '');
    });
});

import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/security/audit-log.html
// Nothing here changes data. "Verify chain" is pressed: it only reads the
// audit rows and writes nothing. "Export CSV" is never pressed; filters are
// typed but only change what the page shows.

const DIR = 'security/audit-log';

async function open(p: Page) {
    await p.goto('/audit-log');
    await expect(p.getByRole('heading', { name: 'Audit log', level: 1 })).toBeVisible();
    await settle(p);
    await expect(p.locator('main tbody tr').first()).toBeVisible();
}

test('Security › Audit log', async ({ page, browser }) => {
    await open(page);
    const main = page.locator('main');
    await expect(main.getByLabel('Workspace')).toBeVisible();

    // 1. The page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    const picker = main.getByLabel('Workspace').locator('xpath=..');
    const buttons = main.getByRole('button', { name: 'Export CSV' });
    const filters = main.getByRole('button', { name: 'Apply filters' }).locator('xpath=ancestor::form[1]');
    const table = main.locator('table');
    const paging = main.getByRole('button', { name: 'Previous page' }).locator('xpath=..');
    const clean = await annotate(page, [
        { n: 1, target: picker },
        { n: 2, target: buttons },
        { n: 3, target: filters },
        { n: 4, target: table },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await filters.screenshot({ path: out(`${DIR}/filters.png`) });
    // The demo log has many entries: keep only the table's first eight rows.
    await page.evaluate(() => window.scrollTo(0, 0));
    const top = await table.boundingBox();
    const eighth = await table.locator('tbody tr').nth(7).boundingBox();
    if (top && eighth) {
        await page.setViewportSize({ width: 1440, height: Math.ceil(eighth.y + eighth.height) + 40 });
        await page.screenshot({ path: out(`${DIR}/table.png`),
            clip: { x: top.x, y: top.y, width: top.width, height: eighth.y + eighth.height - top.y + 2 } });
        await page.setViewportSize({ width: 1440, height: 900 });
    }
    await paging.scrollIntoViewIfNeeded();
    await paging.screenshot({ path: out(`${DIR}/paging.png`) });
    await main.getByRole('button', { name: 'Details' }).first().click();
    await expect(main.locator('pre').first()).toBeVisible();
    await main.locator('pre').first().locator('xpath=ancestor::tr[1]').screenshot({ path: out(`${DIR}/details.png`) });
    await main.getByRole('button', { name: 'Hide details' }).first().click();
    await main.getByRole('button', { name: 'Verify chain' }).click();
    const verdict = main.getByRole('status').filter({ hasText: /rows checked/ });
    await expect(verdict).toBeVisible();
    await verdict.screenshot({ path: out(`${DIR}/verify.png`) });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await open(p);
        const m = p.locator('main');
        await caption(p, 'The audit log is TraceIQ\'s record of who did what in this workspace, newest first.', 2000);
        await caption(p, 'Each row says when, who, what they did, and to which thing.');
        await pointAt(p, m.locator('tbody tr').first(), 2200);
        await caption(p, 'Details shows exactly what changed.');
        const d = m.getByRole('button', { name: 'Details' }).first();
        await pointAt(p, d, 600);
        await d.click();
        await beat(p, 2000);
        await m.getByRole('button', { name: 'Hide details' }).first().click();
        await caption(p, 'Filters narrow the list, for example to one person, one kind of action, or a range of days.');
        await pointAt(p, m.getByLabel('Action'), 1000);
        await pointAt(p, m.getByLabel('User'), 1000);
        await pointAt(p, m.getByRole('button', { name: 'Apply filters' }), 1200);
        await caption(p, 'Rows can never be edited or deleted, and each one is linked to the one before it.');
        await beat(p, 1600);
        await caption(p, 'Verify chain checks those links, so you can prove that nothing was changed afterwards.');
        const v = m.getByRole('button', { name: 'Verify chain' });
        await pointAt(p, v, 700);
        await v.click();
        await expect(m.getByRole('status').filter({ hasText: /rows checked/ })).toBeVisible();
        await pointAt(p, m.getByRole('status').filter({ hasText: /rows checked/ }), 2200);
        await caption(p, 'Export CSV downloads the rows you are looking at, for an auditor or another tool.', {
            say: 'Export C S V downloads the rows you are looking at, for an auditor or another tool.',
        });
        await pointAt(p, m.getByRole('button', { name: 'Export CSV' }), 2200);
        await caption(p, '');
    });
});

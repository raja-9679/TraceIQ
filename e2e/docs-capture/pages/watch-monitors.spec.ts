import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/watch/monitors.html
// The demo's only monitor belongs to "Demo — Public API", so each capture
// switches the sidebar project first (a browser-side preference, not data).
// Nothing here changes data: the status-page buttons and Refresh/Edit links
// are pointed at, never pressed (Refresh only re-reads).

const DIR = 'watch/monitors';

async function useApiProject(page: Page) {
    await page.getByRole('button', { name: /^Project: / }).first().click();
    await page.getByRole('menuitem', { name: /Demo — Public API/ }).click();
    await settle(page);
}

test('Watch › Monitors', async ({ page, browser }) => {
    await page.goto('/monitors');
    await expect(page.getByRole('heading', { name: 'Watch', level: 1 })).toBeVisible();
    await useApiProject(page);
    const main = page.locator('main');
    const detail = main.getByRole('article', { name: 'Selected monitor' });
    await expect(detail.getByText('Recent checks')).toBeVisible();
    await settle(page);

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    const header = main.getByRole('heading', { name: 'Watch', level: 1 }).locator('xpath=ancestor::div[contains(@class,"rounded-3xl")][1]');
    const list = main.locator('section').filter({ has: page.getByRole('button', { pressed: true }) }).first();
    const firstGroup = list.locator('div.rounded-3xl').first();
    const statusPage = main.getByText('Public status page').locator('xpath=ancestor::div[contains(@class,"rounded-2xl")][1]');
    const top = detail.locator('xpath=./div[1]');
    const bars = detail.getByText('Recent checks').locator('xpath=ancestor::div[contains(@class,"flex-col")][1]');
    const stats = detail.getByText('24-hour uptime', { exact: true }).locator('xpath=ancestor::div[contains(@class,"grid")][1]');
    const meaning = detail.getByText('What this means', { exact: true }).locator('xpath=ancestor::div[contains(@class,"rounded-2xl")][1]');
    const evidence = detail.getByText('Evidence', { exact: true }).locator('xpath=ancestor::div[contains(@class,"flex-col")][1]');
    await page.setViewportSize({ width: 1440, height: 1240 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: header },
        { n: 2, target: firstGroup, side: 'left' },
        { n: 3, target: statusPage, side: 'left' },
        { n: 4, target: bars, side: 'right' },
        { n: 5, target: stats, side: 'right' },
        { n: 6, target: meaning, side: 'right' },
        { n: 7, target: evidence, side: 'right' },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await header.screenshot({ path: out(`${DIR}/header.png`) });
    await firstGroup.screenshot({ path: out(`${DIR}/list.png`) });
    await statusPage.screenshot({ path: out(`${DIR}/status-page.png`) });
    await top.screenshot({ path: out(`${DIR}/detail-top.png`) });
    await bars.screenshot({ path: out(`${DIR}/checks.png`) });
    await stats.screenshot({ path: out(`${DIR}/stats.png`) });
    await meaning.screenshot({ path: out(`${DIR}/meaning.png`) });
    await evidence.screenshot({ path: out(`${DIR}/evidence.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/monitors');
        await expect(p.getByRole('heading', { name: 'Watch', level: 1 })).toBeVisible();
        await settle(p);
        await caption(p, 'Monitors run a test on a timer against your live site, and tell you when it goes down.', 2000);
        await caption(p, 'Monitors belong to a project. The demo monitor is in the Public API project.');
        await pointAt(p, p.getByRole('button', { name: /^Project: / }).first(), 1200);
        await useApiProject(p);
        const m = p.locator('main');
        const d = m.getByRole('article', { name: 'Selected monitor' });
        await expect(d.getByText('Recent checks')).toBeVisible();
        await beat(p, 600);
        await caption(p, 'The list groups monitors: down first, then healthy, waiting for a first check, and paused.');
        await pointAt(p, m.getByRole('button', { pressed: true }), 2600);
        await caption(p, 'This one is paused, so it records no checks and sends no alerts.');
        await pointAt(p, d.getByText('Paused', { exact: true }), 2200);
        await caption(p, 'Each bar is one check, oldest on the left. Green means the test passed.');
        await pointAt(p, d.getByRole('img'), 2600);
        await caption(p, 'Uptime over the last day and week, and how many checks failed in a row.');
        await pointAt(p, d.getByText('24-hour uptime', { exact: true }), 1200);
        await pointAt(p, d.getByText('Failures in a row', { exact: true }), 1400);
        const ev = d.getByText('Evidence', { exact: true });
        await ev.scrollIntoViewIfNeeded();
        await caption(p, 'Each check is a real test run. Click one to open its result.');
        await pointAt(p, d.locator('ul li').first(), 2400);
        await caption(p, 'Edit schedule opens the Schedules page, where its timer can be changed or paused.');
        await pointAt(p, d.getByRole('link', { name: 'Edit schedule' }), 2200);
        await caption(p, 'A project can also have a public status page, built from its monitors.');
        await pointAt(p, m.getByText('Public status page'), 2400);
        await caption(p, '');
    });
});

import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/releases/release-readiness.html
// Nothing here changes data: choosing a commit only changes what the page
// reads. The Project menu is never used (it would switch the app's active
// project), and Re-evaluate / Use branch are pointed at, never pressed.

const DIR = 'releases/release-readiness';

const open = async (p: Page) => {
    await p.goto('/release-readiness');
    await expect(p.getByRole('heading', { name: 'Release readiness', level: 1 })).toBeVisible();
    await expect(p.getByRole('status').filter({ hasText: /Ready to release|Not ready|Insufficient data/ })).toBeVisible();
    await settle(p);
};
const section = (p: Page, title: string) =>
    p.locator('main').getByRole('heading', { name: title, level: 2 }).locator('xpath=ancestor::section[1]');

test('Releases › Release readiness', async ({ page, browser }) => {
    await open(page);
    const main = page.locator('main');
    const verdict = main.getByRole('status').filter({ hasText: /Ready to release|Not ready|Insufficient data/ });

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered (the guide explains each number).
    const controls = main.locator('#rr-commit').locator('xpath=ancestor::div[contains(@class,"shrink-0")][1]');
    await page.setViewportSize({ width: 1440, height: 1380 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: controls },
        { n: 2, target: verdict },
        { n: 3, target: section(page, 'Gate checks') },
        { n: 4, target: section(page, 'Runs considered') },
        { n: 5, target: section(page, 'Critical cases') },
        { n: 6, target: section(page, 'Active quarantines') },
        { n: 7, target: section(page, 'Requirements') },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups used next to each explanation.
    await controls.screenshot({ path: out(`${DIR}/controls.png`) });
    await verdict.screenshot({ path: out(`${DIR}/verdict-latest.png`) });
    await section(page, 'Gate checks').screenshot({ path: out(`${DIR}/checks.png`) });
    await section(page, 'Runs considered').screenshot({ path: out(`${DIR}/runs.png`) });
    await section(page, 'Critical cases').screenshot({ path: out(`${DIR}/critical.png`) });
    await section(page, 'Active quarantines').screenshot({ path: out(`${DIR}/quarantines.png`) });
    await section(page, 'Requirements').screenshot({ path: out(`${DIR}/requirements.png`) });

    // The commit menu, open (closed again with Escape).
    await main.locator('#rr-commit').click();
    const listbox = page.getByRole('listbox');
    await expect(listbox).toBeVisible();
    await listbox.screenshot({ path: out(`${DIR}/commit-menu.png`) });
    const options = await listbox.getByRole('option').allInnerTexts();
    await page.keyboard.press('Escape');

    // Find a commit the demo marks "Ready to release", and one marked "Not
    // ready" where a critical case failed, to show those states too.
    let notReady: string | null = null;
    let ready = false;
    let criticalFailed = false;
    for (const label of options.slice(1)) {
        if (ready && criticalFailed) break;
        await main.locator('#rr-commit').click();
        await page.getByRole('option', { name: label, exact: true }).click();
        await settle(page);
        if (!ready && await verdict.filter({ hasText: 'Ready to release' }).count()) {
            await verdict.screenshot({ path: out(`${DIR}/verdict-ready.png`) });
            ready = true;
        }
        if (!(await verdict.filter({ hasText: 'Not ready' }).count())) continue;
        notReady ??= label;
        if (!criticalFailed && await section(page, 'Critical cases').locator('tbody tr td:nth-child(2)').filter({ hasText: /failed/i }).count()) {
            notReady = label;
            criticalFailed = true;
        }
    }
    if (notReady) {
        await main.locator('#rr-commit').click();
        await page.getByRole('option', { name: notReady, exact: true }).click();
        await settle(page);
        await verdict.screenshot({ path: out(`${DIR}/verdict-not-ready.png`) });
        await section(page, 'Critical cases').screenshot({ path: out(`${DIR}/critical-not-ready.png`) });
    }
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await open(p);
        const m = p.locator('main');
        const v = m.getByRole('status').filter({ hasText: /Ready to release|Not ready|Insufficient data/ });
        await caption(p, 'Release readiness gives one answer for one version of your app: ready, or not.');
        await pointAt(p, v.locator('div.text-2xl'), 2200);
        await caption(p, 'Under the answer: the commit it judged, how many runs, and the reasons.');
        await pointAt(p, v.locator('.font-mono').first(), 2400);
        await caption(p, 'Pick another commit here to judge that version instead.');
        await pointAt(p, m.locator('#rr-commit'), 900);
        await m.locator('#rr-commit').click();
        await beat(p, 1800);
        if (notReady) {
            await p.getByRole('option', { name: notReady, exact: true }).click();
            await settle(p);
            await caption(p, 'This commit is not ready. The reason says which check failed, and why.');
            await pointAt(p, v.locator('ul li').first(), 2600);
        } else {
            await p.keyboard.press('Escape');
        }
        await caption(p, 'Gate checks: what was measured, and what the rules require.');
        await pointAt(p, section(p, 'Gate checks'), 2200);
        await caption(p, 'Runs considered: every finished run on that commit. Click one to open it.');
        await pointAt(p, section(p, 'Runs considered').locator('a').first(), 2400);
        const crit = section(p, 'Critical cases');
        await crit.scrollIntoViewIfNeeded();
        await caption(p, 'Critical cases are the tests marked critical, with their result on this commit.');
        await pointAt(p, crit.locator('tbody tr').first(), 2600);
        const quar = section(p, 'Active quarantines');
        await quar.scrollIntoViewIfNeeded();
        await caption(p, 'Quarantined tests are left out of runs, so they are no evidence for this release.');
        await pointAt(p, quar, 2400);
        await caption(p, 'Requirements shows the linked requirements, judged on the same commit.');
        await pointAt(p, section(p, 'Requirements').locator('li').first(), 2600);
        await caption(p, '');
    });
});

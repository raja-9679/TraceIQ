import { expect, test } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/reports/executive-summary.html
// Nothing here changes data: the scope / period buttons and the workspace
// picker only change what is shown, and "Create link" is never pressed (a
// share link is a real, public credential).

const DIR = 'reports/executive-summary';

test('Reports › Executive summary', async ({ page, browser }) => {
    await page.goto('/reports/summary');
    await expect(page.getByRole('heading', { name: 'Executive summary', level: 1 })).toBeVisible();
    const main = page.locator('main');
    await expect(main.getByRole('region', { name: 'Headline' })).toBeVisible();
    await settle(page);

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered (the guide explains each number).
    const toolbar = main.getByRole('group', { name: 'Scope' }).locator('xpath=..');
    const headline = main.getByRole('region', { name: 'Headline' });
    const kpis = main.getByText('Failing results', { exact: true }).locator('xpath=ancestor::div[contains(@class,"grid")][1]');
    const trend = main.getByRole('region', { name: 'Pass rate trend' });
    const lists = main.getByRole('region', { name: 'Top failing tests' }).locator('xpath=..');
    const share = main.locator('section[aria-labelledby="share-links"]');
    const print = main.getByRole('button', { name: 'Print or save as PDF' });
    await page.setViewportSize({ width: 1440, height: 1400 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: toolbar },
        { n: 2, target: headline },
        { n: 3, target: kpis },
        { n: 4, target: trend },
        { n: 5, target: lists },
        { n: 6, target: share },
        { n: 7, target: print, side: 'right' },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups used next to each explanation.
    await toolbar.screenshot({ path: out(`${DIR}/toolbar.png`) });
    await headline.screenshot({ path: out(`${DIR}/headline.png`) });
    await kpis.screenshot({ path: out(`${DIR}/tiles.png`) });
    await trend.screenshot({ path: out(`${DIR}/trend.png`) });
    await lists.screenshot({ path: out(`${DIR}/lists.png`) });
    await share.screenshot({ path: out(`${DIR}/share.png`) });

    // The whole-workspace view adds a table of projects. The docs account
    // also has a workspace of its own, so pick the demo one.
    await main.getByRole('group', { name: 'Scope' }).getByRole('button', { name: 'Whole workspace' }).click();
    const wsPick = main.getByLabel('Workspace');
    await expect(wsPick).toBeVisible();
    await wsPick.selectOption({ label: 'Demo workspace' });
    const projects = main.getByRole('region', { name: 'Projects' });
    await expect(projects.getByText('Demo — Public API')).toBeVisible();
    await settle(page);
    await main.getByRole('region', { name: 'Headline' }).screenshot({ path: out(`${DIR}/workspace-headline.png`) });
    await projects.screenshot({ path: out(`${DIR}/workspace-projects.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/reports/summary');
        await expect(p.getByRole('heading', { name: 'Executive summary', level: 1 })).toBeVisible();
        const m = p.locator('main');
        await expect(m.getByRole('region', { name: 'Headline' })).toBeVisible();
        await settle(p);
        await caption(p, 'The executive summary shows how testing is going, on one page you can print.', 2200);
        await caption(p, 'The headline says it in a sentence or two: pass rate, release readiness and what to watch.');
        await pointAt(p, m.getByRole('region', { name: 'Headline' }).locator('p').first(), 2600);
        await caption(p, 'The tiles give the numbers, with the change against the period before.');
        await pointAt(p, m.getByText('Pass rate', { exact: true }).first(), 1300);
        await pointAt(p, m.getByText('Failing results', { exact: true }), 1300);
        await pointAt(p, m.getByText('Open failure groups', { exact: true }), 1300);
        await caption(p, 'The period buttons choose 7, 30 or 90 days.', { say: 'The period buttons choose seven, thirty or ninety days.' });
        const period = m.getByRole('group', { name: 'Period' });
        await pointAt(p, period.getByRole('button', { name: '7 days' }), 900);
        await period.getByRole('button', { name: '7 days' }).click();
        await settle(p);
        await beat(p, 1200);
        const tr = m.getByRole('region', { name: 'Pass rate trend' });
        await tr.scrollIntoViewIfNeeded();
        await caption(p, 'One bar per day. The taller the bar, the more runs passed that day.');
        const bars = tr.locator('[title]');
        await pointAt(p, bars.nth(Math.max(0, (await bars.count()) - 3)), 2400);
        await m.getByRole('region', { name: 'Top failing tests' }).scrollIntoViewIfNeeded();
        await caption(p, 'Below: the tests that failed most, and the open failure groups behind them.');
        await pointAt(p, m.getByRole('region', { name: 'Top failing tests' }).locator('li').first(), 1600);
        await pointAt(p, m.getByRole('region', { name: 'Open failure groups' }).locator('li').first(), 1600);
        await period.getByRole('button', { name: '30 days' }).click();
        await settle(p);
        await p.evaluate(() => window.scrollTo(0, 0));
        await caption(p, 'Whole workspace adds up every project you can see, with one row per project.');
        const scope = m.getByRole('group', { name: 'Scope' });
        await pointAt(p, scope.getByRole('button', { name: 'Whole workspace' }), 900);
        await scope.getByRole('button', { name: 'Whole workspace' }).click();
        await m.getByLabel('Workspace').selectOption({ label: 'Demo workspace' });
        const pr = m.getByRole('region', { name: 'Projects' });
        await expect(pr.getByText('Demo — Public API')).toBeVisible();
        await settle(p);
        await pr.scrollIntoViewIfNeeded();
        await pointAt(p, pr.locator('tbody tr').first(), 2400);
        const sh = m.locator('section[aria-labelledby="share-links"]');
        await sh.scrollIntoViewIfNeeded();
        await caption(p, 'Share a read-only link with people who have no TraceIQ account. It shows numbers and names only.');
        await pointAt(p, sh.getByRole('button', { name: 'Create link' }), 2600);
        await caption(p, 'Print or save as PDF gives a clean printout of the summary.');
        await p.evaluate(() => window.scrollTo(0, 0));
        await pointAt(p, m.getByRole('button', { name: 'Print or save as PDF' }), 2400);
        await caption(p, '');
    });
});

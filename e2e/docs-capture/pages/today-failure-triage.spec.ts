import { expect, test } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/today/failure-triage.html
// Nothing here changes data: the status menu is opened and closed again, never
// picked from, so the demo's groups stay "Open" from one capture to the next.

const DIR = 'today/failure-triage';

test('Today › Failure triage', async ({ page, browser }) => {
    await page.goto('/triage');
    await expect(page.getByRole('heading', { name: 'Triage', level: 1 })).toBeVisible();
    await settle(page);
    const main = page.locator('main');
    const detail = main.getByRole('article', { name: 'Selected failure cluster' });
    await expect(detail.getByText('Evidence', { exact: true })).toBeVisible();
    await settle(page);

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered (the guide explains each number).
    const tabs = main.getByRole('tablist', { name: 'Filter by status' });
    const list = main.locator('section').filter({ has: page.getByRole('button', { pressed: true }) }).first();
    const facts = detail.getByText('Times it failed', { exact: true }).locator('xpath=ancestor::div[contains(@class,"grid")][1]');
    const evidence = detail.getByText('Evidence', { exact: true }).locator('xpath=ancestor::div[contains(@class,"flex-col")][1]');
    const actions = detail.getByLabel('Cluster status').locator('xpath=ancestor::div[contains(@class,"border-t")][1]');
    await page.setViewportSize({ width: 1440, height: 1500 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: tabs },
        { n: 2, target: list, side: 'left' },
        { n: 3, target: facts },
        { n: 4, target: evidence },
        { n: 5, target: actions },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups used next to each explanation.
    await tabs.screenshot({ path: out(`${DIR}/tabs.png`) });
    await list.screenshot({ path: out(`${DIR}/list.png`) });
    await detail.getByText('What this means', { exact: true }).locator('xpath=ancestor::div[1]').scrollIntoViewIfNeeded();
    const top = detail.locator('xpath=./div[1]');
    await top.screenshot({ path: out(`${DIR}/detail-header.png`) });
    await facts.screenshot({ path: out(`${DIR}/facts.png`) });
    await detail.getByText('Sample error', { exact: true }).locator('xpath=ancestor::div[1]').screenshot({ path: out(`${DIR}/sample-error.png`) });
    await evidence.screenshot({ path: out(`${DIR}/evidence.png`) });
    await actions.screenshot({ path: out(`${DIR}/actions.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/triage');
        await expect(p.getByRole('heading', { name: 'Triage', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        const d = m.getByRole('article', { name: 'Selected failure cluster' });
        await caption(p, 'Failure triage groups failed tests by their cause, so one problem is one item.', 2200);
        await caption(p, 'The line at the top says how many problems there are, and how many failures they caused.');
        await pointAt(p, m.getByText(/problems? behind/), 2200);
        await caption(p, 'The tabs choose which problems to show. Open is chosen first.');
        await pointAt(p, m.getByRole('tab', { name: 'Open' }), 1400);
        await pointAt(p, m.getByRole('tab', { name: 'All' }), 1200);
        const rows = m.getByRole('button', { pressed: false }).filter({ hasText: 'last seen' });
        const first = m.getByRole('button', { pressed: true });
        await caption(p, 'Problems are grouped by kind. The number on the right is how many times it failed.');
        await pointAt(p, first.getByTitle('Times it failed'), 2400);
        await caption(p, 'Click a problem to see its details on the right.');
        await pointAt(p, rows.first(), 1200);
        await rows.first().click();
        await settle(p);
        await beat(p, 600);
        await caption(p, 'How often it failed, when it was first seen, and when it was last seen.');
        await pointAt(p, d.getByText('Times it failed', { exact: true }), 2600);
        await d.getByText('Sample error', { exact: true }).scrollIntoViewIfNeeded();
        await caption(p, 'The sample error is the message from one of these failures.');
        await pointAt(p, d.getByText('Sample error', { exact: true }), 2200);
        await d.getByText('Evidence', { exact: true }).scrollIntoViewIfNeeded();
        await caption(p, 'Evidence lists each failing test and the run it came from. Click one to open that run.');
        await pointAt(p, d.locator('ul li').first(), 2600);
        const status = d.getByLabel('Cluster status');
        await status.scrollIntoViewIfNeeded();
        await caption(p, 'When you start on a problem, set it to Investigating. Set it to Resolved once it is fixed.');
        await pointAt(p, status, 900);
        await status.click();
        await beat(p, 2600);
        await p.keyboard.press('Escape');
        await caption(p, 'Open latest failing run takes you to the newest run where it happened.');
        await pointAt(p, d.getByRole('link', { name: 'Open latest failing run' }), 2400);
        await caption(p, '');
    });
});

import { expect, test } from '@playwright/test';
import { annotate, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/setup/worker-fleet.html
// Needs an instance admin: the lead's capture grants the docs account instance
// admin for the length of the run only (see the guide page's comment).

const DIR = 'setup/worker-fleet';

test('Setup › Worker fleet', async ({ page, browser }) => {
    await page.goto('/admin/workers');
    await expect(page.getByRole('heading', { name: 'Worker Fleet', level: 1 })).toBeVisible();
    const main = page.locator('main');
    await expect(main.getByRole('heading', { name: 'Workers', level: 2 })).toBeVisible();
    await settle(page);
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    const tiles = main.getByText('Alive', { exact: true }).locator('xpath=ancestor::div[contains(@class,"grid")][1]');
    const workers = main.getByRole('heading', { name: 'Workers', level: 2 }).locator('xpath=following-sibling::div[1]');
    const streams = main.getByRole('heading', { name: 'Job streams', level: 2 }).locator('xpath=following-sibling::div[1]');
    await page.setViewportSize({ width: 1440, height: 1100 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: tiles },
        { n: 2, target: workers },
        { n: 3, target: streams },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();
    await tiles.screenshot({ path: out(`${DIR}/tiles.png`) });
    await workers.screenshot({ path: out(`${DIR}/workers.png`) });
    await streams.screenshot({ path: out(`${DIR}/streams.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });

    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/admin/workers');
        await expect(p.getByRole('heading', { name: 'Workers', level: 2 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        await caption(p, 'Worker fleet shows the machines that run your tests, and the work waiting for them.', 2200);
        await caption(p, 'Alive counts the workers that checked in during the last 45 seconds.', {
            say: 'Alive counts the workers that checked in during the last forty-five seconds.',
        });
        await pointAt(p, m.getByText('Alive', { exact: true }), 2000);
        await caption(p, 'Lost workers stopped checking in. Busy ones are running a test right now.');
        await pointAt(p, m.getByText('Lost', { exact: true }), 1200);
        await pointAt(p, m.getByText('Busy', { exact: true }), 1400);
        await caption(p, 'Dead letters are tests that failed to run several times and were set aside.');
        await pointAt(p, m.getByText('Dead letters', { exact: true }), 2200);
        const w = m.getByRole('heading', { name: 'Workers', level: 2 });
        await w.scrollIntoViewIfNeeded();
        await caption(p, 'One row per worker: its status, version, current job, and how many jobs it has done.');
        await pointAt(p, m.locator('table').first().locator('tbody tr').first(), 2600);
        const s = m.getByRole('heading', { name: 'Job streams', level: 2 });
        await s.scrollIntoViewIfNeeded();
        await caption(p, 'Job streams are the queues. Queued means waiting for a worker; in flight means being run.');
        await pointAt(p, m.locator('table').nth(1).locator('tbody tr').first(), 2600);
        await caption(p, 'The page refreshes itself every 15 seconds.', { say: 'The page refreshes itself every fifteen seconds.' });
        await pointAt(p, m.getByRole('button', { name: 'Refresh worker fleet' }), 1800);
        await caption(p, '');
    });
});

import { expect, test, type Page } from '@playwright/test';
import { annotate, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/setup/worker-fleet.html
// Needs an instance admin: run it with e2e/docs-capture/admin-capture.sh,
// which grants the docs account instance admin for the length of the run only.
// Nothing here changes data: only tabs and workers are clicked; no Cancel run,
// Clear, Retry or Discard button is pressed.

const DIR = 'setup/worker-fleet';

async function open(p: Page) {
    await p.goto('/admin/workers');
    await expect(p.getByRole('heading', { name: 'Worker fleet', level: 1 })).toBeVisible();
    await expect(p.getByRole('tab', { name: /Workers/ })).toHaveAttribute('aria-selected', 'true');
    await expect(p.locator('main').getByRole('article', { name: /^Worker / })).toBeVisible();
    await settle(p);
}

test('Setup › Worker fleet', async ({ page, browser }) => {
    await open(page);
    const main = page.locator('main');
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    const tabs = main.getByRole('tablist', { name: 'Fleet sections' });
    const tiles = main.getByRole('region', { name: 'Dead letters', exact: true }).locator('xpath=..');
    const list = main.getByRole('heading', { name: /^(Alive|Lost)$/, level: 2 }).first().locator('xpath=ancestor::section[1]');
    const detail = main.getByRole('article', { name: /^Worker / });
    const refresh = main.getByRole('button', { name: 'Refresh' });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: refresh, side: 'right' },
        { n: 2, target: tabs },
        { n: 3, target: tiles },
        { n: 4, target: list },
        { n: 5, target: detail, side: 'right', inset: true },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();
    await tiles.screenshot({ path: out(`${DIR}/tiles.png`) });
    await list.screenshot({ path: out(`${DIR}/workers.png`) });
    await detail.screenshot({ path: out(`${DIR}/worker-detail.png`) });

    const panel = (title: string) => main.getByRole('heading', { name: title, level: 2 }).locator('xpath=ancestor::section[1]');
    await main.getByRole('tab', { name: /Waiting jobs/ }).click();
    await expect(panel('Waiting jobs')).toBeVisible();
    await settle(page);
    await panel('Waiting jobs').screenshot({ path: out(`${DIR}/waiting.png`) });
    await main.getByRole('tab', { name: /Failed jobs/ }).click();
    await expect(panel('Failed jobs')).toBeVisible();
    await settle(page);
    await panel('Failed jobs').screenshot({ path: out(`${DIR}/failed.png`) });
    await main.getByRole('tab', { name: /Job streams/ }).click();
    await expect(panel('Job streams')).toBeVisible();
    await settle(page);
    await panel('Job streams').screenshot({ path: out(`${DIR}/streams.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });

    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await open(p);
        const m = p.locator('main');
        const tab = (name: RegExp) => m.getByRole('tab', { name });
        await caption(p, 'Worker fleet shows the machines that run your tests, and the work waiting for them.', 2200);
        await caption(p, 'Alive counts the workers that checked in during the last 45 seconds.', {
            say: 'Alive counts the workers that checked in during the last forty-five seconds.',
        });
        await pointAt(p, m.getByRole('region', { name: 'Alive', exact: true }), 2000);
        await caption(p, 'Lost workers stopped checking in. Busy ones are running a test right now.');
        await pointAt(p, m.getByRole('region', { name: 'Lost', exact: true }), 1200);
        await pointAt(p, m.getByRole('region', { name: 'Busy', exact: true }), 1400);
        await caption(p, 'Dead letters are tests that failed to run several times and were set aside.');
        await pointAt(p, m.getByRole('region', { name: 'Dead letters', exact: true }), 2200);
        await caption(p, 'The Workers tab lists every worker. Click one to see its version, current job, and how many jobs it has done.');
        await pointAt(p, m.getByRole('heading', { name: /^(Alive|Lost)$/, level: 2 }).first().locator('xpath=ancestor::section[1]').getByRole('button').first(), 1600);
        await pointAt(p, m.getByRole('article', { name: /^Worker / }), 2000);
        await caption(p, 'Waiting jobs lists every run with work in the queue. Cancel run clears its jobs and cancels the run.');
        await tab(/Waiting jobs/).click();
        await pointAt(p, m.getByRole('heading', { name: 'Waiting jobs', level: 2 }), 2600);
        await caption(p, 'Failed jobs were set aside after crashing three times. You can retry them or discard them.');
        await tab(/Failed jobs/).click();
        await pointAt(p, m.getByRole('heading', { name: 'Failed jobs', level: 2 }), 2400);
        await caption(p, 'Job streams are the queues. Queued means waiting for a worker; in flight means being run.');
        await tab(/Job streams/).click();
        await pointAt(p, m.locator('table').first().locator('tbody tr').first(), 2600);
        await caption(p, 'The page refreshes itself every 15 seconds.', { say: 'The page refreshes itself every fifteen seconds.' });
        await pointAt(p, m.getByRole('button', { name: 'Refresh' }), 1800);
        await caption(p, '');
    });
});

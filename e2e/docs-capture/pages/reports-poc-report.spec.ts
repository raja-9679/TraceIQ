import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/reports/poc-report.html
// Nothing here changes data: "Update report" only re-reads the report with
// other assumptions (a GET). "Create link" and "Print" are never pressed.

const DIR = 'reports/poc-report';

/** The page opens on the account's first workspace; the pictures need the demo one. */
async function pickDemo(p: Page) {
    const main = p.locator('main');
    await main.getByLabel('Workspace').selectOption({ label: 'Demo workspace' });
    await settle(p);
    await expect(main.getByText(/Demo workspace ·/)).toBeVisible();
}

test('Reports › PoC report', async ({ page, browser }) => {
    await page.goto('/reports/poc');
    await expect(page.getByRole('heading', { name: 'PoC report', level: 1 })).toBeVisible();
    await settle(page);
    await pickDemo(page);
    const main = page.locator('main');

    // 1. The page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    const inputs = main.locator('section[aria-labelledby="poc-inputs"]');
    const estimate = main.locator('section[aria-label="Estimated time saved"]');
    const tiles = estimate.locator('xpath=following-sibling::div[1]');
    const setup = main.locator('section[aria-labelledby="poc-setup"]');
    const activity = main.locator('section[aria-labelledby="poc-activity"]');
    const found = main.locator('section[aria-labelledby="poc-value"]');
    const share = main.locator('section[aria-labelledby="poc-share"]');
    const print = main.getByRole('button', { name: 'Print or save as PDF' });
    await page.setViewportSize({ width: 1440, height: 2500 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: print, side: 'right' },
        { n: 2, target: inputs },
        { n: 3, target: estimate },
        { n: 4, target: tiles },
        { n: 5, target: setup },
        { n: 6, target: activity },
        { n: 7, target: found },
        { n: 8, target: share },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups used next to each explanation.
    await inputs.screenshot({ path: out(`${DIR}/inputs.png`) });
    await estimate.screenshot({ path: out(`${DIR}/estimate.png`) });
    await tiles.screenshot({ path: out(`${DIR}/tiles.png`) });
    await setup.screenshot({ path: out(`${DIR}/setup.png`) });
    await activity.screenshot({ path: out(`${DIR}/activity.png`) });
    await found.screenshot({ path: out(`${DIR}/found.png`) });
    await share.screenshot({ path: out(`${DIR}/share.png`) });

    // 4. The estimate with a rate and currency (re-read, nothing saved).
    await inputs.getByLabel('Hourly rate (optional)').fill('40');
    await inputs.getByLabel('Currency code').fill('EUR');
    await inputs.getByRole('button', { name: 'Update report' }).click();
    await settle(page);
    await expect(estimate.getByText(/Estimated EUR/)).toBeVisible();
    await estimate.screenshot({ path: out(`${DIR}/estimate-money.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });

    // 5. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/reports/poc');
        await expect(p.getByRole('heading', { name: 'PoC report', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        await caption(p, 'The PoC report sums up a trial of TraceIQ: what was set up, what ran, and what it found.', 2000);
        await caption(p, 'First choose the workspace the trial used. Here, the demo workspace.');
        const ws = m.getByLabel('Workspace');
        await pointAt(p, ws, 1000);
        await ws.selectOption({ label: 'Demo workspace' });
        await settle(p);
        await caption(p, 'From and To set the dates. The last 30 days are chosen to begin with.', {
            say: 'From and To set the dates. The last thirty days are chosen to begin with.',
        });
        await m.getByLabel('From').scrollIntoViewIfNeeded();
        await pointAt(p, m.getByLabel('From'), 2200);
        await pointAt(p, m.getByLabel('To'), 3200);
        const est = m.locator('section[aria-label="Estimated time saved"]');
        await est.scrollIntoViewIfNeeded();
        await caption(p, 'The estimate: each automated test run, times the minutes a person would need to do it by hand.');
        await pointAt(p, est.locator('p').first(), 2600);
        const mins = m.getByLabel('Minutes per manual execution');
        await mins.scrollIntoViewIfNeeded();
        await caption(p, 'Change the minutes, and add an hourly rate and currency for an amount of money.');
        await mins.fill('');
        await mins.pressSequentially('10', { delay: 120 });
        await m.getByLabel('Hourly rate (optional)').pressSequentially('40', { delay: 120 });
        await m.getByLabel('Currency code').pressSequentially('EUR', { delay: 120 });
        await beat(p, 500);
        await caption(p, 'Update report works it out again. Nothing is saved.');
        const upd = m.getByRole('button', { name: 'Update report' });
        await pointAt(p, upd, 800);
        await upd.click();
        await settle(p);
        await est.scrollIntoViewIfNeeded();
        await caption(p, 'The result is always labelled as an estimate, with the assumptions next to it.');
        await pointAt(p, est.getByText(/Estimated EUR/), 2600);
        const found = m.locator('section[aria-labelledby="poc-value"]');
        await found.scrollIntoViewIfNeeded();
        await caption(p, 'Below: the setup, the activity, and what testing found.');
        await pointAt(p, found.getByText('Failing test results'), 2200);
        const sh = m.locator('section[aria-labelledby="poc-share"]');
        await sh.scrollIntoViewIfNeeded();
        await caption(p, 'To show someone outside, create a read-only link, or print the page.');
        await pointAt(p, sh.getByRole('button', { name: 'Create link' }), 2400);
        await caption(p, '');
    });
});

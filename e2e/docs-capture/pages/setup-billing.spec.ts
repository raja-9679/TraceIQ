import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/setup/billing.html
// Nothing here changes data: no plan button ("Switch to …" / "Upgrade") is
// ever pressed. The capture stack has no plans and no Stripe, so the demo
// workspace shows "No plan (unlimited)" and manual billing; the Plans section
// is hidden when there are no plans. The meters show the demo's seeded usage.

const DIR = 'setup/billing';

async function openDemoWorkspace(p: Page) {
    await p.goto('/billing');
    await expect(p.getByRole('heading', { name: 'Plan and usage', level: 1 })).toBeVisible();
    await settle(p);
    // The page starts on the account's first workspace; the guide shows the demo one.
    await p.getByRole('combobox', { name: 'Workspace' }).click();
    await p.getByRole('option', { name: 'Demo workspace' }).click();
    await settle(p);
    await expect(p.getByText(/Current plan:/)).toBeVisible();
    // The page asks /admin/instance-admins to learn whether the viewer is the
    // instance admin; for anyone else that 403 pops a "Permission Denied"
    // toast (an app bug, reported). Let it time out before taking pictures.
    await expect(p.locator('[data-sonner-toast]')).toHaveCount(0, { timeout: 15_000 });
}

test('Setup › Billing', async ({ page, browser }) => {
    await openDemoWorkspace(page);
    const main = page.locator('main');
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());

    // 1. The page as the demo workspace shows it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    const current = main.getByRole('region', { name: 'Current plan' });
    const heading = current.getByText(/Current plan:/).locator('xpath=ancestor::div[contains(@class,"justify-between")][1]');
    const meters = current.locator('div.grid').first();
    await expect(main.getByRole('region', { name: 'Plans' })).toHaveCount(0);
    const note = main.getByText(/Plan changes take effect immediately/);
    const clean = await annotate(page, [
        { n: 1, target: main.getByRole('combobox', { name: 'Workspace' }), side: 'right' },
        { n: 2, target: heading },
        { n: 3, target: meters },
        { n: 4, target: note },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await heading.screenshot({ path: out(`${DIR}/current.png`) });
    await meters.screenshot({ path: out(`${DIR}/meters.png`) });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/billing');
        await expect(p.getByRole('heading', { name: 'Plan and usage', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        await caption(p, 'Billing shows a workspace’s plan, and how much of it has been used this month.', {
            pause: 1800, say: 'Billing shows a workspace\'s plan, and how much of it has been used this month.',
        });
        const ws = m.getByRole('combobox', { name: 'Workspace' });
        await caption(p, 'Plans belong to a workspace. Choose the workspace here.');
        await pointAt(p, ws, 900);
        await ws.click();
        await beat(p, 900);
        await p.getByRole('option', { name: 'Demo workspace' }).click();
        await settle(p);
        const cur = m.getByRole('region', { name: 'Current plan' });
        await caption(p, 'The current plan. Here there is no plan, so nothing is limited.');
        await pointAt(p, cur.getByText(/Current plan:/), 2200);
        await caption(p, 'The month the counters cover, and how billing works on this server.');
        await pointAt(p, cur.getByText(/Period/), 2200);
        await caption(p, 'Three meters: runs this month, AI generations, and AI tokens this month.');
        await pointAt(p, cur.getByText('Runs this month', { exact: true }), 1200);
        await pointAt(p, cur.getByText('LLM tokens this month', { exact: true }), 1400);
        await caption(p, 'With a limit, the bar fills up and turns yellow, then orange, as it gets close.');
        await pointAt(p, cur.locator('[role="img"]').first(), 2200);
        await caption(p, 'When the server offers plans, they are listed below, with a button to switch.');
        await caption(p, 'This server has no plans set up, so only the note about plan changes follows.');
        await pointAt(p, m.getByText(/Plan changes take effect immediately/), 2200);
        await caption(p, '');
    });
});

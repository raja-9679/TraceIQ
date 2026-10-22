import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/security/access-reviews.html
// Nothing here changes data: "Start review", Keep/Revoke, "Sign off review"
// and "Preview revocations" are never pressed and none of the downloads (CSV,
// printable report) is opened. The demo workspace has two seeded reviews
// (Q4 in review, Q3 signed off); opening one only changes page state.

const DIR = 'security/access-reviews';

async function open(p: Page) {
    await p.goto('/access-reviews');
    await expect(p.getByRole('heading', { name: 'Access Reviews', level: 1 })).toBeVisible();
    await settle(p);
    // The page picks the sidebar project's workspace; make sure it is the demo.
    await p.getByLabel('Review scope').selectOption({ label: 'Demo workspace' });
    await settle(p);
    await expect(p.getByText('Q4 demo workspace access review')).toBeVisible();
    // The page probes /admin/instance-admins to decide whether to offer the
    // whole-instance scope; for anyone else that 403 pops a "Permission Denied"
    // toast (an app bug, see info/GUIDE_FOLLOWUPS.md). Wait for it to go.
    await expect(p.locator('[data-sonner-toast]')).toHaveCount(0, { timeout: 12_000 });
}

test('Security › Access reviews', async ({ page, browser }) => {
    await open(page);
    const main = page.locator('main');

    // 1. The page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    const scope = main.getByLabel('Review scope').locator('xpath=ancestor::label[1]');
    const current = main.getByRole('button', { name: 'Current access (CSV)' });
    const report = main.getByRole('button', { name: 'Printable report' });
    const start = main.getByLabel('Review name').locator('xpath=..');
    const reviews = main.getByRole('heading', { name: 'Reviews', exact: true }).locator('xpath=..');
    const toolbar = scope.locator('xpath=..');
    const clean = await annotate(page, [
        { n: 1, target: scope },
        { n: 2, target: current },
        { n: 3, target: start, side: 'right' },
        { n: 4, target: reviews },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await toolbar.screenshot({ path: out(`${DIR}/toolbar.png`) });
    await reviews.screenshot({ path: out(`${DIR}/reviews.png`) });
    await expect(report).toBeVisible();

    // Inside a review: the open Q4 one, then the signed-off Q3 one.
    await main.getByRole('button', { name: /Q4 demo workspace access review/ }).click();
    await expect(main.getByRole('heading', { name: 'Q4 demo workspace access review' })).toBeVisible();
    await settle(page);
    await page.setViewportSize({ width: 1440, height: 1100 });
    await settle(page);
    await page.screenshot({ path: out(`${DIR}/review-open.png`) });
    await main.getByRole('button', { name: 'All reviews' }).click();
    await settle(page);
    await main.getByRole('button', { name: /Q3 demo workspace access review/ }).click();
    await expect(main.getByRole('heading', { name: 'Q3 demo workspace access review' })).toBeVisible();
    await settle(page);
    await main.getByRole('heading', { name: 'Q3 demo workspace access review' }).locator('xpath=ancestor::div[contains(@class,"rounded-2xl")][1]')
        .screenshot({ path: out(`${DIR}/review-signed.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await open(p);
        const m = p.locator('main');
        await caption(p, 'An access review is a regular check of who can get into TraceIQ, and what they can do there.', 2000);
        await caption(p, 'Scope chooses what to review: one workspace, or the whole server.');
        await pointAt(p, m.getByLabel('Review scope'), 2200);
        await caption(p, 'These two buttons give today\'s list of people and API keys, as a spreadsheet or a printable page.');
        await pointAt(p, m.getByRole('button', { name: 'Current access (CSV)' }), 1400);
        await pointAt(p, m.getByRole('button', { name: 'Printable report' }), 1400);
        await caption(p, 'Start review freezes that list, so each person and key can be checked one by one.');
        await pointAt(p, m.getByLabel('Review name'), 900);
        await pointAt(p, m.getByRole('button', { name: 'Start review' }), 1800);
        await caption(p, 'For each one, the reviewer chooses Keep, or Revoke with a note saying why.');
        await pointAt(p, m.getByRole('heading', { name: 'Reviews', exact: true }), 2000);
        await caption(p, 'Once every item is decided, the reviewer signs the review off. That is recorded in the audit log.');
        await beat(p, 1200);
        await caption(p, 'Access is only removed after sign-off, when someone confirms the revocations.');
        await beat(p, 1200);
        await caption(p, 'Past reviews stay listed here: one still in review, one signed off.');
        await pointAt(p, m.getByText('Q3 demo workspace access review'), 2200);
        await caption(p, '');
    });
});

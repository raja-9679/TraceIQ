import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/security/access-reviews.html
// Nothing here changes data: "Start review" is never pressed and neither of
// the downloads (CSV, printable report) is opened. Only the Scope menu is
// changed, which is page state.

const DIR = 'security/access-reviews';

async function open(p: Page) {
    await p.goto('/access-reviews');
    await expect(p.getByRole('heading', { name: 'Access Reviews', level: 1 })).toBeVisible();
    await settle(p);
    // The capture account's first workspace is not the demo one; pick the demo.
    await p.getByLabel('Review scope').selectOption({ label: 'Demo workspace' });
    await settle(p);
    await expect(p.getByText('No reviews yet for this scope.')).toBeVisible();
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
    // The instance-wide scope, as someone who is not an instance admin sees it.
    await main.getByLabel('Review scope').selectOption({ label: 'Whole instance (instance admins)' });
    await settle(page);
    await reviews.screenshot({ path: out(`${DIR}/instance-scope.png`) });
    await main.getByLabel('Review scope').selectOption({ label: 'Demo workspace' });

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
        await caption(p, 'Past reviews stay listed here. The demo workspace has none yet.');
        await pointAt(p, m.getByText('No reviews yet for this scope.'), 2200);
        await caption(p, '');
    });
});

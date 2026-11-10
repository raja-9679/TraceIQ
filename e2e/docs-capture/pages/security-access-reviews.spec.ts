import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/security/access-reviews.html
// Nothing here changes data: "Start review", Keep/Revoke, "Sign off review"
// and "Preview revocations" are never pressed and none of the downloads (CSV,
// printable report) is opened. The demo workspace has two seeded reviews
// (Q4 in review, Q3 signed off); selecting one only changes page state.

const DIR = 'security/access-reviews';
const Q4 = 'Q4 demo workspace access review';
const Q3 = 'Q3 demo workspace access review';

async function open(p: Page) {
    await p.goto('/access-reviews');
    await expect(p.getByRole('heading', { name: 'Access reviews', level: 1 })).toBeVisible();
    await settle(p);
    // The page picks the sidebar project's workspace; make sure it is the demo.
    await p.getByLabel('Review scope').selectOption({ label: 'Demo workspace' });
    await settle(p);
    await expect(p.locator('main').getByRole('button', { name: new RegExp(Q4) })).toBeVisible();
}

const detail = (p: Page, name: string) => p.locator('main').getByRole('article', { name: `Access review ${name}` });

test('Security › Access reviews', async ({ page, browser }) => {
    await open(page);
    const main = page.locator('main');
    // The first review in the list (Q4, in review) opens on the right by itself.
    await expect(detail(page, Q4)).toBeVisible();
    await settle(page);

    // 1. The page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    const scope = main.getByLabel('Review scope').locator('xpath=ancestor::label[1]');
    const toolbar = scope.locator('xpath=..');
    const downloads = main.getByRole('button', { name: 'Current access (CSV)' });
    const start = main.getByRole('region', { name: 'Start a review' });
    const list = main.getByRole('heading', { name: 'In review', exact: true }).locator('xpath=ancestor::section[1]');
    await page.setViewportSize({ width: 1440, height: 1250 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: scope },
        { n: 2, target: downloads },
        { n: 3, target: start },
        { n: 4, target: list },
        { n: 5, target: detail(page, Q4), side: 'right', inset: true },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await toolbar.screenshot({ path: out(`${DIR}/toolbar.png`) });
    await list.screenshot({ path: out(`${DIR}/reviews.png`) });
    await detail(page, Q4).screenshot({ path: out(`${DIR}/review-open.png`) });
    await main.getByRole('button', { name: new RegExp(Q3) }).click();
    await expect(detail(page, Q3)).toBeVisible();
    await expect(detail(page, Q3).getByRole('button', { name: 'Preview revocations' })).toBeVisible();
    await settle(page);
    await detail(page, Q3).screenshot({ path: out(`${DIR}/review-signed.png`) });
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
        await pointAt(p, m.getByRole('button', { name: 'Printable report' }).first(), 1400);
        await caption(p, 'Start review freezes that list, so each person and key can be checked one by one.');
        await pointAt(p, m.getByLabel('Review name'), 900);
        await pointAt(p, m.getByRole('button', { name: 'Start review' }), 1800);
        await caption(p, 'On the left, the reviews: those still in review, then those signed off. The one picked opens on the right.');
        await pointAt(p, m.getByRole('button', { name: new RegExp(Q4) }), 2200);
        const d = detail(p, Q4);
        await caption(p, 'Each person and key is one item. The reviewer presses Keep, or writes a note saying why and presses Revoke.');
        await pointAt(p, d.getByRole('button', { name: 'Revoke' }).first(), 2400);
        await caption(p, 'Once every item is decided, the reviewer signs the review off. That is recorded in the audit log.');
        await pointAt(p, d.getByRole('button', { name: 'Sign off review' }), 2200);
        await caption(p, 'This older review is already signed off, with one person marked revoke.');
        await m.getByRole('button', { name: new RegExp(Q3) }).click();
        await beat(p, 600);
        await caption(p, 'Access is only removed after sign-off, when someone previews the revocations and confirms them.');
        await pointAt(p, detail(p, Q3).getByRole('button', { name: 'Preview revocations' }), 2400);
        await caption(p, '');
    });
});

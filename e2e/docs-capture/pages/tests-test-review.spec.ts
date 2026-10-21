import { expect, test } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/tests/test-review.html
// Nothing here changes data. The Owner / Backup owner menus save the moment a
// different person is picked, so they are pointed at but never opened, and
// "Mark reviewed" is never pressed.

const DIR = 'tests/test-review';

test('Tests › Test review', async ({ page, browser }) => {
    await page.goto('/tests/review');
    await expect(page.getByRole('heading', { name: 'Test review', level: 1 })).toBeVisible();
    await settle(page);
    const main = page.locator('main');
    const detail = main.getByRole('article', { name: 'Selected test' });
    await expect(detail.getByText('Ownership and review')).toBeVisible();
    await settle(page);

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    const counts = main.getByText(/overdue · \d+ never reviewed/);
    const tabs = main.getByRole('tablist', { name: 'Which tests' });
    const list = main.locator('section').filter({ has: page.getByRole('button', { pressed: true }) }).first();
    const panel = detail.locator('section').filter({ hasText: 'Ownership and review' });
    const clean = await annotate(page, [
        { n: 1, target: counts, side: 'right' },
        { n: 2, target: tabs },
        { n: 3, target: list, side: 'left' },
        { n: 4, target: detail.locator('xpath=./div[1]'), side: 'right' },
        { n: 5, target: panel, side: 'right' },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await tabs.screenshot({ path: out(`${DIR}/tabs.png`) });
    await list.screenshot({ path: out(`${DIR}/list.png`) });
    await detail.locator('xpath=./div[1]').screenshot({ path: out(`${DIR}/detail-header.png`) });
    await panel.screenshot({ path: out(`${DIR}/ownership.png`) });

    // "Mine to review" for an account that owns no tests.
    await tabs.getByRole('tab', { name: 'Mine to review' }).click();
    await settle(page);
    await page.screenshot({ path: out(`${DIR}/mine-empty.png`) });
    await tabs.getByRole('tab', { name: 'Needs review' }).click();
    await settle(page);

    // 4. A short walkthrough video.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/tests/review');
        await expect(p.getByRole('heading', { name: 'Test review', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        const d = m.getByRole('article', { name: 'Selected test' });
        await caption(p, 'Test review lists the tests that a person should look at again.', 2000);
        await caption(p, 'The line at the top counts tests that are overdue, never reviewed, or without an owner.');
        await pointAt(p, m.getByText(/overdue · \d+ never reviewed/), 2600);
        await caption(p, 'Needs review shows only the tests that are due. All tests shows every test.');
        await pointAt(p, m.getByRole('tab', { name: 'Needs review' }), 1300);
        await pointAt(p, m.getByRole('tab', { name: 'All tests' }), 1300);
        const rows = m.locator('section').filter({ has: p.getByRole('button', { pressed: true }) }).first().getByRole('button');
        await caption(p, 'Each row shows the suite, the owner, and when the test was last reviewed.');
        await pointAt(p, rows.nth(1), 900);
        await rows.nth(1).click();
        await beat(p, 1400);
        await caption(p, 'On the right: who owns the test, and a backup in case they are away.');
        await pointAt(p, d.locator('select').first(), 1400);
        await pointAt(p, d.locator('select').nth(1), 1400);
        await caption(p, 'Picking a person saves straight away. Editors can change it.');
        await beat(p, 1200);
        await caption(p, 'After checking that the test is still worth having, press Mark reviewed.');
        await pointAt(p, d.getByRole('button', { name: 'Mark reviewed' }), 2400);
        await caption(p, 'The review then comes round again after the suite’s interval, 90 days unless changed.', {
            say: 'The review then comes round again after the suite’s interval: ninety days, unless it is changed.',
        });
        await pointAt(p, d.getByText(/every \d+ days/).first(), 2200);
        await caption(p, 'Mine to review shows only the tests you own.');
        await pointAt(p, m.getByRole('tab', { name: 'Mine to review' }), 1000);
        await m.getByRole('tab', { name: 'Mine to review' }).click();
        await settle(p);
        await beat(p, 1600);
        await caption(p, '');
    });
});

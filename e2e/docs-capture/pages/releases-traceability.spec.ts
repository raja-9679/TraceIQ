import { expect, test } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/releases/traceability.html
// Nothing here changes data: the video types a reference next to an untraced
// test and clears it again; the Link button is never pressed.

const DIR = 'releases/traceability';

test('Releases › Traceability', async ({ page, browser }) => {
    await page.goto('/traceability');
    await expect(page.getByRole('heading', { name: 'Traceability', level: 1 })).toBeVisible();
    const main = page.locator('main');
    const detail = main.getByRole('article', { name: 'Selected requirement' });
    await expect(detail).toBeVisible();
    await settle(page);

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    const share = main.getByRole('progressbar', { name: 'Share of tests linked' }).locator('xpath=..');
    const list = main.locator('section').filter({ has: page.getByRole('button', { pressed: true }) }).first();
    const untraced = main.getByRole('heading', { name: /Untraced tests/, level: 2 }).locator('xpath=ancestor::section[1]');
    await page.setViewportSize({ width: 1440, height: 1060 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: share },
        { n: 2, target: list, side: 'left' },
        { n: 3, target: detail },
        { n: 4, target: untraced },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await share.screenshot({ path: out(`${DIR}/share.png`) });
    await list.screenshot({ path: out(`${DIR}/list.png`) });
    await detail.screenshot({ path: out(`${DIR}/detail.png`) });
    await untraced.screenshot({ path: out(`${DIR}/untraced.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/traceability');
        await expect(p.getByRole('heading', { name: 'Traceability', level: 1 })).toBeVisible();
        const m = p.locator('main');
        const d = m.getByRole('article', { name: 'Selected requirement' });
        await expect(d).toBeVisible();
        await settle(p);
        await caption(p, 'Traceability links tests to the requirements or tickets they check.');
        await pointAt(p, m.getByRole('heading', { name: 'Traceability', level: 1 }), 2000);
        await caption(p, 'The bar shows how many of the project’s tests are linked to a requirement.');
        await pointAt(p, m.getByRole('progressbar'), 2400);
        await caption(p, 'Each requirement is listed, grouped by how its tests are doing.');
        const rows = m.locator('section').filter({ has: p.getByRole('button', { pressed: true }) }).first().getByRole('button');
        await pointAt(p, rows.first(), 2000);
        if (await rows.count() > 1) {
            await caption(p, 'Click one to see its tests on the right.');
            await pointAt(p, rows.nth(1), 800);
            await rows.nth(1).click();
            await settle(p);
            await beat(p, 800);
        }
        await caption(p, 'The counts say how many linked tests pass, fail, or have not run yet.');
        await pointAt(p, d.getByText('Passing', { exact: true }), 2200);
        await caption(p, 'Covered by lists those tests by name.');
        await pointAt(p, d.getByText('Covered by', { exact: true }), 2000);
        const un = m.getByRole('heading', { name: /Untraced tests/, level: 2 });
        await un.scrollIntoViewIfNeeded();
        await caption(p, 'Untraced tests are not linked to anything yet.');
        await pointAt(p, un, 2000);
        const field = m.getByPlaceholder('REQ / ticket ref').first();
        await caption(p, 'Type a ticket reference next to a test and press Link to connect them.');
        await pointAt(p, field, 600);
        await field.click();
        await field.pressSequentially('DEMO-103', { delay: 140 });
        await pointAt(p, m.getByRole('button', { name: 'Link' }).first(), 1800);
        await field.fill('');
        await caption(p, '');
    });
});

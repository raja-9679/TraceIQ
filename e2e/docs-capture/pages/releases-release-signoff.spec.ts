import { expect, test } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/releases/release-signoff.html
// Nothing here changes data: the Approve/Reject button is never pressed. The
// video types a release name and picks a verdict only to show the form, then
// reloads the page, which forgets them (nothing on this form saves by itself).

const DIR = 'releases/release-signoff';

test('Releases › Release sign-off', async ({ page, browser }) => {
    await page.goto('/release-signoff');
    await expect(page.getByRole('heading', { name: 'Release sign-off', level: 1 })).toBeVisible();
    const main = page.locator('main');
    const runs = main.locator('section[aria-labelledby="signoff-runs"]');
    const form = main.locator('section[aria-labelledby="signoff-form"]');
    const history = main.locator('section[aria-labelledby="signoff-history"]');
    await expect(history).toBeVisible();
    await settle(page);

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered (the guide explains each number).
    await page.setViewportSize({ width: 1440, height: 1100 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: runs },
        { n: 2, target: form },
        { n: 3, target: history },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups used next to each explanation.
    await runs.screenshot({ path: out(`${DIR}/runs.png`) });
    await form.screenshot({ path: out(`${DIR}/form.png`) });
    await history.screenshot({ path: out(`${DIR}/history.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/release-signoff');
        await expect(p.getByRole('heading', { name: 'Release sign-off', level: 1 })).toBeVisible();
        const m = p.locator('main');
        const r = m.locator('section[aria-labelledby="signoff-runs"]');
        const f = m.locator('section[aria-labelledby="signoff-form"]');
        const h = m.locator('section[aria-labelledby="signoff-history"]');
        await settle(p);
        await caption(p, 'Release sign-off records a person approving or rejecting a release.');
        await pointAt(p, m.getByRole('heading', { name: 'Release sign-off', level: 1 }), 2200);
        await caption(p, 'The approval rests on manual runs: tests that a person carried out by hand.');
        await pointAt(p, r.getByRole('heading').first(), 2000);
        await caption(p, 'Tick the runs this release was tested with. Each shows its counts and whether it passed.');
        await pointAt(p, r.locator('ul').last(), 2400);
        await caption(p, 'Then name the release, for example its version number.');
        const label = f.getByLabel('Release');
        await pointAt(p, label, 600);
        await label.click();
        await label.pressSequentially('2.4.0', { delay: 140 });
        await beat(p, 900);
        await caption(p, 'Choose Approve or Reject, and add a note about anything you accepted.');
        await pointAt(p, f.getByRole('radio', { name: 'Approve' }), 900);
        await f.getByRole('radio', { name: 'Reject' }).click();
        await beat(p, 900);
        await pointAt(p, f.getByLabel('Note'), 1400);
        await caption(p, 'The button stays greyed out until the form is complete. The message says what is missing.');
        await pointAt(p, f.getByRole('status'), 2600);
        await h.scrollIntoViewIfNeeded();
        await caption(p, 'Every decision is kept in the sign-off history, newest first.');
        await pointAt(p, h.getByRole('heading').first(), 2400);
        await caption(p, '');
        // Forget the typed name and verdict.
        await p.reload();
        await settle(p);
    });
});

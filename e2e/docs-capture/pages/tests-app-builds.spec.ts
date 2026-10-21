import { expect, test } from '@playwright/test';
import { annotate, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/tests/app-builds.html
// The demo has no app builds, so this shows the page's empty state with its
// upload form. Nothing is chosen or uploaded.

const DIR = 'tests/app-builds';

test('Tests › App builds', async ({ page, browser }) => {
    await page.goto('/app-builds');
    await expect(page.getByRole('heading', { name: 'App builds', level: 1 })).toBeVisible();
    await settle(page);
    const main = page.locator('main');
    const form = main.getByRole('article', { name: 'Upload a build' });
    await expect(form).toBeVisible();

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    const button = main.getByRole('button', { name: 'Upload build' }).first();
    const empty = main.getByText('No builds yet').locator('xpath=ancestor::div[contains(@class,"rounded")][1]');
    const fields = form.locator('div.grid').first();
    const submit = form.getByRole('button', { name: 'Upload build' });
    const clean = await annotate(page, [
        { n: 1, target: button, side: 'right' },
        { n: 2, target: empty },
        { n: 3, target: fields },
        { n: 4, target: submit },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-up of the form.
    await form.screenshot({ path: out(`${DIR}/upload-form.png`) });

    // 4. A short walkthrough video.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/app-builds');
        await expect(p.getByRole('heading', { name: 'App builds', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        const f = m.getByRole('article', { name: 'Upload a build' });
        await caption(p, 'App builds keeps the files of your mobile app, for tests that run on a phone.', 2000);
        await caption(p, 'This project has none yet, so the page starts with the upload form.');
        await pointAt(p, m.getByText('No builds yet'), 2000);
        await caption(p, 'First choose Android or iOS. Android takes an APK or AAB file, iOS an IPA file.', {
            say: 'First choose Android or iOS. Android takes an A P K or A A B file. iOS takes an I P A file.',
        });
        await pointAt(p, f.getByLabel('Platform'), 1600);
        await pointAt(p, f.getByLabel('Binary'), 1600);
        await caption(p, 'The name, version and package id are optional. They help you tell builds apart.');
        await pointAt(p, f.getByLabel(/App name/), 1000);
        await pointAt(p, f.getByLabel(/Version/), 1000);
        await pointAt(p, f.getByLabel(/Package or bundle id/), 1400);
        await caption(p, 'Upload build stores the file. Each build then appears in a list, grouped by platform.');
        await pointAt(p, f.getByRole('button', { name: 'Upload build' }), 2400);
        await caption(p, 'When you run a suite, you pick the build to install on the device.', 2200);
        await caption(p, '');
    });
});

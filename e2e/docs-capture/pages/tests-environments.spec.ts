import { expect, test } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/tests/environments.html
// Nothing here changes data: rows are only selected, the "New environment"
// form is opened and closed with Cancel, and nothing is typed into the
// secret or HAR forms. "Make exclusive", "Set as default", Edit and Delete
// are never pressed.

const DIR = 'tests/environments';

test('Tests › Environments', async ({ page, browser }) => {
    await page.goto('/environments');
    await expect(page.getByRole('heading', { name: 'Environments and secrets', level: 1 })).toBeVisible();
    await settle(page);
    const main = page.locator('main');
    const detail = main.getByRole('article', { name: 'Selected environment' });
    await expect(detail.getByText('Base URL', { exact: true })).toBeVisible();

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    const envList = main.locator('section').filter({ has: page.getByRole('button', { pressed: true }) }).first().locator('xpath=./div[1]');
    const creds = main.getByRole('button', { name: /Secrets/ }).locator('xpath=ancestor::div[contains(@class,"rounded-3xl")][1]');
    const newBtn = main.getByRole('button', { name: 'New environment' });
    const exclusive = detail.getByRole('region', { name: 'Exclusive use' });
    const buttons = detail.getByRole('button', { name: 'Edit' }).locator('xpath=..');
    await page.setViewportSize({ width: 1440, height: 1100 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: newBtn, side: 'right' },
        { n: 2, target: envList, side: 'left' },
        { n: 3, target: creds, side: 'left' },
        { n: 4, target: detail.getByText('Variables', { exact: true }).locator('xpath=ancestor::div[contains(@class,"flex-col")][1]'), side: 'right' },
        { n: 5, target: exclusive, side: 'right' },
        { n: 6, target: buttons },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await envList.screenshot({ path: out(`${DIR}/list.png`) });
    await detail.screenshot({ path: out(`${DIR}/values.png`) });
    await exclusive.screenshot({ path: out(`${DIR}/exclusive.png`) });
    await creds.screenshot({ path: out(`${DIR}/credentials.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });

    // Secrets (read-only look: key names only).
    await main.getByRole('button', { name: /Secrets/ }).click();
    const secrets = main.getByRole('article', { name: 'Secrets' });
    await expect(secrets.getByText('Stored secrets')).toBeVisible();
    await settle(page);
    await secrets.screenshot({ path: out(`${DIR}/secrets.png`) });

    // Recorded traffic (HAR).
    await main.getByRole('button', { name: /Recorded traffic/ }).click();
    const hars = main.getByRole('article', { name: 'Recorded traffic' });
    await expect(hars.getByText('How replay works')).toBeVisible();
    await settle(page);
    await hars.screenshot({ path: out(`${DIR}/har.png`) });

    // The "New environment" form, closed with Cancel.
    await newBtn.click();
    const form = main.getByRole('article', { name: 'New environment' });
    await expect(form.getByLabel('Name')).toBeVisible();
    await form.screenshot({ path: out(`${DIR}/new.png`) });
    await form.getByRole('button', { name: 'Cancel' }).click();
    await expect(form).toBeHidden();

    // 4. A short walkthrough video.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/environments');
        await expect(p.getByRole('heading', { name: 'Environments and secrets', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        const d = m.getByRole('article', { name: 'Selected environment' });
        await caption(p, 'An environment is a place your tests can run against, such as a test site or the live site.', 2000);
        const rows = m.locator('section').filter({ has: p.getByRole('button', { pressed: true }) }).first().getByRole('button');
        await caption(p, 'This project has two. The default one is used when a run doesn’t name another.');
        await pointAt(p, rows.first(), 1600);
        await pointAt(p, rows.nth(1), 1400);
        await caption(p, 'Each has a base address, and variables that steps use as {{env.NAME}}.', {
            say: 'Each one has a base address, and variables that test steps can use by name.',
        });
        await pointAt(p, d.getByText('Base URL', { exact: true }), 1400);
        await pointAt(p, d.getByText('{{env.BASE_URL}}'), 1800);
        await caption(p, 'Click another environment to see its values.');
        await rows.nth(1).click();
        await beat(p, 1600);
        await caption(p, 'Exclusive use lets only one run at a time use the environment. Others wait their turn.');
        await pointAt(p, d.getByRole('region', { name: 'Exclusive use' }), 2600);
        await caption(p, 'Secrets hold passwords and tokens. They are shared by every environment in the project.');
        await pointAt(p, m.getByRole('button', { name: /Secrets/ }), 900);
        await m.getByRole('button', { name: /Secrets/ }).click();
        await beat(p, 1400);
        const s = m.getByRole('article', { name: 'Secrets' });
        await caption(p, 'Only their names are shown. Once saved, a secret value can be replaced, but never read back.');
        await pointAt(p, s.getByText('ALICE_PASSWORD', { exact: true }), 2600);
        await caption(p, 'New environment adds another, for example a staging copy of your site.');
        await pointAt(p, m.getByRole('button', { name: 'New environment' }), 2200);
        await caption(p, '');
    });
});

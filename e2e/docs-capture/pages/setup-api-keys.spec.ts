import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/setup/api-keys.html
// NEVER creates or revokes a key. The New API key form is opened and
// cancelled; nothing is typed into it except in the picture of the form,
// where a name is typed and then the form is cancelled. The demo workspace
// has two dead keys (one expired, one revoked); selecting one only changes
// what the page shows.

const DIR = 'setup/api-keys';

async function openDemo(page: Page) {
    await page.goto('/api-keys');
    await expect(page.getByRole('heading', { name: 'API keys', level: 1 })).toBeVisible();
    await settle(page);
    await page.getByRole('combobox', { name: 'Workspace' }).click();
    await page.getByRole('option', { name: 'Demo workspace' }).click();
    await settle(page);
    await expect(page.getByRole('combobox', { name: 'Workspace' })).toContainText('Demo workspace');
}

test('Setup › API keys', async ({ page, browser }) => {
    await openDemo(page);
    const main = page.locator('main');

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    const picker = page.getByRole('combobox', { name: 'Workspace' });
    const newKey = main.getByRole('button', { name: 'New API key' });
    const list = main.locator('section').filter({ has: page.getByRole('heading', { name: 'Expired' }) });
    const detail = main.getByRole('article', { name: 'Selected API key' });
    await expect(list.getByRole('heading', { name: 'Revoked' })).toBeVisible();
    await expect(detail).toBeVisible();
    const clean = await annotate(page, [
        { n: 1, target: picker },
        { n: 2, target: newKey, side: 'right' },
        { n: 3, target: list },
        { n: 4, target: detail, side: 'right' },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await list.screenshot({ path: out(`${DIR}/list.png`) });
    await list.getByRole('button', { name: /GitHub Actions/ }).click();
    await expect(detail.getByText('Revoked', { exact: true }).first()).toBeVisible();
    await detail.screenshot({ path: out(`${DIR}/detail-revoked.png`) });
    await newKey.click();
    await expect(page.getByLabel('Name', { exact: true })).toBeVisible();
    await page.getByLabel('Name', { exact: true }).fill('ci-regression-bot');
    await beat(page, 300);
    const formCard = main.locator('section').filter({ has: page.getByRole('heading', { name: 'New API key' }) });
    await formCard.screenshot({ path: out(`${DIR}/new-key-form.png`) });
    await page.getByRole('combobox', { name: 'Project scope' }).click();
    await beat(page, 300);
    await page.getByRole('listbox').screenshot({ path: out(`${DIR}/scope-menu.png`) });
    await page.keyboard.press('Escape');
    await main.getByRole('button', { name: 'Cancel' }).click();
    await expect(page.getByLabel('Name', { exact: true })).toBeHidden();

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await openDemo(p);
        const m = p.locator('main');
        await caption(p, 'API keys let other tools, such as a CI pipeline or an AI agent, use TraceIQ without a person signing in.', {
            pause: 1200,
            say: 'A P I keys let other tools, such as a C I pipeline or an A I agent, use TraceIQ without a person signing in.',
        });
        await caption(p, 'Keys belong to a workspace. Pick the workspace here.');
        await pointAt(p, p.getByRole('combobox', { name: 'Workspace' }), 2000);
        await caption(p, 'Keys are listed as active, expired or revoked. The demo has one expired and one revoked key.');
        await pointAt(p, m.getByRole('heading', { name: 'Expired' }), 2200);
        await caption(p, 'New API key opens a short form.');
        const nk = m.getByRole('button', { name: 'New API key' });
        await pointAt(p, nk, 900);
        await nk.click();
        await expect(p.getByLabel('Name', { exact: true })).toBeVisible();
        await caption(p, 'Give it a name that says who uses it, and if you can, limit it to one project.');
        await pointAt(p, p.getByLabel('Name', { exact: true }), 1400);
        await pointAt(p, p.getByRole('combobox', { name: 'Project scope' }), 1600);
        await caption(p, 'You can let it expire after a number of days, and accept it only from certain addresses.');
        await pointAt(p, p.getByLabel('Expires in (days)'), 1400);
        await pointAt(p, p.getByLabel('Allowed source IPs (optional)'), 1600);
        await caption(p, 'The role limit caps what the key may do. It never gets more than its creator\u2019s own access.');
        await pointAt(p, p.getByRole('combobox', { name: 'Role limit' }), 2200);
        await caption(p, 'After Create key, the full key is shown once. Copy it then: it can never be shown again.');
        await pointAt(p, m.getByRole('button', { name: 'Create key' }), 2600);
        await caption(p, '');
        await m.getByRole('button', { name: 'Cancel' }).click();
        await beat(p, 600);
        await caption(p, 'Click a key to see when it was last used. An active key also has a Revoke button.');
        await pointAt(p, m.getByRole('article', { name: 'Selected API key' }), 2400);
        await caption(p, '');
    });
});

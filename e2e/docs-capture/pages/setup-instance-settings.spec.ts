import { expect, test } from '@playwright/test';
import { annotate, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/setup/instance-settings.html
// Needs an instance admin (granted for the length of the lead's capture run).
// Never types into a field or presses Save / Reset / a test button.

const DIR = 'setup/instance-settings';

test('Setup › Instance settings', async ({ page, browser }) => {
    await page.goto('/instance-settings');
    await expect(page.getByRole('heading', { name: 'Instance settings', level: 1 })).toBeVisible();
    const main = page.locator('main');
    const email = main.getByRole('region', { name: 'Email (SMTP)' });
    await expect(email).toBeVisible();
    await settle(page);
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    const intro = main.getByRole('heading', { name: 'Instance configuration', level: 2 }).locator('xpath=ancestor::section[1]');
    const row = email.locator('.divide-y > *').first();
    const footer = email.getByRole('button', { name: 'Save instance settings' }).locator('xpath=..');
    const emailTest = email.getByRole('heading', { name: 'Check the mail settings' }).locator('xpath=..');
    await page.setViewportSize({ width: 1440, height: 1700 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: intro },
        { n: 2, target: email.getByRole('heading', { level: 2 }) },
        { n: 3, target: row },
        { n: 4, target: emailTest },
        { n: 5, target: footer },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();
    await page.setViewportSize({ width: 1440, height: 900 });

    await intro.screenshot({ path: out(`${DIR}/intro.png`) });
    await row.screenshot({ path: out(`${DIR}/setting-row.png`) });
    await emailTest.screenshot({ path: out(`${DIR}/email-test.png`) });
    await footer.screenshot({ path: out(`${DIR}/save.png`) });
    await email.locator('.divide-y > *').filter({ hasText: 'SMTP password' }).screenshot({ path: out(`${DIR}/secret-row.png`) });
    const providers = main.getByRole('region', { name: 'AI providers' });
    await providers.scrollIntoViewIfNeeded();
    await providers.screenshot({ path: out(`${DIR}/ai-providers.png`) });
    const isolation = main.getByRole('region', { name: 'Tenant isolation (row-level security)' });
    await isolation.scrollIntoViewIfNeeded();
    await isolation.screenshot({ path: out(`${DIR}/tenant-isolation.png`) });

    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/instance-settings');
        const m = p.locator('main');
        await expect(m.getByRole('region', { name: 'Email (SMTP)' })).toBeVisible();
        await settle(p);
        await caption(p, 'Instance settings change how the whole TraceIQ server behaves, for every workspace.', 2400);
        await caption(p, 'A value saved here wins over the server’s own configuration file.', {
            say: 'A value saved here wins over the server’s own configuration.',
        });
        await pointAt(p, m.getByText('override the environment'), 2400);
        const em = m.getByRole('region', { name: 'Email (SMTP)' });
        await caption(p, 'Settings come in groups. Email is the server that sends notifications and invites.');
        await pointAt(p, em.getByRole('heading', { level: 2 }), 2200);
        await caption(p, 'Passwords and keys are never shown. A dotted box means one is set; type to replace it.');
        await pointAt(p, em.getByPlaceholder(/not set|set — type to replace/).first(), 2600);
        await caption(p, 'Every group has the same Save button. It saves all your changes, in every group, at once.');
        await pointAt(p, em.getByRole('button', { name: 'Save instance settings' }), 2600);
        const ai = m.getByRole('region', { name: 'AI providers' });
        await ai.scrollIntoViewIfNeeded();
        await caption(p, 'AI providers lists the AI services TraceIQ may use to explain failures.');
        await pointAt(p, ai.getByRole('heading', { level: 2 }), 2400);
        const iso = m.getByRole('region', { name: 'Tenant isolation (row-level security)' });
        await iso.scrollIntoViewIfNeeded();
        await caption(p, 'Further down: sign-in, security alerts, workers, network rules and prices.');
        await pointAt(p, iso.getByRole('heading', { level: 2 }), 2400);
        await caption(p, '');
    });
});

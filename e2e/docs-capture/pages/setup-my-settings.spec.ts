import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/setup/my-settings.html
// Nothing here changes data: sections are switched, menus are never opened,
// no Save button is pressed and no key or authenticator is set up.

const DIR = 'setup/my-settings';

const card = (page: Page, id: string) => page.locator(`section[aria-labelledby="${id}"]`);

async function open(page: Page) {
    await page.goto('/settings');
    await expect(page.getByRole('heading', { name: 'My settings', level: 1 })).toBeVisible();
    await settle(page);
}

async function section(page: Page, name: string) {
    await page.getByRole('navigation', { name: 'Settings sections' }).getByRole('button', { name, exact: true }).click();
    await settle(page);
}

test('Setup › My settings', async ({ page, browser }) => {
    await open(page);

    // 1. The whole page as a reader first meets it (General section).
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. The section list, numbered (the guide explains each number).
    const nav = page.getByRole('navigation', { name: 'Settings sections' });
    const names = ['General', 'Runs and recordings', 'Notifications', 'Account and API keys', 'Two-factor sign-in'];
    const clean = await annotate(page, names.map((name, i) => ({
        n: i + 1, target: nav.getByRole('button', { name, exact: true }), side: 'right' as const,
    })));
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups, one section at a time.
    await card(page, 'settings-features').screenshot({ path: out(`${DIR}/features.png`) });

    await page.setViewportSize({ width: 1440, height: 2200 });
    await section(page, 'Runs and recordings');
    await card(page, 'settings-exec-defaults').screenshot({ path: out(`${DIR}/run-defaults.png`) });
    await card(page, 'settings-exec-browsers').screenshot({ path: out(`${DIR}/browsers.png`) });
    await card(page, 'settings-capture-pointer').screenshot({ path: out(`${DIR}/capture-pointer.png`) });
    await card(page, 'settings-artifacts').screenshot({ path: out(`${DIR}/recordings.png`) });

    await section(page, 'Notifications');
    await card(page, 'settings-notifications').screenshot({ path: out(`${DIR}/notifications.png`) });
    await expect(card(page, 'settings-project-choices').getByText('Demo — Todo web app')).toBeVisible();
    await card(page, 'settings-project-choices').screenshot({ path: out(`${DIR}/per-project.png`) });

    await section(page, 'Account and API keys');
    await card(page, 'settings-profile').screenshot({ path: out(`${DIR}/profile.png`) });
    await card(page, 'settings-api-keys').screenshot({ path: out(`${DIR}/api-keys.png`) });

    await section(page, 'Two-factor sign-in');
    await expect(card(page, 'settings-mfa-enable').getByText('Status:')).toBeVisible();
    await page.locator('main').screenshot({ path: out(`${DIR}/two-factor.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await open(p);
        const n = p.getByRole('navigation', { name: 'Settings sections' });
        const go = async (name: string) => {
            await n.getByRole('button', { name, exact: true }).click();
            await settle(p);
            await beat(p, 500);
        };
        await caption(p, 'My settings holds your own preferences. They never change anyone else’s.', 2200);
        await caption(p, 'The list on the left switches between five sections.');
        await pointAt(p, n, 2200);
        await caption(p, 'AI test generation shows a Generate button on suite pages, in this browser only.');
        await pointAt(p, card(p, 'settings-features').getByText('AI test generation', { exact: true }), 2200);

        await caption(p, '');
        await go('Runs and recordings');
        await caption(p, 'Most cards have their own Save button, which saves only that card.');
        await pointAt(p, card(p, 'settings-exec-defaults').getByRole('button', { name: 'Save' }), 2200);
        await caption(p, 'Run defaults choose the browser and device when the test and its suite don’t.');
        await pointAt(p, card(p, 'settings-exec-defaults').getByText('Default browser', { exact: true }), 2400);
        const rec = card(p, 'settings-artifacts');
        await caption(p, 'Recordings: which of your runs keep a video, a screenshot and a trace.');
        await rec.scrollIntoViewIfNeeded();
        await pointAt(p, rec.getByText('Video recording', { exact: true }), 2000);
        await caption(p, 'The project’s capture level, just above, is the limit. Your choice can only record less.', {
            say: 'The project’s capture level, just above, is the limit. Your choice can only record less.',
        });
        await pointAt(p, card(p, 'settings-capture-pointer'), 2200);

        await caption(p, '');
        await go('Notifications');
        await caption(p, 'Email notifications: your default for emails about test runs.');
        await pointAt(p, card(p, 'settings-notifications').getByText('Send me email about test runs'), 2200);
        await caption(p, 'Per project overrides that default for one project. A change there saves at once.');
        await pointAt(p, card(p, 'settings-project-choices').getByText('Demo — Todo web app'), 2400);

        await caption(p, '');
        await go('Account and API keys');
        await caption(p, 'Account shows your name and email, and the API keys of your workspace.', {
            say: 'Account shows your name and email, and the A P I keys of your workspace.',
        });
        await pointAt(p, card(p, 'settings-profile'), 2400);

        await caption(p, '');
        await go('Two-factor sign-in');
        await caption(p, 'Two-factor sign-in asks for a code from an authenticator app as well as your password.');
        await caption(p, 'The status line says whether it is on for your account.');
        await pointAt(p, card(p, 'settings-mfa-enable').getByText('Status:'), 2200);
        await pointAt(p, p.getByRole('button', { name: 'Set up authenticator' }), 2600);
        await caption(p, '');
    });
});

import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/setup/project-settings.html
// Nothing here changes data: sections are switched, no checkbox, radio button,
// menu or Save button is touched (several controls here save the moment they
// change), no file is chosen.

const DIR = 'setup/project-settings';

const card = (page: Page, id: string) => page.locator(`section[aria-labelledby="${id}"]`);
const NAMES = ['Notifications', 'Data capture and retention', 'Backend traces', 'OpenAPI spec', 'Members and other policies'];

async function open(page: Page) {
    await page.goto('/project-settings');
    await expect(page.getByRole('heading', { name: 'Project settings', level: 1 })).toBeVisible();
    await expect(page.getByText('Demo — Todo web app').first()).toBeVisible();
    await settle(page);
}

async function section(page: Page, name: string) {
    await page.getByRole('navigation', { name: 'Project settings sections' }).getByRole('button', { name, exact: true }).click();
    await settle(page);
}

test('Setup › Project settings', async ({ page, browser }) => {
    await open(page);
    await expect(card(page, 'pn-email')).toBeVisible();

    // 1. The whole page as a reader first meets it (Notifications section).
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. The section list, numbered.
    const nav = page.getByRole('navigation', { name: 'Project settings sections' });
    const clean = await annotate(page, NAMES.map((name, i) => ({
        n: i + 1, target: nav.getByRole('button', { name, exact: true }), side: 'right' as const,
    })));
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await page.setViewportSize({ width: 1440, height: 2400 });
    await settle(page);
    await card(page, 'pn-mine').screenshot({ path: out(`${DIR}/your-choice.png`) });
    await card(page, 'pn-email').screenshot({ path: out(`${DIR}/email.png`) });
    await card(page, 'pn-slack').screenshot({ path: out(`${DIR}/slack.png`) });

    await section(page, 'Data capture and retention');
    const policy = page.locator('main section').filter({ hasText: 'Data capture and redaction' }).first();
    await expect(policy.getByText('Capture level', { exact: true })).toBeVisible();
    await policy.locator('fieldset').first().screenshot({ path: out(`${DIR}/capture-level.png`) });
    await policy.getByText('Recorded right now').locator('xpath=ancestor::div[1]').screenshot({ path: out(`${DIR}/recorded-now.png`) });
    await policy.locator('fieldset').nth(1).screenshot({ path: out(`${DIR}/patterns.png`) });
    await policy.getByText('Extra headers', { exact: true }).locator('xpath=ancestor::div[contains(@class,"grid")][1]').screenshot({ path: out(`${DIR}/lists.png`) });
    await policy.getByText('Store request and response bodies').locator('xpath=ancestor::div[contains(@class,"border-t")][1]').screenshot({ path: out(`${DIR}/retention.png`) });

    await section(page, 'Backend traces');
    await card(page, 'ps-traces').screenshot({ path: out(`${DIR}/traces.png`) });

    await section(page, 'Members and other policies');
    await page.locator('main').getByText('Members and access', { exact: true })
        .locator('xpath=ancestor::div[contains(@class,"flex-col")][2]').screenshot({ path: out(`${DIR}/more.png`) });

    // The demo's OpenAPI spec (two versions) belongs to "Demo — Public API":
    // switch the sidebar project (a browser-side preference, not data).
    await page.getByRole('button', { name: /^Project: / }).first().click();
    await page.getByRole('menuitem', { name: /Demo — Public API/ }).click();
    await settle(page);
    await section(page, 'OpenAPI spec');
    const spec = page.locator('section[aria-labelledby="openapi-spec-title"]');
    await expect(spec.getByText(/breaking/).first()).toBeVisible();
    await settle(page);
    await spec.screenshot({ path: out(`${DIR}/openapi.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await open(p);
        const n = p.getByRole('navigation', { name: 'Project settings sections' });
        const go = async (name: string) => {
            await n.getByRole('button', { name, exact: true }).click();
            await settle(p);
            await beat(p, 500);
        };
        await caption(p, 'Project settings belong to the project chosen in the sidebar, and are shared by everyone on it.', 1800);
        await pointAt(p, p.getByRole('heading', { name: 'Project settings', level: 1 }), 1000);
        await caption(p, 'Five sections. Anyone on the project can look; mostly only project admins can change.');
        await pointAt(p, n, 2200);
        await caption(p, 'Notifications: who is emailed about this project’s runs.');
        await pointAt(p, card(p, 'pn-email').getByText("Send email about this project's runs"), 2200);
        const slack = card(p, 'pn-slack');
        await caption(p, 'Slack and Teams post to this project’s own channel, through a webhook address.');
        await slack.scrollIntoViewIfNeeded();
        await pointAt(p, slack.getByText('Webhook', { exact: true }), 2200);

        await caption(p, '');
        await go('Data capture and retention');
        const pol = p.locator('main section').filter({ hasText: 'Data capture and redaction' }).first();
        await caption(p, 'The capture level decides how much a run may record: from pass or fail only, up to video and traces.');
        await pointAt(p, pol.getByText('Capture level', { exact: true }), 2600);
        await caption(p, 'This box shows what is actually recorded right now.');
        await pol.getByText('Recorded right now').scrollIntoViewIfNeeded();
        await pointAt(p, pol.getByText('Recorded right now'), 2200);
        await caption(p, 'Redaction removes card numbers, tokens and other secrets before anything is stored.');
        await pol.getByText('Redact these value patterns').scrollIntoViewIfNeeded();
        await pointAt(p, pol.getByText('Redact these value patterns'), 2400);
        await caption(p, 'Retention: after how many days old runs are deleted.');
        await pol.getByText('Retention (days)').scrollIntoViewIfNeeded();
        await pointAt(p, pol.getByText('Retention (days)'), 2200);

        await caption(p, '');
        await go('Backend traces');
        await caption(p, 'Backend traces link each test step to what your own servers recorded for it. Off by default.');
        await pointAt(p, card(p, 'ps-traces'), 2600);
        await caption(p, '');
        await go('OpenAPI spec');
        await caption(p, 'OpenAPI spec: a description of your API that API tests can check responses against.', {
            say: 'Open A P I spec: a description of your A P I that A P I tests can check responses against.',
        });
        await pointAt(p, p.getByText('No spec stored for this project.'), 2600);
        await caption(p, '');
        await go('Members and other policies');
        await caption(p, 'The last section links to the pages that hold the other project rules.');
        await pointAt(p, p.getByRole('link', { name: 'Open Workspace' }), 2400);
        await caption(p, '');
    });
});

import { expect, test, type Locator, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/setup/issue-trackers.html
// Nothing here changes data. The demo workspace has no tracker connections,
// so the pictures show the empty page and the "New tracker" form, filled in
// with example values and then cancelled. The token / password field is never
// typed into and "Add tracker" (the save button) is never pressed.

const DIR = 'setup/issue-trackers';

async function openDemoWorkspace(p: Page) {
    await p.goto('/issue-trackers');
    await expect(p.getByRole('heading', { name: 'Issue trackers', level: 1 })).toBeVisible();
    await settle(p);
    // The page starts on the account's first workspace; the guide shows the demo one.
    await p.getByRole('combobox', { name: 'Workspace' }).click();
    await p.getByRole('option', { name: 'Demo workspace' }).click();
    await settle(p);
    await expect(p.getByText('No trackers yet', { exact: true })).toBeVisible();
}

/** Screenshot of a Radix select's trigger together with its open list. */
async function shootOpenSelect(p: Page, trigger: Locator, file: string) {
    const tb = await trigger.boundingBox();
    await trigger.click();
    const listbox = p.getByRole('listbox');
    await expect(listbox).toBeVisible();
    await beat(p, 400); // let the menu's fade-in finish
    const lb = await listbox.boundingBox();
    if (tb && lb) {
        const x = Math.min(tb.x, lb.x) - 12, y = Math.min(tb.y, lb.y) - 12;
        const r = Math.max(tb.x + tb.width, lb.x + lb.width) + 12;
        const b = Math.max(tb.y + tb.height, lb.y + lb.height) + 12;
        await p.screenshot({ path: out(file), clip: { x, y, width: r - x, height: b - y } });
    }
    await p.keyboard.press('Escape');
    await expect(listbox).toBeHidden();
}

test('Setup › Issue trackers', async ({ page, browser }) => {
    await openDemoWorkspace(page);
    const main = page.locator('main');

    // 1. The page as the demo workspace shows it: no trackers yet.
    await page.screenshot({ path: out(`${DIR}/page.png`) });
    await main.getByRole('combobox', { name: 'Workspace' }).locator('xpath=..').screenshot({ path: out(`${DIR}/header-actions.png`) });

    // 2. The "New tracker" form with example values (never saved).
    await main.getByRole('button', { name: 'Add tracker' }).click();
    const form = main.getByRole('article', { name: 'New tracker' });
    await expect(form).toBeVisible();
    await form.getByLabel('Name', { exact: true }).fill('Team Jira');
    await form.getByLabel('Base URL').fill('https://example.atlassian.net');
    await form.getByLabel('Jira email').fill('alex@example.com');
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
    await settle(page);

    const provider = form.getByRole('combobox', { name: 'Provider' });
    const clean = await annotate(page, [
        { n: 1, target: main.getByRole('combobox', { name: 'Workspace' }) },
        { n: 2, target: provider.locator('xpath=ancestor::div[contains(@class,"grid")][1]'), side: 'right' },
        { n: 3, target: form.getByLabel('Base URL').locator('xpath=..'), side: 'right' },
        { n: 4, target: form.getByLabel('Jira email').locator('xpath=ancestor::div[contains(@class,"grid")][1]'), side: 'right' },
        { n: 5, target: form.locator('textarea').locator('xpath=..'), side: 'right' },
        { n: 6, target: form.getByRole('button', { name: 'Cancel', exact: true }).locator('xpath=..') },
    ]);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await form.screenshot({ path: out(`${DIR}/form.png`) });
    await shootOpenSelect(page, provider, `${DIR}/providers.png`);
    await page.setViewportSize({ width: 1440, height: 900 });
    await form.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(form).toBeHidden();

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/issue-trackers');
        await expect(p.getByRole('heading', { name: 'Issue trackers', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        await caption(p, 'Issue trackers connect TraceIQ to the tool your team files bugs in.', 2200);
        const pk = m.getByRole('combobox', { name: 'Workspace' });
        await caption(p, 'Connections belong to a workspace. Choose it here.');
        await pointAt(p, pk, 900);
        await pk.click();
        await beat(p, 900);
        await p.getByRole('option', { name: 'Demo workspace' }).click();
        await settle(p);
        await caption(p, 'This one has no trackers yet. Add tracker opens the form.');
        const add = m.getByRole('button', { name: 'Add tracker' });
        await pointAt(p, add, 1000);
        await add.click();
        await settle(p);
        const f = m.getByRole('article', { name: 'New tracker' });
        const pr = f.getByRole('combobox', { name: 'Provider' });
        await caption(p, 'Pick the tool: Jira, iTop, GitHub, or Linear. Linear is for importing only.');
        await pointAt(p, pr, 700);
        await pr.click();
        await beat(p, 2600);
        await p.keyboard.press('Escape');
        await caption(p, 'Give it a name, and the web address of your tracker.');
        await pointAt(p, f.getByLabel('Name', { exact: true }), 400);
        await f.getByLabel('Name', { exact: true }).pressSequentially('Team Jira', { delay: 70 });
        await pointAt(p, f.getByLabel('Base URL'), 400);
        await f.getByLabel('Base URL').pressSequentially('https://example.atlassian.net', { delay: 35 });
        await caption(p, 'Then the account TraceIQ signs in with, and its token. The token is stored encrypted.');
        await pointAt(p, f.getByLabel('Jira email'), 1200);
        await pointAt(p, f.getByLabel('API token'), 1800);
        await caption(p, 'Settings say where tickets go. For Jira: the project key and the issue type.');
        await pointAt(p, f.locator('textarea'), 2600);
        await caption(p, 'Add tracker saves it. We cancel here, so nothing is saved.');
        await pointAt(p, f.getByRole('button', { name: 'Add tracker' }), 1400);
        await pointAt(p, f.getByRole('button', { name: 'Cancel', exact: true }), 700);
        await f.getByRole('button', { name: 'Cancel', exact: true }).click();
        await beat(p, 700);
        await caption(p, 'Once saved, Create ticket on a run and on Failure triage offers it.');
        await pointAt(p, m.getByText('No trackers yet', { exact: true }), 2400);
        await caption(p, '');
    });
});

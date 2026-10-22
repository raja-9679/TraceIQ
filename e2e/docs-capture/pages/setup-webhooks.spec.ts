import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/setup/webhooks.html
// Nothing here changes data. The demo workspace has one disabled webhook with
// a seeded delivery log (a demo never sends any). The pictures show it, and the
// "New webhook" form, filled in with example values and then cancelled — "Create webhook" is never
// pressed, so no webhook and no signing secret is ever made.

const DIR = 'setup/webhooks';

async function openDemoWorkspace(p: Page) {
    await p.goto('/webhooks');
    await expect(p.getByRole('heading', { name: 'Webhooks', level: 1 })).toBeVisible();
    await settle(p);
    // The page starts on the account's first workspace; the guide shows the demo one.
    const picker = p.getByRole('combobox', { name: 'Workspace' });
    await picker.click();
    await p.getByRole('option', { name: 'Demo workspace' }).click();
    await settle(p);
    await expect(p.locator('main').getByRole('article', { name: 'Selected webhook' })).toBeVisible();
}

test('Setup › Webhooks', async ({ page, browser }) => {
    await openDemoWorkspace(page);
    const main = page.locator('main');

    // 1. The page as the demo workspace shows it: one disabled webhook.
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
    await page.screenshot({ path: out(`${DIR}/page.png`) });
    const detail = main.getByRole('article', { name: 'Selected webhook' });
    await expect(detail.getByText('Recent deliveries')).toBeVisible();
    await settle(page);
    await page.setViewportSize({ width: 1440, height: 1600 });
    await settle(page);
    await detail.screenshot({ path: out(`${DIR}/detail.png`) });
    await main.getByRole('heading', { name: 'Disabled' }).locator('xpath=ancestor::section[1]').screenshot({ path: out(`${DIR}/list.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.evaluate(() => window.scrollTo(0, 0));

    // 2. The "New webhook" form, filled with example values (never submitted).
    await main.getByRole('button', { name: 'New webhook' }).click();
    const form = main.locator('section').filter({ has: page.getByRole('heading', { name: 'New webhook' }) });
    await expect(form).toBeVisible();
    await form.getByLabel('Name', { exact: true }).fill('ci-relay');
    await form.getByLabel('Payload URL').fill('https://example.com/hooks/traceiq');
    await form.getByRole('button', { name: 'run.failed' }).click();
    await form.getByRole('button', { name: 'run.timed_out' }).click();
    await settle(page);

    const picker = main.getByRole('combobox', { name: 'Workspace' });
    const newBtn = main.getByRole('button', { name: 'New webhook' });
    const scope = form.getByText('Project scope', { exact: true }).locator('xpath=..');
    const events = form.locator('fieldset');
    const buttons = form.getByRole('button', { name: 'Cancel', exact: true }).locator('xpath=..');
    const clean = await annotate(page, [
        { n: 1, target: picker },
        { n: 2, target: newBtn, side: 'right' },
        { n: 3, target: form.getByLabel('Name', { exact: true }).locator('xpath=..'), side: 'right' },
        { n: 4, target: form.getByLabel('Payload URL').locator('xpath=..'), side: 'right' },
        { n: 5, target: scope, side: 'right' },
        { n: 6, target: events, side: 'right' },
        { n: 7, target: buttons },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await events.screenshot({ path: out(`${DIR}/events.png`) });
    // The project-scope menu, opened and closed again. (Radix hides the rest
    // of the page from the accessibility tree while it is open, so measure the
    // trigger first.)
    const scopeBox = await form.getByRole('combobox', { name: 'Project scope' }).boundingBox();
    await form.getByRole('combobox', { name: 'Project scope' }).click();
    const listbox = page.getByRole('listbox');
    await expect(listbox).toBeVisible();
    await beat(page, 400); // let the menu's fade-in finish
    const lb = await listbox.boundingBox();
    if (scopeBox && lb) {
        const x = Math.min(scopeBox.x, lb.x) - 12, y = Math.min(scopeBox.y, lb.y) - 12;
        const r = Math.max(scopeBox.x + scopeBox.width, lb.x + lb.width) + 12;
        const b = Math.max(scopeBox.y + scopeBox.height, lb.y + lb.height) + 2;
        await page.screenshot({ path: out(`${DIR}/scope.png`), clip: { x, y, width: r - x, height: b - y } });
    }
    await page.keyboard.press('Escape');
    await form.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(form).toBeHidden();

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/webhooks');
        await expect(p.getByRole('heading', { name: 'Webhooks', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        await caption(p, 'A webhook tells another system, straight away, when a test run has finished.', 2400);
        const pk = m.getByRole('combobox', { name: 'Workspace' });
        await caption(p, 'Webhooks belong to a workspace. Choose the workspace here.');
        await pointAt(p, pk, 900);
        await pk.click();
        await beat(p, 900);
        await p.getByRole('option', { name: 'Demo workspace' }).click();
        await settle(p);
        await caption(p, 'The demo has one webhook, switched off, with its recent deliveries. New webhook opens the form.');
        await pointAt(p, m.getByRole('article', { name: 'Selected webhook' }), 1400);
        const nb = m.getByRole('button', { name: 'New webhook' });
        await pointAt(p, nb, 800);
        await nb.click();
        await settle(p);
        const f = m.locator('section').filter({ has: p.getByRole('heading', { name: 'New webhook' }) });
        await caption(p, 'Give it a name, and the web address that should receive the message.');
        await pointAt(p, f.getByLabel('Name', { exact: true }), 500);
        await f.getByLabel('Name', { exact: true }).pressSequentially('ci-relay', { delay: 70 });
        await pointAt(p, f.getByLabel('Payload URL'), 400);
        await f.getByLabel('Payload URL').pressSequentially('https://example.com/hooks/traceiq', { delay: 35 });
        await caption(p, 'Project scope: every project in the workspace, or just one.');
        const sc = f.getByRole('combobox', { name: 'Project scope' });
        await pointAt(p, sc, 700);
        await sc.click();
        await beat(p, 1800);
        await p.keyboard.press('Escape');
        await caption(p, 'Events: pick which run results to send. Pick none to get them all.');
        await pointAt(p, f.getByRole('button', { name: 'run.passed' }), 900);
        await f.getByRole('button', { name: 'run.failed' }).click();
        await beat(p, 500);
        await pointAt(p, f.getByRole('button', { name: 'run.timed_out' }), 400);
        await f.getByRole('button', { name: 'run.timed_out' }).click();
        await beat(p, 900);
        await caption(p, 'After you create it, the signing secret is shown once. Copy it then.');
        await pointAt(p, f.getByRole('button', { name: 'Create webhook' }), 2400);
        await caption(p, 'We will cancel here, so nothing is saved.');
        await pointAt(p, f.getByRole('button', { name: 'Cancel', exact: true }), 900);
        await f.getByRole('button', { name: 'Cancel', exact: true }).click();
        await beat(p, 800);
        await caption(p, 'Each webhook appears in the list, with its recent deliveries and a switch to pause it.');
        await pointAt(p, m.getByText('Recent deliveries'), 2600);
        await caption(p, '');
    });
});

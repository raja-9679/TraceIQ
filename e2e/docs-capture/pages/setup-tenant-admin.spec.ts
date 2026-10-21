import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/setup/tenant-admin.html
// Nothing here changes data: the Assign workspaces dialog is opened, a
// checkbox in it is never ticked, and it is cancelled.

const DIR = 'setup/tenant-admin';

async function open(page: Page) {
    await page.goto('/admin/users');
    await expect(page.getByRole('heading', { name: 'Tenant users', level: 1 })).toBeVisible();
    await settle(page);
    await expect(page.locator('main').getByRole('article', { name: 'Selected user' })).toBeVisible();
}

test('Setup › Tenant admin', async ({ page, browser }) => {
    await open(page);
    const main = page.locator('main');

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    const heading = page.getByRole('heading', { name: 'Tenant users', level: 1 }).locator('xpath=ancestor::div[contains(@class,"rounded-3xl")][1]');
    const list = main.locator('section').filter({ has: page.getByLabel('Search users') }).first();
    const detail = main.getByRole('article', { name: 'Selected user' });
    const assign = detail.getByRole('button', { name: 'Assign workspaces' });
    const clean = await annotate(page, [
        { n: 1, target: heading },
        { n: 2, target: list, side: 'left' },
        { n: 3, target: detail },
        { n: 4, target: assign },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await list.screenshot({ path: out(`${DIR}/list.png`) });
    await detail.screenshot({ path: out(`${DIR}/detail.png`) });
    await assign.click();
    const dialog = page.getByRole('dialog', { name: /Assign workspaces/ });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText('Demo workspace')).toBeVisible();
    await beat(page, 400);
    await dialog.screenshot({ path: out(`${DIR}/assign-dialog.png`) });
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await open(p);
        const m = p.locator('main');
        await caption(p, 'Tenant admin lists every person in your organisation, across all of its workspaces.', 1600);
        await caption(p, 'Only tenant admins, who manage the whole organisation, see this page in the sidebar.');
        await pointAt(p, p.getByRole('link', { name: 'Tenant admin' }), 2200);
        await caption(p, 'The line at the top counts the people and the workspaces.');
        await pointAt(p, p.getByRole('heading', { name: 'Tenant users', level: 1 }), 2000);
        await caption(p, 'People are split into active and inactive. Each shows their highest role.');
        await pointAt(p, m.getByRole('button', { name: /Alex Morgan/ }), 2400);
        const d = m.getByRole('article', { name: 'Selected user' });
        await caption(p, 'Pick someone to see their details on the right.');
        await pointAt(p, d, 2000);
        await caption(p, 'Assign workspaces adds this person to one or more workspaces, as a member or an admin.');
        const btn = d.getByRole('button', { name: 'Assign workspaces' });
        await pointAt(p, btn, 900);
        await btn.click();
        const dlg = p.getByRole('dialog', { name: /Assign workspaces/ });
        await expect(dlg).toBeVisible();
        await caption(p, 'Choose the role, tick the workspaces, then Assign. Nothing they already have is taken away.');
        await pointAt(p, dlg.getByLabel('Role'), 1400);
        await pointAt(p, dlg.getByText('Demo workspace'), 1800);
        await caption(p, '');
        await dlg.getByRole('button', { name: 'Cancel' }).click();
        await beat(p, 600);
        await caption(p, 'To take someone out of a workspace, use the Users page instead.');
        await pointAt(p, p.getByRole('link', { name: 'Users', exact: true }), 2400);
        await caption(p, '');
    });
});

import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/setup/users.html
// Nothing here changes data: the scope menu only changes what is listed, the
// Invite people dialog is opened and cancelled, Remove from workspace is never
// pressed.

const DIR = 'setup/users';

async function open(page: Page) {
    await page.goto('/users');
    await expect(page.getByRole('heading', { name: 'Users', level: 1 })).toBeVisible();
    await settle(page);
}

async function scope(page: Page, name: RegExp) {
    await page.getByRole('combobox', { name: 'Workspace scope' }).click();
    await page.getByRole('option', { name }).click();
    await settle(page);
}

test('Setup › Users', async ({ page, browser }) => {
    await open(page);
    const main = page.locator('main');

    // "All workspaces" is what a tenant admin sees first.
    await expect(page.getByRole('combobox', { name: 'Workspace scope' })).toContainText('All workspaces');
    await main.getByRole('article', { name: 'Selected member' }).screenshot({ path: out(`${DIR}/all-detail.png`) });

    await scope(page, /^Demo workspace$/);
    await expect(main.getByText(/in Demo workspace/)).toBeVisible();

    // 1. The whole page (one workspace).
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    const picker = page.getByRole('combobox', { name: 'Workspace scope' });
    const invite = main.getByRole('button', { name: 'Invite people' });
    const search = main.getByRole('searchbox', { name: 'Search users' });
    const list = main.locator('section').filter({ has: page.getByLabel('Search users') }).first();
    const detail = main.getByRole('article', { name: 'Selected member' });
    const clean = await annotate(page, [
        { n: 1, target: picker },
        { n: 2, target: invite, side: 'right' },
        { n: 3, target: list, side: 'left' },
        { n: 4, target: detail },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await list.screenshot({ path: out(`${DIR}/list.png`) });
    await detail.screenshot({ path: out(`${DIR}/detail.png`) });
    await picker.click();
    await beat(page, 400);
    await page.getByRole('listbox').screenshot({ path: out(`${DIR}/scope-menu.png`) });
    await page.keyboard.press('Escape');

    await invite.click();
    const dialog = page.getByRole('dialog', { name: 'Invite people' });
    await expect(dialog).toBeVisible();
    await beat(page, 400);
    await dialog.screenshot({ path: out(`${DIR}/invite-dialog.png`) });
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await open(p);
        const m = p.locator('main');
        await caption(p, 'Users lists the people in your workspaces, and lets admins invite or remove them.', 1600);
        const pk = p.getByRole('combobox', { name: 'Workspace scope' });
        await caption(p, 'The menu at the top chooses whose people you see. Tenant admins can also see all workspaces at once.');
        await pointAt(p, pk, 1200);
        await pk.click();
        await beat(p, 1400);
        await p.getByRole('option', { name: /^Demo workspace$/ }).click();
        await settle(p);
        await caption(p, 'The line under the title counts members and pending invitations.');
        await pointAt(p, m.getByText(/in Demo workspace/), 2200);
        await caption(p, 'Search by name or email. Each person shows their role on the right.');
        await pointAt(p, m.getByRole('searchbox', { name: 'Search users' }), 1600);
        await pointAt(p, m.getByRole('button', { name: /Alex Morgan/ }), 1600);
        const d = m.getByRole('article', { name: 'Selected member' });
        await caption(p, 'The card on the right shows the selected person: role, status and when they last signed in.');
        await pointAt(p, d, 2600);
        await caption(p, 'Admins can remove someone from this workspace and all of its teams.');
        await pointAt(p, d.getByRole('button', { name: 'Remove from workspace' }), 2400);
        await caption(p, 'Invite people adds someone to a workspace, or to a single project.');
        const inv = m.getByRole('button', { name: 'Invite people' });
        await pointAt(p, inv, 900);
        await inv.click();
        const dlg = p.getByRole('dialog', { name: 'Invite people' });
        await expect(dlg).toBeVisible();
        await caption(p, 'Type their email, choose where to invite them, and their role.');
        await pointAt(p, dlg.getByLabel('Email address'), 1200);
        await pointAt(p, dlg.getByLabel('Invite to'), 1200);
        await pointAt(p, dlg.getByLabel('Role'), 1400);
        await caption(p, '');
        await dlg.getByRole('button', { name: 'Cancel' }).click();
        await beat(p, 600);
    });
});

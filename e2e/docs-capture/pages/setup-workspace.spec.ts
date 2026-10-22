import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/setup/workspace.html
// Nothing here changes data. Picking the demo workspace in the Workspace menu
// only changes what the page shows. The Invite people and New team dialogs are
// opened and cancelled; no Grant, remove (×), Delete or Create button is pressed.

const DIR = 'setup/workspace';

async function openDemo(page: Page) {
    await page.goto('/workspace');
    await expect(page.getByRole('tablist', { name: 'Workspace sections' })).toBeVisible();
    await settle(page);
    // The page opens on the account's first workspace; the pictures show the demo.
    await page.getByRole('combobox', { name: 'Workspace', exact: true }).click();
    await page.getByRole('option', { name: /Demo workspace/ }).click();
    await expect(page.getByRole('heading', { name: /Demo workspace/, level: 1 })).toBeVisible();
    await settle(page);
}

const tab = (page: Page, name: string) => page.getByRole('tablist', { name: 'Workspace sections' }).getByRole('tab', { name: new RegExp(`^${name}`) });

test('Setup › Workspace', async ({ page, browser }) => {
    await openDemo(page);
    const main = page.locator('main');

    // 1. The whole page as a reader first meets it (Members tab).
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    const picker = page.getByRole('combobox', { name: 'Workspace', exact: true });
    const tabs = page.getByRole('tablist', { name: 'Workspace sections' });
    const invite = main.getByRole('button', { name: 'Invite people' });
    const list = main.locator('section').filter({ has: page.getByText('Members', { exact: true }) }).first();
    const detail = main.getByRole('article', { name: 'Selected member' });
    const demoCard = main.locator('section[aria-labelledby="demo-ws-title"]');
    await page.setViewportSize({ width: 1440, height: 1200 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: picker },
        { n: 2, target: tabs },
        { n: 3, target: invite, side: 'right' },
        { n: 4, target: list, side: 'left' },
        { n: 5, target: detail },
        { n: 6, target: demoCard },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await list.screenshot({ path: out(`${DIR}/members-list.png`) });
    await detail.screenshot({ path: out(`${DIR}/member-detail.png`) });
    await demoCard.screenshot({ path: out(`${DIR}/demo-card.png`) });

    await invite.click();
    const inviteDialog = page.getByRole('dialog', { name: 'Invite to workspace' });
    await expect(inviteDialog).toBeVisible();
    await beat(page, 400);
    await inviteDialog.screenshot({ path: out(`${DIR}/invite-dialog.png`) });
    await inviteDialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(inviteDialog).toBeHidden();

    await tab(page, 'Teams').click();
    await settle(page);
    const teamDetail = main.getByRole('article', { name: 'Selected team' });
    await expect(teamDetail.getByText('QA engineers')).toBeVisible();
    await settle(page);
    await teamDetail.screenshot({ path: out(`${DIR}/team-detail.png`) });
    await main.getByRole('button', { name: 'New team' }).click();
    const teamDialog = page.getByRole('dialog', { name: 'New team' });
    await expect(teamDialog).toBeVisible();
    await beat(page, 400);
    await teamDialog.screenshot({ path: out(`${DIR}/team-dialog.png`) });
    await teamDialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(teamDialog).toBeHidden();

    await tab(page, 'Projects').click();
    await settle(page);
    const projDetail = main.getByRole('article', { name: 'Selected project' });
    await expect(projDetail.getByText('Direct members')).toBeVisible();
    await settle(page);
    await main.locator('section').filter({ has: page.getByText('Projects', { exact: true }) }).first().screenshot({ path: out(`${DIR}/projects-list.png`) });
    await projDetail.screenshot({ path: out(`${DIR}/project-access.png`) });

    await tab(page, 'Danger zone').click();
    await settle(page);
    await main.locator('section[aria-labelledby="danger-title"]').screenshot({ path: out(`${DIR}/danger.png`) });
    // The delete dialog only previews (a dry run) until the name is typed; it is cancelled.
    await main.getByRole('button', { name: 'Delete workspace' }).click();
    const del = page.getByRole('alertdialog');
    await expect(del).toBeVisible();
    await expect(del.getByText('Checking what will be removed')).toBeHidden({ timeout: 15000 });
    await beat(page, 400);
    await del.screenshot({ path: out(`${DIR}/delete-dialog.png`) });
    await del.getByRole('button', { name: 'Cancel' }).click();
    await expect(del).toBeHidden();
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/workspace');
        await expect(p.getByRole('tablist', { name: 'Workspace sections' })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        await caption(p, 'A workspace holds your projects and the people who work on them.', 1600);
        await caption(p, 'If you belong to several workspaces, pick one here.');
        const pick = p.getByRole('combobox', { name: 'Workspace', exact: true });
        await pointAt(p, pick, 900);
        await pick.click();
        await beat(p, 900);
        await p.getByRole('option', { name: /Demo workspace/ }).click();
        await settle(p);
        await caption(p, 'The line under the name counts its members, teams and projects.');
        await pointAt(p, p.getByRole('heading', { name: /Demo workspace/, level: 1 }), 2200);
        await caption(p, 'Members: everyone in the workspace, with their role. Click a person to see their details.');
        await pointAt(p, m.getByRole('article', { name: 'Selected member' }), 2600);
        await caption(p, 'Workspace admins invite people with Invite people.');
        await pointAt(p, m.getByRole('button', { name: 'Invite people' }), 2200);
        await caption(p, '');
        await tab(p, 'Teams').click();
        await settle(p);
        await caption(p, 'Teams group people, so a whole team can be given access to a project at once.');
        await pointAt(p, m.getByRole('article', { name: 'Selected team' }), 2600);
        await caption(p, 'New team makes another one.');
        await pointAt(p, m.getByRole('button', { name: 'New team' }), 1800);
        await caption(p, '');
        await tab(p, 'Projects').click();
        await settle(p);
        const pd = m.getByRole('article', { name: 'Selected project' });
        await caption(p, 'Projects: choose one to see who can use it.');
        await pointAt(p, m.getByRole('button', { name: /Demo — Public API/ }), 1200);
        await m.getByRole('button', { name: /Demo — Public API/ }).click();
        await settle(p);
        await beat(p, 600);
        await caption(p, 'Access comes from teams, or from a direct grant to one person: viewer, editor or admin.');
        await pointAt(p, pd.getByText('Teams with access'), 1600);
        await pointAt(p, pd.getByText('Direct members'), 1600);
        await caption(p, '');
        await tab(p, 'Danger zone').click();
        await settle(p);
        await caption(p, 'The danger zone deletes the whole workspace. It asks you to type its name first.');
        await pointAt(p, m.getByRole('button', { name: 'Delete workspace' }), 2600);
        await caption(p, '');
    });
});

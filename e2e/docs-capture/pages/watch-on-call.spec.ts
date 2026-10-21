import { expect, test } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/watch/on-call.html
// Nothing here changes data: the "Add channel" and "Add window" forms are
// opened and cancelled, never saved; no Test, Edit, Delete or Save button and
// no checkbox of the egress form is pressed. The demo has no paging channels,
// windows or paging decisions (a demo workspace never pages anyone).

const DIR = 'watch/on-call';

test('Watch › On-call', async ({ page, browser }) => {
    await page.goto('/on-call');
    await expect(page.getByRole('heading', { name: 'On-call', level: 1 })).toBeVisible();
    const main = page.locator('main');
    const card = (title: string) => main.locator('section').filter({ has: page.getByRole('heading', { name: title, level: 2 }) });
    const channels = card('Paging channels');
    const windows = card('Maintenance windows');
    const events = card('Recent paging decisions');
    const egress = card('Worker egress allowlist');
    await expect(channels.getByText(/No paging channels/)).toBeVisible();
    await expect(egress.getByText('Effective at dispatch')).toBeVisible();
    await settle(page);

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered (a taller window: the four cards are stacked).
    await page.setViewportSize({ width: 1440, height: 1400 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: channels },
        { n: 2, target: windows },
        { n: 3, target: events },
        { n: 4, target: egress },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups, including the two forms opened and cancelled.
    await channels.screenshot({ path: out(`${DIR}/channels.png`) });
    await windows.screenshot({ path: out(`${DIR}/windows.png`) });
    await events.screenshot({ path: out(`${DIR}/events.png`) });
    await egress.screenshot({ path: out(`${DIR}/egress.png`) });
    await channels.getByRole('button', { name: 'Add channel' }).click();
    await expect(channels.getByLabel('Name')).toBeVisible();
    await channels.screenshot({ path: out(`${DIR}/channel-form.png`) });
    await channels.getByRole('button', { name: 'Cancel' }).click();
    await expect(channels.getByRole('button', { name: 'Add channel' })).toBeVisible();
    await windows.getByRole('button', { name: 'Add window' }).click();
    await expect(windows.getByLabel('Name')).toBeVisible();
    await windows.screenshot({ path: out(`${DIR}/window-form.png`) });
    await windows.getByRole('button', { name: 'Cancel' }).click();
    await expect(windows.getByRole('button', { name: 'Add window' })).toBeVisible();
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/on-call');
        await expect(p.getByRole('heading', { name: 'On-call', level: 1 })).toBeVisible();
        const m = p.locator('main');
        const c = (title: string) => m.locator('section').filter({ has: p.getByRole('heading', { name: title, level: 2 }) });
        await expect(c('Paging channels').getByText(/No paging channels/)).toBeVisible();
        await settle(p);
        await caption(p, 'On-call decides who gets woken up when a monitor goes down, and when nobody should be.', 2000);
        await caption(p, 'A paging channel sends an alert to PagerDuty or Opsgenie. The demo has none.');
        await pointAt(p, c('Paging channels').getByText(/No paging channels/), 2400);
        const add = c('Paging channels').getByRole('button', { name: 'Add channel' });
        await caption(p, 'Adding one asks for a name, the service, its key, and what should page.');
        await pointAt(p, add, 800);
        await add.click();
        await pointAt(p, c('Paging channels').getByLabel('Name'), 1200);
        await pointAt(p, c('Paging channels').getByText(/Page on monitor DOWN/), 1600);
        await caption(p, 'We cancel here, so nothing is saved.');
        await c('Paging channels').getByRole('button', { name: 'Cancel' }).click();
        await beat(p, 600);
        const w = c('Maintenance windows');
        await w.scrollIntoViewIfNeeded();
        await caption(p, 'A maintenance window holds pages back, for example during a planned deploy.');
        await pointAt(p, w.getByRole('heading', { name: 'Maintenance windows' }), 2400);
        const e = c('Recent paging decisions');
        await e.scrollIntoViewIfNeeded();
        await caption(p, 'Every paging decision is listed here: sent, held back, or failed.');
        await pointAt(p, e.getByRole('heading', { name: 'Recent paging decisions' }), 2400);
        const g = c('Worker egress allowlist');
        await g.scrollIntoViewIfNeeded();
        await caption(p, 'Last, the egress allowlist limits which websites this project\'s test browsers may reach.');
        await pointAt(p, g.getByRole('heading', { name: 'Worker egress allowlist' }), 2600);
        await caption(p, 'Here it is not enforced, so tests can reach any site.');
        await pointAt(p, g.getByText('Effective at dispatch'), 2200);
        await caption(p, '');
    });
});

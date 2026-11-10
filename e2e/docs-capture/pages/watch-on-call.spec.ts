import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/watch/on-call.html
// Nothing here changes data: the "Add channel" and "Add window" forms are
// opened and cancelled, never saved; no Send test page, Edit, Delete or Save
// button and no checkbox of the egress form is pressed. The demo has no paging
// channels, windows or paging decisions (a demo workspace never pages anyone).

const DIR = 'watch/on-call';

async function open(p: Page) {
    await p.goto('/on-call');
    await expect(p.getByRole('heading', { name: 'On-call', level: 1 })).toBeVisible();
    await expect(p.getByRole('tab', { name: 'Paging channels' })).toHaveAttribute('aria-selected', 'true');
    await expect(p.locator('main').getByText('No paging channels', { exact: true })).toBeVisible();
    await settle(p);
}

test('Watch › On-call', async ({ page, browser }) => {
    await open(page);
    const main = page.locator('main');
    const listOf = (title: string) => main.getByRole('heading', { name: title, level: 2 }).locator('xpath=ancestor::section[1]');
    const split = (title: string) => listOf(title).locator('xpath=..');
    const tab = (name: string) => main.getByRole('tab', { name });

    // 1. The page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    const tabs = main.getByRole('tablist', { name: 'On-call sections' });
    const empty = main.getByText('No paging channels', { exact: true }).locator('xpath=..');
    const clean = await annotate(page, [
        { n: 1, target: tabs },
        { n: 2, target: listOf('Paging channels') },
        { n: 3, target: empty, side: 'right', inset: true },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups of each tab, including the two forms opened and cancelled.
    await split('Paging channels').screenshot({ path: out(`${DIR}/channels.png`) });
    await main.getByRole('button', { name: 'Add channel' }).click();
    await expect(main.getByRole('article', { name: 'New paging channel' })).toBeVisible();
    await settle(page);
    await main.getByRole('article', { name: 'New paging channel' }).screenshot({ path: out(`${DIR}/channel-form.png`) });
    await main.getByRole('button', { name: 'Cancel' }).click();
    await expect(main.getByRole('button', { name: 'Add channel' })).toBeVisible();

    await tab('Maintenance windows').click();
    await expect(main.getByText('No maintenance windows', { exact: true })).toBeVisible();
    await settle(page);
    await split('Maintenance windows').screenshot({ path: out(`${DIR}/windows.png`) });
    await main.getByRole('button', { name: 'Add window' }).click();
    await expect(main.getByRole('article', { name: 'New maintenance window' })).toBeVisible();
    await settle(page);
    await main.getByRole('article', { name: 'New maintenance window' }).screenshot({ path: out(`${DIR}/window-form.png`) });
    await main.getByRole('button', { name: 'Cancel' }).click();
    await expect(main.getByRole('button', { name: 'Add window' })).toBeVisible();

    await tab('Paging log').click();
    await expect(listOf('Recent paging decisions')).toBeVisible();
    await settle(page);
    await listOf('Recent paging decisions').screenshot({ path: out(`${DIR}/events.png`) });

    await tab('Egress allowlist').click();
    await expect(main.getByText('Effective at dispatch')).toBeVisible();
    await settle(page);
    await listOf('Worker egress allowlist').screenshot({ path: out(`${DIR}/egress.png`) });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await open(p);
        const m = p.locator('main');
        const t = (name: string) => m.getByRole('tab', { name });
        await caption(p, 'On-call decides who gets woken up when a monitor goes down, and when nobody should be.', 2000);
        await caption(p, 'It works on the project chosen in the sidebar, in four tabs.');
        await pointAt(p, m.getByRole('tablist', { name: 'On-call sections' }), 2200);
        await caption(p, 'A paging channel sends an alert to PagerDuty or Opsgenie. The demo has none.');
        await pointAt(p, m.getByText('No paging channels', { exact: true }), 2400);
        const add = m.getByRole('button', { name: 'Add channel' });
        await caption(p, 'Adding one opens a form on the right: a name, the service, its key, and what should page.');
        await pointAt(p, add, 800);
        await add.click();
        await pointAt(p, m.getByLabel('Name'), 1200);
        await pointAt(p, m.getByText(/Page on monitor DOWN/), 1600);
        await caption(p, 'We cancel here, so nothing is saved.');
        await m.getByRole('button', { name: 'Cancel' }).click();
        await beat(p, 600);
        await caption(p, 'A maintenance window holds pages back, for example during a planned deploy.');
        await t('Maintenance windows').click();
        await pointAt(p, m.getByRole('button', { name: 'Add window' }), 2400);
        await caption(p, 'The paging log lists every paging decision: sent, held back, or failed.');
        await t('Paging log').click();
        await pointAt(p, m.getByRole('heading', { name: 'Recent paging decisions' }), 2400);
        await caption(p, 'Last, the egress allowlist limits which websites this project\'s test browsers may reach.');
        await t('Egress allowlist').click();
        await pointAt(p, m.getByRole('heading', { name: 'Worker egress allowlist' }), 2600);
        await caption(p, 'Here it is not enforced, so tests can reach any site.');
        await pointAt(p, m.getByText('Effective at dispatch'), 2200);
        await caption(p, '');
    });
});

import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/tests/schedules.html
// The demo's only schedule (a paused monitor) is in "Demo — Public API", so
// that project is selected before the page loads. Nothing here changes data:
// the Edit dialog is opened and closed with Cancel; Resume and Delete are
// never pressed.

const DIR = 'tests/schedules';
const API_PROJECT = 'Demo — Public API';

async function useApiProject(page: Page) {
    const projects = await page.request.get('/api/projects', {
        headers: { Authorization: `Bearer ${await page.evaluate(() => localStorage.getItem('token'))}` },
    }).then((r) => r.json());
    const id = (projects as { id: number; name: string }[]).find((p) => p.name === API_PROJECT)!.id;
    await page.addInitScript((pid) => localStorage.setItem('activeProjectId', String(pid)), id);
}

test('Tests › Schedules', async ({ page, browser }) => {
    await page.goto('/overview');
    await useApiProject(page);
    await page.goto('/schedules');
    await expect(page.getByRole('heading', { name: 'Schedules', level: 1 })).toBeVisible();
    await settle(page);
    const main = page.locator('main');
    const detail = main.getByRole('article', { name: 'Selected schedule' });
    await expect(detail).toBeVisible();

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    const counts = main.getByText(/schedules? · \d+ active/);
    const tools = main.getByRole('textbox', { name: 'Search schedules' }).locator('xpath=ancestor::div[1]');
    const list = main.locator('section').filter({ has: page.getByRole('button', { pressed: true }) }).first();
    const facts = detail.getByText('Next run', { exact: true }).locator('xpath=ancestor::div[contains(@class,"grid")][1]');
    const timing = detail.getByText('Timing', { exact: true }).locator('xpath=ancestor::div[contains(@class,"rounded")][1]');
    const actions = detail.getByRole('button', { name: /Resume|Pause/ }).locator('xpath=..');
    const clean = await annotate(page, [
        { n: 1, target: counts, side: 'right' },
        { n: 2, target: tools, side: 'right' },
        { n: 3, target: list, side: 'left' },
        { n: 4, target: facts, side: 'right' },
        { n: 5, target: timing, side: 'right' },
        { n: 6, target: actions },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await list.screenshot({ path: out(`${DIR}/list.png`) });
    await detail.locator('xpath=./div[1]').screenshot({ path: out(`${DIR}/detail-header.png`) });
    await facts.screenshot({ path: out(`${DIR}/facts.png`) });
    await timing.screenshot({ path: out(`${DIR}/timing.png`) });
    await actions.screenshot({ path: out(`${DIR}/actions.png`) });

    // The schedule form (the same one "Schedule" opens on a suite or test), closed with Cancel.
    await detail.getByRole('button', { name: 'Edit' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByText('Edit schedule')).toBeVisible();
    await beat(page, 500);
    await dialog.screenshot({ path: out(`${DIR}/form.png`) });
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();

    // 4. A short walkthrough video.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/overview');
        await useApiProject(p);
        await p.goto('/schedules');
        await expect(p.getByRole('heading', { name: 'Schedules', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        const d = m.getByRole('article', { name: 'Selected schedule' });
        await caption(p, 'Schedules run tests by themselves, on a timer.', 2000);
        await caption(p, 'The line at the top counts this project’s schedules, and how many are active.');
        await pointAt(p, m.getByText(/schedules? · \d+ active/), 2200);
        await caption(p, 'Active schedules are listed first. This one is paused.');
        await pointAt(p, m.getByRole('button', { name: /Public API health/ }), 2200);
        await caption(p, 'On the right: when it runs next, when it last ran, what it runs, and on which browser.');
        await pointAt(p, d.getByText('Next run', { exact: true }), 1200);
        await pointAt(p, d.getByText('Last run', { exact: true }), 1200);
        await pointAt(p, d.getByText('Runs', { exact: true }), 1200);
        await caption(p, 'The timing is a cron expression: 0 */6 * * * means every six hours, on the hour.', {
            say: 'The timing is written as a cron expression. This one means every six hours, on the hour.',
        });
        await pointAt(p, d.getByText(/0 \*\/6/).first(), 2600);
        await caption(p, 'Pause stops new runs but keeps the settings. Resume starts it again.');
        await pointAt(p, d.getByRole('button', { name: /Resume|Pause/ }), 2200);
        await caption(p, 'Edit changes the name, how often, the timezone, the browser and the device.');
        await d.getByRole('button', { name: 'Edit' }).click();
        await beat(p, 1200);
        const dlg = p.getByRole('dialog');
        await pointAt(p, dlg.getByLabel('How often'), 1600);
        await pointAt(p, dlg.getByLabel('Timezone'), 1600);
        await caption(p, 'Cancel closes it without saving anything.');
        await pointAt(p, dlg.getByRole('button', { name: 'Cancel' }), 1200);
        await dlg.getByRole('button', { name: 'Cancel' }).click();
        await beat(p, 800);
        await caption(p, 'New schedules are made from a suite or a test, with its Schedule button.', 1600);
        await caption(p, '');
    });
});

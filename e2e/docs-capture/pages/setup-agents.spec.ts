import { expect, test, type Locator } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/setup/agents.html
// Nothing here changes data: "Open" only selects an agent on the page, and
// no Save / Remove controls button, kill-switch box or limit field is touched.
// The page opens on the workspace of the project selected in the sidebar,
// which for the docs account is the demo workspace. Its one agent is
// claude-code, with one session that ran the web suite from a feature branch
// (schedule:<id> and demo-seed are not agents and are hidden).

/** A <select> wrapped in a <label> whose text starts with `name` (the label's
 * accessible name also contains every option, so getByLabel is ambiguous). */
const labelled = (scope: Locator, name: string) =>
    scope.locator('label').filter({ hasText: new RegExp(`^\\s*${name}`) }).locator('select');

const DIR = 'setup/agents';

test('Setup › Agents', async ({ page, browser }) => {
    await page.goto('/agents');
    await expect(page.getByRole('heading', { name: 'Agents', level: 1 })).toBeVisible();
    const main = page.locator('main');
    const list = main.getByRole('region', { name: 'Agents', exact: true });
    await expect(list.getByText('claude-code', { exact: true })).toBeVisible();
    await expect(labelled(main, 'Workspace').locator('option:checked')).toHaveText('Demo workspace');
    await settle(page);

    // 1. The page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Pick an agent (page state only), then number the parts.
    const row = list.locator('li').filter({ hasText: 'claude-code' });
    await row.getByRole('button', { name: 'Open' }).click();
    const controls = main.getByRole('region', { name: /Controls for/ });
    const sessions = main.getByRole('region', { name: 'Sessions', exact: true });
    await expect(controls).toBeVisible();
    await sessions.getByRole('button', { name: 'Show timeline' }).first().click();
    const timeline = main.getByRole('region', { name: 'Session timeline' });
    await expect(timeline).toBeVisible();
    await page.setViewportSize({ width: 1440, height: 1900 });
    await settle(page);
    const addAgent = main.getByRole('region', { name: 'Set controls for an agent id' });
    const defaults = main.getByRole('region', { name: 'Defaults for every agent' });
    const clean = await annotate(page, [
        { n: 1, target: list },
        { n: 2, target: addAgent },
        { n: 3, target: defaults },
        { n: 4, target: controls, side: 'right' },
        { n: 5, target: sessions, side: 'right' },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await list.screenshot({ path: out(`${DIR}/list.png`) });
    await defaults.screenshot({ path: out(`${DIR}/defaults.png`) });
    await controls.screenshot({ path: out(`${DIR}/controls.png`) });
    await sessions.screenshot({ path: out(`${DIR}/sessions.png`) });
    await timeline.screenshot({ path: out(`${DIR}/timeline.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/agents');
        await expect(p.getByRole('heading', { name: 'Agents', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        const l = m.getByRole('region', { name: 'Agents', exact: true });
        await caption(p, 'Agents are programs, often AI assistants, that use TraceIQ on someone’s behalf.', {
            pause: 1800, say: 'Agents are programs, often AI assistants, that use TraceIQ on someone\'s behalf.',
        });
        await caption(p, 'This list shows every agent name seen in the workspace, with what it did.');
        await pointAt(p, l.locator('li').first(), 2200);
        await caption(p, 'In the demo, claude-code worked in thirteen sessions, each allowed three runs.');
        await pointAt(p, l.getByText('claude-code', { exact: true }), 2200);
        const r = l.locator('li').filter({ hasText: 'claude-code' });
        await caption(p, 'Open an agent to see its controls and its sessions.');
        await pointAt(p, r.getByRole('button', { name: 'Open' }), 900);
        await r.getByRole('button', { name: 'Open' }).click();
        await settle(p);
        const c = m.getByRole('region', { name: /Controls for/ });
        await caption(p, 'The kill switch stops this agent from changing anything here. It can still read.');
        await pointAt(p, c.getByText(/Block this agent/), 2400);
        await caption(p, 'Budgets limit each session: runs, AI tokens, proposals and minutes.');
        await pointAt(p, c.getByText('Runs per session'), 1200);
        await pointAt(p, c.getByText('Minutes per session'), 1200);
        await caption(p, 'An empty box inherits the default. Zero means no limit.');
        await pointAt(p, c.getByRole('textbox').first(), 2200);
        const s = m.getByRole('region', { name: 'Sessions', exact: true });
        await s.scrollIntoViewIfNeeded();
        await caption(p, 'Each session shows what it used against its limits, and what was refused.');
        await pointAt(p, s.locator('li').first(), 2200);
        await caption(p, 'Show timeline lists everything that session did, newest first.');
        await s.getByRole('button', { name: 'Show timeline' }).first().click();
        await settle(p);
        await pointAt(p, m.getByRole('region', { name: 'Session timeline' }), 2400);
        const d = m.getByRole('region', { name: 'Defaults for every agent' });
        await d.scrollIntoViewIfNeeded();
        await caption(p, 'Defaults for every agent apply to any agent without its own controls.');
        await pointAt(p, d.getByRole('heading', { name: 'Defaults for every agent' }), 2400);
        await caption(p, 'Changes only take effect when an admin presses Save.');
        await pointAt(p, d.getByRole('button', { name: 'Save' }), 1800);
        await caption(p, '');
    });
});

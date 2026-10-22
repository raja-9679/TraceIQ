import { expect, test, type Locator } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/setup/policies.html
// Nothing here changes data: the "Allow AI features" box is never clicked and
// Save is never pressed. The denial filters are only read (GET requests).
// The page opens on the workspace of the project selected in the sidebar,
// which for the docs account is the demo workspace.

/** A <select> wrapped in a <label> whose text starts with `name` (the label's
 * accessible name also contains every option, so getByLabel is ambiguous). */
const labelled = (scope: Locator, name: string) =>
    scope.locator('label').filter({ hasText: new RegExp(`^\\s*${name}`) }).locator('select');

const DIR = 'setup/policies';

test('Setup › Policies', async ({ page, browser }) => {
    await page.goto('/policies');
    await expect(page.getByRole('heading', { name: 'Policies', level: 1 })).toBeVisible();
    const main = page.locator('main');
    const ai = main.getByRole('region', { name: 'AI use in this workspace' });
    const rules = main.getByRole('region', { name: 'Rules', exact: true });
    const denials = main.getByRole('region', { name: 'Recent denials' });
    await expect(rules.getByText('plan.run_quota')).toBeVisible();
    await expect(denials.getByRole('list', { name: 'Refused actions' }).or(denials.getByText(/Nothing was refused/)).first()).toBeVisible();
    await expect(labelled(main, 'Workspace')).toHaveValue(/\d+/);
    await expect(labelled(main, 'Workspace').locator('option:checked')).toHaveText('Demo workspace');
    await settle(page);

    // 1. The page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    await page.setViewportSize({ width: 1440, height: 1300 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: labelled(main, 'Workspace').locator('xpath=..') },
        { n: 2, target: ai },
        { n: 3, target: rules },
        { n: 4, target: denials, side: 'right' },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await ai.screenshot({ path: out(`${DIR}/ai.png`) });
    const firstGroup = rules.locator('h3').first().locator('xpath=..');
    await firstGroup.screenshot({ path: out(`${DIR}/rules-group.png`) });
    // The card stretches to the height of the Rules column, so keep only its top.
    const shootTop = async (file: string, below: Locator) => {
        await page.evaluate(() => window.scrollTo(0, 0));
        const top = await denials.boundingBox();
        const end = await below.boundingBox();
        if (top && end) {
            await page.screenshot({ path: out(file),
                clip: { x: top.x, y: top.y, width: top.width, height: end.y + end.height - top.y + 24 } });
        }
    };
    await shootTop(`${DIR}/denials.png`, denials.getByRole('list', { name: 'Refused actions' }).or(denials.getByText(/Nothing was refused/)).first());
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/policies');
        await expect(p.getByRole('heading', { name: 'Policies', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        const a = m.getByRole('region', { name: 'AI use in this workspace' });
        const r = m.getByRole('region', { name: 'Rules', exact: true });
        const d = m.getByRole('region', { name: 'Recent denials' });
        await caption(p, 'Policies gathers every check that can stop something from happening in TraceIQ.', 2000);
        await caption(p, 'First, one switch: may this workspace use AI at all?');
        await pointAt(p, a.getByText('Allow AI features'), 2200);
        await caption(p, 'Only workspace admins can change it, and they press Save to apply it.');
        await pointAt(p, a.getByText(/AI on|AI off/), 2000);
        await caption(p, 'Rules lists every check, grouped by what it protects.');
        await pointAt(p, r.locator('h3').first(), 1800);
        await caption(p, 'Each rule says what it does, and where its setting lives.');
        await pointAt(p, r.getByText('plan.run_quota'), 1400);
        await r.getByText('plan.run_quota').scrollIntoViewIfNeeded();
        await pointAt(p, r.getByText('plan.run_quota').locator('xpath=ancestor::li[1]').getByText('Configured in:'), 1800);
        await caption(p, 'Refuses stops the action. Defers makes it wait. Resolves settings only adjusts it.');
        await pointAt(p, r.getByText('Defers').first(), 2200);
        // The card is as tall as the Rules column: scroll to its heading, not its middle.
        await p.evaluate(() => window.scrollTo(0, 0));
        await caption(p, 'Recent denials lists what was actually refused, newest first.');
        await pointAt(p, d.getByRole('heading', { name: 'Recent denials' }), 1800);
        await caption(p, 'Narrow it down by action, rule, agent or period.');
        await pointAt(p, labelled(d, 'Action'), 900);
        await labelled(d, 'Period').selectOption('90');
        await settle(p);
        await pointAt(p, labelled(d, 'Period'), 1200);
        await caption(p, 'Each refusal says which rule, when, the reason given, where, and who asked.');
        await pointAt(p, d.getByRole('list', { name: 'Refused actions' }).getByRole('listitem').first(), 2400);
        await caption(p, '');
    });
});

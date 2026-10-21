import { expect, test } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/today/proposals.html
// Nothing here changes data: tabs and status filters only change what is
// shown, and the threshold editor is opened and cancelled, never saved. The
// two policy toggles save on click, so they are pointed at, never pressed.
// The demo workspace has no proposals, so every picture is an empty queue.

const DIR = 'today/proposals';

test('Today › Proposals', async ({ page, browser }) => {
    await page.goto('/proposals');
    await expect(page.getByRole('heading', { name: 'Proposals', level: 1 })).toBeVisible();
    const main = page.locator('main');
    const policy = main.getByRole('region', { name: 'Review policy' });
    await expect(policy).toBeVisible();
    await expect(main.getByText(/No waiting for review test proposals/)).toBeVisible();
    await settle(page);

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered (the guide explains each number).
    const tabs = main.getByRole('tablist', { name: 'Proposal type' });
    const filters = main.getByRole('group', { name: 'Filter by status' });
    const empty = main.getByText(/No waiting for review test proposals/).locator('xpath=..');
    const clean = await annotate(page, [
        { n: 1, target: tabs },
        { n: 2, target: filters, side: 'right' },
        { n: 3, target: policy },
        { n: 4, target: empty },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups used next to each explanation.
    await tabs.screenshot({ path: out(`${DIR}/tabs.png`) });
    await filters.screenshot({ path: out(`${DIR}/filters.png`) });
    await policy.screenshot({ path: out(`${DIR}/policy.png`) });
    // The threshold editor, opened and cancelled (nothing is saved).
    await policy.getByRole('button', { name: 'Edit' }).click();
    await expect(policy.getByLabel(/Auto-apply confidence threshold/)).toBeVisible();
    await policy.screenshot({ path: out(`${DIR}/policy-edit.png`) });
    await policy.getByRole('button', { name: 'Cancel' }).click();
    await expect(policy.getByRole('button', { name: 'Edit' })).toBeVisible();
    // The second tab and its four status filters.
    await tabs.getByRole('tab', { name: 'Selector fixes' }).click();
    await expect(main.getByText(/No waiting for review heal proposals/)).toBeVisible();
    await settle(page);
    await page.screenshot({ path: out(`${DIR}/selector-fixes.png`) });
    await filters.screenshot({ path: out(`${DIR}/filters-heals.png`) });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/proposals');
        await expect(p.getByRole('heading', { name: 'Proposals', level: 1 })).toBeVisible();
        const m = p.locator('main');
        const pol = m.getByRole('region', { name: 'Review policy' });
        await expect(pol).toBeVisible();
        await settle(p);
        await caption(p, 'Proposals is where suggested changes to your tests wait for a person to review them.', 2200);
        const t = m.getByRole('tablist', { name: 'Proposal type' });
        await caption(p, 'Test changes come from AI agents: new tests, edits, moves and deletions.');
        await pointAt(p, t.getByRole('tab', { name: 'Test changes' }), 2400);
        const f = m.getByRole('group', { name: 'Filter by status' });
        await caption(p, 'The filters on the right show what is waiting, or what was already decided.');
        await pointAt(p, f.getByRole('button', { name: 'Waiting' }), 1400);
        await pointAt(p, f.getByRole('button', { name: 'Accepted' }), 1000);
        await f.getByRole('button', { name: 'Accepted' }).click();
        await settle(p);
        await beat(p, 1200);
        await f.getByRole('button', { name: 'Waiting' }).click();
        await settle(p);
        await caption(p, 'Auto-apply lets confident new tests and edits go in without waiting. It is off here.');
        await pointAt(p, pol.getByText('Auto-apply', { exact: true }), 2600);
        await caption(p, 'A workspace admin can require a second person to approve, or a validation run first.');
        await pointAt(p, pol.getByText('Second approver', { exact: true }), 1600);
        await pointAt(p, pol.getByText('Validate before accept', { exact: true }), 1600);
        await caption(p, 'The demo has no proposals, so the queue is empty.');
        await pointAt(p, m.getByText(/No waiting for review test proposals/), 2200);
        await caption(p, 'Selector fixes are new ways to find a button or field that moved on the page.');
        await pointAt(p, t.getByRole('tab', { name: 'Selector fixes' }), 900);
        await t.getByRole('tab', { name: 'Selector fixes' }).click();
        await settle(p);
        await beat(p, 1600);
        await caption(p, 'TraceIQ suggests them after a step fails. Nothing changes until someone applies one.');
        await pointAt(p, m.getByText(/No waiting for review heal proposals/), 2600);
        await caption(p, '');
    });
});

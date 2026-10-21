import { expect, test, type Locator, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/setup/ai-usage.html
// Read-only page. The demo workspace has no recorded AI calls (the demo
// generator writes no LLMUsageEvent rows and the capture stack has no AI
// provider), so the pictures show the empty state and the two menus.

const DIR = 'setup/ai-usage';

async function openDemoWorkspace(p: Page) {
    await p.goto('/ai-usage');
    await expect(p.getByRole('heading', { name: 'AI usage', level: 1 })).toBeVisible();
    await settle(p);
    // The page starts on the account's first workspace; the guide shows the demo one.
    await p.getByRole('combobox', { name: 'Workspace' }).click();
    await p.getByRole('option', { name: 'Demo workspace' }).click();
    await settle(p);
    await expect(p.getByText(/No LLM calls in the last \d+ days/)).toBeVisible();
}

/** Screenshot of a Radix select's trigger together with its open list. */
async function shootOpenSelect(p: Page, trigger: Locator, file: string) {
    const tb = await trigger.boundingBox();
    await trigger.click();
    const listbox = p.getByRole('listbox');
    await expect(listbox).toBeVisible();
    await beat(p, 400); // let the menu's fade-in finish
    const lb = await listbox.boundingBox();
    if (tb && lb) {
        const x = Math.min(tb.x, lb.x) - 12, y = Math.min(tb.y, lb.y) - 12;
        const r = Math.max(tb.x + tb.width, lb.x + lb.width) + 12;
        const b = Math.max(tb.y + tb.height, lb.y + lb.height) + 12;
        await p.screenshot({ path: out(file), clip: { x, y, width: r - x, height: b - y } });
    }
    await p.keyboard.press('Escape');
    await expect(listbox).toBeHidden();
}

test('Setup › AI usage', async ({ page, browser }) => {
    await openDemoWorkspace(page);
    const main = page.locator('main');
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());

    // 1. The page as the demo workspace shows it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    const windowPicker = main.getByRole('combobox', { name: 'Time window' });
    const wsPicker = main.getByRole('combobox', { name: 'Workspace' });
    const empty = main.getByText(/No LLM calls in the last/).locator('xpath=..');
    const clean = await annotate(page, [
        { n: 1, target: windowPicker },
        { n: 2, target: wsPicker, side: 'right' },
        { n: 3, target: empty },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-up: the time-window menu.
    await shootOpenSelect(page, windowPicker, `${DIR}/window.png`);

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/ai-usage');
        await expect(p.getByRole('heading', { name: 'AI usage', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        await caption(p, 'AI usage shows every call TraceIQ made to an AI service for your workspace.', 2000);
        const ws = m.getByRole('combobox', { name: 'Workspace' });
        await caption(p, 'Usage is counted per workspace. Choose the workspace here.');
        await pointAt(p, ws, 900);
        await ws.click();
        await beat(p, 900);
        await p.getByRole('option', { name: 'Demo workspace' }).click();
        await settle(p);
        const tw = m.getByRole('combobox', { name: 'Time window' });
        await caption(p, 'This menu picks how far back to look: 7, 30 or 90 days.', {
            say: 'This menu picks how far back to look: seven, thirty or ninety days.',
        });
        await pointAt(p, tw, 700);
        await tw.click();
        await beat(p, 1800);
        await p.getByRole('option', { name: '90 days' }).click();
        await settle(p);
        await caption(p, 'The demo workspace has made no AI calls, so the page says so.');
        await pointAt(p, m.getByText(/No LLM calls in the last/), 2200);
        await caption(p, 'With calls, you would see four totals: tokens, calls, input and output, and the average wait.');
        await pointAt(p, m.getByRole('heading', { name: 'AI usage', level: 1 }), 2600);
        await caption(p, 'Then a bar per day, and tables by AI provider and model, and by feature.');
        await beat(p, 2400);
        await caption(p, 'A token is a small piece of text. AI services count and charge by tokens.');
        await beat(p, 2400);
        await tw.click();
        await beat(p, 600);
        await p.getByRole('option', { name: '30 days' }).click();
        await settle(p);
        await caption(p, 'This page only reports. To switch AI off for a workspace, use the Policies page.');
        await beat(p, 2200);
        await caption(p, '');
    });
});

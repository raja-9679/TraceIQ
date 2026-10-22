import { expect, test } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/reports/business-journeys.html
// Nothing here changes data: the "New journey" form is filled in on screen
// (cases added to its list, nothing typed that saves) and then left with
// Cancel. "Save journey" and the card's Edit are never pressed.

const DIR = 'reports/business-journeys';

test('Reports › Business journeys', async ({ page, browser }) => {
    await page.goto('/business-journeys');
    await expect(page.getByRole('heading', { name: 'Business journeys', level: 1 })).toBeVisible();
    await settle(page);
    const main = page.locator('main');

    // 1. The page as a reader first meets it: the demo's one journey.
    const card = main.locator('section').filter({ has: page.getByRole('heading', { name: /^Sign in and manage todos/, level: 2 }) });
    await expect(card.getByText('Daily pass rate, 14 days')).toBeVisible();
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. The board's parts, numbered.
    const newBtn = main.getByRole('button', { name: 'New journey' });
    let clean = await annotate(page, [
        { n: 1, target: newBtn, side: 'right' },
        { n: 2, target: card, side: 'right' },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();
    await card.screenshot({ path: out(`${DIR}/card.png`) });

    // 3. The form, with three cases picked (only on screen; it is cancelled).
    await newBtn.click();
    const form = main.locator('section').filter({ has: page.getByRole('heading', { name: 'New journey', level: 2 }) });
    await expect(form).toBeVisible();
    await settle(page);
    for (const name of ['Homepage loads', 'Sign in as alice', 'Add a todo']) {
        await form.locator('li').filter({ hasText: name }).getByRole('button', { name: 'Add' }).click();
    }
    const picked = form.getByRole('heading', { name: /Cases in this journey/ }).locator('xpath=..');
    const adder = form.getByLabel('Add cases').locator('xpath=..');
    const buttons = form.getByRole('button', { name: 'Save journey' }).locator('xpath=..');
    await page.setViewportSize({ width: 1440, height: 1400 });
    await settle(page);
    clean = await annotate(page, [
        { n: 3, target: form.getByLabel('Name') },
        { n: 4, target: picked, side: 'left' },
        { n: 5, target: adder, side: 'right' },
        { n: 6, target: buttons },
    ]);
    await page.screenshot({ path: out(`${DIR}/form-annotated.png`) });
    await clean();
    // The details half of the form: from its heading down to the case lists.
    const fb = await form.boundingBox();
    const cb = await picked.boundingBox();
    if (!fb || !cb) throw new Error('form not visible');
    await page.screenshot({ path: out(`${DIR}/form-details.png`), clip: { x: fb.x, y: fb.y, width: fb.width, height: cb.y - fb.y - 10 } });
    await picked.locator('xpath=..').screenshot({ path: out(`${DIR}/form-cases.png`) });
    await form.getByRole('button', { name: 'Cancel' }).click();
    await expect(card).toBeVisible();
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/business-journeys');
        await expect(p.getByRole('heading', { name: 'Business journeys', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        await caption(p, 'Business journeys groups tests into the journeys your business cares about.', 2200);
        await caption(p, 'For example, Checkout or Sign-up: several tests that, together, are one thing a customer does.');
        await beat(p, 600);
        await caption(p, 'New journey opens a form. Editors can use it.');
        const nb = m.getByRole('button', { name: 'New journey' });
        await pointAt(p, nb, 1400);
        await nb.click();
        const f = m.locator('section').filter({ has: p.getByRole('heading', { name: 'New journey', level: 2 }) });
        await expect(f).toBeVisible();
        await settle(p);
        await caption(p, 'Give it a name, an owner, and how critical it is.');
        await pointAt(p, f.getByLabel('Name'), 1200);
        await pointAt(p, f.getByLabel('Owner'), 1000);
        await pointAt(p, f.getByLabel('Criticality'), 1200);
        await caption(p, 'Requirement refs links it to tickets, such as DEMO-101.', {
            say: 'Requirement refs links it to tickets, such as demo one-oh-one.',
        });
        await pointAt(p, f.getByLabel('Requirement refs'), 2200);
        const addCases = f.getByLabel('Add cases');
        await addCases.scrollIntoViewIfNeeded();
        await caption(p, 'Then pick the tests that make up the journey, in the order a customer meets them.');
        for (const name of ['Homepage loads', 'Sign in as alice', 'Add a todo']) {
            const b = f.locator('li').filter({ hasText: name }).getByRole('button', { name: 'Add' });
            await pointAt(p, b, 500);
            await b.click();
            await beat(p, 500);
        }
        await caption(p, 'Up and Down change the order. Remove takes a test out again.');
        await pointAt(p, f.getByRole('button', { name: 'Down' }).first(), 1400);
        await pointAt(p, f.getByRole('button', { name: 'Remove' }).first(), 1200);
        await caption(p, 'Save journey adds it to the board. We press Cancel, so nothing is saved.');
        const cancel = f.getByRole('button', { name: 'Cancel' });
        await cancel.scrollIntoViewIfNeeded();
        await pointAt(p, f.getByRole('button', { name: 'Save journey' }), 1400);
        await pointAt(p, cancel, 1000);
        await caption(p, '');
        await cancel.click();
        await settle(p);
        await caption(p, 'On the board, each journey gets a card: its health, a 14-day trend, its requirements and its traffic.', {
            say: 'On the board, each journey gets a card: its health, a fourteen-day trend, its requirements and its traffic.',
        });
        await pointAt(p, m.locator('section').filter({ has: p.getByRole('heading', { name: /^Sign in and manage todos/, level: 2 }) }), 2600);
        await caption(p, '');
    });
});

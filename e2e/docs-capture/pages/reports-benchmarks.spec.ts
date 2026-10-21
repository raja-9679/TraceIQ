import { expect, test } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/reports/benchmarks.html
// Nothing here changes data: the "New benchmark" form is filled in on screen
// and left with Cancel. "Start benchmark" (which would start runs) is never
// pressed.

const DIR = 'reports/benchmarks';
const SITES = 'Live | https://www.example.com\nStaging | https://staging.example.com';

test('Reports › Benchmarks', async ({ page, browser }) => {
    await page.goto('/benchmarks');
    await expect(page.getByRole('heading', { name: 'Benchmarks', level: 1 })).toBeVisible();
    await settle(page);
    const main = page.locator('main');

    // 1. The page as a reader first meets it (the demo has no benchmarks).
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    const newBtn = main.getByRole('button', { name: 'New benchmark' });
    const empty = main.getByText('No benchmarks yet', { exact: true }).locator('xpath=..');
    let clean = await annotate(page, [
        { n: 1, target: newBtn, side: 'right' },
        { n: 2, target: empty },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. The form, filled in on screen (cancelled afterwards).
    await newBtn.click();
    const form = main.locator('section').filter({ has: page.getByRole('heading', { name: 'New benchmark', level: 2 }) });
    await expect(form).toBeVisible();
    await settle(page);
    await form.getByLabel('Name').fill('Live vs staging');
    const suite = form.getByLabel('Suite');
    await suite.selectOption({ index: 1 });
    await form.getByLabel(/Sites \(2 to 10\)/).fill(SITES);
    await expect(form.getByText('2 site(s)')).toBeVisible();
    const sites = form.getByLabel(/Sites \(2 to 10\)/).locator('xpath=..');
    const buttons = form.getByRole('button', { name: 'Start benchmark' }).locator('xpath=..');
    clean = await annotate(page, [
        { n: 3, target: form.getByLabel('Name') },
        { n: 4, target: sites },
        { n: 5, target: buttons },
    ]);
    await page.screenshot({ path: out(`${DIR}/form-annotated.png`) });
    await clean();
    await form.screenshot({ path: out(`${DIR}/form.png`) });
    await form.getByRole('button', { name: 'Cancel' }).click();
    await expect(main.getByText('No benchmarks yet', { exact: true })).toBeVisible();

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/benchmarks');
        await expect(p.getByRole('heading', { name: 'Benchmarks', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        await caption(p, 'Benchmarks runs the same tests against several sites and compares the results.', 2200);
        await caption(p, 'For example, your live site against staging, or before and after a change.');
        await beat(p, 600);
        const nb = m.getByRole('button', { name: 'New benchmark' });
        await caption(p, 'New benchmark opens a form. Editors can use it.');
        await pointAt(p, nb, 1400);
        await nb.click();
        const f = m.locator('section').filter({ has: p.getByRole('heading', { name: 'New benchmark', level: 2 }) });
        await expect(f).toBeVisible();
        await settle(p);
        await caption(p, 'Give it a name, and choose the suite of tests to run.');
        await f.getByLabel('Name').pressSequentially('Live vs staging', { delay: 50 });
        await pointAt(p, f.getByLabel('Suite'), 600);
        await f.getByLabel('Suite').selectOption({ index: 1 });
        await beat(p, 800);
        await caption(p, 'Then list the sites, one per line: a short label, a bar, and the address.');
        const t = f.getByLabel(/Sites \(2 to 10\)/);
        await t.click();
        await t.pressSequentially(SITES, { delay: 25 });
        await beat(p, 600);
        await caption(p, 'You need between 2 and 10 sites. The count underneath checks what you typed.', {
            say: 'You need between two and ten sites. The count underneath checks what you typed.',
        });
        await pointAt(p, f.getByText('2 site(s)'), 2000);
        await caption(p, 'Start benchmark would start one run per site. We press Cancel instead.');
        await pointAt(p, f.getByRole('button', { name: 'Start benchmark' }), 1400);
        const cancel = f.getByRole('button', { name: 'Cancel' });
        await pointAt(p, cancel, 1000);
        await caption(p, '');
        await cancel.click();
        await settle(p);
        await caption(p, 'Each benchmark then shows a table per site, and every test side by side.');
        await pointAt(p, m.getByText('No benchmarks yet', { exact: true }), 2600);
        await caption(p, '');
    });
});

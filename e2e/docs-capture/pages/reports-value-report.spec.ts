import { expect, test } from '@playwright/test';
import { annotate, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/reports/value-report.html
// Nothing here changes data: the scope and date pickers and "Update report"
// only re-read the report. "Print" is never pressed.

const DIR = 'reports/value-report';

test('Reports › Value report', async ({ page, browser }) => {
    await page.goto('/reports/value');
    await expect(page.getByRole('heading', { name: 'Value report', level: 1 })).toBeVisible();
    await settle(page);
    const main = page.locator('main');
    await expect(main.locator('section[aria-label="Time to diagnosis"]')).toBeVisible();

    // 1. The page as a reader first meets it (scope: this project).
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    const controls = main.getByLabel('Scope').locator('xpath=ancestor::form[1]');
    const scopeLine = main.getByText(/validation runs excluded/);
    const ttd = main.locator('section[aria-label="Time to diagnosis"]');
    const acc = main.locator('section[aria-label="AI failure-analysis accuracy"]');
    const saved = main.locator('section[aria-label="CI time saved by targeted runs"]');
    const props = main.locator('section[aria-label="Proposal outcomes"]');
    const links = main.locator('section[aria-label="Related reports"]');
    await page.setViewportSize({ width: 1440, height: 1500 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: controls },
        { n: 2, target: scopeLine },
        { n: 3, target: ttd },
        { n: 4, target: acc },
        { n: 5, target: saved },
        { n: 6, target: props },
        { n: 7, target: links },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups used next to each explanation.
    await controls.screenshot({ path: out(`${DIR}/controls.png`) });
    await ttd.screenshot({ path: out(`${DIR}/time-to-diagnosis.png`) });
    await acc.screenshot({ path: out(`${DIR}/accuracy.png`) });
    await saved.screenshot({ path: out(`${DIR}/time-saved.png`) });
    await props.screenshot({ path: out(`${DIR}/proposals.png`) });
    await links.screenshot({ path: out(`${DIR}/links.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/reports/value');
        await expect(p.getByRole('heading', { name: 'Value report', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        await caption(p, 'The Value report shows what TraceIQ did for you over a period, in four numbers.', 2000);
        await caption(p, 'Each card says whether its number is measured, or an estimate.');
        const t = m.locator('section[aria-label="Time to diagnosis"]');
        await pointAt(p, t.getByText('Measured', { exact: true }), 1400);
        await pointAt(p, m.locator('section[aria-label="CI time saved by targeted runs"]').getByText('Estimate', { exact: true }), 1400);
        await caption(p, 'Time to diagnosis: how long a new failure waited before someone acted on it.');
        await pointAt(p, t.locator('span.text-\\[28px\\]'), 2400);
        await caption(p, 'Under every number: how many cases it is based on, what it means, and where the data comes from.');
        await pointAt(p, t.getByText('Sample:'), 1300);
        await pointAt(p, t.getByText('Definition.'), 1500);
        await caption(p, 'A card with too little data says so, instead of showing a zero.');
        await pointAt(p, m.locator('section[aria-label="AI failure-analysis accuracy"]').getByText(/No top hypothesis/), 2400);
        await caption(p, 'Scope switches between this project and the whole workspace.');
        const scope = m.getByLabel('Scope');
        await pointAt(p, scope, 1000);
        await scope.selectOption('workspace');
        await settle(p);
        await caption(p, 'In a workspace, choose which one. Then pick the dates and press Update report.');
        const ws = m.locator('#value-ws');
        await pointAt(p, ws, 600);
        await ws.selectOption({ label: 'Demo workspace' });
        await settle(p);
        await pointAt(p, m.getByLabel('From'), 800);
        await pointAt(p, m.getByRole('button', { name: 'Update report' }), 1200);
        await caption(p, 'The line above the cards says what was covered: here, both demo projects.');
        await pointAt(p, m.getByText(/projects you can read ·/), 2400);
        const l = m.locator('section[aria-label="Related reports"]');
        await l.scrollIntoViewIfNeeded();
        await caption(p, 'Reported elsewhere links to related numbers kept on other pages.');
        await pointAt(p, l.getByRole('link').first(), 2400);
        await caption(p, '');
    });
});

import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/setup/sizing-calculator.html
// The calculator stores nothing: changing a number only recalculates.

const DIR = 'setup/sizing-calculator';
const card = (page: Page, id: string) => page.locator(`section[aria-labelledby="${id}"]`);

async function open(page: Page) {
    await page.goto('/sizing');
    await expect(page.getByRole('heading', { name: /Sizing calculator/, level: 1 })).toBeVisible();
    await expect(page.getByText('Browser workers', { exact: true })).toBeVisible();
    await settle(page);
}

test('Setup › Sizing calculator', async ({ page, browser }) => {
    await open(page);
    const main = page.locator('main');

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered (a tall window: the page is long).
    await page.setViewportSize({ width: 1440, height: 2600 });
    await settle(page);
    const tall = await page.evaluate(() => document.documentElement.scrollHeight);
    await page.setViewportSize({ width: 1440, height: tall });
    await settle(page);
    const stats = main.getByText('Browser workers', { exact: true }).locator('xpath=ancestor::div[contains(@class,"grid")][1]');
    const clean = await annotate(page, [
        { n: 1, target: card(page, 'sizing-workload'), side: 'left' },
        { n: 2, target: card(page, 'sizing-storage'), side: 'left' },
        { n: 3, target: stats },
        { n: 4, target: card(page, 'sizing-components') },
        { n: 5, target: card(page, 'sizing-deploy') },
        { n: 6, target: card(page, 'sizing-formulas') },
        { n: 7, target: card(page, 'sizing-assumptions') },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await card(page, 'sizing-workload').screenshot({ path: out(`${DIR}/workload.png`) });
    await card(page, 'sizing-storage').screenshot({ path: out(`${DIR}/storage.png`) });
    await stats.screenshot({ path: out(`${DIR}/results.png`) });
    await card(page, 'sizing-components').screenshot({ path: out(`${DIR}/components.png`) });
    await card(page, 'sizing-deploy').screenshot({ path: out(`${DIR}/deploy.png`) });
    await card(page, 'sizing-formulas').screenshot({ path: out(`${DIR}/formulas.png`) });
    await card(page, 'sizing-assumptions').screenshot({ path: out(`${DIR}/assumptions.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await open(p);
        const m = p.locator('main');
        await caption(p, 'The sizing calculator estimates the machines a TraceIQ installation needs.', 1600);
        await caption(p, 'On the left, describe the work: how many tests run a day, and how long each one takes.');
        await pointAt(p, p.getByLabel('Executions per day'), 1400);
        await pointAt(p, p.getByLabel('Average test duration'), 1400);
        await caption(p, 'On the right, the answer: how many browser workers, and how much storage.');
        await pointAt(p, m.getByText('Browser workers', { exact: true }), 1400);
        await pointAt(p, m.getByText('Object store', { exact: true }), 1400);
        await caption(p, 'Change a number and the estimate is worked out again straight away.');
        const exec = p.getByLabel('Executions per day');
        await exec.click();
        await exec.fill('8000');
        await settle(p);
        await beat(p, 600);
        await pointAt(p, m.getByText('Browser workers', { exact: true }), 2200);
        await caption(p, 'Nothing is saved. It is a calculator, and it reads none of your data.');
        await beat(p, 600);
        const comp = card(p, 'sizing-components');
        await caption(p, 'Components splits the total into the parts of TraceIQ, with how many copies of each to run.');
        await comp.scrollIntoViewIfNeeded();
        await pointAt(p, comp.getByText('execution-worker'), 2400);
        const dep = card(p, 'sizing-deploy');
        await caption(p, 'Deploy it gives ready-made settings for whoever installs TraceIQ.');
        await dep.scrollIntoViewIfNeeded();
        await pointAt(p, dep.getByText('Compose (.env)'), 2200);
        const formulas = card(p, 'sizing-formulas');
        await caption(p, 'Further down, every number shows the formula behind it, with your own figures put in.');
        await formulas.scrollIntoViewIfNeeded();
        await pointAt(p, formulas.getByText('Browser workers (peak concurrency)'), 2600);
        const assumptions = card(p, 'sizing-assumptions');
        await caption(p, 'And the assumptions each figure rests on, with where it comes from.');
        await assumptions.scrollIntoViewIfNeeded();
        await pointAt(p, assumptions, 2400);
        await caption(p, '');
    });
});

import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/tests/frontend-quality.html
// The demo's web project (the sidebar's active project) has a crawl with two
// runs, a month of page-budget samples and failed steps for Selector health.
// Nothing here changes data: the "New crawl" form is opened and closed with
// Cancel, "Run now" / "Delete" are never pressed, nothing is loaded from
// Storybook, and the sidebar's project switcher is not touched.

const DIR = 'tests/frontend-quality';
const CRAWL = 'example.com link check (demo)';

async function open(p: Page) {
    await p.goto('/frontend-quality');
    await expect(p.getByRole('heading', { name: 'Frontend quality', level: 1 })).toBeVisible();
    // The first crawl opens by itself, on its newest run.
    await expect(p.locator('main').getByRole('article', { name: `Crawl ${CRAWL}` }).locator('table')).toBeVisible();
    await settle(p);
}

test('Tests › Frontend quality', async ({ page, browser }) => {
    await open(page);
    const main = page.locator('main');
    const tabs = main.getByRole('tablist', { name: 'Frontend quality views' });
    const panel = main.getByRole('tabpanel');
    const card = panel.getByRole('article', { name: `Crawl ${CRAWL}` });

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    await page.setViewportSize({ width: 1440, height: 1500 });
    await settle(page);
    const intro = panel.getByRole('button', { name: 'New crawl' }).locator('xpath=..');
    const list = panel.getByRole('heading', { name: 'Crawls' }).locator('xpath=ancestor::div[contains(@class,"rounded-3xl")][1]');
    const top = card.getByRole('heading', { name: CRAWL, level: 2 }).locator('xpath=ancestor::div[contains(@class,"justify-between")][1]');
    const runPick = card.getByLabel('Run', { exact: true }).locator('xpath=..');
    const problems = card.locator('table').locator('xpath=ancestor::div[contains(@class,"space-y-3")][1]');
    const clean = await annotate(page, [
        { n: 1, target: tabs },
        { n: 2, target: intro },
        { n: 3, target: list },
        { n: 4, target: top, side: 'right' },
        { n: 5, target: runPick, side: 'right' },
        { n: 6, target: problems, side: 'right' },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();
    await card.screenshot({ path: out(`${DIR}/crawl.png`) });

    // 3. Close-ups: the crawl form, then each tab.
    await panel.getByRole('button', { name: 'New crawl' }).click();
    const formCard = panel.getByRole('article', { name: 'New crawl' });
    await expect(formCard.getByLabel('Start URL')).toBeVisible();
    await settle(page);
    await formCard.screenshot({ path: out(`${DIR}/crawl-form.png`) });
    await formCard.getByRole('button', { name: 'Cancel' }).click();
    await expect(formCard).toBeHidden();

    for (const [name, file, wait] of [
        ['Page budgets', 'budgets', panel.getByRole('img', { name: /lcp_ms over the last/ })],
        ['Selector health', 'selectors', panel.getByRole('heading', { name: 'Selectors, worst first' })],
        ['Storybook', 'storybook', panel.getByLabel('Storybook URL')],
    ] as const) {
        await tabs.getByRole('tab', { name }).click();
        await expect(wait).toBeVisible();
        await settle(page);
        await panel.screenshot({ path: out(`${DIR}/${file}.png`) });
    }
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await open(p);
        const m = p.locator('main');
        const t = m.getByRole('tablist', { name: 'Frontend quality views' });
        const pn = m.getByRole('tabpanel');
        await caption(p, 'Frontend quality checks how well your website itself is built, beyond single tests.', 2000);
        await caption(p, 'It has four tabs. The first crawls your site and checks every link and file.');
        await pointAt(p, t.getByRole('tab', { name: 'Link & asset crawler' }), 1800);
        await caption(p, 'Saved crawls are listed on the left. The selected one shows its newest run and the problems it found.');
        await pointAt(p, pn.getByRole('article', { name: `Crawl ${CRAWL}` }).locator('table'), 2400);
        await caption(p, 'New crawl sets one up: where to start, how deep to go, and how many pages.');
        await pointAt(p, pn.getByRole('button', { name: 'New crawl' }), 900);
        await pn.getByRole('button', { name: 'New crawl' }).click();
        await beat(p, 900);
        const f = pn.getByRole('article', { name: 'New crawl' });
        await pointAt(p, f.getByLabel('Start URL'), 1300);
        await pointAt(p, f.getByLabel('Max depth'), 1000);
        await pointAt(p, f.getByLabel('Max pages'), 1000);
        await caption(p, 'Only this project’s own sites can be crawled. Cancel closes the form.');
        await pointAt(p, f.getByRole('button', { name: 'Cancel' }), 1200);
        await f.getByRole('button', { name: 'Cancel' }).click();
        await caption(p, 'Page budgets tracks page speed and size, measured by a step in your tests.');
        await t.getByRole('tab', { name: 'Page budgets' }).click();
        await pointAt(p, t.getByRole('tab', { name: 'Page budgets' }), 2200);
        await caption(p, 'Selector health lists the page elements that tests keep failing to find.');
        await t.getByRole('tab', { name: 'Selector health' }).click();
        await pointAt(p, t.getByRole('tab', { name: 'Selector health' }), 2200);
        await caption(p, 'Storybook turns each story of a component library into a test.');
        await t.getByRole('tab', { name: 'Storybook' }).click();
        await pointAt(p, t.getByRole('tab', { name: 'Storybook' }), 2200);
        await caption(p, '');
    });
});

import { expect, test } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/tests/frontend-quality.html
// The demo has no crawls, page-budget samples or selector trouble yet, so the
// tabs show their empty states. Nothing here changes data: the "New crawl"
// form is opened and closed with Cancel, nothing is loaded from Storybook,
// and the project picker is not touched.

const DIR = 'tests/frontend-quality';

test('Tests › Frontend quality', async ({ page, browser }) => {
    await page.goto('/frontend-quality');
    await expect(page.getByRole('heading', { name: 'Frontend Quality', level: 1 })).toBeVisible();
    await settle(page);
    const main = page.locator('main');
    const tabs = main.getByRole('tablist', { name: 'Frontend quality views' });
    await expect(main.getByText('No crawls yet.')).toBeVisible();

    // 1. The whole page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    const picker = main.getByRole('combobox', { name: 'Project' });
    const panel = main.getByRole('tabpanel');
    const clean = await annotate(page, [
        { n: 1, target: picker, side: 'right' },
        { n: 2, target: tabs },
        { n: 3, target: panel.locator('xpath=./div/div[1]') },
        { n: 4, target: main.getByText('No crawls yet.') },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups: the crawl form, then each tab.
    await main.getByRole('button', { name: 'New crawl' }).click();
    const form = panel.locator('form');
    await expect(form.getByLabel('Start URL')).toBeVisible();
    await beat(page, 400);
    await form.screenshot({ path: out(`${DIR}/crawl-form.png`) });
    await form.getByRole('button', { name: 'Cancel' }).click();
    await expect(form).toBeHidden();

    for (const [name, file, wait] of [
        ['Page budgets', 'budgets', 'No page-budget samples yet.'],
        ['Selector health', 'selectors', 'No selector trouble in this window.'],
        ['Storybook', 'storybook', 'Storybook URL'],
    ] as const) {
        await tabs.getByRole('tab', { name }).click();
        await expect(main.getByRole('tabpanel').getByText(wait).first()).toBeVisible();
        await settle(page);
        await main.getByRole('tabpanel').screenshot({ path: out(`${DIR}/${file}.png`) });
    }
    await tabs.getByRole('tab', { name: 'Link & asset crawler' }).click();

    // 4. A short walkthrough video.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await p.goto('/frontend-quality');
        await expect(p.getByRole('heading', { name: 'Frontend Quality', level: 1 })).toBeVisible();
        await settle(p);
        const m = p.locator('main');
        const t = m.getByRole('tablist', { name: 'Frontend quality views' });
        await caption(p, 'Frontend quality checks how well your website itself is built, beyond single tests.', 2000);
        await caption(p, 'It has four tabs. The first crawls your site and checks every link and file.');
        await pointAt(p, t.getByRole('tab', { name: 'Link & asset crawler' }), 1800);
        await caption(p, 'New crawl sets one up: where to start, how deep to go, and how many pages.');
        await pointAt(p, m.getByRole('button', { name: 'New crawl' }), 900);
        await m.getByRole('button', { name: 'New crawl' }).click();
        await beat(p, 900);
        const f = m.getByRole('tabpanel').locator('form');
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
        await caption(p, 'The demo has no results yet, so each tab shows its empty state.', 1800);
        await caption(p, '');
    });
});

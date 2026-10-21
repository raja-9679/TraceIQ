import { expect, test, type Page } from '@playwright/test';
import { annotate, beat, caption, out, pointAt, record, settle } from '../capture';

// Guide page: frontend/public/docs/security/evidence-packs.html
// Nothing here changes data: "Build evidence pack" and "Verify pack" are
// never pressed and no file is chosen. The demo workspace has no packs.

const DIR = 'security/evidence-packs';

async function open(p: Page) {
    await p.goto('/compliance/evidence-packs');
    await expect(p.getByRole('heading', { name: 'Evidence packs', level: 1 })).toBeVisible();
    await settle(p);
    await expect(p.getByText('No packs yet', { exact: true })).toBeVisible();
}

const card = (p: Page, id: string) => p.locator(`section[aria-labelledby="${id}"]`);

test('Security › Evidence packs', async ({ page, browser }) => {
    await open(page);
    const main = page.locator('main');

    // 1. The page as a reader first meets it.
    await page.screenshot({ path: out(`${DIR}/page.png`) });

    // 2. Its parts, numbered.
    const picker = main.getByLabel('Workspace').locator('xpath=..');
    await page.setViewportSize({ width: 1440, height: 1240 });
    await settle(page);
    const clean = await annotate(page, [
        { n: 1, target: picker, side: 'right' },
        { n: 2, target: card(page, 'pack-build') },
        { n: 3, target: card(page, 'pack-list') },
        { n: 4, target: card(page, 'pack-verify') },
    ]);
    await page.screenshot({ path: out(`${DIR}/page-annotated.png`) });
    await clean();

    // 3. Close-ups.
    await card(page, 'pack-build').screenshot({ path: out(`${DIR}/build.png`) });
    await card(page, 'pack-list').screenshot({ path: out(`${DIR}/packs.png`) });
    await card(page, 'pack-verify').screenshot({ path: out(`${DIR}/verify.png`) });
    await page.setViewportSize({ width: 1440, height: 900 });

    // 4. A short walkthrough video, with captions saying what is on screen.
    await record(browser, `${DIR}/walkthrough.webm`, async (p) => {
        await open(p);
        const m = p.locator('main');
        await caption(p, 'An evidence pack is one zip file with the records an auditor asks for, for one workspace and one period.', 2000);
        await caption(p, 'It holds the audit log, access reviews, controls, data policies, security findings and restore drills.');
        await beat(p, 1400);
        await caption(p, 'Choose the first and last day. The last 90 days are filled in for you.', {
            say: 'Choose the first and last day. The last ninety days are filled in for you.',
        });
        await pointAt(p, m.getByLabel('From'), 1200);
        await pointAt(p, m.getByLabel('To'), 1200);
        await caption(p, 'Build evidence pack starts building it in the background.');
        await pointAt(p, m.getByRole('button', { name: 'Build evidence pack' }), 2200);
        const list = card(p, 'pack-list');
        await caption(p, 'Finished packs are listed here, ready to download. The demo workspace has none yet.');
        await list.scrollIntoViewIfNeeded();
        await pointAt(p, list.getByText('No packs yet', { exact: true }), 2400);
        const v = card(p, 'pack-verify');
        await caption(p, 'Every pack carries a signed list of its files. Verify a pack checks that nothing was changed.');
        await v.scrollIntoViewIfNeeded();
        await pointAt(p, v.getByText('Pack (.zip)'), 2400);
        await caption(p, 'So an auditor can trust the files really came from TraceIQ, exactly as they were made.');
        await pointAt(p, v.getByRole('button', { name: 'Verify pack' }), 2200);
        await caption(p, '');
    });
});

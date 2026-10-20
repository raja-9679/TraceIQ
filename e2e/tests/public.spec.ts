import { expect, test } from '@playwright/test';
import { settle, watch } from './support/health';

// The pages a signed-out visitor can reach. Run without a session.

const PUBLIC_PAGES = [
    { path: '/login', heading: 'Welcome Back' },
    { path: '/signup', heading: 'Create Account' },
];

for (const p of PUBLIC_PAGES) {
    test(`public page ${p.path} renders cleanly`, async ({ page, baseURL }) => {
        const problems = watch(page, baseURL!);
        await page.goto(p.path);
        await expect(page.getByRole('heading', { level: 1, name: p.heading })).toBeVisible();
        await settle(problems);
        expect(problems.errors, `problems on ${p.path}`).toEqual([]);
    });
}

test('an app page without a session sends you to /login', async ({ page }) => {
    await page.goto('/suites');
    await expect(page).toHaveURL(/\/login/);
});

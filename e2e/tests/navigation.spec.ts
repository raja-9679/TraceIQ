import { expect, test } from '@playwright/test';
import { sidebarDestinations } from './support/nav';
import { settle, watch } from './support/health';

// (5) Every sidebar destination opens with no uncaught exception, no console
// error from the app, no 5xx, and its main heading rendered. The list comes
// from frontend/src/components/layout/nav.ts, so new pages are covered
// automatically.

const destinations = sidebarDestinations();

test('the sidebar definition parsed into a plausible number of destinations', () => {
    // Guards the text parser in support/nav.ts: if nav.ts is reformatted and
    // the regex stops matching, this fails instead of silently testing nothing.
    expect(destinations.length).toBeGreaterThan(40);
    expect(destinations.map((d) => d.href)).toEqual(expect.arrayContaining(['/overview', '/suites', '/runs', '/settings']));
});

test.describe('sidebar destinations', () => {
    test.describe.configure({ mode: 'parallel' });

    for (const d of destinations) {
        test(`${d.section}: ${d.name} (${d.href})`, async ({ page, baseURL }, info) => {
            const problems = watch(page, baseURL!);
            await page.goto(d.href);
            await expect(page, 'a signed-in user is not bounced to login').not.toHaveURL(/\/login/);
            await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
            await settle(problems);
            if (problems.clientErrors.length) {
                await info.attach('api-4xx.txt', { body: problems.clientErrors.join('\n'), contentType: 'text/plain' });
            }
            expect(problems.errors, `problems on ${d.href}`).toEqual([]);
        });
    }
});

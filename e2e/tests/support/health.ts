import { expect, type ConsoleMessage, type Page } from '@playwright/test';

// Collects everything that means "this page is broken" while a test drives it:
// uncaught exceptions, console errors from the app, and 5xx responses.

// Console errors that are NOT the app's own, each with the reason it is
// tolerated. Keep this list short and specific — a broad pattern here hides
// real regressions.
const ALLOWED_CONSOLE: { pattern: RegExp; why: string }[] = [
    {
        // Chromium logs every non-2xx response as a console error. That is the
        // browser describing the network, not the app failing: 5xx responses
        // are checked separately (below), and 4xx are expected on pages that
        // probe optional features (e.g. a 404 for "no config saved yet").
        pattern: /^Failed to load resource: the server responded with a status of 4\d\d/,
        why: 'browser network log for a 4xx; 5xx is asserted from the response itself',
    },
    {
        // The suite deliberately runs over plain http on a non-localhost name
        // (http://frontend) — the same as a self-hoster on http://<LAN host>,
        // which SELF_HOSTING.md supports. nginx always sends COOP; Chromium
        // ignores it off a secure origin and says so. A browser notice about
        // the origin, not an app error.
        pattern: /^The Cross-Origin-Opener-Policy header has been ignored, because the URL's origin was untrustworthy/,
        why: 'plain-http origin; COOP only applies on https/localhost',
    },
];

export type Problems = {
    errors: string[];
    /** 4xx responses from /api — not failures, but attached to the report. */
    clientErrors: string[];
    inflight: () => number;
    lastActivity: () => number;
};

const isApp = (url: string, origin: string) => url.startsWith(origin);

export function watch(page: Page, origin: string): Problems {
    const errors: string[] = [];
    const clientErrors: string[] = [];
    let inflight = 0;
    let lastActivity = Date.now();

    page.on('pageerror', (err) => errors.push(`pageerror: ${err.message}`));
    page.on('console', (msg: ConsoleMessage) => {
        if (msg.type() !== 'error') return;
        const text = msg.text();
        if (ALLOWED_CONSOLE.some((a) => a.pattern.test(text))) return;
        const loc = msg.location();
        errors.push(`console.error: ${text}${loc.url ? ` (${loc.url}:${loc.lineNumber})` : ''}`);
    });
    page.on('request', (req) => {
        if (!isApp(req.url(), origin)) return;
        inflight += 1;
        lastActivity = Date.now();
    });
    const done = () => {
        inflight = Math.max(0, inflight - 1);
        lastActivity = Date.now();
    };
    page.on('requestfinished', (req) => { if (isApp(req.url(), origin)) done(); });
    page.on('requestfailed', (req) => { if (isApp(req.url(), origin)) done(); });
    page.on('response', (res) => {
        const status = res.status();
        const label = `${status} ${res.request().method()} ${res.url().replace(origin, '')}`;
        if (status >= 500) errors.push(`HTTP ${label}`);
        else if (status >= 400 && res.url().includes('/api/')) clientErrors.push(label);
    });

    return { errors, clientErrors, inflight: () => inflight, lastActivity: () => lastActivity };
}

/**
 * Wait until the page has made no same-origin request for `quietMs`. Pages
 * fan out several queries after the heading paints; errors usually surface
 * when those land. Bounded — a page that polls continuously still settles
 * between polls, and one that never does fails with a clear message.
 */
export async function settle(problems: Problems, quietMs = 750, timeout = 20_000) {
    await expect
        .poll(() => problems.inflight() === 0 && Date.now() - problems.lastActivity() >= quietMs, {
            message: 'page never went quiet (requests kept firing)',
            timeout,
            intervals: [100, 250, 250, 500],
        })
        .toBe(true);
}

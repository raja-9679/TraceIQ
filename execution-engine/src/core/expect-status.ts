/**
 * `goto` params.expect_status: the HTTP status the page itself must answer
 * with. Playwright's page.goto() does not fail on a 404/500 — the error page
 * renders like any other — so without this a news site's removed article
 * passes a "page opens" test.
 *
 * Accepted forms: 200 · "200" · "200,301" · "2xx" · [200, "3xx"].
 * The status checked is the FINAL response after redirects; the redirect
 * chain is named in the failure so a 301 → 404 is readable.
 */

export type StatusMatcher = { label: string; matches: (status: number) => boolean };

/** null = no expectation (unset/blank). Throws on a malformed spec. */
export function parseExpectedStatus(spec: unknown): StatusMatcher | null {
    if (spec === undefined || spec === null) return null;
    const parts: string[] = (Array.isArray(spec) ? spec : String(spec).split(','))
        .map((p) => String(p).trim().toLowerCase())
        .filter((p) => p !== '');
    if (parts.length === 0) return null;

    const tests = parts.map((p) => {
        if (/^[1-5]xx$/.test(p)) {
            const cls = Number(p[0]);
            return (s: number) => Math.floor(s / 100) === cls;
        }
        if (/^[1-5]\d\d$/.test(p)) {
            const want = Number(p);
            return (s: number) => s === want;
        }
        throw new Error(`expect_status: "${p}" is not a status code (use e.g. 200, 404, 2xx or "200,301")`);
    });
    return { label: parts.join(', '), matches: (s) => tests.some((t) => t(s)) };
}

/** Minimal slice of Playwright's Response/Request that the check reads. */
export interface NavResponse {
    status(): number;
    url(): string;
    request(): { redirectedFrom(): { url(): string; redirectedFrom(): any } | null };
}

/** Status codes of the redirect hops that led to `response`, oldest first. */
function redirectChain(response: NavResponse): string[] {
    const urls: string[] = [];
    let req = response.request().redirectedFrom();
    while (req && urls.length < 20) {
        urls.unshift(req.url());
        req = req.redirectedFrom();
    }
    return urls;
}

/**
 * Error message when the navigation's status does not match, else null.
 * A null response (same-document navigation, about:blank) cannot be checked
 * and fails — an expectation that silently passes is worse than none.
 */
export function navigationStatusError(response: NavResponse | null, matcher: StatusMatcher): string | null {
    if (!response) {
        return `Expected HTTP status ${matcher.label}, but the navigation produced no HTTP response`;
    }
    const status = response.status();
    if (matcher.matches(status)) return null;
    const chain = redirectChain(response);
    const via = chain.length ? ` (redirected from ${chain.join(' → ')})` : '';
    return `Expected HTTP status ${matcher.label}, got ${status} for ${response.url()}${via}`;
}

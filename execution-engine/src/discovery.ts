import { Browser } from 'playwright';

/**
 * Mode-2 (URL-only) app-surface discovery.
 *
 * Given a base URL — no source access — BFS-crawl same-origin pages and
 * extract the interactable surface: forms (with inputs), buttons, and
 * internal links. The result feeds `crawl_app_surface` so an agent can
 * propose smoke tests for an app it cannot read the code of.
 */

export interface DiscoveryRequest {
    base_url: string;
    max_pages?: number;
    // Project auth session, injected by the backend when one exists, so the
    // crawl can see pages behind login.
    storage_state?: any;
}

export interface PageSurface {
    url: string;
    title: string;
    forms: Array<{
        action: string | null;
        method: string;
        inputs: Array<{ name: string | null; type: string; placeholder: string | null; required: boolean }>;
        submit_text: string | null;
    }>;
    buttons: Array<{ text: string; selector_hint: string | null }>;
    links: Array<{ text: string; href: string }>;
}

export interface DiscoveryResult {
    status: 'complete' | 'error';
    base_url: string;
    pages: PageSurface[];
    pages_visited: number;
    pages_skipped: number;
    error?: string;
}

const HARD_MAX_PAGES = 50;
const PAGE_TIMEOUT_MS = 20_000;

// Pages that are near-certainly not worth crawling.
const SKIP_RE = /\.(png|jpe?g|gif|svg|css|js|ico|pdf|zip|mp4|webm|woff2?)(\?|$)|\/logout|\/signout/i;

function normalizeUrl(raw: string, origin: string): string | null {
    try {
        const u = new URL(raw, origin);
        if (u.origin !== origin) return null;
        u.hash = '';
        return u.toString();
    } catch {
        return null;
    }
}

export async function crawlSurface(browser: Browser, req: DiscoveryRequest): Promise<DiscoveryResult> {
    const maxPages = Math.min(Math.max(1, req.max_pages || 10), HARD_MAX_PAGES);
    const origin = new URL(req.base_url).origin;

    const contextOptions: any = {};
    if (req.storage_state) contextOptions.storageState = req.storage_state;
    const context = await browser.newContext(contextOptions);

    const queue: string[] = [req.base_url];
    const seen = new Set<string>([req.base_url]);
    const pages: PageSurface[] = [];
    let skipped = 0;

    try {
        const page = await context.newPage();
        page.setDefaultTimeout(PAGE_TIMEOUT_MS);

        while (queue.length > 0 && pages.length < maxPages) {
            const url = queue.shift()!;
            try {
                await page.goto(url, { waitUntil: 'domcontentloaded', timeout: PAGE_TIMEOUT_MS });
                // Give SPAs a beat to render their initial view.
                await page.waitForTimeout(750);

                const surface: PageSurface = await page.evaluate(() => {
                    const visibleText = (el: Element) => (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 80);
                    const forms = Array.from(document.querySelectorAll('form')).slice(0, 20).map(f => ({
                        action: f.getAttribute('action'),
                        method: (f.getAttribute('method') || 'GET').toUpperCase(),
                        inputs: Array.from(f.querySelectorAll('input, textarea, select')).slice(0, 25).map(i => ({
                            name: i.getAttribute('name'),
                            type: i.getAttribute('type') || i.tagName.toLowerCase(),
                            placeholder: i.getAttribute('placeholder'),
                            required: i.hasAttribute('required'),
                        })),
                        submit_text: (() => {
                            const btn = f.querySelector('button[type=submit], input[type=submit], button:not([type])');
                            return btn ? visibleText(btn) || (btn as HTMLInputElement).value || null : null;
                        })(),
                    }));
                    const buttons = Array.from(document.querySelectorAll('button, [role=button], input[type=button]'))
                        .filter(b => !b.closest('form'))
                        .slice(0, 40)
                        .map(b => ({
                            text: visibleText(b),
                            selector_hint: b.getAttribute('data-testid') ? `[data-testid="${b.getAttribute('data-testid')}"]`
                                : b.id ? `#${b.id}` : null,
                        }))
                        .filter(b => b.text || b.selector_hint);
                    const links = Array.from(document.querySelectorAll('a[href]')).slice(0, 100).map(a => ({
                        text: visibleText(a),
                        href: a.getAttribute('href') || '',
                    }));
                    return { url: location.href, title: document.title, forms, buttons, links };
                });

                pages.push(surface);
                console.log(`[Discovery] ${pages.length}/${maxPages}: ${url} — ${surface.forms.length} form(s), ${surface.buttons.length} button(s)`);

                for (const link of surface.links) {
                    const normalized = normalizeUrl(link.href, origin);
                    if (!normalized || seen.has(normalized) || SKIP_RE.test(normalized)) continue;
                    seen.add(normalized);
                    queue.push(normalized);
                }
            } catch (pageErr: any) {
                skipped++;
                console.warn(`[Discovery] Skipping ${url}: ${pageErr.message}`);
            }
        }

        return {
            status: 'complete',
            base_url: req.base_url,
            pages,
            pages_visited: pages.length,
            pages_skipped: skipped,
        };
    } catch (err: any) {
        return {
            status: 'error',
            base_url: req.base_url,
            pages,
            pages_visited: pages.length,
            pages_skipped: skipped,
            error: err.message,
        };
    } finally {
        await context.close().catch(() => { });
    }
}

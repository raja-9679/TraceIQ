// Web-vitals capture — reads performance metrics from the page a test just
// exercised so every UI run doubles as a performance check.
//
// Collected once per test, right before teardown, for the page's current
// document: TTFB / DCL / load come from Navigation Timing (all browsers);
// FCP from the paint timeline; LCP and CLS from buffered PerformanceObserver
// entries (Chromium only — null elsewhere). Never throws: a page that closed
// early or a browser without an API simply yields nulls.

import { Page } from 'playwright';

export interface WebVitals {
    url: string;
    ttfb_ms: number | null;
    fcp_ms: number | null;
    lcp_ms: number | null;
    cls: number | null;
    dom_content_loaded_ms: number | null;
    load_ms: number | null;
}

const COLLECT_TIMEOUT_MS = 3_000;

export async function collectWebVitals(page: Page): Promise<WebVitals | null> {
    try {
        const url = page.url();
        if (!/^https?:/.test(url)) return null;

        const vitals = await Promise.race([
            page.evaluate(() => new Promise<any>((resolve) => {
                const round = (v: any) => (typeof v === 'number' && isFinite(v) ? Math.round(v * 1000) / 1000 : null);

                const nav = performance.getEntriesByType('navigation')[0] as any;
                const fcpEntry = performance.getEntriesByType('paint')
                    .find((p) => p.name === 'first-contentful-paint');

                let lcp: number | null = null;
                let cls: number | null = null;
                try {
                    const lcpObserver = new PerformanceObserver(() => { /* buffered read below */ });
                    lcpObserver.observe({ type: 'largest-contentful-paint', buffered: true } as any);
                    const clsObserver = new PerformanceObserver(() => { /* buffered read below */ });
                    clsObserver.observe({ type: 'layout-shift', buffered: true } as any);

                    // Buffered entries are delivered on a task boundary; read on
                    // the next tick so takeRecords() sees them.
                    setTimeout(() => {
                        try {
                            const lcpEntries = lcpObserver.takeRecords() as any[];
                            if (lcpEntries.length) lcp = lcpEntries[lcpEntries.length - 1].startTime;
                            let shift = 0;
                            for (const e of clsObserver.takeRecords() as any[]) {
                                if (!e.hadRecentInput) shift += e.value;
                            }
                            cls = shift;
                            lcpObserver.disconnect();
                            clsObserver.disconnect();
                        } catch { /* leave nulls */ }
                        resolve({
                            ttfb_ms: nav ? round(nav.responseStart - nav.requestStart) : null,
                            fcp_ms: fcpEntry ? round(fcpEntry.startTime) : null,
                            lcp_ms: round(lcp),
                            cls: round(cls),
                            dom_content_loaded_ms: nav ? round(nav.domContentLoadedEventEnd) : null,
                            load_ms: nav && nav.loadEventEnd > 0 ? round(nav.loadEventEnd) : null,
                        });
                    }, 50);
                } catch {
                    // Firefox/WebKit: no LCP/layout-shift observer types.
                    resolve({
                        ttfb_ms: nav ? round(nav.responseStart - nav.requestStart) : null,
                        fcp_ms: fcpEntry ? round(fcpEntry.startTime) : null,
                        lcp_ms: null,
                        cls: null,
                        dom_content_loaded_ms: nav ? round(nav.domContentLoadedEventEnd) : null,
                        load_ms: nav && nav.loadEventEnd > 0 ? round(nav.loadEventEnd) : null,
                    });
                }
            })),
            new Promise<null>((resolve) => setTimeout(() => resolve(null), COLLECT_TIMEOUT_MS)),
        ]);

        if (!vitals) return null;
        return { url, ...vitals };
    } catch (err: any) {
        console.warn('[WebVitals] collection failed:', err?.message || err);
        return null;
    }
}

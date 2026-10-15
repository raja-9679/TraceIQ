/**
 * Layout assertion steps (WP10): `expect-no-horizontal-overflow` and
 * `expect-rtl-layout`.
 *
 * Horizontal overflow is the most common responsive/RTL regression: a fixed
 * width, a long unbreakable word or a mirrored absolute position pushes
 * content past the viewport edge and the page scrolls sideways. The check
 * runs in the page:
 *   1. the document itself scrolls horizontally (scrollWidth > clientWidth),
 *   2. visible elements whose box crosses the left or right viewport edge —
 *      unless an ancestor clips them (overflow hidden/clip/auto/scroll), which
 *      is how carousels and scrollers legitimately hold off-screen content.
 */
import type { FrameLocator, Page } from 'playwright';

export interface OverflowOffender {
    selector: string;
    left: number;
    right: number;
    width: number;
    side: 'left' | 'right';
}

export interface OverflowReport {
    viewport_width: number;
    document_scroll_width: number;
    document_overflows: boolean;
    offenders: OverflowOffender[];
    offender_count: number;
}

export interface OverflowOptions {
    tolerance_px?: number;
    ignore?: string[];
    scope?: string | null;
    max_offenders?: number;
}

/** Runs in the page; self-contained (serialised by Playwright). */
function measureOverflow(args: { tolerance: number; ignore: string[]; scope: string | null; max: number }) {
    const doc = document.documentElement;
    const vw = doc.clientWidth || window.innerWidth;
    const tol = args.tolerance;
    const ignored = (el: Element) => args.ignore.some(sel => {
        try { return !!el.closest(sel); } catch { return false; }
    });
    const describe = (el: Element): string => {
        const testId = el.getAttribute('data-testid') || el.getAttribute('data-test');
        if (testId) return `[data-testid="${testId}"]`;
        if (el.id) return `${el.tagName.toLowerCase()}#${el.id}`;
        const cls = (el.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean).slice(0, 2);
        const parent = el.parentElement;
        let nth = '';
        if (parent) {
            const same = Array.from(parent.children).filter(c => c.tagName === el.tagName);
            if (same.length > 1) nth = `:nth-of-type(${same.indexOf(el) + 1})`;
        }
        return `${el.tagName.toLowerCase()}${cls.map(c => '.' + c).join('')}${nth}`.slice(0, 160);
    };
    const clipped = (el: Element): boolean => {
        for (let p = el.parentElement; p && p !== doc && p !== document.body; p = p.parentElement) {
            const ox = getComputedStyle(p).overflowX;
            if (ox === 'hidden' || ox === 'clip' || ox === 'auto' || ox === 'scroll') {
                const r = p.getBoundingClientRect();
                // The clipping box itself must sit inside the viewport, or it
                // is the thing overflowing (and will be reported on its own).
                if (r.left >= -tol && r.right <= vw + tol) return true;
            }
        }
        return false;
    };
    const root: Element = args.scope ? (document.querySelector(args.scope) || document.body) : document.body;
    const offenders: any[] = [];
    const reported: Element[] = [];
    let count = 0;
    const all = root ? [root, ...Array.from(root.querySelectorAll('*'))] : [];
    for (const el of all) {
        if (['SCRIPT', 'STYLE', 'HEAD', 'META', 'LINK', 'TEMPLATE', 'NOSCRIPT', 'BR'].includes(el.tagName)) continue;
        const style = getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || (style.position === 'fixed' && style.opacity === '0')) continue;
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        const overRight = r.right - vw;
        const overLeft = -r.left;
        if (overRight <= tol && overLeft <= tol) continue;
        if (ignored(el) || clipped(el)) continue;
        // Report the outermost offender only: its descendants overflow with it.
        if (reported.some(o => o.contains(el))) continue;
        reported.push(el);
        count++;
        if (offenders.length < args.max) {
            offenders.push({
                selector: describe(el),
                left: Math.round(r.left), right: Math.round(r.right), width: Math.round(r.width),
                side: overRight > overLeft ? 'right' : 'left',
            });
        }
    }
    const visible = offenders;
    return {
        viewport_width: vw,
        document_scroll_width: doc.scrollWidth,
        document_overflows: doc.scrollWidth > vw + tol,
        offenders: visible,
        offender_count: count,
    };
}

export async function checkHorizontalOverflow(page: Page, opts: OverflowOptions = {}): Promise<OverflowReport> {
    const args = {
        tolerance: Math.max(0, Math.min(100, Number(opts.tolerance_px ?? 1) || 0)),
        ignore: (Array.isArray(opts.ignore) ? opts.ignore : []).filter(s => typeof s === 'string').slice(0, 20),
        scope: typeof opts.scope === 'string' && opts.scope ? opts.scope : null,
        max: Math.max(1, Math.min(50, Number(opts.max_offenders ?? 10) || 10)),
    };
    return page.evaluate(measureOverflow, args);
}

export function overflowFailure(report: OverflowReport): string | null {
    if (!report.document_overflows && report.offender_count === 0) return null;
    const parts: string[] = [];
    if (report.document_overflows) {
        parts.push(`page scrolls horizontally (scrollWidth ${report.document_scroll_width}px > viewport ${report.viewport_width}px)`);
    }
    if (report.offender_count) {
        const list = report.offenders.slice(0, 5)
            .map(o => `${o.selector} (${o.side === 'right' ? `right edge ${o.right}px` : `left edge ${o.left}px`})`)
            .join(', ');
        parts.push(`${report.offender_count} element(s) outside the viewport: ${list}`);
    }
    return `Horizontal overflow at ${report.viewport_width}px: ${parts.join('; ')}`;
}

export interface DirectionReport {
    html_dir: string | null;
    body_dir: string | null;
    computed: string;
    element_direction?: string | null;
}

export async function readDirection(page: Page, context: Page | FrameLocator, selector?: string): Promise<DirectionReport> {
    const base = await page.evaluate(() => ({
        html_dir: document.documentElement.getAttribute('dir'),
        body_dir: document.body ? document.body.getAttribute('dir') : null,
        computed: getComputedStyle(document.body || document.documentElement).direction,
    }));
    if (selector) {
        const dir = await context.locator(selector).first()
            .evaluate((el: Element) => getComputedStyle(el).direction, undefined, { timeout: 5000 })
            .catch(() => null);
        return { ...base, element_direction: dir };
    }
    return base;
}

export function directionFailure(report: DirectionReport, expected: 'rtl' | 'ltr'): string | null {
    const declared = (report.html_dir || report.body_dir || '').toLowerCase();
    if (report.computed !== expected) {
        return `Expected ${expected.toUpperCase()} layout but the document is ${report.computed.toUpperCase()}`
            + ` (html dir=${report.html_dir ?? 'unset'}${report.body_dir ? `, body dir=${report.body_dir}` : ''})`;
    }
    if (expected === 'rtl' && declared !== 'rtl') {
        // Computed RTL via CSS only: screen readers and form controls follow
        // the dir attribute, not `direction`.
        return 'Layout is RTL only through CSS `direction`; set dir="rtl" on <html> so assistive tech and form controls follow it';
    }
    if (report.element_direction !== undefined && report.element_direction !== expected) {
        return `Element direction is ${report.element_direction ?? 'not found'}, expected ${expected}`;
    }
    return null;
}

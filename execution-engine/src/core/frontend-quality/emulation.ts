/**
 * Frontend matrix emulation (WP10): turns the job's `settings.emulation`
 * (one variant of a viewport x locale x colour-scheme sweep, built by
 * backend/app/services/frontend_matrix.py) into Playwright context options,
 * a locale cookie, and goto-URL rewriting.
 *
 * Every field is optional and an absent `emulation` is a no-op, so an older
 * backend's jobs run exactly as before.
 */
import type { BrowserContext } from 'playwright';

export interface EmulationViewport { name: string; width: number; height: number }
export interface EmulationLocale {
    locale: string;
    query?: Record<string, string>;
    cookie?: { name: string; value: string; domain?: string };
    url_template?: string;
}
export interface Emulation {
    viewport?: EmulationViewport;
    locale?: EmulationLocale;
    color_scheme?: 'light' | 'dark' | 'no-preference';
    reduced_motion?: boolean;
    label?: string;
    baseline_key?: string | null;
    step_screenshots?: boolean;
}

const SCHEMES = new Set(['light', 'dark', 'no-preference']);
const LOCALE_RE = /^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8}){0,3}$/;

export function emulationOf(settings: any): Emulation | null {
    const e = settings?.emulation;
    return e && typeof e === 'object' ? e as Emulation : null;
}

/**
 * Merge the variant into context options built for the job. Applied AFTER
 * device emulation on purpose: a sweep's explicit width is the point of the
 * run, so it wins over a device descriptor's viewport (the descriptor's user
 * agent / touch / scale factor are kept).
 */
export function applyEmulationOptions(contextOptions: any, settings: any): any {
    const e = emulationOf(settings);
    if (!e) return contextOptions;
    const vp = e.viewport;
    if (vp && Number.isInteger(vp.width) && Number.isInteger(vp.height)
        && vp.width >= 200 && vp.width <= 3840 && vp.height >= 200 && vp.height <= 4320) {
        contextOptions.viewport = { width: vp.width, height: vp.height };
        // Video at the viewport's own size: a 375px page letterboxed into
        // 1280x720 is unreadable. Capped, the encoder dislikes huge frames.
        if (contextOptions.recordVideo) {
            const scale = Math.min(1, 1280 / vp.width, 1280 / vp.height);
            contextOptions.recordVideo = {
                ...contextOptions.recordVideo,
                size: { width: even(vp.width * scale), height: even(vp.height * scale) },
            };
        }
    }
    const tag = e.locale?.locale;
    if (tag && LOCALE_RE.test(tag)) {
        contextOptions.locale = tag;
        // Playwright derives Accept-Language from `locale`; stated explicitly
        // too so a server that only reads the header sees it on every request.
        contextOptions.extraHTTPHeaders = {
            ...(contextOptions.extraHTTPHeaders || {}),
            'Accept-Language': acceptLanguage(tag),
        };
    }
    if (e.color_scheme && SCHEMES.has(e.color_scheme)) contextOptions.colorScheme = e.color_scheme;
    if (e.reduced_motion) contextOptions.reducedMotion = 'reduce';
    return contextOptions;
}

function even(n: number): number {
    const v = Math.max(2, Math.round(n));
    return v % 2 === 0 ? v : v - 1;
}

export function acceptLanguage(tag: string): string {
    const lang = tag.split('-')[0];
    return lang && lang !== tag ? `${tag},${lang};q=0.9` : tag;
}

/** Host the locale cookie is set for: explicit, else environment base_url, else first allowed domain. */
export function cookieDomain(e: Emulation, settings: any): string | null {
    const explicit = e.locale?.cookie?.domain;
    if (explicit) return explicit;
    try {
        const base = settings?.environment?.base_url;
        if (base) return new URL(base).hostname;
    } catch { /* fall through */ }
    const first = (settings?.allowed_domains || [])[0];
    const domain = typeof first === 'string' ? first : first?.domain;
    if (typeof domain === 'string' && domain) {
        return domain.includes('://') ? safeHost(domain) : domain.split('/')[0].split(':')[0].replace(/^\*\./, '.');
    }
    return null;
}

function safeHost(url: string): string | null {
    try { return new URL(url).hostname; } catch { return null; }
}

/** Locale cookie on the context. Without a known domain it is written by
 * an init script instead (non-HttpOnly, lands from the first document on). */
export async function applyEmulationToContext(context: BrowserContext, settings: any): Promise<void> {
    const e = emulationOf(settings);
    const cookie = e?.locale?.cookie;
    if (!e || !cookie?.name) return;
    const domain = cookieDomain(e, settings);
    try {
        if (domain) {
            await context.addCookies([{ name: cookie.name, value: String(cookie.value), domain, path: '/' }]);
            return;
        }
    } catch (err: any) {
        console.warn(`[Emulation] could not set locale cookie on ${domain}: ${err?.message}`);
    }
    await context.addInitScript(({ name, value }: { name: string; value: string }) => {
        try { document.cookie = `${name}=${encodeURIComponent(value)}; path=/`; } catch { /* opaque origin */ }
    }, { name: cookie.name, value: String(cookie.value) });
}

/**
 * A goto URL as this locale variant should open it: the locale's query
 * parameters added, then its url_template applied. Templates are validated
 * by the backend to start from `{origin}` or `{url}`, so a rewrite can change
 * the path and query but never send the journey to another site — re-checked
 * here (a rewritten URL on a different origin is discarded).
 */
export function localiseUrl(raw: string, settings: any): string {
    const e = emulationOf(settings);
    const loc = e?.locale;
    if (!loc || !/^https?:/i.test(raw)) return raw;
    let url: URL;
    try { url = new URL(raw); } catch { return raw; }
    for (const [k, v] of Object.entries(loc.query || {})) {
        url.searchParams.set(k, fill(String(v), loc.locale));
    }
    let out = url.toString();
    const template = loc.url_template;
    if (template && (template.startsWith('{origin}') || template.startsWith('{url}'))) {
        const lang = loc.locale.split('-')[0];
        const candidate = template
            .replace(/\{origin\}/g, url.origin)
            .replace(/\{path\}/g, url.pathname)
            .replace(/\{query\}/g, url.search)
            .replace(/\{url\}/g, out)
            .replace(/\{locale\}/g, loc.locale)
            .replace(/\{lang\}/g, lang);
        try {
            if (new URL(candidate).origin === url.origin) out = candidate;
            else console.warn(`[Emulation] url_template left the origin (${candidate}); ignored`);
        } catch {
            console.warn(`[Emulation] url_template produced an invalid URL; ignored`);
        }
    }
    return out;
}

function fill(value: string, locale: string): string {
    return value.replace(/\{locale\}/g, locale).replace(/\{lang\}/g, locale.split('-')[0]);
}

/** The `device` slot visual baselines are stored under for this variant. */
export function baselineDevice(jobDevice: string | null | undefined, settings: any): string | null {
    const key = emulationOf(settings)?.baseline_key;
    if (!key || !key.startsWith('vp:')) return jobDevice || null;
    // Keeps the `vp:` prefix: the backend resolves those exact-match only.
    return jobDevice ? `vp:${jobDevice}|${key.slice(3)}` : key;
}

export function wantsStepScreenshots(settings: any): boolean {
    return emulationOf(settings)?.step_screenshots === true;
}

/** File name of the per-step sweep screenshot (unique per case in a continuous job). */
export function sweepShotFile(caseId: number | string | undefined, stepIndex: number): string {
    return `sweep-${caseId ?? 'case'}-s${stepIndex}.png`;
}

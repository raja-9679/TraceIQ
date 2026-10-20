import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

// Every sidebar destination, read from the frontend's own nav definition so a
// page added there is covered here without anyone remembering to list it.
//
// nav.ts imports React/lucide, which this package does not install, so it is
// parsed as text. Every entry (place, child, setup link) is written as
// `name: '…', href: '…'`; the count check in navigation.spec.ts fails loudly if
// a reformat ever makes this regex miss entries.

export type Destination = { name: string; href: string; section: string };

const NAV_FILE = resolve(__dirname, '../../../frontend/src/components/layout/nav.ts');

export function sidebarDestinations(): Destination[] {
    const src = readFileSync(NAV_FILE, 'utf8');
    const setupAt = src.indexOf('export const setupLinks');
    if (setupAt < 0) throw new Error(`setupLinks not found in ${NAV_FILE}`);

    const out: Destination[] = [];
    const seen = new Set<string>();
    const entry = /name:\s*'([^']+)',\s*href:\s*'([^']+)'/g;
    for (const m of src.matchAll(entry)) {
        const [, name, href] = m;
        if (seen.has(href)) continue; // a place's href repeats as its first child
        seen.add(href);
        out.push({ name, href, section: (m.index ?? 0) > setupAt ? 'setup' : 'places' });
    }
    return out;
}

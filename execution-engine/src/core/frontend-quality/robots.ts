/**
 * Minimal robots.txt support for the site crawler (RFC 9309 matching rules):
 * the group for our user agent (else `*`), Allow/Disallow with `*` and `$`
 * wildcards, longest match wins, Allow wins a tie. Crawl-delay is honoured as
 * a floor on the request spacing.
 *
 * Patterns come from the site under test, so they are matched with a small
 * hand-written matcher — never compiled into a RegExp (a hostile robots.txt
 * could otherwise stall the worker with a backtracking pattern).
 */

export const CRAWLER_UA_TOKEN = 'traceiq';

export interface RobotsRule { allow: boolean; pattern: string }
export interface Robots { rules: RobotsRule[]; crawlDelayMs: number | null }

const MAX_RULES = 500;
const MAX_PATTERN = 500;

export function parseRobots(text: string, agent = CRAWLER_UA_TOKEN): Robots {
    type Group = { agents: string[]; rules: RobotsRule[]; delay: number | null };
    const groups: Group[] = [];
    let current: Group | null = null;
    let lastWasAgent = false;
    for (const rawLine of String(text || '').split(/\r?\n/).slice(0, 5000)) {
        const line = rawLine.replace(/#.*$/, '').trim();
        if (!line) continue;
        const idx = line.indexOf(':');
        if (idx < 0) continue;
        const key = line.slice(0, idx).trim().toLowerCase();
        const value = line.slice(idx + 1).trim();
        if (key === 'user-agent') {
            if (!current || !lastWasAgent) {
                current = { agents: [], rules: [], delay: null };
                groups.push(current);
            }
            current.agents.push(value.toLowerCase());
            lastWasAgent = true;
            continue;
        }
        lastWasAgent = false;
        if (!current) continue;
        if ((key === 'allow' || key === 'disallow') && current.rules.length < MAX_RULES) {
            // An empty Disallow means "allow everything" — no rule.
            if (value) current.rules.push({ allow: key === 'allow', pattern: value.slice(0, MAX_PATTERN) });
        } else if (key === 'crawl-delay') {
            const secs = Number(value);
            if (isFinite(secs) && secs >= 0) current.delay = Math.min(secs, 60) * 1000;
        }
    }
    const me = agent.toLowerCase();
    const mine = groups.filter(g => g.agents.some(a => a && a !== '*' && (a.startsWith(me) || me.startsWith(a))));
    const chosen = mine.length ? mine : groups.filter(g => g.agents.includes('*'));
    return {
        rules: chosen.flatMap(g => g.rules),
        crawlDelayMs: chosen.reduce<number | null>((acc, g) => g.delay === null ? acc : Math.max(acc ?? 0, g.delay), null),
    };
}

/** Does `pattern` (robots syntax: `*` any run, trailing `$` = end) match `path`? */
export function patternMatches(pattern: string, path: string): boolean {
    let p = pattern;
    const anchored = p.endsWith('$');
    if (anchored) p = p.slice(0, -1);
    const parts = p.split('*');
    // First part must be a prefix.
    if (!path.startsWith(parts[0])) return false;
    let pos = parts[0].length;
    for (let i = 1; i < parts.length; i++) {
        const part = parts[i];
        if (i === parts.length - 1 && anchored) {
            // Last part must end the path, at or after pos.
            return path.length - part.length >= pos && path.endsWith(part);
        }
        const found = path.indexOf(part, pos);
        if (found < 0) return false;
        pos = found + part.length;
    }
    return anchored ? pos === path.length : true;
}

export function isAllowed(robots: Robots | null, pathAndQuery: string): boolean {
    if (!robots || !robots.rules.length) return true;
    let best: RobotsRule | null = null;
    for (const rule of robots.rules) {
        if (!patternMatches(rule.pattern, pathAndQuery)) continue;
        if (!best || rule.pattern.length > best.pattern.length
            || (rule.pattern.length === best.pattern.length && rule.allow && !best.allow)) {
            best = rule;
        }
    }
    return best ? best.allow : true;
}

import { describe, expect, it } from 'vitest';
import {
    formatAge, formatPromQueries, localInputToIso, parseCidrList, parseHostList,
    parsePromQueries, parseRegions,
} from './fleet';

describe('fleet helpers', () => {
    it('parses regions, lowercases and de-duplicates; blank is null', () => {
        expect(parseRegions(' EU-west, us ,eu-west,, ')).toEqual(['eu-west', 'us']);
        expect(parseRegions('  ')).toBeNull();
    });

    it('parses CIDR lists separated by commas or whitespace', () => {
        expect(parseCidrList('10.0.0.0/8, 192.0.2.1\n2001:db8::/32')).toEqual(
            ['10.0.0.0/8', '192.0.2.1', '2001:db8::/32']);
        expect(parseCidrList('')).toBeNull();
        expect(parseHostList('a.com\nb.com, c.com')).toEqual(['a.com', 'b.com', 'c.com']);
    });

    it('round-trips "name | query" lines', () => {
        const q = parsePromQueries('CPU | rate(cpu[1m])\n\nup\n | sum(x)\nbad |  ');
        expect(q).toEqual([
            { name: 'CPU', query: 'rate(cpu[1m])' },
            { name: 'query 2', query: 'up' },
            { name: 'query 3', query: 'sum(x)' },
        ]);
        expect(formatPromQueries(q)).toBe('CPU | rate(cpu[1m])\nquery 2 | up\nquery 3 | sum(x)');
        expect(formatPromQueries(undefined)).toBe('');
        // A query containing "|" keeps everything after the first separator.
        expect(parsePromQueries('Errors | a or on() b | c')[0].query).toBe('a or on() b | c');
    });

    it('formats ages and converts datetime-local to UTC ISO', () => {
        expect(formatAge(5)).toBe('5s');
        expect(formatAge(125)).toBe('2m');
        expect(formatAge(null)).toBe('—');
        expect(localInputToIso('')).toBeNull();
        expect(localInputToIso('2026-10-15T10:00')).toMatch(/Z$/);
    });
});

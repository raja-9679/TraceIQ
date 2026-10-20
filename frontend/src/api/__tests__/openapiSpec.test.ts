import { describe, expect, it } from 'vitest';
import { parseSpecFile } from '../openapiSpec';

describe('parseSpecFile', () => {
    it('accepts an OpenAPI JSON document', () => {
        const doc = parseSpecFile('{"openapi":"3.0.0","info":{"title":"Shop"},"paths":{"/a":{}}}');
        expect(doc.openapi).toBe('3.0.0');
    });
    it('accepts Swagger 2', () => {
        expect(parseSpecFile('{"swagger":"2.0","paths":{}}').swagger).toBe('2.0');
    });
    it('explains YAML or broken JSON', () => {
        expect(() => parseSpecFile('openapi: 3.0.0')).toThrow(/YAML is not supported/);
    });
    it('refuses JSON that is not an OpenAPI document', () => {
        expect(() => parseSpecFile('{"name":"x"}')).toThrow(/not an OpenAPI document/);
        expect(() => parseSpecFile('[1,2]')).toThrow(/not an OpenAPI document/);
    });
});

import { defaultDiffPair, groupByOperation, ruleLabel, type SpecChange, type SpecVersion } from '../openapiSpec';

const v = (version: number): SpecVersion => ({
    version, sha256: 'x', title: null, spec_version: null, source_url: null, operation_count: 1,
    via: 'put', created_by_id: null, created_at: '2026-10-20T00:00:00', is_current: false,
});
const change = (over: Partial<SpecChange>): SpecChange => ({
    id: Math.random().toString(36), severity: 'non_breaking', rule: 'response_property_added', method: 'GET',
    path: '/a', operation_id: null, location: 'responses/200', message: 'm', status: '200', old: null, new: null,
    affected_cases: [], promoted_reason: null, ...over,
});

describe('spec diff helpers', () => {
    it('defaults to the newest version against the one before', () => {
        expect(defaultDiffPair([v(1), v(3), v(2)])).toEqual({ from: 2, to: 3 });
        expect(defaultDiffPair([v(1)])).toBeNull();
    });
    it('groups by operation with breaking operations first and de-duplicated tests', () => {
        const t = { case_id: 7, name: 'pets', test_suite_id: 1 };
        const groups = groupByOperation([
            change({ path: '/a' }),
            change({ path: '/b', severity: 'breaking', rule: 'response_property_removed', affected_cases: [t] }),
            change({ path: '/b', affected_cases: [t] }),
        ]);
        expect(groups.map((g) => g.key)).toEqual(['GET /b', 'GET /a']);
        expect(groups[0].breaking).toHaveLength(1);
        expect(groups[0].nonBreaking).toHaveLength(1);
        expect(groups[0].cases).toEqual([t]);
    });
    it('turns rule ids into labels', () => {
        expect(ruleLabel('response_property_removed')).toBe('Response property removed');
    });
});

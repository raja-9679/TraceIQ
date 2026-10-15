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

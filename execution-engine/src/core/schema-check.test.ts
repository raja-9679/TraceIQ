import { test } from 'node:test';
import * as assert from 'node:assert/strict';
import { checkExpectSchema, formatViolations } from './schema-check';

const pet = {
    type: 'object', required: ['id', 'name'], additionalProperties: false,
    properties: { id: { type: 'integer' }, name: { type: 'string' }, status: { enum: ['a', 'b'] } },
};

test('valid body has no violations', () => {
    const body = { id: 1, name: 'rex' };
    assert.deepEqual(checkExpectSchema({ schema: pet }, JSON.stringify(body), body), []);
});

test('every violation is reported with its path', () => {
    const body = { id: 'x', status: 'z', extra: true };
    const v = checkExpectSchema({ schema: pet }, '', body);
    const byPath = v.map(x => `${x.path} ${x.message}`);
    assert.ok(byPath.includes('/id must be integer'));
    assert.ok(byPath.includes('/ missing required property "name"'));
    assert.ok(byPath.includes('/ unexpected property "extra"'));
    assert.ok(byPath.some(s => s.startsWith('/status must be one of')));
    const msg = formatViolations(v, 'openapi GET /pets/{id} 200');
    assert.match(msg, /openapi GET \/pets\/\{id\} 200/);
    assert.match(msg, /4 violation/);
});

test('schema may be a JSON string; nested array paths are precise', () => {
    const schema = JSON.stringify({ type: 'array', items: pet });
    const body = [{ id: 1, name: 'a' }, { id: 2 }];
    const v = checkExpectSchema({ schema }, '', body);
    assert.deepEqual(v, [{ path: '/1', message: 'missing required property "name"' }]);
});

test('backend-resolved OpenAPI schema with components and recursion validates', () => {
    const resolved = {
        allOf: [{ $ref: '#/components/schemas/Node' }],
        components: { schemas: { Node: {
            type: 'object', required: ['v'], properties: {
                v: { type: 'integer', nullable: true },
                next: { $ref: '#/components/schemas/Node' },
            } } } },
    };
    assert.deepEqual(checkExpectSchema({ schema: resolved }, '', { v: null, next: { v: 2 } }), []);
    const v = checkExpectSchema({ schema: resolved }, '', { v: 1, next: { v: 'x' } });
    assert.equal(v[0].path, '/next/v');
});

test('unresolved / errored / non-JSON cases fail rather than pass', () => {
    assert.match(checkExpectSchema({ openapi: { operationId: 'x' }, error: 'no stored spec' }, '', {})[0].message,
        /could not be resolved: no stored spec/);
    assert.match(checkExpectSchema({ openapi: { operationId: 'x' } }, '', {})[0].message, /was not resolved/);
    assert.match(checkExpectSchema({ schema: pet }, '<html>', undefined)[0].message, /not JSON/);
    assert.match(checkExpectSchema({ schema: '{nope' }, '', {})[0].message, /not valid JSON/);
    assert.match(checkExpectSchema({ schema: { type: 'nonsense' } }, '', {})[0].message, /invalid JSON Schema/);
});

test('violations are capped', () => {
    const schema = { type: 'array', items: { type: 'string' } };
    const body = Array.from({ length: 50 }, (_, i) => i);
    const v = checkExpectSchema({ schema }, '', body);
    assert.equal(v.length, 20);
    assert.match(formatViolations(v), /20\+ violation/);
});

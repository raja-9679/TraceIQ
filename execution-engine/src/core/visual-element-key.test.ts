/**
 * Element-key normalisation. The vectors are shared with the backend twin
 * (tests/test_visual_review.py) — change both or neither.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { elementKey, normaliseSelector, ELEMENT_KEY_RE } from './visual-element-key';

const VECTORS: Array<[string, string, string]> = [
    ['#hero', '#hero', 'el-f7b1217bf4c3'],
    ['  .nav   >  a.active ', '.nav>a.active', 'el-50ba70928376'],
    ['.nav>a.active', '.nav>a.active', 'el-50ba70928376'],
    ['text="Sign  in" , #x', 'text="Sign  in",#x', 'el-97974f64808e'],
    ["[title='a > b']", "[title='a > b']", 'el-0395e9b4d766'],
    ['div  span', 'div span', 'el-c600d917b8e6'],
];

test('normalises whitespace outside quotes only and hashes to a stable key', () => {
    for (const [raw, norm, key] of VECTORS) {
        assert.equal(normaliseSelector(raw), norm, raw);
        assert.equal(elementKey(raw), key, raw);
        assert.match(key, ELEMENT_KEY_RE);
    }
});

test('no selector means the full-page key', () => {
    assert.equal(elementKey(''), '');
    assert.equal(elementKey('   '), '');
    assert.equal(elementKey(undefined), '');
});

test('different elements get different keys', () => {
    assert.notEqual(elementKey('#hero'), elementKey('#footer'));
    assert.notEqual(elementKey('div span'), elementKey('div>span'));
});

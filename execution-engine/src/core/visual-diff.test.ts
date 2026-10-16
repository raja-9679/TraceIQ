/**
 * Diff statistics (WP14e): change regions and crop hashes computed from a
 * pixelmatch diff. Pure functions on raw RGBA, so no PNG codec is needed.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { dHash, diffMaskFromPixelmatch, findDiffRegions, summariseDiff } from '../visual-diff';

function canvas(w: number, h: number, v = 255): Uint8Array {
    const a = new Uint8Array(w * h * 4).fill(v);
    return a;
}
function paint(a: Uint8Array, w: number, box: { x: number; y: number; width: number; height: number }, rgb: number[]) {
    for (let y = box.y; y < box.y + box.height; y++) {
        for (let x = box.x; x < box.x + box.width; x++) {
            const i = (y * w + x) * 4;
            a[i] = rgb[0]; a[i + 1] = rgb[1]; a[i + 2] = rgb[2]; a[i + 3] = 255;
        }
    }
}

test('the mask picks exactly pixelmatch red, not grey or anti-alias yellow', () => {
    const w = 3, h = 1;
    const d = new Uint8Array([255, 0, 0, 255, 200, 200, 200, 255, 255, 255, 0, 255]);
    assert.deepEqual([...diffMaskFromPixelmatch(d, w, h)], [1, 0, 0]);
});

test('two separate changes become two boxes, largest first, with pixel counts', () => {
    const w = 200, h = 200;
    const mask = new Uint8Array(w * h);
    const set = (x0: number, y0: number, bw: number, bh: number) => {
        for (let y = y0; y < y0 + bh; y++) for (let x = x0; x < x0 + bw; x++) mask[y * w + x] = 1;
    };
    set(10, 10, 20, 5);       // 100 px
    set(100, 150, 40, 10);    // 400 px
    const regions = findDiffRegions(mask, w, h);
    assert.equal(regions.length, 2);
    assert.deepEqual(regions[0], { x: 100, y: 150, width: 40, height: 10, pixels: 400 });
    assert.deepEqual(regions[1], { x: 10, y: 10, width: 20, height: 5, pixels: 100 });
});

test('nearby glyph-sized changes merge into one region', () => {
    const w = 120, h = 40;
    const mask = new Uint8Array(w * h);
    for (const x0 of [5, 25, 45, 65]) for (let y = 10; y < 20; y++) for (let x = x0; x < x0 + 6; x++) mask[y * w + x] = 1;
    const regions = findDiffRegions(mask, w, h);
    assert.equal(regions.length, 1);
    assert.equal(regions[0].x, 5);
    assert.equal(regions[0].width, 66);
    assert.equal(regions[0].pixels, 240);
});

test('no changed pixels -> no regions; the cap holds', () => {
    assert.deepEqual(findDiffRegions(new Uint8Array(100), 10, 10), []);
    const w = 400, h = 400;
    const mask = new Uint8Array(w * h);
    for (let gy = 0; gy < 10; gy++) for (let gx = 0; gx < 10; gx++) mask[(gy * 40) * w + gx * 40] = 1;
    assert.equal(findDiffRegions(mask, w, h, { max: 5 }).length, 5);
});

test('dHash: same picture at another place hashes alike, a different picture does not', () => {
    const w = 300, h = 100;
    const a = canvas(w, h);
    paint(a, w, { x: 10, y: 10, width: 30, height: 40 }, [0, 0, 0]);
    paint(a, w, { x: 210, y: 10, width: 30, height: 40 }, [0, 0, 0]);
    const h1 = dHash(a, w, h, { x: 0, y: 0, width: 80, height: 60 });
    const h2 = dHash(a, w, h, { x: 200, y: 0, width: 80, height: 60 });
    assert.equal(h1, h2);
    assert.match(h1, /^[0-9a-f]{16}$/);
    const b = canvas(w, h);
    paint(b, w, { x: 50, y: 30, width: 25, height: 25 }, [0, 0, 0]);
    assert.notEqual(dHash(b, w, h, { x: 0, y: 0, width: 80, height: 60 }), h1);
});

test('summariseDiff attaches both crop hashes to each region', () => {
    const w = 64, h = 64;
    const base = canvas(w, h), cand = canvas(w, h), diff = canvas(w, h, 230);
    paint(cand, w, { x: 8, y: 8, width: 16, height: 16 }, [0, 0, 0]);
    paint(diff, w, { x: 8, y: 8, width: 16, height: 16 }, [255, 0, 0]);
    const regions = summariseDiff({ baseline: base, candidate: cand, diff, width: w, height: h });
    assert.equal(regions.length, 1);
    assert.equal(regions[0].pixels, 256);
    assert.match(regions[0].hash_candidate!, /^[0-9a-f]{16}$/);
    assert.match(regions[0].hash_baseline!, /^[0-9a-f]{16}$/);
});

// End-to-end through pixelmatch/pngjs when the worker image has them (the
// shared dev node_modules may not).
let hasCodec = true;
try { require('pngjs'); require('pixelmatch'); } catch { hasCodec = false; }

test('compareScreenshots reports ratio, size and regions', { skip: !hasCodec }, async () => {
    const fs = require('fs'); const os = require('os'); const path = require('path');
    const { PNG } = require('pngjs');
    const { compareScreenshots } = require('../visual-diff');
    const w = 80, h = 60;
    const mk = (dark: boolean) => {
        const p = new PNG({ width: w, height: h });
        p.data.fill(255);
        if (dark) paint(p.data, w, { x: 10, y: 10, width: 20, height: 10 }, [0, 0, 0]);
        return PNG.sync.write(p);
    };
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vd-'));
    const cand = path.join(dir, 'visual-s1.png');
    fs.writeFileSync(cand, mk(true));
    const r = await compareScreenshots({ candidatePath: cand, baselineBytes: mk(false), tolerance: 0.01 });
    assert.equal(r.passed, false);
    assert.equal(r.diffPixels, 200);
    assert.equal(r.width, w);
    assert.equal(r.sizeMismatch, false);
    assert.deepEqual(r.regions.map((x: any) => [x.x, x.y, x.width, x.height]), [[10, 10, 20, 10]]);
    fs.rmSync(dir, { recursive: true, force: true });
});

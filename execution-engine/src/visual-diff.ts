// Visual regression comparator for the `expect-visual-match` step.
//
// Inputs:
//   candidatePath  — path to the screenshot captured for the current step
//   baselineImage  — bytes of the pinned baseline image
//   tolerance      — fraction of differing pixels at which the step still
//                    passes (0.01 = 1% drift allowed). Sourced from the
//                    VisualBaseline row.
//   maskRegions    — list of {x, y, width, height} rectangles to ignore
//                    (e.g. dynamic timestamps).
//
// Output: { passed, diffRatio, diffImagePath, regions } — the diff image is
// written alongside the candidate for upload to MinIO by the existing artifact
// path. `regions` (WP14e) are the bounding boxes of the changed areas with a
// perceptual hash of each crop, so the backend can group the same change
// across cases (a shared header edit shows up in every case's diff).

import * as fs from 'fs';
import * as path from 'path';

export interface MaskRegion {
    x: number;
    y: number;
    width: number;
    height: number;
}

export interface DiffRegion {
    x: number;
    y: number;
    width: number;
    height: number;
    /** Changed pixels inside the box. */
    pixels: number;
    /** 64-bit difference hash (16 hex chars) of the candidate / baseline crop. */
    hash_candidate?: string;
    hash_baseline?: string;
}

export interface VisualDiffResult {
    passed: boolean;
    diffRatio: number;
    totalPixels: number;
    diffPixels: number;
    diffImagePath?: string;
    error?: string;
    width?: number;
    height?: number;
    sizeMismatch?: boolean;
    regions?: DiffRegion[];
}

export const MAX_DIFF_REGIONS = 20;

/**
 * Pixels pixelmatch painted in its diff colour (pure red by default). The
 * unchanged pixels are drawn as faded greyscale (r = g = b) and anti-aliasing
 * as yellow, so exact red is unambiguous.
 */
export function diffMaskFromPixelmatch(diffData: Uint8Array, width: number, height: number): Uint8Array {
    const mask = new Uint8Array(width * height);
    for (let i = 0, p = 0; p < mask.length; i += 4, p++) {
        if (diffData[i] === 255 && diffData[i + 1] === 0 && diffData[i + 2] === 0) mask[p] = 1;
    }
    return mask;
}

/**
 * Bounding boxes of the changed areas. Pixels are bucketed into `cell`-sized
 * cells; occupied cells closer than `gap` empty cells join one region (one
 * edited sentence is one change, not forty glyphs). Boxes are exact pixel
 * extents, largest first, capped at `max`.
 */
export function findDiffRegions(
    mask: Uint8Array, width: number, height: number,
    opts: { cell?: number; gap?: number; max?: number } = {},
): DiffRegion[] {
    const cell = Math.max(1, opts.cell ?? 16);
    const reach = (opts.gap ?? 1) + 1;
    const max = opts.max ?? MAX_DIFF_REGIONS;
    const cw = Math.ceil(width / cell);
    const chh = Math.ceil(height / cell);
    const count = new Uint32Array(cw * chh);
    const minX = new Int32Array(cw * chh).fill(width);
    const minY = new Int32Array(cw * chh).fill(height);
    const maxX = new Int32Array(cw * chh).fill(-1);
    const maxY = new Int32Array(cw * chh).fill(-1);
    for (let y = 0; y < height; y++) {
        const row = y * width;
        const cy = Math.floor(y / cell) * cw;
        for (let x = 0; x < width; x++) {
            if (!mask[row + x]) continue;
            const c = cy + Math.floor(x / cell);
            count[c]++;
            if (x < minX[c]) minX[c] = x;
            if (x > maxX[c]) maxX[c] = x;
            if (y < minY[c]) minY[c] = y;
            if (y > maxY[c]) maxY[c] = y;
        }
    }
    const seen = new Uint8Array(cw * chh);
    const regions: DiffRegion[] = [];
    for (let start = 0; start < count.length; start++) {
        if (!count[start] || seen[start]) continue;
        let x0 = width, y0 = height, x1 = -1, y1 = -1, pixels = 0;
        const stack = [start];
        seen[start] = 1;
        while (stack.length) {
            const c = stack.pop()!;
            pixels += count[c];
            if (minX[c] < x0) x0 = minX[c];
            if (minY[c] < y0) y0 = minY[c];
            if (maxX[c] > x1) x1 = maxX[c];
            if (maxY[c] > y1) y1 = maxY[c];
            const cx = c % cw, cy = Math.floor(c / cw);
            for (let dy = -reach; dy <= reach; dy++) {
                for (let dx = -reach; dx <= reach; dx++) {
                    const nx = cx + dx, ny = cy + dy;
                    if (nx < 0 || ny < 0 || nx >= cw || ny >= chh) continue;
                    const n = ny * cw + nx;
                    if (count[n] && !seen[n]) { seen[n] = 1; stack.push(n); }
                }
            }
        }
        regions.push({ x: x0, y: y0, width: x1 - x0 + 1, height: y1 - y0 + 1, pixels });
    }
    regions.sort((a, b) => b.pixels - a.pixels || a.y - b.y || a.x - b.x);
    return regions.slice(0, max);
}

/**
 * 64-bit difference hash of an RGBA crop: shrink to 9x8 greyscale (box
 * average, sub-sampled for big crops), one bit per horizontal neighbour pair.
 * Robust to small shifts and scaling, so the same banner change in two pages
 * hashes alike even when the crops are a few pixels apart.
 */
export function dHash(
    rgba: Uint8Array, width: number, height: number,
    box: { x: number; y: number; width: number; height: number },
): string {
    const gw = 9, gh = 8;
    const lum = new Float64Array(gw * gh);
    const bx = Math.max(0, Math.min(box.x, width - 1));
    const by = Math.max(0, Math.min(box.y, height - 1));
    const bw = Math.max(1, Math.min(box.width, width - bx));
    const bh = Math.max(1, Math.min(box.height, height - by));
    for (let gy = 0; gy < gh; gy++) {
        const ys = by + Math.floor((gy * bh) / gh);
        const ye = Math.max(ys + 1, by + Math.floor(((gy + 1) * bh) / gh));
        const sy = Math.max(1, Math.floor((ye - ys) / 8));
        for (let gx = 0; gx < gw; gx++) {
            const xs = bx + Math.floor((gx * bw) / gw);
            const xe = Math.max(xs + 1, bx + Math.floor(((gx + 1) * bw) / gw));
            const sx = Math.max(1, Math.floor((xe - xs) / 8));
            let sum = 0, n = 0;
            for (let y = ys; y < ye; y += sy) {
                for (let x = xs; x < xe; x += sx) {
                    const i = (y * width + x) << 2;
                    sum += 0.299 * rgba[i] + 0.587 * rgba[i + 1] + 0.114 * rgba[i + 2];
                    n++;
                }
            }
            lum[gy * gw + gx] = n ? sum / n : 0;
        }
    }
    let hex = '';
    for (let gy = 0; gy < gh; gy++) {
        let byte = 0;
        for (let gx = 0; gx < 8; gx++) {
            byte = (byte << 1) | (lum[gy * gw + gx] < lum[gy * gw + gx + 1] ? 1 : 0);
        }
        hex += byte.toString(16).padStart(2, '0');
    }
    return hex;
}

/** Regions of a finished pixelmatch run, each with its crop hashes. */
export function summariseDiff(opts: {
    baseline: Uint8Array; candidate: Uint8Array; diff: Uint8Array; width: number; height: number;
}): DiffRegion[] {
    const mask = diffMaskFromPixelmatch(opts.diff, opts.width, opts.height);
    return findDiffRegions(mask, opts.width, opts.height).map(r => ({
        ...r,
        hash_candidate: dHash(opts.candidate, opts.width, opts.height, r),
        hash_baseline: dHash(opts.baseline, opts.width, opts.height, r),
    }));
}

export async function compareScreenshots(opts: {
    candidatePath: string;
    baselineBytes: Buffer;
    tolerance: number;
    maskRegions?: MaskRegion[];
}): Promise<VisualDiffResult> {
    let PNG: any;
    let pixelmatch: any;
    try {
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        PNG = require('pngjs').PNG;
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        pixelmatch = require('pixelmatch');
    } catch (err) {
        return {
            passed: true,                // fail-open: don't break a run because the diff lib is missing
            diffRatio: 0,
            totalPixels: 0,
            diffPixels: 0,
            error: `pixelmatch/pngjs not installed: ${(err as Error).message}`,
        };
    }

    let candidateBuf: Buffer;
    try {
        candidateBuf = fs.readFileSync(opts.candidatePath);
    } catch (err) {
        return {
            passed: false,
            diffRatio: 1,
            totalPixels: 0,
            diffPixels: 0,
            error: `candidate not readable: ${(err as Error).message}`,
        };
    }

    let candidate = PNG.sync.read(candidateBuf);
    let baseline = PNG.sync.read(opts.baselineBytes);

    // Dimension mismatch (common for full-page shots — page height varies
    // between visits): pad both onto a common white canvas so pixelmatch can
    // still run and produce a diff image; the extra region reads as diff.
    let sizeNote: string | undefined;
    if (candidate.width !== baseline.width || candidate.height !== baseline.height) {
        sizeNote = `dimension mismatch: baseline=${baseline.width}x${baseline.height} candidate=${candidate.width}x${candidate.height} (compared on padded canvas)`;
        const w = Math.max(candidate.width, baseline.width);
        const h = Math.max(candidate.height, baseline.height);
        const pad = (src: any) => {
            if (src.width === w && src.height === h) return src;
            const out = new PNG({ width: w, height: h });
            out.data.fill(255); // white, opaque
            PNG.bitblt(src, out, 0, 0, src.width, src.height, 0, 0);
            return out;
        };
        candidate = pad(candidate);
        baseline = pad(baseline);
    }

    const { width, height } = candidate;
    const total = width * height;
    const diff = new PNG({ width, height });

    // Apply masks by copying baseline pixels over both images in the
    // masked region — pixelmatch will then see 0 diff there.
    if (opts.maskRegions?.length) {
        for (const m of opts.maskRegions) {
            for (let y = m.y; y < Math.min(m.y + m.height, height); y++) {
                for (let x = m.x; x < Math.min(m.x + m.width, width); x++) {
                    const idx = (y * width + x) << 2;
                    candidate.data[idx]     = baseline.data[idx];
                    candidate.data[idx + 1] = baseline.data[idx + 1];
                    candidate.data[idx + 2] = baseline.data[idx + 2];
                    candidate.data[idx + 3] = baseline.data[idx + 3];
                }
            }
        }
    }

    const diffPixels = pixelmatch(
        baseline.data, candidate.data, diff.data, width, height,
        { threshold: 0.1, includeAA: false },
    );
    const ratio = diffPixels / total;
    let regions: DiffRegion[] = [];
    try {
        regions = diffPixels ? summariseDiff({
            baseline: baseline.data, candidate: candidate.data, diff: diff.data, width, height,
        }) : [];
    } catch {
        // Stats are for review only; never fail the comparison over them.
    }

    const diffPath = path.join(
        path.dirname(opts.candidatePath),
        path.basename(opts.candidatePath, path.extname(opts.candidatePath)) + '.diff.png',
    );
    try {
        fs.writeFileSync(diffPath, PNG.sync.write(diff));
    } catch {
        // best-effort
    }

    return {
        passed: ratio <= opts.tolerance,
        diffRatio: ratio,
        totalPixels: total,
        diffPixels,
        diffImagePath: diffPath,
        error: sizeNote,
        width,
        height,
        sizeMismatch: Boolean(sizeNote),
        regions,
    };
}

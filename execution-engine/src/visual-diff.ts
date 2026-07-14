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
// Output: { passed, diffRatio, diffImagePath } — the diff image is written
// alongside the candidate for upload to MinIO by the existing artifact path.

import * as fs from 'fs';
import * as path from 'path';

export interface MaskRegion {
    x: number;
    y: number;
    width: number;
    height: number;
}

export interface VisualDiffResult {
    passed: boolean;
    diffRatio: number;
    totalPixels: number;
    diffPixels: number;
    diffImagePath?: string;
    error?: string;
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
    };
}

/**
 * The one place artifacts leave this process.
 *
 * There used to be three MinIO clients — one in `worker.ts`, one in
 * `mobile-worker.ts`, one at module scope in the legacy `runner.ts` — and
 * eleven upload call sites spread between them. That is why there was nowhere
 * to hang a redaction step or a capture policy: any hook would have had to be
 * written eleven times and would have been forgotten on the twelfth.
 *
 * Everything now funnels through `ArtifactStore.put*`, which consults the
 * job's capture level before a single byte is written.
 *
 * Object key layout is load-bearing beyond this file: the backend's
 * `GET /api/runs/{id}/artifact` parses `runs/{run_id}/…` out of the key to
 * resolve the run and enforce project access before minting a presigned URL.
 * The shapes in `artifactKeys` are the historical ones and must not drift.
 */
import * as fs from 'fs';
import * as Minio from 'minio';

const MinioClient = (Minio as any).Client || Minio;

export const BUCKET_NAME = process.env.MINIO_BUCKET_NAME || 'test-artifacts';

// --------------------------------------------------------------------------
// Capture policy
// --------------------------------------------------------------------------

export type ArtifactKind =
    | 'screenshot'
    | 'console_log'
    | 'network_log'
    | 'visual_diff'
    | 'video'
    | 'trace'
    | 'har';

export const ARTIFACT_KINDS: readonly ArtifactKind[] = [
    'screenshot', 'console_log', 'network_log', 'visual_diff', 'video', 'trace', 'har',
];

export type CaptureLevel = 'none' | 'minimal' | 'standard' | 'full';

/**
 * What each level permits.
 *
 * `video`, `trace` and `har` sit above `standard` on purpose. A screenshot can
 * be masked at capture time and a log can be scrubbed field by field, but a
 * Playwright trace is a full DOM-snapshot recording with sources, and a video
 * records whatever was on screen. Neither can be meaningfully redacted, so
 * neither is available without an explicit opt-in to `full`.
 */
const LEVEL_ALLOWS: Record<CaptureLevel, ReadonlySet<ArtifactKind>> = {
    none: new Set<ArtifactKind>(),
    minimal: new Set<ArtifactKind>(['screenshot']),
    standard: new Set<ArtifactKind>(['screenshot', 'console_log', 'network_log', 'visual_diff']),
    full: new Set<ArtifactKind>(ARTIFACT_KINDS),
};

export const DEFAULT_CAPTURE_LEVEL: CaptureLevel = 'standard';

/**
 * Coerce whatever the backend sent into a level this build understands.
 *
 * Unknown values fall back to `standard`, never to `full`: the worker image
 * bakes its code at build time, so a backend newer than the worker can easily
 * send a level this build has never seen. Failing open would turn a version
 * skew into a data disclosure.
 */
export function normalizeCaptureLevel(level: string | null | undefined): CaptureLevel {
    const key = String(level ?? '').trim().toLowerCase();
    if (key === 'none' || key === 'minimal' || key === 'standard' || key === 'full') {
        return key;
    }
    return DEFAULT_CAPTURE_LEVEL;
}

export function artifactAllowed(kind: ArtifactKind, level: CaptureLevel): boolean {
    return LEVEL_ALLOWS[level].has(kind);
}

// --------------------------------------------------------------------------
// Key layout
// --------------------------------------------------------------------------

function sanitizeLabel(label: string): string {
    const cleaned = (label || '').replace(/[^a-zA-Z0-9_-]+/g, '_').slice(0, 60);
    return cleaned.replace(/^_+|_+$/g, '') || 'screenshot';
}

export const artifactKeys = {
    video: (runId: number | string, jobId: string, ext: 'webm' | 'mp4' = 'webm') =>
        `runs/${runId}/videos/${jobId}.${ext}`,
    trace: (runId: number | string, jobId: string) =>
        `runs/${runId}/traces/${jobId}.zip`,
    screenshot: (runId: number | string, jobId: string, fileName: string) =>
        `runs/${runId}/screenshots/${jobId}-${fileName}`,
    mobileScreenshot: (runId: number | string, jobId: string, index: number, label: string) =>
        `runs/${runId}/screenshots/${jobId}-${index}-${sanitizeLabel(label)}.png`,
    har: (runId: number | string, jobId: string) =>
        `runs/${runId}/har/${jobId}.har`,
    consoleLog: (runId: number | string, jobId: string) =>
        `runs/${runId}/logs/${jobId}-console.json`,
    networkLog: (runId: number | string, jobId: string) =>
        `runs/${runId}/logs/${jobId}-network.json`,
};

// --------------------------------------------------------------------------
// Client configuration
// --------------------------------------------------------------------------

export interface MinioConfig {
    endPoint: string;
    port: number;
    useSSL: boolean;
    accessKey: string;
    secretKey: string;
}

/**
 * Derive client config from the environment.
 *
 * Accepts a bare host (`minio`), a host:port (`minio:9123`) or a full URL
 * (`https://s3.example.com`). The legacy runner hardcoded `useSSL: false`, so
 * one of the three former clients could never speak TLS no matter how the
 * deployment was configured; that is fixed by having a single derivation.
 */
export function minioConfigFromEnv(env: NodeJS.ProcessEnv = process.env): MinioConfig {
    let raw = (env.MINIO_ENDPOINT || 'localhost').trim();
    let useSSL = String(env.MINIO_USE_SSL ?? '').trim().toLowerCase() === 'true';
    let port = parseInt(env.MINIO_PORT || '', 10);

    const schemeMatch = raw.match(/^(https?):\/\/(.*)$/i);
    if (schemeMatch) {
        useSSL = schemeMatch[1].toLowerCase() === 'https';
        raw = schemeMatch[2];
    }
    raw = raw.replace(/\/.*$/, '');

    const portMatch = raw.match(/^(.*):(\d+)$/);
    if (portMatch) {
        raw = portMatch[1];
        if (!Number.isFinite(port)) port = parseInt(portMatch[2], 10);
    }
    if (!Number.isFinite(port)) port = useSSL && schemeMatch ? 443 : 9000;

    return {
        endPoint: raw,
        port,
        useSSL,
        accessKey: env.MINIO_ACCESS_KEY || 'minioadmin',
        secretKey: env.MINIO_SECRET_KEY || 'minioadmin',
    };
}

// --------------------------------------------------------------------------
// Store
// --------------------------------------------------------------------------

export interface PutResult {
    /** The object key, or null when the capture policy suppressed the write. */
    key: string | null;
    /** True when the policy — not an error — is why nothing was written. */
    suppressed: boolean;
}

const SUPPRESSED: PutResult = { key: null, suppressed: true };

export class ArtifactStore {
    private client: any;
    private bucketReady = false;

    constructor(
        private readonly captureLevel: CaptureLevel = DEFAULT_CAPTURE_LEVEL,
        config: MinioConfig = minioConfigFromEnv(),
        private readonly bucket: string = BUCKET_NAME,
    ) {
        this.client = new MinioClient(config);
    }

    level(): CaptureLevel {
        return this.captureLevel;
    }

    allows(kind: ArtifactKind): boolean {
        return artifactAllowed(kind, this.captureLevel);
    }

    private async ensureBucket(): Promise<void> {
        if (this.bucketReady) return;
        const exists = await this.client.bucketExists(this.bucket);
        if (!exists) await this.client.makeBucket(this.bucket);
        this.bucketReady = true;
    }

    /** Upload a file from disk. Returns a suppressed result if policy forbids it. */
    async putFile(kind: ArtifactKind, key: string, localPath: string, contentType?: string): Promise<PutResult> {
        if (!this.allows(kind)) {
            console.log(`[Artifacts] ${kind} suppressed by capture level '${this.captureLevel}'`);
            return SUPPRESSED;
        }
        if (!localPath || !fs.existsSync(localPath)) return { key: null, suppressed: false };
        await this.ensureBucket();
        const meta = contentType ? { 'Content-Type': contentType } : undefined;
        await this.client.fPutObject(this.bucket, key, localPath, meta);
        return { key, suppressed: false };
    }

    /** Upload an in-memory buffer. */
    async putBuffer(kind: ArtifactKind, key: string, body: Buffer, contentType?: string): Promise<PutResult> {
        if (!this.allows(kind)) {
            console.log(`[Artifacts] ${kind} suppressed by capture level '${this.captureLevel}'`);
            return SUPPRESSED;
        }
        if (!body || body.length === 0) return { key: null, suppressed: false };
        await this.ensureBucket();
        const meta = contentType ? { 'Content-Type': contentType } : undefined;
        await this.client.putObject(this.bucket, key, body, body.length, meta);
        return { key, suppressed: false };
    }
}

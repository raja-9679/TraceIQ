/**
 * The single upload chokepoint.
 *
 * Before this existed there were three MinIO clients (worker, mobile-worker,
 * legacy runner) and eleven scattered upload call sites, which is why there
 * was nowhere to put a redaction or capture-policy hook.
 *
 * Object key shapes are asserted literally because the backend parses them:
 * `GET /api/runs/{id}/artifact` resolves `runs/{run_id}/…` to a run and
 * enforces project access before minting a presigned URL. Changing a key
 * shape silently breaks artifact authorization.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
    ARTIFACT_KINDS,
    artifactAllowed,
    artifactKeys,
    minioConfigFromEnv,
    normalizeCaptureLevel,
} from './artifact-store';

// --------------------------------------------------------------------------
// Key shapes — must match what shipped, byte for byte
// --------------------------------------------------------------------------

test('video key matches the historical web layout', () => {
    assert.equal(artifactKeys.video(12, 'job-a'), 'runs/12/videos/job-a.webm');
});

test('mobile video key keeps its mp4 extension', () => {
    assert.equal(artifactKeys.video(12, 'job-a', 'mp4'), 'runs/12/videos/job-a.mp4');
});

test('trace key matches the historical layout', () => {
    assert.equal(artifactKeys.trace(12, 'job-a'), 'runs/12/traces/job-a.zip');
});

test('screenshot key matches the historical layout', () => {
    assert.equal(artifactKeys.screenshot(12, 'job-a', 'failure.png'),
        'runs/12/screenshots/job-a-failure.png');
});

test('har key matches the historical layout', () => {
    assert.equal(artifactKeys.har(12, 'job-a'), 'runs/12/har/job-a.har');
});

test('console and network log keys match the historical layout', () => {
    assert.equal(artifactKeys.consoleLog(12, 'job-a'), 'runs/12/logs/job-a-console.json');
    assert.equal(artifactKeys.networkLog(12, 'job-a'), 'runs/12/logs/job-a-network.json');
});

test('mobile screenshot key sanitises the label and keeps the index', () => {
    assert.equal(artifactKeys.mobileScreenshot(12, 'job-a', 3, 'after tap/login'),
        'runs/12/screenshots/job-a-3-after_tap_login.png');
});

test('mobile screenshot label falls back when empty', () => {
    assert.equal(artifactKeys.mobileScreenshot(12, 'job-a', 0, ''),
        'runs/12/screenshots/job-a-0-screenshot.png');
});

test('every key stays under the runs/{runId}/ prefix the backend authorizes on', () => {
    const keys = [
        artifactKeys.video(9, 'j'), artifactKeys.trace(9, 'j'),
        artifactKeys.screenshot(9, 'j', 'a.png'), artifactKeys.har(9, 'j'),
        artifactKeys.consoleLog(9, 'j'), artifactKeys.networkLog(9, 'j'),
        artifactKeys.mobileScreenshot(9, 'j', 1, 'x'),
    ];
    for (const key of keys) assert.ok(key.startsWith('runs/9/'), `${key} escaped the run prefix`);
});

// --------------------------------------------------------------------------
// Capture-level gating
// --------------------------------------------------------------------------

test('capture level none permits nothing at all', () => {
    for (const kind of ARTIFACT_KINDS) {
        assert.equal(artifactAllowed(kind, 'none'), false, `${kind} leaked at level none`);
    }
});

test('capture level minimal permits only screenshots', () => {
    assert.equal(artifactAllowed('screenshot', 'minimal'), true);
    assert.equal(artifactAllowed('video', 'minimal'), false);
    assert.equal(artifactAllowed('har', 'minimal'), false);
    assert.equal(artifactAllowed('trace', 'minimal'), false);
});

test('capture level standard permits screenshots and logs but not raw recordings', () => {
    assert.equal(artifactAllowed('screenshot', 'standard'), true);
    assert.equal(artifactAllowed('console_log', 'standard'), true);
    assert.equal(artifactAllowed('network_log', 'standard'), true);
    // A trace carries full DOM snapshots and a video records everything on
    // screen. Neither can be redacted, so neither is available below `full`.
    assert.equal(artifactAllowed('trace', 'standard'), false);
    assert.equal(artifactAllowed('video', 'standard'), false);
    assert.equal(artifactAllowed('har', 'standard'), false);
});

test('capture level full permits every kind', () => {
    for (const kind of ARTIFACT_KINDS) {
        assert.equal(artifactAllowed(kind, 'full'), true, `${kind} blocked at level full`);
    }
});

test('an unrecognised capture level falls back to standard, never to full', () => {
    // Version skew: a backend newer than this worker may send a level this
    // build has never heard of. Failing open to `full` would be a disclosure.
    assert.equal(normalizeCaptureLevel('supercapture' as any), 'standard');
    assert.equal(normalizeCaptureLevel(undefined), 'standard');
    assert.equal(normalizeCaptureLevel(null as any), 'standard');
    assert.equal(normalizeCaptureLevel(''), 'standard');
});

test('a recognised capture level is passed through', () => {
    assert.equal(normalizeCaptureLevel('none'), 'none');
    assert.equal(normalizeCaptureLevel('FULL'), 'full');
    assert.equal(normalizeCaptureLevel(' minimal '), 'minimal');
});

// --------------------------------------------------------------------------
// Client config
// --------------------------------------------------------------------------

test('config reads endpoint, port and credentials from the environment', () => {
    const cfg = minioConfigFromEnv({
        MINIO_ENDPOINT: 'minio', MINIO_PORT: '9000',
        MINIO_ACCESS_KEY: 'ak', MINIO_SECRET_KEY: 'sk',
    });
    assert.equal(cfg.endPoint, 'minio');
    assert.equal(cfg.port, 9000);
    assert.equal(cfg.accessKey, 'ak');
    assert.equal(cfg.secretKey, 'sk');
});

test('TLS is off unless MINIO_USE_SSL is exactly true', () => {
    assert.equal(minioConfigFromEnv({}).useSSL, false);
    assert.equal(minioConfigFromEnv({ MINIO_USE_SSL: 'false' }).useSSL, false);
    assert.equal(minioConfigFromEnv({ MINIO_USE_SSL: 'true' }).useSSL, true);
});

test('MINIO_USE_SSL is honoured regardless of case or padding', () => {
    // The legacy runner hardcoded useSSL:false and so ignored this entirely,
    // which meant one of the three clients could never speak TLS.
    assert.equal(minioConfigFromEnv({ MINIO_USE_SSL: 'TRUE' }).useSSL, true);
    assert.equal(minioConfigFromEnv({ MINIO_USE_SSL: ' true ' }).useSSL, true);
});

test('an endpoint given with a scheme is split into host and TLS flag', () => {
    const cfg = minioConfigFromEnv({ MINIO_ENDPOINT: 'https://s3.example.com' });
    assert.equal(cfg.endPoint, 's3.example.com');
    assert.equal(cfg.useSSL, true);
});

test('a bare host with no port defaults to 9000', () => {
    assert.equal(minioConfigFromEnv({ MINIO_ENDPOINT: 'minio' }).port, 9000);
});

test('a port embedded in the endpoint is used', () => {
    const cfg = minioConfigFromEnv({ MINIO_ENDPOINT: 'minio:9123' });
    assert.equal(cfg.endPoint, 'minio');
    assert.equal(cfg.port, 9123);
});

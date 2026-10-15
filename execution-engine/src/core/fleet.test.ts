import { test } from 'node:test';
import assert from 'node:assert/strict';

import { Heartbeat, HEARTBEAT_INDEX, HEARTBEAT_PREFIX, HEARTBEAT_TTL_S, buildHeartbeat, workerVersion } from './heartbeat';
import { streamsFor, workerRegion } from './job-queue';
import { tlsFilesFromEnv } from './backend-http';

function fakeRedis() {
    const calls: any[] = [];
    return {
        calls,
        set: async (...a: any[]) => { calls.push(['set', ...a]); },
        zadd: async (...a: any[]) => { calls.push(['zadd', ...a]); },
        del: async (...a: any[]) => { calls.push(['del', ...a]); },
        zrem: async (...a: any[]) => { calls.push(['zrem', ...a]); },
    };
}

const baseState = () => ({
    workerId: 'worker-abc12345', kind: 'web' as const, region: 'eu-west',
    streams: ['jobs:pending:eu-west', 'jobs:pending'], stepTypes: ['goto', 'click'],
    capabilities: { browsers: ['chromium'] }, startedAt: '2026-10-15T00:00:00Z',
    jobsProcessed: 0, jobsFailed: 0, currentJob: null,
});

test('heartbeat writes the payload with a TTL and indexes the worker', async () => {
    const r = fakeRedis();
    const hb = new Heartbeat(r, baseState());
    await hb.beat(new Date(1_700_000_000_000));
    const [set, zadd] = r.calls;
    assert.equal(set[0], 'set');
    assert.equal(set[1], HEARTBEAT_PREFIX + 'worker-abc12345');
    assert.deepEqual(set.slice(3), ['EX', HEARTBEAT_TTL_S]);
    const payload = JSON.parse(set[2]);
    assert.equal(payload.worker_id, 'worker-abc12345');
    assert.equal(payload.region, 'eu-west');
    assert.deepEqual(payload.step_types, ['goto', 'click']);
    assert.deepEqual(zadd, ['zadd', HEARTBEAT_INDEX, 1_700_000_000, 'worker-abc12345']);
});

test('heartbeat tracks the current job and the processed/failed counters', async () => {
    const r = fakeRedis();
    const hb = new Heartbeat(r, baseState());
    hb.jobStarted({ job_id: 'j1', run_id: 7 });
    assert.equal(hb.snapshot.currentJob?.job_id, 'j1');
    hb.jobFinished(true);
    hb.jobStarted({ job_id: 'j2', run_id: 7 });
    hb.jobFinished(false);
    assert.equal(hb.snapshot.currentJob, null);
    assert.equal(hb.snapshot.jobsProcessed, 2);
    assert.equal(hb.snapshot.jobsFailed, 1);
});

test('heartbeat never throws when Redis is down', async () => {
    const hb = new Heartbeat({
        set: async () => { throw new Error('ECONNREFUSED'); },
        zadd: async () => undefined, del: async () => undefined, zrem: async () => undefined,
    }, baseState());
    await hb.beat();   // must not reject
});

test('graceful stop removes the heartbeat immediately', async () => {
    const r = fakeRedis();
    const hb = new Heartbeat(r, baseState());
    await hb.stop();
    assert.deepEqual(r.calls.map(c => c[0]), ['del', 'zrem']);
});

test('version comes from package.json and the build git sha', () => {
    const v = workerVersion({ TRACEIQ_GIT_SHA: 'abc123' } as any);
    assert.equal(v.git_sha, 'abc123');
    assert.ok(v.version);
    const payload = buildHeartbeat(baseState(), new Date(0), { HOSTNAME: 'w1' } as any);
    assert.equal(payload.hostname, 'w1');
    assert.equal(payload.git_sha, null);
});

test('no WORKER_REGION consumes exactly the global stream (unchanged behaviour)', () => {
    assert.deepEqual(streamsFor('jobs:pending', {} as any), ['jobs:pending']);
    assert.equal(workerRegion({} as any), null);
});

test('a regional worker consumes its region first, then helps the global pool', () => {
    assert.deepEqual(streamsFor('jobs:pending', { WORKER_REGION: 'EU-West' } as any),
        ['jobs:pending:eu-west', 'jobs:pending']);
    assert.deepEqual(streamsFor('jobs:mobile:pending', { WORKER_REGION: 'ap1', WORKER_REGION_EXCLUSIVE: 'true' } as any),
        ['jobs:mobile:pending:ap1']);
});

test('an invalid region label fails at startup instead of consuming a dead stream', () => {
    assert.throws(() => workerRegion({ WORKER_REGION: 'eu west' } as any), /invalid/);
    assert.throws(() => workerRegion({ WORKER_REGION: 'x:y' } as any), /invalid/);
});

test('mTLS client identity needs both cert and key', () => {
    assert.equal(tlsFilesFromEnv({} as any), null);
    assert.deepEqual(tlsFilesFromEnv({ WORKER_TLS_CERT_FILE: '/c', WORKER_TLS_KEY_FILE: '/k' } as any),
        { cert: '/c', key: '/k', ca: undefined });
    assert.throws(() => tlsFilesFromEnv({ WORKER_TLS_CERT_FILE: '/c' } as any), /together/);
    assert.deepEqual(tlsFilesFromEnv({ WORKER_TLS_CA_FILE: '/ca' } as any), { cert: undefined, key: undefined, ca: '/ca' });
});

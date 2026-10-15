// Worker heartbeat (WP8): proof of life + what this worker is, for the
// backend's fleet page (GET /api/admin/workers) and Prometheus gauges.
//
// Written straight to Redis — the worker already holds a connection, and
// routing liveness through the API would make "API down" look like "every
// worker dead". Key shapes are shared with backend app/services/worker_fleet.py:
//
//   workers:hb:<worker_id>   JSON, EX 45   (absence = dead; a crashed worker
//                                           cannot announce its own death)
//   workers:index            ZSET id -> last-seen epoch seconds
//
// The worker id is the Redis consumer name, so XPENDING's per-consumer counts
// join onto the heartbeat.

import * as os from 'os';
import * as path from 'path';

export const HEARTBEAT_PREFIX = 'workers:hb:';
export const HEARTBEAT_INDEX = 'workers:index';
export const HEARTBEAT_INTERVAL_MS = 15_000;
export const HEARTBEAT_TTL_S = 45;

export interface CurrentJob {
    job_id: string;
    run_id: number;
    started_at: string;
}

export interface HeartbeatState {
    workerId: string;
    kind: 'web' | 'mobile' | 'local';
    region: string | null;
    streams: string[];
    stepTypes: readonly string[];
    capabilities: Record<string, any>;
    startedAt: string;
    jobsProcessed: number;
    jobsFailed: number;
    currentJob: CurrentJob | null;
}

/** package.json version + the build's git sha (Dockerfile.worker
 * `--build-arg GIT_SHA=...` -> TRACEIQ_GIT_SHA). */
export function workerVersion(env: NodeJS.ProcessEnv = process.env): { version: string | null; git_sha: string | null } {
    let version: string | null = null;
    for (const candidate of [path.join(__dirname, '..', '..', 'package.json'), path.join(process.cwd(), 'package.json')]) {
        try {
            // eslint-disable-next-line @typescript-eslint/no-var-requires
            version = require(candidate).version || null;
            if (version) break;
        } catch { /* try the next one */ }
    }
    const sha = (env.TRACEIQ_GIT_SHA || env.GIT_SHA || '').trim();
    return { version, git_sha: sha ? sha.slice(0, 40) : null };
}

export function buildHeartbeat(state: HeartbeatState, now: Date = new Date(), env: NodeJS.ProcessEnv = process.env): Record<string, any> {
    return {
        worker_id: state.workerId,
        kind: state.kind,
        hostname: env.HOSTNAME || os.hostname(),
        ...workerVersion(env),
        region: state.region,
        streams: state.streams,
        step_types: [...state.stepTypes],
        capabilities: state.capabilities,
        current_job: state.currentJob,
        jobs_processed: state.jobsProcessed,
        jobs_failed: state.jobsFailed,
        started_at: state.startedAt,
        pid: process.pid,
        last_seen: now.getTime() / 1000,
    };
}

/** Minimal Redis surface used here (ioredis-compatible; faked in tests). */
export interface HeartbeatRedis {
    set(key: string, value: string, mode: 'EX', seconds: number): Promise<any>;
    zadd(key: string, score: number, member: string): Promise<any>;
    del(key: string): Promise<any>;
    zrem(key: string, member: string): Promise<any>;
}

export class Heartbeat {
    private timer: NodeJS.Timeout | null = null;

    constructor(private redis: HeartbeatRedis, private state: HeartbeatState) {}

    get snapshot(): HeartbeatState { return this.state; }

    jobStarted(job: { job_id: string; run_id: number }): void {
        this.state.currentJob = { job_id: job.job_id, run_id: job.run_id, started_at: new Date().toISOString() };
        void this.beat();
    }

    jobFinished(failed: boolean): void {
        this.state.currentJob = null;
        this.state.jobsProcessed += 1;
        if (failed) this.state.jobsFailed += 1;
        void this.beat();
    }

    async beat(now: Date = new Date()): Promise<void> {
        try {
            const payload = buildHeartbeat(this.state, now);
            await this.redis.set(HEARTBEAT_PREFIX + this.state.workerId, JSON.stringify(payload), 'EX', HEARTBEAT_TTL_S);
            await this.redis.zadd(HEARTBEAT_INDEX, payload.last_seen, this.state.workerId);
        } catch (err: any) {
            // Never let liveness reporting break job processing.
            console.warn(`[Heartbeat] write failed: ${err?.message || err}`);
        }
    }

    start(intervalMs: number = HEARTBEAT_INTERVAL_MS): void {
        void this.beat();
        this.timer = setInterval(() => { void this.beat(); }, intervalMs);
        this.timer.unref();
    }

    /** Graceful shutdown: remove the key so the fleet page shows the worker
     * as gone now, instead of "lost" after the TTL. */
    async stop(): Promise<void> {
        if (this.timer) clearInterval(this.timer);
        this.timer = null;
        try {
            await this.redis.del(HEARTBEAT_PREFIX + this.state.workerId);
            await this.redis.zrem(HEARTBEAT_INDEX, this.state.workerId);
        } catch { /* best effort */ }
    }
}

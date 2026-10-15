// DB-driven worker configuration (WP8).
//
// Polls GET /api/internal/worker-config at start and every 60s and applies it:
//   * `env`  — values for env vars the worker reads at use time
//              (DEFAULT_TIMEOUT, MAX_JOB_DURATION_MS, RUNTIME_HEAL_ENABLED,
//              HAR_CAPTURE_ENABLED). `null` = keep the worker's OWN env value.
//   * `llm`  — the worker-side LLM provider; `null` = the worker's own env.
//
// Fallback is the env: the original value of every managed variable is
// snapshotted before the first apply and restored whenever the backend stops
// managing it. When the backend is unreachable (or predates this endpoint)
// the last applied config stays in force — the same last-known-good rule the
// backend's instance-settings cache follows, so a backend blip cannot flip a
// worker's timeouts back and forth.
//
// Env-only by construction (needed to start and to reach the backend at all):
// REDIS_URL, WORKER_WEBHOOK_SECRET / WEBHOOK_SECRET, BACKEND_URL,
// WORKER_TLS_*, MINIO_*, WORKER_REGION, APPIUM_URL / device-cloud credentials.
//
// The delivered LLM key is never logged.

import { backendFetch } from './backend-http';
import { reconfigureProvider, WorkerLlmConfig } from '../llm-provider';

export const MANAGED_ENV = ['DEFAULT_TIMEOUT', 'MAX_JOB_DURATION_MS', 'RUNTIME_HEAL_ENABLED', 'HAR_CAPTURE_ENABLED'] as const;

export interface WorkerConfig {
    version: number;
    env: Record<string, string | null>;
    llm: WorkerLlmConfig | null;
}

const original: Record<string, string | undefined> = {};
let snapshotted = false;

function snapshot(env: NodeJS.ProcessEnv): void {
    if (snapshotted) return;
    for (const key of MANAGED_ENV) original[key] = env[key];
    snapshotted = true;
}

/** Apply `cfg` to `env`. Returns the keys whose value changed. Exported for tests. */
export function applyWorkerEnv(cfg: Pick<WorkerConfig, 'env'>, env: NodeJS.ProcessEnv = process.env): string[] {
    snapshot(env);
    const changed: string[] = [];
    for (const key of MANAGED_ENV) {
        const delivered = cfg.env ? cfg.env[key] : null;
        const next = delivered === null || delivered === undefined ? original[key] : String(delivered);
        if (env[key] !== next) {
            if (next === undefined) delete env[key];
            else env[key] = next;
            changed.push(key);
        }
    }
    return changed;
}

/** For tests: forget the env snapshot. */
export function _resetWorkerConfigState(): void {
    for (const k of Object.keys(original)) delete original[k];
    snapshotted = false;
}

export async function fetchWorkerConfig(): Promise<WorkerConfig | null> {
    try {
        const res = await backendFetch('/api/internal/worker-config', { timeoutMs: 10000 });
        if (res.status === 404) return null;          // older backend
        if (!res.ok) {
            console.warn(`[WorkerConfig] backend returned ${res.status}; keeping current config`);
            return null;
        }
        const body = await res.json();
        if (!body || typeof body !== 'object' || typeof body.env !== 'object') return null;
        return body as WorkerConfig;
    } catch (err: any) {
        console.warn(`[WorkerConfig] backend unreachable (${err?.message || err}); keeping current config`);
        return null;
    }
}

export async function refreshWorkerConfig(): Promise<boolean> {
    const cfg = await fetchWorkerConfig();
    if (!cfg) return false;
    const changed = applyWorkerEnv(cfg);
    if (changed.length) console.log(`[WorkerConfig] applied: ${changed.join(', ')}`);
    reconfigureProvider(cfg.llm || null);
    return true;
}

let timer: NodeJS.Timeout | null = null;

export async function startWorkerConfigPolling(intervalMs = parseInt(process.env.WORKER_CONFIG_POLL_MS || '60000', 10)): Promise<void> {
    await refreshWorkerConfig();
    if (timer) clearInterval(timer);
    timer = setInterval(() => { void refreshWorkerConfig(); }, Math.max(5000, intervalMs));
    timer.unref();
}

export function stopWorkerConfigPolling(): void {
    if (timer) clearInterval(timer);
    timer = null;
}

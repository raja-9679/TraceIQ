import { api } from '@/lib/api';

// Mirrors backend/app/services/worker_fleet.py::fleet_snapshot (WP8).

export interface FleetWorker {
    worker_id: string;
    alive: boolean;
    last_seen: number | null;
    age_seconds: number;
    kind: 'web' | 'mobile' | 'local';
    hostname: string | null;
    version: string | null;
    git_sha: string | null;
    region: string | null;
    streams: string[];
    capabilities: Record<string, unknown>;
    current_job: { job_id: string; run_id: number; started_at?: string } | null;
    jobs_processed: number;
    jobs_failed: number;
    started_at: string | null;
    workspace_id: number | null;
    step_types_reported: boolean;
    missing_step_types: string[];
    version_skew: boolean;
}

export interface FleetStream {
    stream: string;
    group: string;
    region: string | null;
    length: number;
    lag: number | null;
    pending: number;
    consumers: { name: string; pending: number }[];
}

export interface FleetSnapshot {
    generated_at: number;
    heartbeat_interval_seconds: number;
    heartbeat_ttl_seconds: number;
    workers: FleetWorker[];
    streams: FleetStream[];
    dead_letter_depth: number;
    catalog_step_types: string[];
    warnings: string[];
    summary: { alive: number; lost: number; busy: number; skewed: number };
}

// Mirrors backend/app/api/job_queue.py. Jobs are summaries: the payload
// (which carries resolved secrets) never leaves the backend.
export interface QueuedJob {
    stream: string;
    entry_id: string;
    job_id: string | null;
    run_id: number | null;
    job_type: string;
    test_case_id: number | null;
    test_case_name: string | null;
    case_count: number;
    browser: string | null;
    device: string | null;
    retry_count: number | null;
    queued_at_ms: number | null;
    waiting_seconds: number | null;
    claimed: boolean;
    consumer: string | null;
    claimed_for_seconds: number | null;
    deliveries: number | null;
}

export interface QueueRun {
    run_id: number;
    status: string;
    live: boolean;
    project_id: number | null;
    project_name: string | null;
    suite_name: string | null;
    test_case_name: string | null;
    created_at: string | null;
}

export interface JobQueueSnapshot {
    generated_at: number;
    streams: { stream: string; group: string; total: number; shown: number; jobs: QueuedJob[] }[];
    runs: Record<string, QueueRun>;
}

export interface ClearQueueRequest {
    run_ids?: number[];
    entries?: { stream: string; entry_id: string }[];
    clear_all?: boolean;
}

export interface ClearQueueResult {
    cancelled_run_ids: number[];
    already_finished_run_ids: number[];
    jobs_deleted: number;
    jobs_left_running: number;
}

export interface DeadLetterEntry {
    entry_id: string;
    job_id: string | null;
    run_id: string | null;
    error: string | null;
    test_case_id: number | null;
    retry_count: number | null;
    payload_present: boolean;
}

export const fleetApi = {
    get: async (): Promise<FleetSnapshot> => (await api.get('/admin/workers')).data,
    queue: async (): Promise<JobQueueSnapshot> => (await api.get('/admin/job-queue')).data,
    clearQueue: async (body: ClearQueueRequest): Promise<ClearQueueResult> =>
        (await api.post('/admin/job-queue/clear', body)).data,
    deadLetters: async (): Promise<{ depth: number; entries: DeadLetterEntry[] }> =>
        (await api.get('/admin/dead-letter')).data,
    replayDeadLetters: async (body: { entry_ids?: string[]; replay_all?: boolean }) =>
        (await api.post('/admin/dead-letter/replay', body)).data,
    discardDeadLetters: async (body: { entry_ids?: string[]; discard_all?: boolean }) =>
        (await api.delete('/admin/dead-letter', { data: body })).data,
};

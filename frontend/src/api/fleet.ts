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

export const fleetApi = {
    get: async (): Promise<FleetSnapshot> => (await api.get('/admin/workers')).data,
};

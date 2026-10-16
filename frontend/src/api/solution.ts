import { api } from '@/lib/api';

// WP13c clients: sizing calculator (backend/app/api/sizing.py), demo
// workspace (app/api/demo_workspace.py) and integration test packs
// (app/api/test_packs.py).

export type CaptureLevel = 'none' | 'minimal' | 'standard' | 'full';

export interface SizingInput {
    executions_per_day: number;
    avg_duration_s: number;
    peak_hour_share: number;
    matrix_factor: number;
    largest_suite_cases: number;
    target_max_queue_wait_s: number;
    capture_level: CaptureLevel;
    failure_rate: number;
    retention_days: number;
    mobile_emulator: boolean;
    mobile_workers: number;
    ha: boolean;
    artifact_sizes_kb?: Record<string, number> | null;
}

export interface SizingFormula {
    key: string;
    label: string;
    formula: string;
    substituted: string;
    value: number;
    unit: string;
}

export interface SizingComponent { name: string; replicas: number; cpu: number; ram_gb: number }

export interface SizingResult {
    inputs: SizingInput;
    results: {
        workers: number;
        worker_cpu: number;
        worker_ram_gb: number;
        peak_jobs_per_hour: number;
        api_replicas: number;
        celery_replicas: number;
        postgres: { size_gb: number; cpu: number; ram_gb: number };
        redis_mb: number;
        object_store_gb: number;
        artifact_kb_per_job: number;
        artifact_kb_by_kind: Record<string, number>;
        mobile: { mobile_workers: number; emulators: number; cpu: number; ram_gb: number; note: string } | null;
        total_cpu: number;
        total_ram_gb: number;
        recommended_host: { cpu: number; ram_gb: number } | null;
        single_host: boolean;
        components: SizingComponent[];
    };
    compose: { EXECUTION_WORKERS: number; env: string };
    helm: { replicas: number; keda_min_replicas: number; keda_max_replicas: number; lag_count: number; values_yaml: string };
    formulas: SizingFormula[];
    assumptions: { key: string; text: string; source: string }[];
    defaults: { artifact_sizes_kb: Record<string, number>; capture_levels: CaptureLevel[] };
}

export interface DemoWorkspace {
    workspace_id: number;
    name: string;
    is_demo: true;
    created_at: string;
    projects: { id: number; name: string }[];
    runs_seeded?: number;
}

export interface PackVariable {
    name: string;
    description: string;
    required: boolean;
    secret: boolean;
    default: string | null;
}

export interface PackSummary {
    id: string;
    version: string;
    title: string;
    category: string | null;
    description: string;
    suite_name: string;
    variables: PackVariable[];
    suites: { name: string; description: string | null; cases: { name: string; description: string | null; tags: string[]; step_types: string[] }[] }[];
    case_count: number;
}

export interface PackImportResult {
    suite_id: number;
    suite_name: string;
    case_count: number;
    pack: { id: string; version: string };
    environment: { id: number; name: string };
    variables: { set: string[]; defaults_used: string[]; kept_existing: string[] };
    secrets: { stored: string[]; already_set: string[] };
    missing: string[];
}

export const sizingApi = {
    defaults: async (): Promise<SizingResult> => (await api.get('/sizing/defaults')).data,
    estimate: async (input: SizingInput): Promise<SizingResult> => (await api.post('/sizing/estimate', input)).data,
};

export const demoApi = {
    get: async (): Promise<DemoWorkspace | null> => (await api.get('/demo-workspace')).data.demo,
    create: async (): Promise<DemoWorkspace> => (await api.post('/demo-workspace')).data.demo,
    remove: async (): Promise<{ status: string; total_rows_deleted: number; objects_deleted: number }> =>
        (await api.delete('/demo-workspace')).data,
};

export const packsApi = {
    list: async (): Promise<PackSummary[]> => (await api.get('/test-packs')).data.packs,
    importPack: async (projectId: number, packId: string, body: { variables: Record<string, string>; environment_id?: number | null }): Promise<PackImportResult> =>
        (await api.post(`/projects/${projectId}/test-packs/${encodeURIComponent(packId)}/import`, body)).data,
    environments: async (projectId: number): Promise<{ id: number; name: string; is_default: boolean }[]> =>
        (await api.get(`/projects/${projectId}/environments`)).data,
};

/** Readable message from an axios error (string or {message} detail). */
export function errorText(e: unknown, fallback: string): string {
    const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail === 'object' && 'message' in detail) return String((detail as { message: unknown }).message);
    return fallback;
}

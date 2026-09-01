import { api } from '@/lib/api';

// Mirrors backend/app/api/data_policy.py

/** Least to most revealing. */
export type CaptureLevel = 'none' | 'minimal' | 'standard' | 'full';

export interface DataPolicy {
    capture_level: CaptureLevel;
    store_bodies: boolean;
    redact_headers: string[];
    redact_body_fields: string[];
    /** null means "use the built-in patterns"; [] means "scan for nothing". */
    redact_patterns: string[] | null;
    mask_selectors: string[];
    retention_days: number;
}

export interface DataPolicyView {
    /** What the project asked for. null when it has never been configured. */
    stored: Partial<DataPolicy> | null;
    /** What it actually gets, after the instance ceiling is applied. */
    effective: DataPolicy;
    instance_max_capture_level: CaptureLevel;
    requested_capture_level: CaptureLevel;
    /** True when the instance ceiling is holding this project below its request. */
    clamped: boolean;
    available_capture_levels: CaptureLevel[];
    available_patterns: string[];
    default_patterns: string[];
    /** Which artifact kinds the effective level permits. */
    permits: Record<string, boolean>;
}

export const getDataPolicy = (projectId: number) =>
    api.get<DataPolicyView>(`/projects/${projectId}/data-policy`).then((r) => r.data);

/**
 * Partial update — only the keys you send are changed. Omitted keys are left
 * alone, so editing one field cannot silently reset the others.
 */
export const updateDataPolicy = (projectId: number, patch: Partial<DataPolicy>) =>
    api.put<DataPolicyView>(`/projects/${projectId}/data-policy`, patch).then((r) => r.data);

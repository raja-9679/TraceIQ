import { api } from '@/lib/api';

// Mirrors backend/app/api/developer.py (project OpenAPI spec, WP11). The spec
// is what `expect_schema: {openapi: {...}}` steps resolve against at dispatch.

export interface SpecOperation {
    method: string;
    path: string;
    operationId: string | null;
}

export interface OpenApiSpecView {
    exists: boolean;
    title?: string | null;
    version?: string | null;
    source_url?: string | null;
    updated_at?: string | null;
    operations: SpecOperation[];
}

export const getOpenApiSpec = async (projectId: number): Promise<OpenApiSpecView> =>
    (await api.get(`/projects/${projectId}/openapi-spec`)).data;

/** Upload a parsed document, or ask the backend to fetch it from a URL. */
export const putOpenApiSpec = async (
    projectId: number, body: { spec?: Record<string, unknown>; url?: string },
): Promise<OpenApiSpecView> => (await api.put(`/projects/${projectId}/openapi-spec`, body)).data;

export const deleteOpenApiSpec = async (projectId: number): Promise<void> => {
    await api.delete(`/projects/${projectId}/openapi-spec`);
};

/** JSON only: YAML would need a parser in the bundle; convert first. */
export function parseSpecFile(text: string): Record<string, unknown> {
    let doc: unknown;
    try {
        doc = JSON.parse(text);
    } catch {
        throw new Error('The file is not valid JSON. Export the spec as JSON (YAML is not supported here), or give its URL instead.');
    }
    if (!doc || typeof doc !== 'object' || Array.isArray(doc)) {
        throw new Error('The file is not an OpenAPI document.');
    }
    const d = doc as Record<string, unknown>;
    if (!(d.openapi || d.swagger) || typeof d.paths !== 'object') {
        throw new Error('This is not an OpenAPI document: it needs an "openapi" (or "swagger") field and a "paths" object.');
    }
    return d;
}

// --- Version history + breaking-change diff (WP16b) --------------------------
// Mirrors the /openapi-spec/versions and /openapi-spec/diff endpoints in
// backend/app/api/developer.py; classification rules in
// backend/app/services/openapi_diff.py.

export interface SpecVersion {
    version: number;
    sha256: string;
    title: string | null;
    spec_version: string | null;
    source_url: string | null;
    operation_count: number;
    via: string;
    created_by_id: number | null;
    created_at: string;
    is_current: boolean;
}

export interface SpecVersionList {
    versions: SpecVersion[];
    max_versions: number;
    pinned_baseline: number | null;
}

export type ChangeSeverity = 'breaking' | 'non_breaking';

export interface SpecChangeCase {
    case_id: number;
    name: string | null;
    test_suite_id: number | null;
}

export interface SpecChange {
    id: string;
    severity: ChangeSeverity;
    rule: string;
    method: string;
    path: string;
    operation_id: string | null;
    location: string;
    message: string;
    status: string | null;
    old: unknown;
    new: unknown;
    affected_cases: SpecChangeCase[];
    promoted_reason: string | null;
}

export interface SpecAffectedTest extends SpecChangeCase {
    operations: string[];
    change_ids: string[];
}

export interface SpecDiff {
    from_version: number;
    to_version: number;
    breaking_count: number;
    non_breaking_count: number;
    truncated: boolean;
    changes: SpecChange[];
    affected_tests: SpecAffectedTest[];
}

export const listSpecVersions = async (projectId: number): Promise<SpecVersionList> =>
    (await api.get(`/projects/${projectId}/openapi-spec/versions`)).data;

export const getSpecDiff = async (projectId: number, from?: number, to?: number): Promise<SpecDiff> =>
    (await api.get(`/projects/${projectId}/openapi-spec/diff`, { params: { from, to } })).data;

/** SARIF 2.1.0 log of the same diff, as a Blob to save. */
export const getSpecDiffSarif = async (projectId: number, from: number, to: number): Promise<Blob> =>
    (await api.get(`/projects/${projectId}/openapi-spec/diff/sarif`, { params: { from, to }, responseType: 'blob' })).data;

/** Default comparison: the newest version against the one before it. */
export function defaultDiffPair(versions: SpecVersion[]): { from: number; to: number } | null {
    const nums = versions.map((v) => v.version).sort((a, b) => b - a);
    return nums.length >= 2 ? { from: nums[1], to: nums[0] } : null;
}

export interface OperationGroup {
    key: string;
    method: string;
    path: string;
    breaking: SpecChange[];
    nonBreaking: SpecChange[];
    cases: SpecChangeCase[];
}

/** Changes grouped per operation, operations with breaking changes first. */
export function groupByOperation(changes: SpecChange[]): OperationGroup[] {
    const groups = new Map<string, OperationGroup>();
    for (const c of changes) {
        const key = `${c.method} ${c.path}`.trim() || 'spec';
        let g = groups.get(key);
        if (!g) {
            g = { key, method: c.method, path: c.path, breaking: [], nonBreaking: [], cases: [] };
            groups.set(key, g);
        }
        (c.severity === 'breaking' ? g.breaking : g.nonBreaking).push(c);
        for (const a of c.affected_cases) {
            if (!g.cases.some((x) => x.case_id === a.case_id)) g.cases.push(a);
        }
    }
    return [...groups.values()].sort((a, b) =>
        (b.breaking.length > 0 ? 1 : 0) - (a.breaking.length > 0 ? 1 : 0) || a.key.localeCompare(b.key));
}

/** "response_property_removed" → "Response property removed" */
export function ruleLabel(rule: string): string {
    const text = rule.replace(/_/g, ' ');
    return text.charAt(0).toUpperCase() + text.slice(1);
}

import { api } from '@/lib/api';

// Mirrors backend/app/api/access_matrix.py + app/services/access_matrix.py

export type CellExpect = 'allow' | 'deny' | 'skip';
export type Verdict = 'pass' | 'fail' | 'inconclusive' | 'pending';
export type RowKind = 'case' | 'request' | 'page';

export interface CellSpec {
    expect: CellExpect;
    /** allow: exact status required; deny: an extra status that also counts as denied. */
    status?: number | null;
}

export interface SwapSpec {
    owner_persona_id: number;
    method: string;
    url: string;
    body?: unknown;
    extractor: { source: 'json' | 'regex' | 'header'; expr: string };
    placeholder: string;
}

export interface MatrixRow {
    key: string;
    label: string;
    kind: RowKind;
    test_case_id?: number | null;
    method?: string;
    url?: string | null;
    body?: unknown;
    headers?: Record<string, string> | null;
    selector?: string | null;
    swap?: SwapSpec | null;
    /** Keyed by persona id (string) or "anonymous". */
    cells: Record<string, CellSpec>;
}

export interface AccessMatrix {
    id: number;
    project_id: number;
    name: string;
    description?: string | null;
    base_url?: string | null;
    persona_ids: number[];
    include_anonymous: boolean;
    deny_statuses: number[];
    rows: MatrixRow[];
    created_at: string;
    updated_at: string;
}

export type AccessMatrixInput = Pick<AccessMatrix, 'name' | 'description' | 'base_url' | 'persona_ids' | 'include_anonymous' | 'rows'> & {
    deny_statuses?: number[] | null;
};

export interface MatrixPersona {
    id: number;
    name: string;
    project_id: number | null;
    last_refreshed_at: string | null;
}

export interface Observation {
    status?: number;
    url?: string;
    final_url?: string;
    headers?: Record<string, string>;
    body_excerpt?: string;
    element_present?: boolean | null;
    screenshot?: string | null;
    owner_status?: number;
    swapped_id?: string;
    error_kind?: string;
    error?: string;
    run_id?: number;
    run_status?: string;
}

export interface MatrixCellResult {
    id: number;
    row_key: string;
    persona_key: string;
    expect: 'allow' | 'deny';
    expected_status: number | null;
    verdict: Verdict;
    reason: string | null;
    observed: Observation | null;
    test_run_id: number | null;
    completed_at: string | null;
}

export interface MatrixRunSummary {
    pass: number;
    fail: number;
    inconclusive: number;
    pending: number;
    total: number;
    access_granted: number;
    allow_regressions: number;
    findings?: { created: number; updated: number; resolved: number };
}

export interface MatrixRun {
    id: number;
    matrix_id: number;
    status: 'pending' | 'running' | 'completed' | 'error';
    matrix_group: string;
    summary: MatrixRunSummary | null;
    authorized: boolean;
    error: string | null;
    created_at: string;
    finished_at: string | null;
    snapshot?: {
        name: string;
        base_url: string | null;
        columns: string[];
        persona_names: Record<string, string>;
        deny_statuses: number[];
        rows: MatrixRow[];
    };
    cells?: MatrixCellResult[];
}

export interface MatrixFinding {
    id: number;
    title: string;
    severity: string;
    status: 'open' | 'acknowledged' | 'false_positive' | 'resolved';
    category: string;
    evidence: string | null;
    target_url: string | null;
    created_at: string;
    resolved_at: string | null;
    /** The latest run of this matrix whose cell granted the access. */
    matrix_run_id: number;
    cell_id: number;
    row_key: string;
    persona_key: string;
}

export interface ProjectCase {
    id: number;
    name: string;
    test_suite_id: number;
}

export const accessMatrixApi = {
    list: (projectId: number) =>
        api.get<AccessMatrix[]>(`/projects/${projectId}/access-matrices`).then(r => r.data),
    personas: (projectId: number) =>
        api.get<MatrixPersona[]>(`/projects/${projectId}/access-matrix-personas`).then(r => r.data),
    create: (projectId: number, body: AccessMatrixInput) =>
        api.post<AccessMatrix>(`/projects/${projectId}/access-matrices`, body).then(r => r.data),
    update: (id: number, body: AccessMatrixInput) =>
        api.put<AccessMatrix>(`/access-matrices/${id}`, body).then(r => r.data),
    remove: (id: number) => api.delete(`/access-matrices/${id}`).then(r => r.data),
    run: (id: number, authorized: boolean) =>
        api.post<MatrixRun>(`/access-matrices/${id}/run`, { authorized }).then(r => r.data),
    runs: (id: number) => api.get<MatrixRun[]>(`/access-matrices/${id}/runs`).then(r => r.data),
    getRun: (runId: number) => api.get<MatrixRun>(`/access-matrix-runs/${runId}`).then(r => r.data),
    screenshot: (runId: number, cellId: number) =>
        api.get<{ url: string }>(`/access-matrix-runs/${runId}/cells/${cellId}/screenshot`).then(r => r.data),
    findings: (id: number) =>
        api.get<MatrixFinding[]>(`/access-matrices/${id}/findings`).then(r => r.data),
    cases: (projectId: number) =>
        api.get<{ items: ProjectCase[] }>(`/cases`, { params: { project_id: projectId, limit: 200 } })
            .then(r => r.data.items),
};

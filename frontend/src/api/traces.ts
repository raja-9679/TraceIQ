import { api } from '@/lib/api';

// Mirrors backend/app/services/trace_backends.py::build_result_traces and
// app/api/trace_correlation.py (WP16a).

export interface TraceSpan {
    span_id: string;
    parent_span_id: string | null;
    service: string;
    operation: string;
    /** From the step's first span, in ms. */
    offset_ms: number;
    duration_ms: number;
    error: boolean;
    status_message: string | null;
    attributes: Record<string, string>;
}

export interface StepTraces {
    step: string;
    index: number;
    type: string | null;
    trace_id: string;
    span_id: string;
    requests: number | null;
    status: 'ok' | 'no_telemetry';
    reason: string | null;
    spans: TraceSpan[];
    error_spans: number;
    truncated: boolean;
}

export interface ResultTraces {
    configured: boolean;
    backend: 'jaeger' | 'tempo' | null;
    status: 'ok' | 'no_telemetry';
    reason: string | null;
    error: string | null;
    steps: StepTraces[];
}

export interface TraceSettings {
    enabled: boolean;
    cross_origin: boolean;
    backend_configured: boolean;
    backend: 'jaeger' | 'tempo' | null;
}

export const tracesApi = {
    forResult: async (runId: number | string, resultId: number): Promise<ResultTraces> =>
        (await api.get(`/runs/${runId}/results/${resultId}/traces`)).data,
    getSettings: async (projectId: number): Promise<TraceSettings> =>
        (await api.get(`/projects/${projectId}/trace-settings`)).data,
    putSettings: async (projectId: number, body: Partial<Pick<TraceSettings, 'enabled' | 'cross_origin'>>): Promise<TraceSettings> =>
        (await api.put(`/projects/${projectId}/trace-settings`, body)).data,
};

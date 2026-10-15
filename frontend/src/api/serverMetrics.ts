import { api } from '@/lib/api';

// Mirrors backend/app/services/server_metrics.py::evaluate (WP8).

export interface ServerMetricSeries {
    name: string;
    query: string;
    result: { labels: Record<string, string>; points: [number, number][] }[];
    error: string | null;
}

export interface ServerMetrics {
    configured: boolean;
    window: { start: string; end: string };
    step_seconds: number;
    series: ServerMetricSeries[];
    queries_defined: number;
}

export const serverMetricsApi = {
    get: async (runId: number | string, resultId: number): Promise<ServerMetrics> =>
        (await api.get(`/runs/${runId}/results/${resultId}/server-metrics`)).data,
};

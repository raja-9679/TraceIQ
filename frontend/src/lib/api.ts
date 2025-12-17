import axios from "axios";

export const api = axios.create({
    baseURL: "http://localhost:8000/api",
});

export interface TestRun {
    id: number;
    created_at: string;
    status: "pending" | "running" | "passed" | "failed" | "error";
    total_tests: number;
    passed_tests: number;
    failed_tests: number;
    duration_ms: number | null;
    trace_url?: string;
    video_url?: string;
    error_message?: string;
    ai_analysis?: string;
    response_status?: number;
    request_headers?: Record<string, string>;
    response_headers?: Record<string, string>;
}

export const getRuns = async (): Promise<TestRun[]> => {
    const response = await api.get("/runs");
    return response.data;
};

export const getRun = async (id: number): Promise<TestRun> => {
    if (!id || id <= 0) {
        throw new Error("Invalid run ID");
    }
    const response = await api.get(`/runs/${id}`);
    return response.data;
};

export const triggerRun = async (): Promise<TestRun> => {
    const response = await api.post("/runs");
    return response.data;
};

export const getArtifactUrl = async (path: string): Promise<string> => {
    // path is like "runs/1/trace.zip"
    const response = await api.get(`/artifacts/${encodeURIComponent(path)}`);
    return response.data.url;
};

import axios from "axios";

export const api = axios.create({
    baseURL: "http://localhost:8000/api",
});

api.interceptors.request.use((config) => {
    const token = localStorage.getItem("token");
    if (token) {
        config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
});

export interface TestRun {
    id: number;
    created_at: string;
    status: "pending" | "running" | "passed" | "failed" | "error";
    total_tests: number;
    passed_tests: number;
    failed_tests: number;
    duration_ms: number | null;
    suite_name?: string;
    test_case_name?: string;
    trace_url?: string;
    video_url?: string;
    error_message?: string;
    ai_analysis?: string;
    response_status?: number;
    request_headers?: Record<string, string>;
    response_headers?: Record<string, string>;
    network_events?: any[];
    execution_log?: any[];
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

export const triggerRun = async (suiteId: number, caseId?: number): Promise<TestRun | TestRun[]> => {
    let url = `/runs?suite_id=${suiteId}`;
    if (caseId) {
        url += `&case_id=${caseId}`;
    }
    const response = await api.post(url);
    return response.data;
};

export const getTestCase = async (caseId: number): Promise<any> => {
    const response = await api.get(`/cases/${caseId}`);
    return response.data;
};

export const updateTestCase = async (caseId: number, data: any): Promise<any> => {
    const response = await api.put(`/cases/${caseId}`, data);
    return response.data;
};

export const getArtifactUrl = async (path: string): Promise<string> => {
    // path is like "runs/1/trace.zip"
    const response = await api.get(`/artifacts/${encodeURIComponent(path)}`);
    return response.data.url;
};

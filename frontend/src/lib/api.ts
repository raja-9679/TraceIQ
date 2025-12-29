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
    browser?: string;
    device?: string;
    screenshots?: string[];
    results?: {
        id: number;
        test_name: string;
        status: "pending" | "running" | "passed" | "failed" | "error";
        duration_ms: number;
        error_message?: string;
        screenshots?: string[];
        response_status?: number;
        response_headers?: Record<string, string>;
        response_body?: string;
        request_headers?: Record<string, string>;
        request_body?: string;
        request_url?: string;
        request_method?: string;
    }[];
    user?: {
        id: number;
        email: string;
        full_name: string;
    };
}

export const getRuns = async (
    limit: number = 50,
    offset: number = 0,
    search?: string,
    status?: string,
    browser?: string,
    device?: string
): Promise<{ runs: TestRun[], total: number, limit: number, offset: number }> => {
    const params = new URLSearchParams({
        limit: limit.toString(),
        offset: offset.toString(),
    });
    if (search) params.append('search', search);
    if (status) params.append('status', status);
    if (browser) params.append('browser', browser);
    if (device) params.append('device', device);

    const response = await api.get(`/runs?${params.toString()}`);
    return response.data;
};

export const getRun = async (id: number): Promise<TestRun> => {
    if (!id || id <= 0) {
        throw new Error("Invalid run ID");
    }
    const response = await api.get(`/runs/${id}`);
    return response.data;
};

export const triggerRun = async (suiteId: number, caseId?: number, browser: string | string[] = "chromium", device?: string | string[]): Promise<TestRun | TestRun[]> => {
    let url = `/runs?suite_id=${suiteId}`;

    if (Array.isArray(browser)) {
        browser.forEach(b => url += `&browser=${b}`);
    } else {
        url += `&browser=${browser}`;
    }

    if (caseId) {
        url += `&case_id=${caseId}`;
    }

    if (device) {
        if (Array.isArray(device)) {
            device.forEach(d => url += `&device=${encodeURIComponent(d)}`);
        } else {
            url += `&device=${encodeURIComponent(device)}`;
        }
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

export const deleteRun = async (runId: number): Promise<void> => {
    await api.delete(`/runs/${runId}`);
};

// Settings API
export interface UserSettings {
    id: number;
    user_id: number;
    theme: string;
    timezone: string;
    date_format: string;
    default_browser: string;
    default_device: string;
    default_timeout: number;
    auto_retry: boolean;
    max_retries: number;
    parallel_execution: boolean;
    max_parallel_tests: number;
    multi_browser_enabled: boolean;
    selected_browsers: string[];
    multi_device_enabled: boolean;
    selected_devices: string[];
    email_notifications: boolean;
    notify_on_completion: boolean;
    notify_on_failure: boolean;
    daily_summary: boolean;
    notification_email: string | null;
    video_recording: string;
    screenshot_on_error: boolean;
    trace_files: boolean;
    retention_period: number;
    auto_cleanup: boolean;
}

export const getSettings = async (): Promise<UserSettings> => {
    const response = await api.get("/settings");
    return response.data;
};

export const updateSettings = async (settings: Partial<UserSettings>): Promise<UserSettings> => {
    const response = await api.put("/settings", settings);
    return response.data;
};

export const deleteRuns = async (data: { runIds?: number[], all?: boolean }): Promise<void> => {
    let url = "/runs";
    if (data.all) {
        url += "?all=true";
    } else if (data.runIds && data.runIds.length > 0) {
        // Pass IDs as repeated query params: ?run_ids=1&run_ids=2
        const params = new URLSearchParams();
        data.runIds.forEach(id => params.append("run_ids", id.toString()));
        url += `?${params.toString()}`;
    }
    await api.delete(url);
};

export const updateTestSuite = async (suiteId: number, data: any): Promise<any> => {
    const response = await api.put(`/suites/${suiteId}`, data);
    return response.data;
};

export const exportTestCase = async (caseId: number): Promise<any> => {
    const response = await api.get(`/cases/${caseId}/export`);
    return response.data;
};

export const importTestCase = async (suiteId: number, data: any): Promise<any> => {
    const response = await api.post(`/suites/${suiteId}/import-case`, data);
    return response.data;
};

export const exportTestSuite = async (suiteId: number): Promise<any> => {
    const response = await api.get(`/suites/${suiteId}/export`);
    return response.data;
};

export const importTestSuite = async (suiteId?: number, data?: any): Promise<any> => {
    const url = suiteId ? `/suites/${suiteId}/import-suite` : `/suites/import-suite`;
    const response = await api.post(url, data);
    return response.data;
};

export const getAuditLog = async (entityType: string, entityId: number): Promise<any[]> => {
    const response = await api.get(`/audit/${entityType}/${entityId}`);
    return response.data;
};


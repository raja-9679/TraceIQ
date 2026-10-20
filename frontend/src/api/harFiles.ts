import { api } from '@/lib/api';

// Mirrors backend/app/api/har_files.py.

export interface HarFile {
    id: number;
    project_id: number;
    name: string;
    size_bytes: number;
    entry_count: number;
    hosts: string[];
    sha256: string;
    created_by_id: number | null;
    created_at: string;
}

export const MAX_HAR_MB = 20;

export const harFilesApi = {
    list: async (projectId: number): Promise<HarFile[]> =>
        (await api.get(`/projects/${projectId}/har-files`)).data,
    upload: async (projectId: number, file: File, name?: string): Promise<HarFile> => {
        const form = new FormData();
        form.append('file', file);
        if (name) form.append('name', name);
        return (await api.post(`/projects/${projectId}/har-files`, form)).data;
    },
    remove: async (id: number): Promise<void> => {
        await api.delete(`/har-files/${id}`);
    },
};

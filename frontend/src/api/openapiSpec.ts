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

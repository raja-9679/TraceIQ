// Helpers for the http-request `expect_schema` editor (WP11). The worker
// validates the JSON response body against `schema`; an `openapi` reference
// is resolved to a schema by the backend at dispatch from the project's
// stored OpenAPI spec.

export type ExpectSchemaMode = 'none' | 'inline' | 'openapi';

export interface OpenApiRef {
    operationId?: string;
    method?: string;
    path?: string;
}

export interface ExpectSchema {
    schema?: Record<string, unknown>;
    openapi?: OpenApiRef;
    status?: string;
}

export function schemaMode(es: ExpectSchema | undefined | null): ExpectSchemaMode {
    if (!es || typeof es !== 'object') return 'none';
    if (es.openapi) return 'openapi';
    return 'inline';
}

export function parseInlineSchema(text: string): { schema?: Record<string, unknown>; error?: string } {
    if (!text.trim()) return { error: 'Enter a JSON Schema object' };
    try {
        const parsed = JSON.parse(text);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
            return { error: 'A JSON Schema must be an object' };
        }
        return { schema: parsed };
    } catch {
        return { error: 'Not valid JSON' };
    }
}

// operationId wins when given (it survives path renames); otherwise
// method + path. Empty fields are dropped so the stored step stays minimal.
export function buildOpenApiSchema(fields: { operationId: string; method: string; path: string; status: string }): ExpectSchema {
    const ref: OpenApiRef = fields.operationId.trim()
        ? { operationId: fields.operationId.trim() }
        : {
            ...(fields.method.trim() ? { method: fields.method.trim().toUpperCase() } : {}),
            ...(fields.path.trim() ? { path: fields.path.trim() } : {}),
        };
    return { openapi: ref, ...(fields.status.trim() ? { status: fields.status.trim() } : {}) };
}

export function openApiRefError(es: ExpectSchema): string | null {
    const ref = es.openapi || {};
    if (ref.operationId) return null;
    if (ref.method && ref.path) return null;
    return 'Give an operationId, or both method and path';
}

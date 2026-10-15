/**
 * `expect_schema` on http-request steps (WP11): validate a response body
 * against a JSON Schema and report every violation by path.
 *
 * Shapes (params.expect_schema):
 *   { schema: {...} }                       inline JSON Schema (object or JSON string)
 *   { openapi: {...}, schema: {...}, source } resolved by the backend at dispatch
 *   { openapi: {...}, error: "..." }        the backend could not resolve it
 *
 * Unlike the older `json-schema` assertion this is strict: the schema means
 * what it says (no implicit additionalProperties/nullable loosening), because
 * a contract check that quietly relaxes the contract is not a contract check.
 * `nullable` (OpenAPI 3.0) is understood by ajv natively.
 */
import Ajv from 'ajv';
import addFormats from 'ajv-formats';

export const MAX_SCHEMA_VIOLATIONS = 20;

export interface SchemaViolation {
    path: string;
    message: string;
}

function makeAjv(): Ajv {
    // strict: false — OpenAPI documents carry keywords JSON Schema doesn't
    // know (`example`, `xml`, `discriminator`, the `components` holder our
    // resolver wraps around the schema); they must not fail compilation.
    const ajv = new Ajv({ allErrors: true, strict: false });
    addFormats(ajv);
    return ajv;
}

export function checkExpectSchema(expectSchema: any, rawBody: string, jsonBody: any): SchemaViolation[] {
    if (!expectSchema || typeof expectSchema !== 'object') {
        return [{ path: '', message: 'expect_schema must be an object' }];
    }
    if (expectSchema.error) {
        return [{ path: '', message: `expect_schema could not be resolved: ${expectSchema.error}` }];
    }
    let schema = expectSchema.schema;
    if (typeof schema === 'string') {
        try { schema = JSON.parse(schema); } catch (e: any) {
            return [{ path: '', message: `expect_schema.schema is not valid JSON: ${e.message}` }];
        }
    }
    if (!schema || typeof schema !== 'object') {
        return [{ path: '', message: expectSchema.openapi
            ? 'expect_schema.openapi was not resolved (worker received no schema; is the backend older than this step?)'
            : 'expect_schema needs a schema object' }];
    }
    if (jsonBody === undefined) {
        const head = (rawBody || '').slice(0, 80);
        return [{ path: '', message: `response is not JSON (starts with ${JSON.stringify(head)})` }];
    }
    let validate;
    try {
        validate = makeAjv().compile(schema);
    } catch (e: any) {
        return [{ path: '', message: `invalid JSON Schema: ${e.message}` }];
    }
    if (validate(jsonBody)) return [];
    return (validate.errors || []).slice(0, MAX_SCHEMA_VIOLATIONS).map((e: any) => {
        let message = e.message || e.keyword;
        if (e.keyword === 'required') message = `missing required property "${e.params?.missingProperty}"`;
        else if (e.keyword === 'additionalProperties') message = `unexpected property "${e.params?.additionalProperty}"`;
        else if (e.keyword === 'enum') message = `must be one of ${JSON.stringify(e.params?.allowedValues)}`;
        return { path: e.instancePath || '/', message };
    });
}

export function formatViolations(violations: SchemaViolation[], source?: string): string {
    const where = source ? ` (${source})` : '';
    const total = violations.length >= MAX_SCHEMA_VIOLATIONS ? `${violations.length}+` : `${violations.length}`;
    return `Response schema check failed${where}: ${total} violation(s): ` +
        violations.map(v => (v.path ? `${v.path}: ${v.message}` : v.message)).join('; ');
}

import { Page, FrameLocator, Locator } from 'playwright';
import { DOMParser } from '@xmldom/xmldom';
import * as xpath from 'xpath';
import Ajv from 'ajv';
import addFormats from 'ajv-formats';
import * as path from 'path';
import { spawn } from 'child_process';

// Dot-path lookup into a parsed JSON body. Supports `a.b.c`, numeric array
// segments (`items.0.id`), and bracket indexing (`items[0].id`). Returns
// undefined when any segment is missing.
export function jsonPath(obj: any, pathExpr: string): any {
    if (!pathExpr) return undefined;
    let current = obj;
    // normalize [n] → .n so both syntaxes work
    const parts = pathExpr.replace(/\[(\d+)\]/g, '.$1').split('.').filter(Boolean);
    for (const part of parts) {
        if (current === undefined || current === null) return undefined;
        current = current[part];
    }
    return current;
}

// Lightweight test-data generator for {{fake.KIND}} interpolation. Kept
// dependency-free on purpose; covers the common kinds. Unknown kinds return
// the token unchanged so mistakes are visible rather than silently blank.
function generateFake(kind: string): string {
    const rnd = (n: number) => Math.floor(Math.random() * n);
    const pick = <T>(a: T[]): T => a[rnd(a.length)];
    const firsts = ['alex', 'sam', 'jordan', 'taylor', 'riley', 'morgan', 'casey', 'jamie'];
    const lasts = ['smith', 'jones', 'patel', 'kim', 'garcia', 'khan', 'lee', 'nair'];
    const first = pick(firsts);
    const last = pick(lasts);
    const suffix = Date.now().toString(36) + rnd(1e6).toString(36);
    switch (kind.toLowerCase()) {
        case 'uuid':
            return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
                const r = rnd(16);
                const v = c === 'x' ? r : (r & 0x3) | 0x8;
                return v.toString(16);
            });
        case 'email':
            return `${first}.${last}.${suffix}@example.com`;
        case 'name':
            return `${first[0].toUpperCase()}${first.slice(1)} ${last[0].toUpperCase()}${last.slice(1)}`;
        case 'first_name':
        case 'firstname':
            return `${first[0].toUpperCase()}${first.slice(1)}`;
        case 'last_name':
        case 'lastname':
            return `${last[0].toUpperCase()}${last.slice(1)}`;
        case 'username':
            return `${first}_${last}_${rnd(1000)}`;
        case 'phone':
            return `+1${(2000000000 + rnd(999999999)).toString().slice(0, 10)}`;
        case 'number':
        case 'int':
            return String(rnd(1000000));
        case 'date':
            // Deterministic offset from a fixed epoch to avoid new Date() dependence.
            return new Date(Date.now() - rnd(1e10)).toISOString().slice(0, 10);
        case 'word':
            return pick(['lorem', 'ipsum', 'dolor', 'sit', 'amet', 'consectetur']);
        default:
            return `{{fake.${kind}}}`;
    }
}

export class TestExecutor {
    public static async executeStep(
        page: Page,
        context: Page | FrameLocator,
        step: any,
        globalSettings: any = {},
        testCaseContext?: any
    ): Promise<any> {
        console.log(`  Step: ${step.type} ${step.selector || ''} ${step.value || ''}`);

        const moveMouseTo = async (locator: Locator) => {
            try {
                await locator.hover();
            } catch (e) {
                // ignore
            }
        };

        const resolve = (val: any): any => {
            if (typeof val === 'string') {
                let out = val;
                // {{env.KEY}} — environment variables, {{secret.KEY}} — project
                // secrets: both dispatched in job settings by the backend.
                const envVars = globalSettings?.environment?.variables;
                if (envVars) {
                    out = out.replace(/\{\{\s*env\.(\w+)\s*\}\}/g, (_: string, key: string) =>
                        envVars[key] !== undefined ? String(envVars[key]) : `{{env.${key}}}`);
                }
                const secrets = globalSettings?.secrets;
                if (secrets) {
                    out = out.replace(/\{\{\s*secret\.(\w+)\s*\}\}/g, (_: string, key: string) =>
                        secrets[key] !== undefined ? String(secrets[key]) : `{{secret.${key}}}`);
                }
                // {{data.KEY}} — the current data-driven dataset row.
                const dataRow = testCaseContext?.data;
                if (dataRow) {
                    out = out.replace(/\{\{\s*data\.(\w+)\s*\}\}/g, (_: string, key: string) =>
                        dataRow[key] !== undefined ? String(dataRow[key]) : `{{data.${key}}}`);
                }
                // {{fake.KIND}} — generated test data (email/uuid/name/...).
                if (out.includes('{{fake.')) {
                    out = out.replace(/\{\{\s*fake\.(\w+)\s*\}\}/g, (_: string, kind: string) =>
                        generateFake(kind));
                }
                // {{name}} — runtime variables from extract-value / scripts.
                if (testCaseContext?.variables) {
                    out = out.replace(/\{\{\s*(\w+)\s*\}\}/g, (_: string, key: string) =>
                        testCaseContext.variables[key] !== undefined ? String(testCaseContext.variables[key]) : `{{${key}}}`);
                }
                return out;
            }
            // Simple recursion for objects/arrays (shallow for headers/params is usually enough, but let's go one level deep if needed)
            if (val && typeof val === 'object') {
                if (Array.isArray(val)) return val.map(item => resolve(item));
                const newObj: any = {};
                for (const k in val) newObj[k] = resolve(val[k]);
                return newObj;
            }
            return val;
        };

        const getLocator = (selector: string) => {
            return context.locator(selector).first();
        };

        // Get timeout from environment with sensible default for slow pages
        const gotoTimeout = parseInt(process.env.DEFAULT_TIMEOUT || '60000', 10);

        switch (step.type) {
            case 'goto': {
                let url = resolve(step.value || step.selector) || 'about:blank';
                // Relative URL + environment base_url → environment-portable suites.
                if (url.startsWith('/') && globalSettings?.environment?.base_url) {
                    url = String(globalSettings.environment.base_url).replace(/\/$/, '') + url;
                    console.log(`  Resolved relative URL against environment base_url: ${url}`);
                }
                if (globalSettings?.params && Object.keys(globalSettings.params).length > 0) {
                    try {
                        const urlObj = new URL(url);
                        for (const [key, value] of Object.entries(globalSettings.params)) {
                            urlObj.searchParams.append(key, String(value));
                        }
                        url = urlObj.toString();
                        console.log(`  Modified URL with params: ${url}`);
                    } catch (e) {
                        console.warn(`  Could not append params to URL ${url}: ${e}`);
                    }
                }

                let attempts = 0;
                const maxAttempts = 3;
                const waitUntil = (step.params?.wait_until as 'load' | 'domcontentloaded' | 'networkidle' | 'commit') || 'domcontentloaded';

                while (attempts < maxAttempts) {
                    try {
                        // Check if page is closed before attempting navigation
                        if (page.isClosed()) {
                            throw new Error('Page has been closed - cannot navigate');
                        }
                        await page.goto(url, { waitUntil, timeout: gotoTimeout });
                        break;
                    } catch (e: any) {
                        attempts++;
                        console.warn(`  Goto attempt ${attempts} failed: ${e}`);
                        // Don't retry if page/context is closed - it's unrecoverable
                        if (e.message?.includes('closed') || e.message?.includes('Target page') || e.message?.includes('context')) {
                            throw e;
                        }
                        if (attempts === maxAttempts) throw e;
                        await new Promise(r => setTimeout(r, 2000)); // Increased retry delay
                    }
                }
                break;
            }

            case 'http-request': {
                const method = step.params?.method || 'GET';
                const rawUrl = resolve(step.value || step.selector);
                const stepHeaders = resolve(step.params?.headers || {});
                const stepParams = resolve(step.params?.params || {});
                const body = resolve(step.params?.body);

                const mergedHeaders = { ...globalSettings.headers, ...stepHeaders };
                const mergedParams = { ...globalSettings.params, ...stepParams };

                // Support comma-separated URLs for batch checking with same schema/assertions
                const urls = rawUrl.split(',').map((u: string) => u.trim()).filter((u: string) => u);
                
                if (urls.length === 0) {
                    throw new Error('No valid URL provided for http-request');
                }

                // For single URL, use original behavior
                // For multiple URLs, run same assertions on all and collect results
                const allResults: any[] = [];
                const errors: string[] = [];

                for (const reqUrl of urls) {
                    console.log(`  [API] ${method} ${reqUrl} (Headers: ${Object.keys(mergedHeaders).length}, Params: ${Object.keys(mergedParams).length})${urls.length > 1 ? ` [${allResults.length + 1}/${urls.length}]` : ''}`);

                    let apiResponse;
                    let actualRequestHeaders = mergedHeaders;
                    let actualRequestUrl = reqUrl;
                    const requestHandler = async (request: any) => {
                        try {
                            const requestUrl = request.url();
                            if ((requestUrl === reqUrl || requestUrl.split('?')[0] === reqUrl.split('?')[0]) &&
                                request.method() === method) {
                                actualRequestHeaders = await request.allHeaders();
                                actualRequestUrl = requestUrl;
                            }
                        } catch (e) { }
                    };

                    page.context().on('request', requestHandler);
                    try {
                        apiResponse = await page.request.fetch(reqUrl, {
                            method,
                            headers: mergedHeaders,
                            params: mergedParams,
                            data: body,
                            timeout: 30000
                        });
                    } finally {
                        page.context().off('request', requestHandler);
                    }

                    const status = apiResponse.status();
                    const apiHeaders = apiResponse.headers();
                    const respBody = await apiResponse.text();
                    let jsonBody;
                    try { jsonBody = JSON.parse(respBody); } catch (e) { }

                    const resultObject: any = {
                        type: 'http-request',
                        url: reqUrl,
                        status,
                        headers: apiHeaders,
                        body: respBody,
                        request: {
                            url: actualRequestUrl,
                            method,
                            headers: actualRequestHeaders,
                            params: mergedParams,
                            body
                        }
                    };

                    // Run assertions for this URL
                    if (step.params?.assertions) {
                        for (const assertion of step.params.assertions) {
                            try {
                                if (assertion.type === 'status') {
                                    if (status !== parseInt(assertion.value)) {
                                        throw new Error(`Expected status ${assertion.value} but got ${status}`);
                                    }
                                } else if (assertion.type === 'json-path') {
                                    if (!jsonBody) throw new Error("Response is not JSON, cannot perform json-path assertion");
                                    const current = jsonPath(jsonBody, assertion.path);
                                    if (assertion.operator === 'equals') {
                                        if (String(current) !== String(assertion.value)) {
                                            throw new Error(`Expected ${assertion.path} to equal ${assertion.value} but got ${current}`);
                                        }
                                    } else if (assertion.operator === 'contains') {
                                        if (!String(current).includes(String(assertion.value))) {
                                            throw new Error(`Expected ${assertion.path} to contain ${assertion.value} but got ${current}`);
                                        }
                                    }
                                } else if (assertion.type === 'json-schema') {
                                    if (!jsonBody) throw new Error("Response is not JSON, cannot perform json-schema assertion");
                                    const ajv = new Ajv({ 
                                        allErrors: true, 
                                        strict: false,
                                        // Don't remove additional properties, just validate what's defined
                                        removeAdditional: false,
                                        // Allow additional properties by default unless explicitly set
                                        // This prevents errors when response has extra fields not in schema
                                    });
                                    addFormats(ajv);
                                    let schema = JSON.parse(assertion.value || '{}');
                                    
                                    // Transform schema to:
                                    // 1. Add additionalProperties: true to all objects
                                    // 2. For non-required fields, allow null as a valid type
                                    const transformSchema = (obj: any, requiredFields: string[] = []): any => {
                                        if (obj && typeof obj === 'object') {
                                            // Add additionalProperties: true if not set
                                            if (obj.type === 'object' && obj.properties && obj.additionalProperties === undefined) {
                                                obj.additionalProperties = true;
                                            }
                                            
                                            // Get required fields for this object level
                                            const required = obj.required || [];
                                            
                                            // Recurse into properties
                                            if (obj.properties) {
                                                for (const key of Object.keys(obj.properties)) {
                                                    const prop = obj.properties[key];
                                                    
                                                    // If field is NOT required, allow null type
                                                    if (!required.includes(key) && prop.type && !Array.isArray(prop.type)) {
                                                        // Convert single type to array including null
                                                        prop.type = [prop.type, 'null'];
                                                    }
                                                    
                                                    transformSchema(prop, prop.required || []);
                                                }
                                            }
                                            // Recurse into array items
                                            if (obj.items) {
                                                transformSchema(obj.items, obj.items.required || []);
                                            }
                                        }
                                        return obj;
                                    };
                                    schema = transformSchema(schema, schema.required || []);
                                    
                                    const validate = ajv.compile(schema);
                                    if (!validate(jsonBody)) {
                                        // Filter errors to show only meaningful ones
                                        const errors = validate.errors?.filter((e: any) => {
                                            // Skip additional properties errors since we allow them by default
                                            if (e.keyword === 'additionalProperties') return false;
                                            return true;
                                        });
                                        
                                        if (errors && errors.length > 0) {
                                            const schemaErrors = errors.map((e: any) => {
                                                if (e.keyword === 'required') {
                                                    return `Missing required field: "${e.params?.missingProperty}" at ${e.instancePath || 'root'}`;
                                                } else if (e.keyword === 'type') {
                                                    const path = e.instancePath || 'root';
                                                    return `"${path}": expected ${e.params?.type}`;
                                                }
                                                return `${e.instancePath || 'root'} ${e.message}`;
                                            }).join(', ');
                                            throw new Error(`JSON Schema validation failed: ${schemaErrors}`);
                                        }
                                    }
                                }
                            } catch (e: any) {
                                errors.push(`[${reqUrl}] ${e.message}`);
                                resultObject.error = e.message;
                            }
                        }
                    }

                    // Extract values from the JSON response into runtime
                    // variables for later steps ({{name}} interpolation) — the
                    // login→token→authorized-call chaining primitive.
                    //   extract: [{ path: "data.token", variable: "token" }]
                    if (step.params?.extract && testCaseContext) {
                        if (!testCaseContext.variables) testCaseContext.variables = {};
                        for (const ex of step.params.extract) {
                            if (!ex?.path || !ex?.variable) continue;
                            if (jsonBody === undefined) {
                                throw new Error(`extract: response from ${reqUrl} is not JSON, cannot extract "${ex.path}"`);
                            }
                            const value = jsonPath(jsonBody, ex.path);
                            if (value === undefined && ex.required !== false) {
                                throw new Error(`extract: path "${ex.path}" not found in response from ${reqUrl}`);
                            }
                            testCaseContext.variables[ex.variable] =
                                (value !== null && typeof value === 'object') ? JSON.stringify(value) : value;
                            console.log(`  [API] extracted {{${ex.variable}}} from ${ex.path}`);
                        }
                    }

                    allResults.push(resultObject);
                }

                // If multiple URLs, return combined result
                if (urls.length > 1) {
                    const combinedResult = {
                        type: 'http-request-batch',
                        totalUrls: urls.length,
                        successCount: allResults.filter(r => !r.error).length,
                        failedCount: allResults.filter(r => r.error).length,
                        results: allResults,
                        // Use first result for backward compatibility
                        status: allResults[0]?.status,
                        headers: allResults[0]?.headers,
                        body: allResults[0]?.body,
                        request: allResults[0]?.request
                    };

                    if (errors.length > 0) {
                        const err = new Error(`${errors.length} URL(s) failed validation:\n${errors.join('\n')}`);
                        (err as any).stepResult = combinedResult;
                        throw err;
                    }

                    return combinedResult;
                }

                // Single URL - original behavior
                if (errors.length > 0) {
                    const err = new Error(errors[0]);
                    (err as any).stepResult = allResults[0];
                    throw err;
                }

                return allResults[0];
            }

            case 'oauth2-token': {
                // Client-credentials token fetch for the app under test.
                //   value/params.token_url  — the token endpoint
                //   params.client_id / client_secret (use {{secret.X}})
                //   params.scope / audience — optional
                //   params.variable — runtime var name (default access_token)
                const tokenUrl = resolve(step.value || step.params?.token_url);
                const clientId = resolve(step.params?.client_id);
                const clientSecret = resolve(step.params?.client_secret);
                if (!tokenUrl || !clientId || !clientSecret) {
                    throw new Error('oauth2-token requires token_url, client_id and client_secret');
                }
                const varName = step.params?.variable || 'access_token';
                const form: Record<string, string> = {
                    grant_type: 'client_credentials',
                    client_id: clientId,
                    client_secret: clientSecret,
                };
                const scope = resolve(step.params?.scope);
                if (scope) form.scope = scope;
                const audience = resolve(step.params?.audience);
                if (audience) form.audience = audience;

                console.log(`  [OAuth2] client_credentials → ${tokenUrl}`);
                const tokenResp = await page.request.post(tokenUrl, { form, timeout: 30000 });
                const tokenText = await tokenResp.text();
                if (!tokenResp.ok()) {
                    throw new Error(`oauth2-token: token endpoint returned ${tokenResp.status()}: ${tokenText.slice(0, 300)}`);
                }
                let tokenJson: any;
                try { tokenJson = JSON.parse(tokenText); } catch {
                    throw new Error('oauth2-token: token endpoint did not return JSON');
                }
                const accessToken = tokenJson.access_token;
                if (!accessToken) {
                    throw new Error(`oauth2-token: no access_token in response (keys: ${Object.keys(tokenJson).join(', ')})`);
                }
                if (testCaseContext) {
                    if (!testCaseContext.variables) testCaseContext.variables = {};
                    testCaseContext.variables[varName] = accessToken;
                    if (tokenJson.token_type) testCaseContext.variables[`${varName}_type`] = tokenJson.token_type;
                }
                console.log(`  [OAuth2] stored {{${varName}}} (${String(accessToken).length} chars, never logged)`);
                break;
            }

            case 'graphql': {
                // GraphQL request with data-path assertions.
                //   value/selector — endpoint URL
                //   params.query / params.variables
                //   params.allow_errors — skip the default errors[] check
                //   params.assertions: [{type:'status'|'data-path', path, operator, value}]
                //   params.extract: [{path, variable}] — paths are relative to `data`
                const gqlUrl = resolve(step.value || step.selector);
                const query = resolve(step.params?.query);
                if (!gqlUrl || !query) throw new Error('graphql requires a URL and params.query');
                const gqlVariables = resolve(step.params?.variables || {});
                const gqlHeaders = {
                    'content-type': 'application/json',
                    ...globalSettings.headers,
                    ...resolve(step.params?.headers || {}),
                };

                console.log(`  [GraphQL] POST ${gqlUrl}`);
                const gqlResp = await page.request.post(gqlUrl, {
                    headers: gqlHeaders,
                    data: { query, variables: gqlVariables },
                    timeout: 30000,
                });
                const gqlStatus = gqlResp.status();
                const gqlText = await gqlResp.text();
                let gqlJson: any;
                try { gqlJson = JSON.parse(gqlText); } catch { /* asserted below */ }

                const gqlResult: any = {
                    type: 'graphql',
                    url: gqlUrl,
                    status: gqlStatus,
                    headers: gqlResp.headers(),
                    body: gqlText,
                    request: { url: gqlUrl, method: 'POST', headers: gqlHeaders, body: JSON.stringify({ query, variables: gqlVariables }) },
                };

                try {
                    if (gqlJson === undefined) {
                        throw new Error(`GraphQL response is not JSON (status ${gqlStatus})`);
                    }
                    if (!step.params?.allow_errors && Array.isArray(gqlJson.errors) && gqlJson.errors.length) {
                        const messages = gqlJson.errors.map((e: any) => e.message || JSON.stringify(e)).slice(0, 5).join('; ');
                        throw new Error(`GraphQL errors: ${messages}`);
                    }
                    for (const assertion of step.params?.assertions || []) {
                        if (assertion.type === 'status') {
                            if (gqlStatus !== parseInt(assertion.value)) {
                                throw new Error(`Expected status ${assertion.value} but got ${gqlStatus}`);
                            }
                        } else if (assertion.type === 'data-path') {
                            const actual = jsonPath(gqlJson.data, assertion.path);
                            const op = assertion.operator || 'equals';
                            if (op === 'exists') {
                                if (actual === undefined || actual === null) {
                                    throw new Error(`Expected data.${assertion.path} to exist`);
                                }
                            } else if (op === 'equals') {
                                if (String(actual) !== String(assertion.value)) {
                                    throw new Error(`Expected data.${assertion.path} to equal ${assertion.value} but got ${actual}`);
                                }
                            } else if (op === 'contains') {
                                if (!String(actual).includes(String(assertion.value))) {
                                    throw new Error(`Expected data.${assertion.path} to contain ${assertion.value} but got ${actual}`);
                                }
                            }
                        }
                    }
                    if (step.params?.extract && testCaseContext) {
                        if (!testCaseContext.variables) testCaseContext.variables = {};
                        for (const ex of step.params.extract) {
                            if (!ex?.path || !ex?.variable) continue;
                            const value = jsonPath(gqlJson.data, ex.path);
                            if (value === undefined && ex.required !== false) {
                                throw new Error(`extract: path "data.${ex.path}" not found in GraphQL response`);
                            }
                            testCaseContext.variables[ex.variable] =
                                (value !== null && typeof value === 'object') ? JSON.stringify(value) : value;
                        }
                    }
                } catch (e: any) {
                    gqlResult.error = e.message;
                    const err = new Error(e.message);
                    (err as any).stepResult = gqlResult;
                    throw err;
                }

                return gqlResult;
            }

            case 'feed-check': {
                const rawFeedUrl = step.value || step.selector;
                const stepHeaders = resolve(step.params?.headers || {});
                const stepParams = resolve(step.params?.params || {});
                
                // Merge suite/global headers with step-level headers (step headers override)
                const mergedHeaders = { ...globalSettings.headers, ...stepHeaders };
                const mergedParams = { ...globalSettings.params, ...stepParams };

                // Support comma-separated URLs for batch checking with same assertions
                const feedUrls = rawFeedUrl.split(',').map((u: string) => u.trim()).filter((u: string) => u);
                
                if (feedUrls.length === 0) {
                    throw new Error('No valid URL provided for feed-check');
                }

                const allResults: any[] = [];
                const errors: string[] = [];

                for (const feedUrl of feedUrls) {
                    console.log(`  [Feed] Checking ${feedUrl}${feedUrls.length > 1 ? ` [${allResults.length + 1}/${feedUrls.length}]` : ''}`);

                    let feedResponse;
                    let actualRequestHeaders = mergedHeaders;
                    let actualRequestUrl = feedUrl;
                    const requestHandler = async (request: any) => {
                        try {
                            const requestUrl = request.url();
                            if ((requestUrl === feedUrl || requestUrl.split('?')[0] === feedUrl.split('?')[0]) && request.method() === 'GET') {
                                actualRequestHeaders = await request.allHeaders();
                                actualRequestUrl = requestUrl;
                            }
                        } catch (e) { }
                    };

                    page.context().on('request', requestHandler);
                    try {
                        feedResponse = await page.request.get(feedUrl, { headers: mergedHeaders, params: mergedParams });
                    } finally {
                        page.context().off('request', requestHandler);
                    }

                    if (!feedResponse.ok()) {
                        errors.push(`[${feedUrl}] Failed to fetch feed: ${feedResponse.status()}`);
                        allResults.push({
                            type: 'feed-check',
                            url: feedUrl,
                            status: feedResponse.status(),
                            error: `Failed to fetch feed: ${feedResponse.status()}`
                        });
                        continue;
                    }

                    const feedText = await feedResponse.text();
                    const doc = new DOMParser().parseFromString(feedText, 'text/xml');

                    const resultObject: any = {
                        type: 'feed-check',
                        url: feedUrl,
                        status: feedResponse.status(),
                        headers: feedResponse.headers(),
                        body: feedText,
                        request: {
                            url: actualRequestUrl,
                            method: 'GET',
                            headers: actualRequestHeaders,
                            params: mergedParams
                        }
                    };

                    // Run assertions for this feed URL
                    if (step.params?.assertions) {
                        for (const assertion of step.params.assertions) {
                            try {
                                if (assertion.type === 'xpath') {
                                    const nodes = xpath.select(assertion.path, doc);
                                    const nodeValue = nodes[0] ? (nodes[0] as any).textContent : null;

                                    if (assertion.operator === 'equals') {
                                        if (nodeValue !== assertion.value) throw new Error(`Expected XPath ${assertion.path} to equal ${assertion.value} but got ${nodeValue}`);
                                    } else if (assertion.operator === 'contains') {
                                        if (!nodeValue || !nodeValue.includes(assertion.value)) throw new Error(`Expected XPath ${assertion.path} to contain ${assertion.value} but got ${nodeValue}`);
                                    } else if (assertion.operator === 'exists') {
                                        if (!nodes || nodes.length === 0) throw new Error(`Expected XPath ${assertion.path} to exist`);
                                    }
                                } else if (assertion.type === 'text') {
                                    if (!feedText.includes(assertion.value)) throw new Error(`Expected feed to contain text "${assertion.value}"`);
                                }
                            } catch (e: any) {
                                errors.push(`[${feedUrl}] ${e.message}`);
                                resultObject.error = e.message;
                            }
                        }
                    }

                    allResults.push(resultObject);
                }

                // If multiple URLs, return combined result
                if (feedUrls.length > 1) {
                    const combinedResult = {
                        type: 'feed-check-batch',
                        totalUrls: feedUrls.length,
                        successCount: allResults.filter(r => !r.error).length,
                        failedCount: allResults.filter(r => r.error).length,
                        results: allResults,
                        // Use first result for backward compatibility
                        status: allResults[0]?.status,
                        headers: allResults[0]?.headers,
                        body: allResults[0]?.body,
                        request: allResults[0]?.request
                    };

                    if (errors.length > 0) {
                        const err = new Error(`${errors.length} feed URL(s) failed validation:\n${errors.join('\n')}`);
                        (err as any).stepResult = combinedResult;
                        throw err;
                    }

                    return combinedResult;
                }

                // Single URL - original behavior
                if (errors.length > 0) {
                    const err = new Error(errors[0]);
                    (err as any).stepResult = allResults[0];
                    throw err;
                }

                return allResults[0];
            }

            case 'click': {
                const clickSelector = step.selector || step.value;
                if (clickSelector) {
                    const locator = getLocator(clickSelector);
                    const timeout = step.timeout ? parseInt(String(step.timeout)) : 30000;
                    await locator.waitFor({ state: 'visible', timeout });
                    await moveMouseTo(locator);
                    await locator.click({ timeout });
                }
                break;
            }

            case 'fill':
                if (step.selector) {
                    const locator = getLocator(step.selector);
                    const timeout = step.timeout ? parseInt(String(step.timeout)) : 30000;
                    await locator.waitFor({ state: 'visible', timeout });
                    await moveMouseTo(locator);
                    await locator.fill(step.value || '', { timeout });
                }
                break;

            case 'check': {
                const checkSelector = step.selector || step.value;
                if (checkSelector) {
                    const locator = getLocator(checkSelector);
                    const timeout = step.timeout ? parseInt(String(step.timeout)) : 30000;
                    await locator.waitFor({ state: 'visible', timeout });
                    await moveMouseTo(locator);
                    await locator.check({ timeout });
                }
                break;
            }

            case 'expect-visible': {
                const visibleSelector = step.selector || step.value;
                if (visibleSelector) {
                    const timeout = step.timeout ? parseInt(String(step.timeout)) : 30000;
                    if ('waitForSelector' in context) {
                        await (context as Page).waitForSelector(visibleSelector, { state: 'visible', timeout });
                    } else {
                        await getLocator(visibleSelector).waitFor({ state: 'visible', timeout });
                    }
                }
                break;
            }

            case 'wait-for-selector': {
                const waitSelector = step.selector || step.value;
                if (waitSelector) {
                    const timeout = step.timeout ? parseInt(String(step.timeout)) : 30000;
                    if ('waitForSelector' in context) {
                        await (context as Page).waitForSelector(waitSelector, { state: 'attached', timeout });
                    } else {
                        await getLocator(waitSelector).waitFor({ state: 'attached', timeout });
                    }
                }
                break;
            }

            case 'expect-hidden': {
                const hiddenSelector = step.selector || step.value;
                if (hiddenSelector) {
                    if ('waitForSelector' in context) {
                        await (context as Page).waitForSelector(hiddenSelector, { state: 'hidden', timeout: 50000 });
                    } else {
                        await getLocator(hiddenSelector).waitFor({ state: 'hidden', timeout: 50000 });
                    }
                }
                break;
            }

            case 'expect-text':
                if (step.selector && step.value) {
                    const locator = getLocator(step.selector);
                    await locator.waitFor({ state: 'visible', timeout: 50000 });
                    const text = await locator.textContent();
                    if (!text?.includes(step.value)) {
                        throw new Error(`Expected text "${step.value}" not found in element "${step.selector}"`);
                    }
                }
                break;

            case 'expect-url': {
                const expectedUrl = step.value || step.selector;
                if (expectedUrl) await page.waitForURL(expectedUrl, { timeout: 15000 });
                break;
            }

            case 'expect-title': {
                const expectedTitle = resolve(step.value || step.selector);
                if (!expectedTitle) throw new Error('expect-title requires a value');
                const titleOperator = step.params?.operator || 'contains';
                const actualTitle = await page.title();
                if (titleOperator === 'equals') {
                    if (actualTitle !== expectedTitle) {
                        throw new Error(`Expected title to equal "${expectedTitle}", but got "${actualTitle}"`);
                    }
                } else if (titleOperator === 'matches') {
                    if (!new RegExp(expectedTitle).test(actualTitle)) {
                        throw new Error(`Expected title to match /${expectedTitle}/, but got "${actualTitle}"`);
                    }
                } else if (!actualTitle.includes(expectedTitle)) {
                    throw new Error(`Expected title to contain "${expectedTitle}", but got "${actualTitle}"`);
                }
                break;
            }

            case 'hover': {
                const hoverSelector = step.selector || step.value;
                if (hoverSelector) {
                    const locator = getLocator(hoverSelector);
                    await locator.hover();
                }
                break;
            }

            case 'select-option':
                if (step.selector && step.value) {
                    const locator = getLocator(step.selector);
                    await moveMouseTo(locator);
                    await locator.selectOption(step.value);
                }
                break;

            case 'press-key': {
                const key = step.value || step.selector;
                if (key) await page.keyboard.press(key);
                break;
            }

            case 'mock-response': {
                // Stub matching network responses. selector = URL glob/pattern;
                // params.status/body/content_type/headers shape the response.
                const urlPattern = step.selector || '**/*';
                const mockStatus = step.params?.status ?? 200;
                const contentType = step.params?.content_type
                    || (step.params?.json !== undefined ? 'application/json' : 'text/plain');
                const bodyRaw = step.params?.json !== undefined
                    ? JSON.stringify(step.params.json)
                    : resolve(step.value ?? step.params?.body ?? '');
                await page.route(urlPattern, route => route.fulfill({
                    status: mockStatus,
                    contentType,
                    headers: step.params?.headers || {},
                    body: bodyRaw,
                }));
                console.log(`  [Mock] ${urlPattern} -> ${mockStatus} (${contentType})`);
                break;
            }

            case 'block-request': {
                // Abort matching requests (e.g. block analytics/3rd-party).
                const blockPattern = step.selector || step.value;
                if (!blockPattern) throw new Error('block-request requires a selector (URL pattern)');
                await page.route(blockPattern, route => route.abort());
                console.log(`  [Block] ${blockPattern}`);
                break;
            }

            case 'set-network-latency': {
                // Delay matching requests by params.ms milliseconds before
                // continuing them (simulate slow network / spinners).
                const latencyPattern = step.selector || '**/*';
                const delayMs = Number(step.params?.ms ?? step.value ?? 0);
                await page.route(latencyPattern, async route => {
                    if (delayMs > 0) await new Promise(r => setTimeout(r, delayMs));
                    await route.continue();
                });
                console.log(`  [Latency] ${latencyPattern} +${delayMs}ms`);
                break;
            }

            case 'check-accessibility': {
                // Run axe-core against the current page. params.impact filters
                // the minimum severity that fails the step (default 'serious').
                // params.fail=false makes it report-only (never throws).
                let AxeBuilder: any;
                try {
                    AxeBuilder = require('@axe-core/playwright').default;
                } catch (e) {
                    console.warn('  [A11y] @axe-core/playwright not installed; skipping (report-only)');
                    break;
                }
                const results = await new AxeBuilder({ page }).analyze();
                const order: Record<string, number> = { minor: 0, moderate: 1, serious: 2, critical: 3 };
                const threshold = order[String(step.params?.impact || 'serious')] ?? 2;
                const blocking = (results.violations || []).filter(
                    (v: any) => (order[v.impact as string] ?? 0) >= threshold);
                const summary = blocking.map((v: any) => `${v.id} (${v.impact}, ${v.nodes.length})`).join('; ');
                console.log(`  [A11y] ${results.violations.length} violation type(s); `
                    + `${blocking.length} at/above '${step.params?.impact || 'serious'}'`);
                const a11yResult = {
                    type: 'check-accessibility',
                    total_violations: results.violations.length,
                    blocking_violations: blocking.length,
                    violations: blocking.map((v: any) => ({
                        id: v.id, impact: v.impact, help: v.help,
                        nodes: v.nodes.length, helpUrl: v.helpUrl,
                    })),
                };
                if (step.params?.fail !== false && blocking.length > 0) {
                    const err: any = new Error(`Accessibility check failed: ${blocking.length} violation(s) — ${summary}`);
                    err.stepResult = a11yResult;
                    throw err;
                }
                return a11yResult;
            }

            case 'screenshot': {
                const screenshotName = step.value || `screenshot-${Date.now()}`;
                const videoPath = await page.video()?.path();
                const screenshotPath = path.join(videoPath ? path.dirname(videoPath) : '/tmp', `${screenshotName}.png`);
                await page.screenshot({ path: screenshotPath, fullPage: true });
                console.log(`Screenshot saved to: ${screenshotPath}`);
                break;
            }

            case 'expect-visual-match': {
                // Phase B: perceptual diff against a stored VisualBaseline.
                // Workflow:
                //   1. Capture candidate screenshot.
                //   2. Resolve baseline via the backend (HTTP) using
                //      (test_case_id, step_id, browser, device).
                //   3. Run pixelmatch; fail the step if diffRatio > tolerance.
                //   4. Always upload the diff image alongside other artifacts.
                const stepId = step.id || `visual-${Date.now()}`;
                const videoPathVisual = await page.video()?.path();
                const candidateDir = videoPathVisual ? path.dirname(videoPathVisual) : '/tmp';
                const candidatePath = path.join(candidateDir, `visual-${stepId}.png`);
                await page.screenshot({ path: candidatePath, fullPage: true });

                // Lazy require to keep this branch optional: a deployment
                // without pixelmatch installed continues working (with the
                // step degrading to a passing capture-only behavior).
                try {
                    // eslint-disable-next-line @typescript-eslint/no-var-requires
                    const { compareScreenshots } = require('../visual-diff');
                    // eslint-disable-next-line @typescript-eslint/no-var-requires
                    const { resolveBaseline, fetchImageBytes } = require('../baseline-client');

                    // executeStep is static — the case id/browser come from the
                    // per-case context the worker builds, not from `this`.
                    const testCaseId = testCaseContext?.id || step.params?.test_case_id;
                    const browserName = testCaseContext?.browser || 'chromium';
                    const baseline = await resolveBaseline({
                        testCaseId,
                        stepId,
                        browser: browserName,
                        device: testCaseContext?.device,
                    });
                    if (!baseline) {
                        console.log(`[visual-match] no baseline for step ${stepId} — capture-only`);
                        break;
                    }
                    const baselineBytes = await fetchImageBytes(baseline.image_url);
                    const result = await compareScreenshots({
                        candidatePath,
                        baselineBytes,
                        tolerance: baseline.tolerance ?? 0.01,
                        maskRegions: baseline.mask_regions || [],
                    });
                    console.log(
                        `[visual-match] step=${stepId} diffRatio=${result.diffRatio.toFixed(4)} passed=${result.passed}`,
                    );
                    if (!result.passed) {
                        throw new Error(
                            `Visual regression: diffRatio=${result.diffRatio.toFixed(4)} > tolerance=${baseline.tolerance ?? 0.01}` +
                            (result.diffImagePath ? ` (diff at ${result.diffImagePath})` : ''),
                        );
                    }
                } catch (err: any) {
                    if (err?.message?.startsWith('Visual regression')) throw err;
                    console.log(`[visual-match] degraded to capture-only: ${err?.message}`);
                }
                break;
            }

            case 'scroll-to': {
                const scrollSelector = step.selector || step.value;
                if (scrollSelector) {
                    const locator = getLocator(scrollSelector);
                    await locator.scrollIntoViewIfNeeded();
                }
                break;
            }

            case 'wait-timeout': {
                const timeout = parseInt(step.value || step.selector || '1000');
                try {
                    await page.waitForTimeout(timeout);
                } catch (err: any) {
                    // Page might be closed during parallel execution
                    if (err.message?.includes('Target page, context or browser has been closed')) {
                        throw new Error('Test execution interrupted: browser context was closed');
                    }
                    throw err;
                }
                break;
            }

            case 'carousel-find': {
                const targetSelector = step.selector;
                const nextButtonSelector = step.value;
                const maxSwipes = step.params?.max_swipes || 10;

                let found = false;
                for (let i = 0; i < maxSwipes; i++) {
                    const target = getLocator(targetSelector);
                    if (await target.isVisible().catch(() => false)) {
                        found = true;
                        break;
                    }
                    console.log(`  [Carousel] Target not visible, clicking next (${i + 1}/${maxSwipes})`);
                    const nextBtn = getLocator(nextButtonSelector);
                    if (await nextBtn.isVisible()) {
                        await nextBtn.click();
                        try {
                            await page.waitForTimeout(500);
                        } catch (err: any) {
                            // Ignore timeout errors if page is closed
                            if (!err.message?.includes('Target page, context or browser has been closed')) {
                                throw err;
                            }
                        }
                    } else {
                        throw new Error(`Carousel next button '${nextButtonSelector}' not found/visible`);
                    }
                }
                if (!found) {
                    const target = getLocator(targetSelector);
                    if (await target.isVisible().catch(() => false)) {
                        found = true;
                    } else {
                        throw new Error(`Could not find target '${targetSelector}' in carousel after ${maxSwipes} attempts`);
                    }
                }
                break;
            }

            case 'verify-nth-child': {
                const parentSelector = step.selector;
                const index = parseInt(step.value || '0');
                const expectedText = step.params?.text;

                const elements = getLocator(parentSelector);
                const count = await elements.count();

                if (index < 0 || index >= count) {
                    // Note: count is 0 if none found, which is index out of bounds 0 >= 0
                    throw new Error(`Index ${index} out of bounds (found ${count} elements for '${parentSelector}')`);
                }

                const child = elements.nth(index);
                if (expectedText) {
                    await child.waitFor({ state: 'visible', timeout: 30000 });
                    const text = await child.textContent();
                    if (!text?.includes(expectedText)) {
                        throw new Error(`Expected nth-child(${index}) to contain "${expectedText}" but got "${text}"`);
                    }
                }
                break;
            }

            case 'count-children': {
                const parentSelector = step.selector;
                const expectedCount = parseInt(step.value || '0');
                const operator = step.params?.operator || 'equals';

                if (expectedCount > 0) {
                    try {
                        await context.locator(parentSelector).first().waitFor({ state: 'attached', timeout: 5000 });
                    } catch (e) { }
                }

                const count = await context.locator(parentSelector).count();
                console.log(`  [Count] Found ${count} elements matching '${parentSelector}'`);

                if (operator === 'equals' && count !== expectedCount) throw new Error(`Expected ${expectedCount} children, found ${count}`);
                if (operator === 'gte' && count < expectedCount) throw new Error(`Expected at least ${expectedCount} children, found ${count}`);
                if (operator === 'lte' && count > expectedCount) throw new Error(`Expected at most ${expectedCount} children, found ${count}`);
                break;
            }

            case 'extract-value': {
                const selector = step.selector;
                const variableName = step.value;
                if (!selector || !variableName) throw new Error("Extract Value requires both a selector and a variable name");

                const locator = getLocator(selector);
                await locator.waitFor({ state: 'attached', timeout: 30000 });
                const text = await locator.textContent();

                if (testCaseContext && testCaseContext.variables) {
                    testCaseContext.variables[variableName] = text?.trim();
                    console.log(`  [Extract] Stored '${variableName}': ${testCaseContext.variables[variableName]}`);
                }
                break;
            }

            case 'run-script': {
                const language = step.params?.language || 'javascript';
                const script = step.params?.body || step.value;

                if (language === 'javascript') {
                    // Execute in browser context
                    const vars = testCaseContext?.variables || {};
                    const result = await page.evaluate(({ code, variables }) => {
                        // Create a function from the string that accepts 'variables'
                        return new Function('variables', code)(variables);
                    }, { code: script, variables: vars });
                    console.log(`  [Script-JS] Result:`, result);

                    if (step.params?.variableName && testCaseContext?.variables) {
                        testCaseContext.variables[step.params.variableName] = result;
                    }
                } else if (language === 'python') {
                    // Python scripts run arbitrary code in the worker container,
                    // so they are OFF by default. Enable per-deployment only when
                    // test authors are trusted (ALLOW_PYTHON_SCRIPTS=true).
                    if (process.env.ALLOW_PYTHON_SCRIPTS !== 'true') {
                        throw new Error(
                            "run-script 'python' is disabled on this worker "
                            + "(set ALLOW_PYTHON_SCRIPTS=true to enable). Use the "
                            + "'javascript' language, which runs sandboxed in the page.");
                    }
                    // Execute in runner environment
                    const wrapper = `
import sys
import json
import io
import contextlib

def run(context):
${script.split('\n').map((line: string) => '    ' + line).join('\n')}

if __name__ == "__main__":
    try:
        input_data = sys.stdin.read()
        context = json.loads(input_data)
        
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
             result = run(context)
        captured_stdout = f.getvalue()
        
        print(json.dumps({"status": "success", "result": result, "logs": captured_stdout}))
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}))
`;
                    // Strip infrastructure credentials from the child's env so a
                    // script can't exfiltrate MinIO/Redis/LLM keys even when enabled.
                    const scrubbedEnv = { ...process.env };
                    for (const k of Object.keys(scrubbedEnv)) {
                        if (/MINIO|REDIS|SECRET|OPENAI|ANTHROPIC|API_KEY|PASSWORD|TOKEN|WEBHOOK/i.test(k)) {
                            delete scrubbedEnv[k];
                        }
                    }
                    const child = spawn('python3', ['-c', wrapper], { env: scrubbedEnv });

                    const inputContext = {
                        variables: testCaseContext?.variables || {}
                    };

                    let stdout = '';
                    let stderr = '';

                    child.stdout.on('data', (data) => stdout += data.toString());
                    child.stderr.on('data', (data) => stderr += data.toString());
                    child.stdin.write(JSON.stringify(inputContext));
                    child.stdin.end();

                    await new Promise((resolve, reject) => {
                        child.on('error', (err) => {
                            reject(new Error(`Failed to start Python process: ${err.message}`));
                        });

                        child.on('close', (code) => {
                            if (code !== 0) {
                                reject(new Error(`Python script exited with code ${code}. Stderr: ${stderr}`));
                            } else {
                                try {
                                    const response = JSON.parse(stdout.trim());
                                    if (response.status === 'error') {
                                        reject(new Error(`Script Error: ${response.message}`));
                                    } else {
                                        if (response.logs) {
                                            console.log(`  [Script-Py Logs]:\n${response.logs}`);
                                        }
                                        console.log(`  [Script-Py] Result:`, response.result);
                                        if (step.params?.variableName && testCaseContext?.variables) {
                                            testCaseContext.variables[step.params.variableName] = response.result;
                                        }
                                        resolve(response.result);
                                    }
                                } catch (e) {
                                    reject(new Error(`Failed to parse Python output: ${stdout}\nStderr: ${stderr}`));
                                }
                            }
                        });
                    });
                }
                break;
            }

            case 'assert': {
                const selector = step.selector;
                const source = step.params?.source || 'text';
                const operator = step.params?.operator || 'equals';
                let expectedValue = resolve(step.value);
                const attributeName = step.params?.attribute;

                if (!selector) throw new Error("Assertion requires a selector");

                const locator = getLocator(selector);

                // Wait for element (unless we are checking count and expect 0)
                if (source !== 'count') {
                    await locator.waitFor({ state: 'attached', timeout: 30000 });
                }

                let actualValue: any;

                if (source === 'text') {
                    actualValue = (await locator.textContent())?.trim();
                } else if (source === 'value') {
                    actualValue = await locator.inputValue();
                } else if (source === 'attribute') {
                    if (!attributeName) throw new Error("Attribute name required for attribute assertion");
                    actualValue = await locator.getAttribute(attributeName);
                } else if (source === 'count') {
                    actualValue = await context.locator(selector).count();
                } else if (source === 'css') {
                    const propertyName = step.params?.property || attributeName;
                    if (!propertyName) throw new Error("CSS property name required for css assertion (params.property)");
                    actualValue = await locator.evaluate(
                        (el, prop) => window.getComputedStyle(el).getPropertyValue(prop),
                        propertyName,
                    );
                    actualValue = String(actualValue).trim();
                }

                console.log(`  [Assert] ${source} of '${selector}' is '${actualValue}'. Checking ${operator} '${expectedValue}'`);

                if (operator === 'equals') {
                    if (String(actualValue) !== String(expectedValue)) {
                        throw new Error(`Assertion Failed: Expected ${source} to equal '${expectedValue}', but got '${actualValue}'`);
                    }
                } else if (operator === 'contains') {
                    if (!String(actualValue).includes(String(expectedValue))) {
                        throw new Error(`Assertion Failed: Expected ${source} to contain '${expectedValue}', but got '${actualValue}'`);
                    }
                } else if (operator === 'matches') {
                    const regex = new RegExp(String(expectedValue));
                    if (!regex.test(String(actualValue))) {
                        throw new Error(`Assertion Failed: Expected ${source} to match regex '${expectedValue}', but got '${actualValue}'`);
                    }
                } else if (operator === 'gt') {
                    if (Number(actualValue) <= Number(expectedValue)) {
                        throw new Error(`Assertion Failed: Expected ${source} (${actualValue}) to be > ${expectedValue}`);
                    }
                } else if (operator === 'lt') {
                    if (Number(actualValue) >= Number(expectedValue)) {
                        throw new Error(`Assertion Failed: Expected ${source} (${actualValue}) to be < ${expectedValue}`);
                    }
                }
                break;
            }

            case 'amp-validate': {
                const ampUrl = resolve(step.value || step.selector);
                if (!ampUrl) throw new Error('No URL provided for AMP validation');

                console.log(`  [AMP] Validating: ${ampUrl}`);

                // Fetch the page HTML
                let ampResponse;
                try {
                    ampResponse = await page.request.get(ampUrl, { timeout: 30000 });
                } catch (fetchErr: any) {
                    throw new Error(`Failed to fetch URL for AMP validation: ${fetchErr.message}`);
                }

                if (!ampResponse.ok()) {
                    const errResult = {
                        type: 'amp-validate',
                        url: ampUrl,
                        amp_status: 'FAIL',
                        http_status: ampResponse.status(),
                        errors: [{ severity: 'ERROR', line: 0, col: 0, message: `HTTP ${ampResponse.status()} - Failed to fetch page`, specUrl: '', code: 'HTTP_ERROR' }],
                        warnings: [],
                        error_count: 1,
                        warning_count: 0,
                        request: { url: ampUrl, method: 'GET' }
                    };
                    const err = new Error(`AMP Validation failed: HTTP ${ampResponse.status()}`);
                    (err as any).stepResult = errResult;
                    throw err;
                }

                const htmlContent = await ampResponse.text();

                // Run AMP validator
                const amphtmlValidator = require('amphtml-validator');
                const validator = await amphtmlValidator.getInstance();
                const validationResult = validator.validateString(htmlContent);

                // Categorize issues
                const errors: any[] = [];
                const warnings: any[] = [];

                for (const issue of validationResult.errors) {
                    const entry = {
                        severity: issue.severity,
                        line: issue.line,
                        col: issue.col,
                        message: issue.message,
                        specUrl: issue.specUrl || '',
                        code: issue.code || ''
                    };
                    if (issue.severity === 'ERROR') {
                        errors.push(entry);
                    } else {
                        warnings.push(entry);
                    }
                }

                const ampStatus = validationResult.status; // 'PASS' or 'FAIL'
                console.log(`  [AMP] Result: ${ampStatus} (${errors.length} errors, ${warnings.length} warnings)`);

                const resultObject = {
                    type: 'amp-validate',
                    url: ampUrl,
                    amp_status: ampStatus,
                    http_status: ampResponse.status(),
                    errors,
                    warnings,
                    error_count: errors.length,
                    warning_count: warnings.length,
                    request: { url: ampUrl, method: 'GET' }
                };

                if (ampStatus === 'FAIL') {
                    const topErrors = errors.slice(0, 5).map(e => `Line ${e.line}: ${e.message}`).join('\n');
                    const err = new Error(`AMP Validation failed with ${errors.length} error(s):\n${topErrors}`);
                    (err as any).stepResult = resultObject;
                    throw err;
                }

                return resultObject;
            }

            case 'double-click': {
                const sel = step.selector || step.value;
                if (sel) {
                    const locator = getLocator(sel);
                    const timeout = step.timeout ? parseInt(String(step.timeout)) : 30000;
                    await locator.waitFor({ state: 'visible', timeout });
                    await moveMouseTo(locator);
                    await locator.dblclick({ timeout });
                }
                break;
            }

            case 'right-click': {
                const sel = step.selector || step.value;
                if (sel) {
                    const locator = getLocator(sel);
                    const timeout = step.timeout ? parseInt(String(step.timeout)) : 30000;
                    await locator.waitFor({ state: 'visible', timeout });
                    await moveMouseTo(locator);
                    await locator.click({ button: 'right', timeout });
                }
                break;
            }

            case 'uncheck': {
                const sel = step.selector || step.value;
                if (sel) {
                    const locator = getLocator(sel);
                    const timeout = step.timeout ? parseInt(String(step.timeout)) : 30000;
                    await locator.waitFor({ state: 'visible', timeout });
                    await moveMouseTo(locator);
                    await locator.uncheck({ timeout });
                }
                break;
            }

            case 'drag-and-drop': {
                // selector = source element, value = target element selector
                const sourceSel = step.selector;
                const targetSel = resolve(step.value);
                if (!sourceSel || !targetSel) throw new Error('drag-and-drop requires selector (source) and value (target selector)');
                const timeout = step.timeout ? parseInt(String(step.timeout)) : 30000;
                const source = getLocator(sourceSel);
                const target = getLocator(targetSel);
                await source.waitFor({ state: 'visible', timeout });
                await target.waitFor({ state: 'visible', timeout });
                await source.dragTo(target, { timeout });
                break;
            }

            case 'upload-file': {
                // Two modes:
                //  1. params.files: [{name, content_base64}] — agent-friendly inline fixtures,
                //     written to a temp dir on the worker and attached.
                //  2. value: comma-separated worker-local paths (pre-mounted fixture files).
                const sel = step.selector;
                if (!sel) throw new Error('upload-file requires a selector for the file input');
                const timeout = step.timeout ? parseInt(String(step.timeout)) : 30000;
                const locator = getLocator(sel);
                await locator.waitFor({ state: 'attached', timeout });

                const inlineFiles = step.params?.files;
                if (Array.isArray(inlineFiles) && inlineFiles.length > 0) {
                    const fs = require('fs');
                    const os = require('os');
                    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'traceiq-upload-'));
                    const filePaths: string[] = [];
                    for (const f of inlineFiles) {
                        if (!f?.name) throw new Error('upload-file: each entry in params.files needs a name');
                        const safeName = path.basename(String(f.name));
                        const filePath = path.join(tmpDir, safeName);
                        const content = f.content_base64 !== undefined
                            ? Buffer.from(String(f.content_base64), 'base64')
                            : Buffer.from(String(f.content ?? ''), 'utf-8');
                        fs.writeFileSync(filePath, content);
                        filePaths.push(filePath);
                    }
                    await locator.setInputFiles(filePaths, { timeout });
                    console.log(`  [Upload] Attached ${filePaths.length} inline file(s)`);
                } else if (step.value) {
                    const filePaths = String(resolve(step.value)).split(',').map((p: string) => p.trim()).filter(Boolean);
                    await locator.setInputFiles(filePaths, { timeout });
                    console.log(`  [Upload] Attached ${filePaths.length} file(s) from worker paths`);
                } else {
                    throw new Error('upload-file requires params.files (inline) or value (worker-local path)');
                }
                break;
            }

            case 'download-file': {
                // Waits for a download event. Optional params.trigger_selector is clicked
                // to start the download (steps are sequential, so the trigger must be
                // part of this step to overlap with the wait).
                const timeout = step.timeout ? parseInt(String(step.timeout)) : 30000;
                const triggerSel = step.params?.trigger_selector || step.selector;
                const downloadPromise = page.waitForEvent('download', { timeout });
                if (triggerSel) {
                    const trigger = getLocator(triggerSel);
                    await trigger.waitFor({ state: 'visible', timeout });
                    await trigger.click({ timeout });
                }
                const download = await downloadPromise;
                const suggested = download.suggestedFilename();

                const expectContains = resolve(step.params?.filename_contains);
                if (expectContains && !suggested.includes(String(expectContains))) {
                    throw new Error(`Downloaded file "${suggested}" does not contain expected "${expectContains}"`);
                }

                // Save alongside other artifacts so it is uploaded with the run.
                const videoPath = await page.video()?.path();
                const saveDir = videoPath ? path.dirname(videoPath) : '/tmp';
                const savePath = path.join(saveDir, `download-${path.basename(suggested)}`);
                await download.saveAs(savePath);
                console.log(`  [Download] Saved "${suggested}" to ${savePath}`);

                if (step.params?.variableName && testCaseContext?.variables) {
                    testCaseContext.variables[step.params.variableName] = suggested;
                }
                break;
            }

            case 'handle-dialog': {
                // Arms a one-shot handler for the NEXT dialog (alert/confirm/prompt).
                // Place this step BEFORE the step that triggers the dialog.
                // params.action: 'accept' (default) | 'dismiss'
                // params.prompt_text: text typed into prompt() dialogs before accepting
                // params.variableName: stores the dialog message for later assertion
                const action = step.params?.action || 'accept';
                const promptText = resolve(step.params?.prompt_text);
                const variableName = step.params?.variableName;
                page.once('dialog', async (dialog) => {
                    console.log(`  [Dialog] ${dialog.type()}: "${dialog.message()}" -> ${action}`);
                    if (variableName && testCaseContext?.variables) {
                        testCaseContext.variables[variableName] = dialog.message();
                    }
                    try {
                        if (action === 'dismiss') {
                            await dialog.dismiss();
                        } else {
                            await dialog.accept(promptText !== undefined && promptText !== null ? String(promptText) : undefined);
                        }
                    } catch (e: any) {
                        console.warn(`  [Dialog] handler error: ${e.message}`);
                    }
                });
                break;
            }

            case 'switch-tab': {
                // Switches the active page for subsequent steps. Returns a marker the
                // step loop uses to swap its `page` reference (like switch-frame).
                // value: 'latest' | 'new' | 1-based index | URL substring
                // params.trigger_selector: clicked to open the popup/new tab (overlapped wait)
                const timeout = step.timeout ? parseInt(String(step.timeout)) : 30000;
                const target = String(resolve(step.value ?? 'latest')).trim();
                const triggerSel = step.params?.trigger_selector;
                let newPage: Page | undefined;

                if (triggerSel) {
                    const popupPromise = page.context().waitForEvent('page', { timeout });
                    const trigger = getLocator(triggerSel);
                    await trigger.waitFor({ state: 'visible', timeout });
                    await trigger.click({ timeout });
                    newPage = await popupPromise;
                    await newPage.waitForLoadState('domcontentloaded', { timeout }).catch(() => { });
                } else {
                    const pages = page.context().pages();
                    if (target === 'latest' || target === 'new') {
                        newPage = pages[pages.length - 1];
                    } else if (/^\d+$/.test(target)) {
                        const idx = parseInt(target) - 1;
                        if (idx < 0 || idx >= pages.length) throw new Error(`switch-tab: index ${target} out of range (${pages.length} tabs open)`);
                        newPage = pages[idx];
                    } else {
                        newPage = pages.find(p => p.url().includes(target));
                        if (!newPage) throw new Error(`switch-tab: no open tab with URL containing "${target}" (${pages.map(p => p.url()).join(', ')})`);
                    }
                }

                if (!newPage) throw new Error('switch-tab: could not resolve target tab');
                newPage.setDefaultTimeout(parseInt(process.env.DEFAULT_TIMEOUT || '30000'));
                await newPage.bringToFront().catch(() => { });
                console.log(`  [Tab] Switched to: ${newPage.url()}`);
                return { __switchToPage: newPage };
            }

            case 'wait-for-response': {
                // Waits for a network response whose URL contains `value`.
                // params.status: expected HTTP status (optional)
                // params.trigger_selector: clicked after arming the wait, so the
                //   triggering request and the wait overlap (optional)
                const urlPart = String(resolve(step.value || step.selector) || '').trim();
                if (!urlPart) throw new Error('wait-for-response requires a URL substring in value');
                const timeout = step.timeout ? parseInt(String(step.timeout)) : 30000;
                const expectedStatus = step.params?.status ? parseInt(String(step.params.status)) : undefined;

                const responsePromise = page.waitForResponse(
                    (resp) => resp.url().includes(urlPart) && (expectedStatus === undefined || resp.status() === expectedStatus),
                    { timeout }
                );
                if (step.params?.trigger_selector) {
                    const trigger = getLocator(step.params.trigger_selector);
                    await trigger.waitFor({ state: 'visible', timeout });
                    await trigger.click({ timeout });
                }
                const resp = await responsePromise;
                console.log(`  [Network] Matched response ${resp.status()} ${resp.url()}`);
                if (step.params?.variableName && testCaseContext?.variables) {
                    testCaseContext.variables[step.params.variableName] = String(resp.status());
                }
                break;
            }

            case 'expect-not-text': {
                // Asserts the element's text does NOT contain value. The element must
                // exist; use expect-hidden to assert absence of the element itself.
                if (!step.selector || step.value === undefined) throw new Error('expect-not-text requires selector and value');
                const timeout = step.timeout ? parseInt(String(step.timeout)) : 30000;
                const locator = getLocator(step.selector);
                await locator.waitFor({ state: 'attached', timeout });
                const text = (await locator.textContent()) || '';
                const forbidden = String(resolve(step.value));
                if (text.includes(forbidden)) {
                    throw new Error(`Expected text "${forbidden}" to be absent from "${step.selector}", but it is present`);
                }
                break;
            }

            default:
                if (step.type === 'switch-frame') break; // Handled in the main loop
                console.warn(`Unknown step type: ${step.type}`);
        }
    }
}

/**
 * Template interpolation shared by every executor (web test-executor,
 * mobile worker): {{env.KEY}}, {{secret.KEY}}, {{data.KEY}}, {{fake.KIND}},
 * {{run.KEY}}, {{unique.NAME}}, {{setup.NAME}}, {{flag.NAME}} and bare {{name}} runtime
 * variables. Unknown tokens are left in place so mistakes are visible rather
 * than silently blank.
 */
import type { RunScope } from './run-scope';

// Lightweight test-data generator for {{fake.KIND}} interpolation. Kept
// dependency-free on purpose; covers the common kinds. Unknown kinds return
// the token unchanged so mistakes are visible rather than silently blank.
//
// `random` is the seeded stream when the run has a data seed; every draw
// (including the email suffix and the date) then comes from it, so the same
// seed + case + row reproduces the same values. Without one, behaviour is the
// historical Math.random/Date.now mix.
const SEEDED_DATE_ANCHOR = Date.UTC(2026, 0, 1);

export function generateFake(kind: string, random?: () => number): string {
    const draw = random || Math.random;
    const rnd = (n: number) => Math.floor(draw() * n);
    const pick = <T>(a: T[]): T => a[rnd(a.length)];
    const firsts = ['alex', 'sam', 'jordan', 'taylor', 'riley', 'morgan', 'casey', 'jamie'];
    const lasts = ['smith', 'jones', 'patel', 'kim', 'garcia', 'khan', 'lee', 'nair'];
    const first = pick(firsts);
    const last = pick(lasts);
    const suffix = random
        ? rnd(36 ** 6).toString(36).padStart(6, '0') + rnd(1e6).toString(36)
        : Date.now().toString(36) + rnd(1e6).toString(36);
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
            return new Date((random ? SEEDED_DATE_ANCHOR : Date.now()) - rnd(1e10)).toISOString().slice(0, 10);
        case 'word':
            return pick(['lorem', 'ipsum', 'dolor', 'sit', 'amet', 'consectetur']);
        default:
            return `{{fake.${kind}}}`;
    }
}

export interface TemplateContext {
    /** {{env.KEY}} — ProjectEnvironment variables from job settings. */
    envVars?: Record<string, any> | null;
    /** {{secret.KEY}} — decrypted project secrets from job settings. */
    secrets?: Record<string, any> | null;
    /** {{data.KEY}} — the current data-driven dataset row. */
    dataRow?: Record<string, any> | null;
    /** {{name}} — runtime variables from extract-value / scripts. */
    variables?: Record<string, any> | null;
    /** {{inbox.address[.label]}} / {{webhook.url[.label]}} — per-run values
     *  the backend derived at dispatch (job `test_case.test_hooks`). */
    hooks?: { inbox?: Record<string, string>; webhook?: Record<string, string> } | null;
    /** {{run.KEY}} / {{unique.NAME}} and the seeded {{fake.*}} stream. */
    scope?: RunScope | null;
    /** {{setup.NAME}} — values exported by the suite setup hooks. */
    setup?: Record<string, any> | null;
    /** {{flag.NAME}} — feature flags / variant the run declared (job
     *  `settings.flags`). Booleans render as "true" / "false". */
    flags?: Record<string, any> | null;
}

/**
 * The template context for a step, from the job settings and the case's
 * runtime context object (`testCaseContext` in the executors). One builder
 * so the step switch, suite hooks and cleanup URLs resolve identically.
 */
export function templateContextFrom(globalSettings: any, testCaseContext: any): TemplateContext {
    return {
        envVars: globalSettings?.environment?.variables,
        secrets: globalSettings?.secrets,
        dataRow: testCaseContext?.data,
        variables: testCaseContext?.variables,
        hooks: testCaseContext?.hooks,
        scope: testCaseContext?.scope,
        setup: testCaseContext?.setup,
        flags: globalSettings?.flags,
    };
}

/**
 * Resolve every template token in `val` (strings resolve in place; objects
 * and arrays recurse). Replacement order matches the historical behaviour of
 * the web executor: env → secret → data → fake → runtime variables.
 */
export function resolveTemplates(val: any, ctx: TemplateContext): any {
    if (typeof val === 'string') {
        let out = val;
        // Keys may contain hyphens (e.g. an env var named "TH-URL") — the
        // environments UI/API accepts them, so the token grammar must too.
        // \w alone stopped at the first hyphen and left the token unresolved.
        if (ctx.envVars) {
            const envVars = ctx.envVars;
            out = out.replace(/\{\{\s*env\.([\w-]+)\s*\}\}/g, (_: string, key: string) =>
                envVars[key] !== undefined ? String(envVars[key]) : `{{env.${key}}}`);
        }
        if (ctx.secrets) {
            const secrets = ctx.secrets;
            out = out.replace(/\{\{\s*secret\.([\w-]+)\s*\}\}/g, (_: string, key: string) =>
                secrets[key] !== undefined ? String(secrets[key]) : `{{secret.${key}}}`);
        }
        if (ctx.hooks && out.includes('{{')) {
            // Unknown labels stay as tokens, like everything else here, so a
            // typo is visible in the app under test rather than a blank field.
            const inbox = ctx.hooks.inbox || {};
            const webhook = ctx.hooks.webhook || {};
            out = out.replace(/\{\{\s*inbox\.address(?:\.([\w-]+))?\s*\}\}/g, (tok: string, label?: string) =>
                inbox[label || 'default'] ?? tok);
            out = out.replace(/\{\{\s*webhook\.url(?:\.([\w-]+))?\s*\}\}/g, (tok: string, label?: string) =>
                webhook[label || 'default'] ?? tok);
        }
        if (ctx.dataRow) {
            const dataRow = ctx.dataRow;
            out = out.replace(/\{\{\s*data\.([\w-]+)\s*\}\}/g, (_: string, key: string) =>
                dataRow[key] !== undefined ? String(dataRow[key]) : `{{data.${key}}}`);
        }
        if (ctx.scope && out.includes('{{')) {
            const scope = ctx.scope;
            out = out.replace(/\{\{\s*run\.(\w+)\s*\}\}/g, (tok: string, key: string) =>
                scope.vars[key] ?? tok);
            out = out.replace(/\{\{\s*unique\.([\w-]+)\s*\}\}/g, (_: string, name: string) =>
                scope.unique(name));
        }
        if (ctx.setup && out.includes('{{')) {
            const setup = ctx.setup;
            out = out.replace(/\{\{\s*setup\.([\w-]+)\s*\}\}/g, (tok: string, key: string) =>
                setup[key] !== undefined ? String(setup[key]) : tok);
        }
        if (ctx.flags && out.includes('{{')) {
            const flags = ctx.flags;
            // Flag names may contain '.' and '-' (validated by the backend).
            out = out.replace(/\{\{\s*flag\.([\w.-]+)\s*\}\}/g, (tok: string, key: string) =>
                flags[key] !== undefined ? String(flags[key]) : tok);
        }
        if (out.includes('{{fake.')) {
            const random = ctx.scope?.fakeRng;
            out = out.replace(/\{\{\s*fake\.(\w+)\s*\}\}/g, (_: string, kind: string) =>
                generateFake(kind, random));
        }
        if (ctx.variables) {
            const variables = ctx.variables;
            out = out.replace(/\{\{\s*([\w-]+)\s*\}\}/g, (_: string, key: string) =>
                variables[key] !== undefined ? String(variables[key]) : `{{${key}}}`);
        }
        return out;
    }
    if (val && typeof val === 'object') {
        if (Array.isArray(val)) return val.map(item => resolveTemplates(item, ctx));
        const newObj: any = {};
        for (const k in val) newObj[k] = resolveTemplates(val[k], ctx);
        return newObj;
    }
    return val;
}

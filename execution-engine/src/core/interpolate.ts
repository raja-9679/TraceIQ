/**
 * Template interpolation shared by every executor (web test-executor,
 * mobile worker): {{env.KEY}}, {{secret.KEY}}, {{data.KEY}}, {{fake.KIND}},
 * and bare {{name}} runtime variables. Unknown tokens are left in place so
 * mistakes are visible rather than silently blank.
 */

// Lightweight test-data generator for {{fake.KIND}} interpolation. Kept
// dependency-free on purpose; covers the common kinds. Unknown kinds return
// the token unchanged so mistakes are visible rather than silently blank.
export function generateFake(kind: string): string {
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

export interface TemplateContext {
    /** {{env.KEY}} — ProjectEnvironment variables from job settings. */
    envVars?: Record<string, any> | null;
    /** {{secret.KEY}} — decrypted project secrets from job settings. */
    secrets?: Record<string, any> | null;
    /** {{data.KEY}} — the current data-driven dataset row. */
    dataRow?: Record<string, any> | null;
    /** {{name}} — runtime variables from extract-value / scripts. */
    variables?: Record<string, any> | null;
}

/**
 * Resolve every template token in `val` (strings resolve in place; objects
 * and arrays recurse). Replacement order matches the historical behaviour of
 * the web executor: env → secret → data → fake → runtime variables.
 */
export function resolveTemplates(val: any, ctx: TemplateContext): any {
    if (typeof val === 'string') {
        let out = val;
        if (ctx.envVars) {
            const envVars = ctx.envVars;
            out = out.replace(/\{\{\s*env\.(\w+)\s*\}\}/g, (_: string, key: string) =>
                envVars[key] !== undefined ? String(envVars[key]) : `{{env.${key}}}`);
        }
        if (ctx.secrets) {
            const secrets = ctx.secrets;
            out = out.replace(/\{\{\s*secret\.(\w+)\s*\}\}/g, (_: string, key: string) =>
                secrets[key] !== undefined ? String(secrets[key]) : `{{secret.${key}}}`);
        }
        if (ctx.dataRow) {
            const dataRow = ctx.dataRow;
            out = out.replace(/\{\{\s*data\.(\w+)\s*\}\}/g, (_: string, key: string) =>
                dataRow[key] !== undefined ? String(dataRow[key]) : `{{data.${key}}}`);
        }
        if (out.includes('{{fake.')) {
            out = out.replace(/\{\{\s*fake\.(\w+)\s*\}\}/g, (_: string, kind: string) =>
                generateFake(kind));
        }
        if (ctx.variables) {
            const variables = ctx.variables;
            out = out.replace(/\{\{\s*(\w+)\s*\}\}/g, (_: string, key: string) =>
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

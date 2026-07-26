// Load-test executor (executor=load) — wraps k6.
//
// A load case is declarative: its first `load-test` step describes the target
// and thresholds, and this module GENERATES the k6 script (no user-supplied
// code runs, unlike raw_playwright). Requires the `k6` binary in the worker
// image (Dockerfile.worker copies it from grafana/k6).
//
// Result shape: aggregate metrics parsed from k6's --summary-export, plus a
// per-threshold verdict re-evaluated in TS so breaches carry readable
// messages. Time-series output (--out json) is intentionally deferred —
// it is enormous; see SCOPE_NOTES.

import { spawn } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';

export interface LoadSpec {
    target_url: string;
    method?: string;
    headers?: Record<string, string>;
    body?: any;
    vus?: number;                // virtual users (default 10, cap 500)
    duration_s?: number;         // steady-state duration (default 30, cap 900)
    ramp_up_s?: number;          // optional ramp to `vus`
    think_time_s?: number;       // sleep between iterations (default 1)
    thresholds?: {
        p95_ms?: number;         // http_req_duration p(95)
        p99_ms?: number;
        error_rate?: number;     // 0–1, http_req_failed
        min_rps?: number;        // http_reqs rate
    };
}

export interface LoadRunOutcome {
    status: 'passed' | 'failed' | 'error';
    duration_ms: number;
    error?: string;
    payload?: {
        target_url: string;
        vus: number;
        duration_s: number;
        metrics: Record<string, any>;
        thresholds: Array<{ name: string; limit: number; actual: number | null; passed: boolean }>;
    };
}

const ALLOWED_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'];
const MAX_VUS = parseInt(process.env.LOAD_MAX_VUS || '500', 10);
const MAX_DURATION_S = parseInt(process.env.LOAD_MAX_DURATION_S || '900', 10);

// {{env.X}} / {{secret.X}} interpolation for load specs (the step-executor's
// resolver is browser-context-bound; load jobs have no page).
function interpolate(value: string, settings: any): string {
    let out = value;
    const envVars = settings?.environment?.variables;
    if (envVars) {
        out = out.replace(/\{\{\s*env\.(\w+)\s*\}\}/g, (_: string, k: string) =>
            envVars[k] !== undefined ? String(envVars[k]) : `{{env.${k}}}`);
    }
    const secrets = settings?.secrets;
    if (secrets) {
        out = out.replace(/\{\{\s*secret\.(\w+)\s*\}\}/g, (_: string, k: string) =>
            secrets[k] !== undefined ? String(secrets[k]) : `{{secret.${k}}}`);
    }
    return out;
}

function buildScript(spec: Required<Pick<LoadSpec, 'target_url' | 'method' | 'vus' | 'duration_s' | 'think_time_s'>> & LoadSpec): string {
    const t = spec.thresholds || {};
    const durationThresholds: string[] = [];
    if (t.p95_ms) durationThresholds.push(`p(95)<${t.p95_ms}`);
    if (t.p99_ms) durationThresholds.push(`p(99)<${t.p99_ms}`);
    const thresholds: Record<string, string[]> = {};
    if (durationThresholds.length) thresholds['http_req_duration'] = durationThresholds;
    if (t.error_rate !== undefined) thresholds['http_req_failed'] = [`rate<${t.error_rate}`];
    if (t.min_rps) thresholds['http_reqs'] = [`rate>${t.min_rps}`];

    const options: any = { thresholds };
    if (spec.ramp_up_s) {
        options.stages = [
            { duration: `${spec.ramp_up_s}s`, target: spec.vus },
            { duration: `${spec.duration_s}s`, target: spec.vus },
        ];
    } else {
        options.vus = spec.vus;
        options.duration = `${spec.duration_s}s`;
    }

    // Everything user-controlled is JSON.stringify'd into the script, so the
    // generated code cannot be escaped into arbitrary k6 JS.
    return [
        `import http from 'k6/http';`,
        `import { check, sleep } from 'k6';`,
        `export const options = ${JSON.stringify(options)};`,
        `const URL = ${JSON.stringify(spec.target_url)};`,
        `const METHOD = ${JSON.stringify(spec.method)};`,
        `const HEADERS = ${JSON.stringify(spec.headers || {})};`,
        `const BODY = ${JSON.stringify(spec.body ?? null)};`,
        `export default function () {`,
        `  const payload = BODY === null ? null : (typeof BODY === 'string' ? BODY : JSON.stringify(BODY));`,
        `  const res = http.request(METHOD, URL, payload, { headers: HEADERS });`,
        `  check(res, { 'status < 400': (r) => r.status > 0 && r.status < 400 });`,
        spec.think_time_s > 0 ? `  sleep(${spec.think_time_s});` : '',
        `}`,
    ].filter(Boolean).join('\n');
}

function metricValue(metrics: Record<string, any>, name: string, key: string): number | null {
    const m = metrics?.[name];
    if (!m) return null;
    const v = m[key] ?? m.value ?? m.rate;
    return typeof v === 'number' ? v : null;
}

export async function runLoadTest(rawSpec: LoadSpec, settings: any, artifactsDir: string): Promise<LoadRunOutcome> {
    const start = Date.now();

    const method = (rawSpec.method || 'GET').toUpperCase();
    if (!ALLOWED_METHODS.includes(method)) {
        return { status: 'error', duration_ms: 0, error: `load-test: unsupported method ${method}` };
    }
    if (!rawSpec.target_url) {
        return { status: 'error', duration_ms: 0, error: 'load-test: target_url is required' };
    }

    const spec = {
        ...rawSpec,
        target_url: interpolate(rawSpec.target_url, settings),
        method,
        headers: Object.fromEntries(Object.entries(rawSpec.headers || {})
            .map(([k, v]) => [k, interpolate(String(v), settings)])),
        vus: Math.min(Math.max(1, rawSpec.vus || 10), MAX_VUS),
        duration_s: Math.min(Math.max(1, rawSpec.duration_s || 30), MAX_DURATION_S),
        think_time_s: rawSpec.think_time_s ?? 1,
    };

    const scriptPath = path.join(artifactsDir, 'load-script.js');
    const summaryPath = path.join(artifactsDir, 'load-summary.json');
    fs.writeFileSync(scriptPath, buildScript(spec));

    const totalBudgetMs = (spec.duration_s + (spec.ramp_up_s || 0) + 90) * 1000;

    const exit = await new Promise<{ code: number | null; err?: string; stderr: string }>((resolve) => {
        const proc = spawn('k6', ['run', '--quiet', `--summary-export=${summaryPath}`, scriptPath], {
            stdio: ['ignore', 'pipe', 'pipe'],
        });
        let stderr = '';
        proc.stdout.on('data', () => { /* --quiet keeps this minimal */ });
        proc.stderr.on('data', (d) => { stderr += d.toString().slice(0, 2000 - stderr.length); });
        const timer = setTimeout(() => {
            proc.kill('SIGKILL');
            resolve({ code: null, err: `load-test: k6 exceeded time budget (${Math.round(totalBudgetMs / 1000)}s)`, stderr });
        }, totalBudgetMs);
        proc.on('error', (e: any) => {
            clearTimeout(timer);
            resolve({
                code: null,
                err: e.code === 'ENOENT'
                    ? 'load-test: k6 binary not found in the worker image — rebuild with the updated Dockerfile.worker'
                    : `load-test: failed to start k6: ${e.message}`,
                stderr,
            });
        });
        proc.on('exit', (code) => { clearTimeout(timer); resolve({ code, stderr }); });
    });

    if (exit.err) {
        return { status: 'error', duration_ms: Date.now() - start, error: exit.err };
    }

    let metrics: Record<string, any> = {};
    try {
        metrics = JSON.parse(fs.readFileSync(summaryPath, 'utf-8')).metrics || {};
    } catch {
        return {
            status: 'error', duration_ms: Date.now() - start,
            error: `load-test: k6 exited ${exit.code} without a summary${exit.stderr ? `: ${exit.stderr.slice(0, 500)}` : ''}`,
        };
    }

    // Re-evaluate thresholds in TS so failures carry readable messages
    // (k6 only signals "some threshold crossed" via exit code 99).
    const t = spec.thresholds || {};
    const verdicts: Array<{ name: string; limit: number; actual: number | null; passed: boolean }> = [];
    const check = (name: string, limit: number | undefined, actual: number | null, cmp: (a: number, l: number) => boolean) => {
        if (limit === undefined || limit === null) return;
        verdicts.push({ name, limit, actual, passed: actual !== null && cmp(actual, limit) });
    };
    check('p95_ms', t.p95_ms, metricValue(metrics, 'http_req_duration', 'p(95)'), (a, l) => a < l);
    check('p99_ms', t.p99_ms, metricValue(metrics, 'http_req_duration', 'p(99)'), (a, l) => a < l);
    check('error_rate', t.error_rate, metricValue(metrics, 'http_req_failed', 'value'), (a, l) => a < l);
    check('min_rps', t.min_rps, metricValue(metrics, 'http_reqs', 'rate'), (a, l) => a > l);

    const breached = verdicts.filter((v) => !v.passed);
    const passed = exit.code === 0 && breached.length === 0;

    const compact = {
        http_reqs: metrics.http_reqs,
        http_req_duration: metrics.http_req_duration,
        http_req_failed: metrics.http_req_failed,
        iterations: metrics.iterations,
        vus_max: metrics.vus_max,
        data_received: metrics.data_received,
        data_sent: metrics.data_sent,
    };

    return {
        status: passed ? 'passed' : 'failed',
        duration_ms: Date.now() - start,
        error: passed ? undefined : (
            breached.length
                ? 'Thresholds breached: ' + breached.map((b) => `${b.name} (limit ${b.limit}, actual ${b.actual === null ? 'n/a' : Math.round((b.actual + Number.EPSILON) * 1000) / 1000})`).join(', ')
                : `k6 exited ${exit.code}${exit.stderr ? `: ${exit.stderr.slice(0, 300)}` : ''}`
        ),
        payload: {
            target_url: spec.target_url,
            vus: spec.vus,
            duration_s: spec.duration_s,
            metrics: compact,
            thresholds: verdicts,
        },
    };
}

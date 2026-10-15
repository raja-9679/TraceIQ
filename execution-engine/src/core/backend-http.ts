// Worker -> backend HTTP for the internal endpoints (WP8).
//
// One place that knows how to reach the backend, so optional mTLS applies to
// every internal call (worker-config, llm-usage, visual baselines, test-hooks)
// instead of to whichever caller remembered it.
//
//   WORKER_TLS_CERT_FILE / WORKER_TLS_KEY_FILE   client certificate + key (PEM)
//   WORKER_TLS_CA_FILE                           CA bundle that signed the
//                                                proxy's server certificate
//
// Without the cert/key pair this is plain `fetch`, exactly as before. With it,
// requests go through node:https with a client-cert agent (global fetch has no
// client-certificate option without the undici package). The shared
// X-Worker-Secret is still sent either way — see backend app/core/worker_auth.py
// for why the two layers are kept together.

import * as fs from 'fs';
import * as http from 'http';
import * as https from 'https';

export const BACKEND_URL = (process.env.BACKEND_URL || 'http://backend:8000').replace(/\/$/, '');
export const WORKER_TOKEN = process.env.WORKER_WEBHOOK_SECRET || process.env.WEBHOOK_SECRET || '';

export interface BackendResponse {
    status: number;
    ok: boolean;
    text(): Promise<string>;
    json(): Promise<any>;
}

export interface TlsFiles {
    cert?: string;
    key?: string;
    ca?: string;
}

export function tlsFilesFromEnv(env: NodeJS.ProcessEnv = process.env): TlsFiles | null {
    const cert = env.WORKER_TLS_CERT_FILE;
    const key = env.WORKER_TLS_KEY_FILE;
    const ca = env.WORKER_TLS_CA_FILE;
    if (!cert && !key && !ca) return null;
    if ((cert && !key) || (key && !cert)) {
        // Half a client identity is a misconfiguration, not "no mTLS": refuse
        // loudly at first use rather than silently connecting without a cert.
        throw new Error('WORKER_TLS_CERT_FILE and WORKER_TLS_KEY_FILE must be set together');
    }
    return { cert, key, ca };
}

let agent: https.Agent | null | undefined;

function tlsAgent(): https.Agent | null {
    if (agent !== undefined) return agent;
    const files = tlsFilesFromEnv();
    agent = files ? new https.Agent({
        cert: files.cert ? fs.readFileSync(files.cert) : undefined,
        key: files.key ? fs.readFileSync(files.key) : undefined,
        ca: files.ca ? fs.readFileSync(files.ca) : undefined,
        keepAlive: true,
    }) : null;
    return agent;
}

/** Reset the cached agent (tests). */
export function _resetBackendAgent(): void {
    agent = undefined;
}

function viaAgent(url: string, init: { method: string; headers: Record<string, string>; body?: string; timeoutMs: number },
                  tls: https.Agent): Promise<BackendResponse> {
    return new Promise((resolve, reject) => {
        const u = new URL(url);
        if (u.protocol !== 'https:') {
            reject(new Error('worker mTLS is configured but BACKEND_URL is not https://'));
            return;
        }
        const req = https.request(u, { method: init.method, headers: init.headers, agent: tls }, (res: http.IncomingMessage) => {
            const chunks: Buffer[] = [];
            res.on('data', (c: Buffer) => chunks.push(c));
            res.on('end', () => {
                const body = Buffer.concat(chunks).toString('utf8');
                const status = res.statusCode || 0;
                resolve({
                    status, ok: status >= 200 && status < 300,
                    text: async () => body,
                    json: async () => JSON.parse(body),
                });
            });
            res.on('error', reject);
        });
        req.setTimeout(init.timeoutMs, () => req.destroy(new Error(`backend request timed out after ${init.timeoutMs}ms`)));
        req.on('error', reject);
        if (init.body !== undefined) req.write(init.body);
        req.end();
    });
}

export async function backendFetch(path: string, init: {
    method?: string; headers?: Record<string, string>; body?: string; timeoutMs?: number;
} = {}): Promise<BackendResponse> {
    const url = `${BACKEND_URL}${path}`;
    const headers = { 'X-Worker-Secret': WORKER_TOKEN, ...(init.headers || {}) };
    const method = init.method || 'GET';
    const timeoutMs = init.timeoutMs ?? 15000;
    const tls = tlsAgent();
    if (tls) return viaAgent(url, { method, headers, body: init.body, timeoutMs }, tls);
    const res = await fetch(url, { method, headers, body: init.body, signal: AbortSignal.timeout(timeoutMs) });
    return {
        status: res.status, ok: res.ok,
        text: () => res.text(),
        json: () => res.json(),
    };
}

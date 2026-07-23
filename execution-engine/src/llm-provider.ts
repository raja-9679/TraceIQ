// LLM provider abstraction for the execution engine.
//
// Selects a backing provider based on env vars at module load:
//   LLM_PROVIDER=anthropic|openai|gemini|ollama|openai-compatible  (explicit)
//   ANTHROPIC_API_KEY              (implicit → anthropic)
//   GEMINI_API_KEY                 (implicit → gemini)
//   OPENAI_API_KEY                 (implicit → openai)
//   OLLAMA_BASE_URL                (implicit → ollama; local + free, no key)
// Gemini, Ollama, and openai-compatible (Groq/OpenRouter/LM Studio/vLLM via
// LLM_BASE_URL + LLM_API_KEY) all reuse the OpenAI client with a base URL.
// Falls back to a null provider that returns "" so callers don't have to
// guard every call on key-present checks.

export interface LLMCallOpts {
    system?: string;
    maxTokens?: number;
    // Usage-metering context: which feature made the call and for which run.
    // The backend resolves workspace/project from the run id.
    feature?: string;
    runId?: number;
}

export interface LLMProvider {
    name: string;
    complete(prompt: string, opts?: LLMCallOpts): Promise<string>;
}

// ---------------------------------------------------------------------------
// Usage reporting — every call posts its token counts to the backend so the
// AI-usage dashboard covers worker-side calls too. Fire-and-forget: metering
// must never slow down or fail a test run.
// ---------------------------------------------------------------------------

const BACKEND_URL = process.env.BACKEND_URL || 'http://backend:8000';
const WORKER_TOKEN = process.env.WORKER_WEBHOOK_SECRET || process.env.WEBHOOK_SECRET || '';

function reportUsage(event: {
    provider: string; model: string; feature?: string; runId?: number;
    inputTokens: number; outputTokens: number; latencyMs: number;
    success: boolean; error?: string;
}): void {
    fetch(`${BACKEND_URL}/api/internal/llm-usage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Worker-Secret': WORKER_TOKEN },
        body: JSON.stringify({
            events: [{
                provider: event.provider,
                model: event.model,
                feature: event.feature || 'unknown',
                run_id: event.runId ?? null,
                input_tokens: event.inputTokens,
                output_tokens: event.outputTokens,
                latency_ms: event.latencyMs,
                success: event.success,
                error: event.error || null,
            }],
        }),
    }).then((res) => {
        if (!res.ok) console.warn(`[LLM] usage report returned ${res.status}`);
    }).catch((err) => {
        console.warn('[LLM] usage report failed:', err?.message || err);
    });
}

// Any OpenAI-wire-compatible endpoint: OpenAI, Gemini (compat endpoint),
// Ollama, Groq, OpenRouter, LM Studio, vLLM.
class OpenAICompatibleProvider implements LLMProvider {
    name: string;
    private client: any;
    private model: string;

    constructor(name: string, apiKey: string, model: string, baseURL?: string) {
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        const OpenAI = require('openai').default;
        this.name = name;
        this.client = baseURL ? new OpenAI({ apiKey, baseURL }) : new OpenAI({ apiKey });
        this.model = model;
    }

    async complete(prompt: string, opts: LLMCallOpts = {}): Promise<string> {
        const started = Date.now();
        try {
            const messages: any[] = [];
            if (opts.system) messages.push({ role: 'system', content: opts.system });
            messages.push({ role: 'user', content: prompt });
            const resp = await this.client.chat.completions.create({
                model: this.model,
                messages,
                max_tokens: opts.maxTokens ?? 1024,
            });
            reportUsage({
                provider: this.name, model: this.model,
                feature: opts.feature, runId: opts.runId,
                inputTokens: resp.usage?.prompt_tokens ?? 0,
                outputTokens: resp.usage?.completion_tokens ?? 0,
                latencyMs: Date.now() - started, success: true,
            });
            return (resp.choices[0].message.content || '').trim();
        } catch (err: any) {
            reportUsage({
                provider: this.name, model: this.model,
                feature: opts.feature, runId: opts.runId,
                inputTokens: 0, outputTokens: 0,
                latencyMs: Date.now() - started, success: false,
                error: String(err?.message || err),
            });
            console.error(`[LLM] ${this.name} call failed:`, err);
            return '';
        }
    }
}

const GEMINI_OPENAI_BASE_URL = 'https://generativelanguage.googleapis.com/v1beta/openai/';

class AnthropicProvider implements LLMProvider {
    name = 'anthropic';
    private client: any;
    private model: string;

    constructor(apiKey: string, model: string) {
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        const Anthropic = require('@anthropic-ai/sdk').default;
        this.client = new Anthropic({ apiKey });
        this.model = model;
    }

    async complete(prompt: string, opts: LLMCallOpts = {}): Promise<string> {
        const started = Date.now();
        try {
            const params: any = {
                model: this.model,
                max_tokens: opts.maxTokens ?? 1024,
                messages: [{ role: 'user', content: prompt }],
            };
            if (opts.system) params.system = opts.system;
            const resp = await this.client.messages.create(params);
            reportUsage({
                provider: this.name, model: this.model,
                feature: opts.feature, runId: opts.runId,
                inputTokens: resp.usage?.input_tokens ?? 0,
                outputTokens: resp.usage?.output_tokens ?? 0,
                latencyMs: Date.now() - started, success: true,
            });
            const parts = (resp.content || []).filter((b: any) => b.type === 'text').map((b: any) => b.text);
            return parts.join('').trim();
        } catch (err: any) {
            reportUsage({
                provider: this.name, model: this.model,
                feature: opts.feature, runId: opts.runId,
                inputTokens: 0, outputTokens: 0,
                latencyMs: Date.now() - started, success: false,
                error: String(err?.message || err),
            });
            console.error('[LLM] Anthropic call failed:', err);
            return '';
        }
    }
}

class NullProvider implements LLMProvider {
    name = 'null';
    async complete(): Promise<string> {
        return '';
    }
}

export function pickProvider(): LLMProvider {
    const explicit = (process.env.LLM_PROVIDER || '').toLowerCase().trim();
    const model = process.env.LLM_MODEL;
    try {
        if (explicit === 'anthropic' || (!explicit && process.env.ANTHROPIC_API_KEY)) {
            const key = process.env.ANTHROPIC_API_KEY;
            if (!key) {
                console.warn('[LLM] LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY missing; using null provider');
                return new NullProvider();
            }
            return new AnthropicProvider(key, model || 'claude-opus-4-8');
        }
        if (explicit === 'gemini' || (!explicit && (process.env.GEMINI_API_KEY || process.env.GOOGLE_API_KEY))) {
            const key = process.env.GEMINI_API_KEY || process.env.GOOGLE_API_KEY;
            if (!key) {
                console.warn('[LLM] LLM_PROVIDER=gemini but GEMINI_API_KEY missing; using null provider');
                return new NullProvider();
            }
            return new OpenAICompatibleProvider('gemini', key, model || 'gemini-2.0-flash', GEMINI_OPENAI_BASE_URL);
        }
        if (explicit === 'openai' || (!explicit && process.env.OPENAI_API_KEY)) {
            const key = process.env.OPENAI_API_KEY;
            if (!key) return new NullProvider();
            return new OpenAICompatibleProvider('openai', key, model || 'gpt-4o');
        }
        if (explicit === 'ollama' || (!explicit && process.env.OLLAMA_BASE_URL)) {
            // Local + free, no API key. From inside Docker use
            // OLLAMA_BASE_URL=http://host.docker.internal:11434/v1
            const baseURL = process.env.OLLAMA_BASE_URL || 'http://localhost:11434/v1';
            return new OpenAICompatibleProvider('ollama', 'ollama', model || 'llama3.1', baseURL);
        }
        if (explicit === 'openai-compatible' || explicit === 'custom') {
            const baseURL = process.env.LLM_BASE_URL;
            if (!baseURL || !model) {
                console.warn('[LLM] LLM_PROVIDER=openai-compatible needs LLM_BASE_URL and LLM_MODEL; using null provider');
                return new NullProvider();
            }
            return new OpenAICompatibleProvider('openai-compatible', process.env.LLM_API_KEY || 'not-needed', model, baseURL);
        }
    } catch (err) {
        console.error('[LLM] Provider init failed; using null provider:', err);
    }
    return new NullProvider();
}

// Resolve once at module load.
export const provider: LLMProvider = pickProvider();

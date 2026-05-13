// LLM provider abstraction for the execution engine.
//
// Selects a backing provider based on env vars at module load:
//   LLM_PROVIDER=openai|anthropic  (explicit)
//   ANTHROPIC_API_KEY              (implicit → anthropic)
//   OPENAI_API_KEY                 (implicit → openai)
// Falls back to a null provider that returns "" so callers don't have to
// guard every call on key-present checks.

export interface LLMProvider {
    name: string;
    complete(prompt: string, opts?: { system?: string; maxTokens?: number }): Promise<string>;
}

class OpenAIProvider implements LLMProvider {
    name = 'openai';
    private client: any;
    private model: string;

    constructor(apiKey: string, model: string) {
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        const OpenAI = require('openai').default;
        this.client = new OpenAI({ apiKey });
        this.model = model;
    }

    async complete(prompt: string, opts: { system?: string; maxTokens?: number } = {}): Promise<string> {
        try {
            const messages: any[] = [];
            if (opts.system) messages.push({ role: 'system', content: opts.system });
            messages.push({ role: 'user', content: prompt });
            const resp = await this.client.chat.completions.create({
                model: this.model,
                messages,
                max_tokens: opts.maxTokens ?? 1024,
            });
            return (resp.choices[0].message.content || '').trim();
        } catch (err) {
            console.error('[LLM] OpenAI call failed:', err);
            return '';
        }
    }
}

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

    async complete(prompt: string, opts: { system?: string; maxTokens?: number } = {}): Promise<string> {
        try {
            const params: any = {
                model: this.model,
                max_tokens: opts.maxTokens ?? 1024,
                messages: [{ role: 'user', content: prompt }],
            };
            if (opts.system) params.system = opts.system;
            const resp = await this.client.messages.create(params);
            const parts = (resp.content || []).filter((b: any) => b.type === 'text').map((b: any) => b.text);
            return parts.join('').trim();
        } catch (err) {
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
            return new AnthropicProvider(key, model || 'claude-opus-4-7');
        }
        if (explicit === 'openai' || (!explicit && process.env.OPENAI_API_KEY)) {
            const key = process.env.OPENAI_API_KEY;
            if (!key) return new NullProvider();
            return new OpenAIProvider(key, model || 'gpt-4o');
        }
    } catch (err) {
        console.error('[LLM] Provider init failed; using null provider:', err);
    }
    return new NullProvider();
}

// Resolve once at module load.
export const provider: LLMProvider = pickProvider();

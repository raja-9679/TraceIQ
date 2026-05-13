import OpenAI from 'openai';

const openai = new OpenAI({
    apiKey: process.env.OPENAI_API_KEY,
});

// Max characters to send to OpenAI (~5 000 tokens at ~4 chars/token)
const DOM_SNAPSHOT_MAX_CHARS = 20_000;

// Per-run limit on AI healing calls to prevent runaway spend
const MAX_HEALS_PER_RUN = parseInt(process.env.AI_MAX_HEALS_PER_RUN || '10');

// Simple fixed-size LRU cache: Map preserves insertion order.
// Key: `<brokenSelector>||<domPrefix>` → healed selector string.
const CACHE_MAX_SIZE = 500;
const healCache = new Map<string, string>();

// Per-run call counters; cleaned up when a run finishes.
const runHealCounts = new Map<number, number>();

function lruGet(key: string): string | undefined {
    if (!healCache.has(key)) return undefined;
    // Re-insert to mark as recently used
    const val = healCache.get(key)!;
    healCache.delete(key);
    healCache.set(key, val);
    return val;
}

function lruSet(key: string, value: string): void {
    if (healCache.has(key)) healCache.delete(key);
    if (healCache.size >= CACHE_MAX_SIZE) {
        // Evict least-recently-used (first inserted) entry
        healCache.delete(healCache.keys().next().value);
    }
    healCache.set(key, value);
}

export class AIEngine {
    async healSelector(brokenSelector: string, domSnapshot: string, runId?: number): Promise<string> {
        if (!process.env.OPENAI_API_KEY) return "";

        // Per-run cost cap
        if (runId !== undefined) {
            const count = runHealCounts.get(runId) ?? 0;
            if (count >= MAX_HEALS_PER_RUN) {
                console.warn(`[AI] Heal cap reached for run ${runId} (${MAX_HEALS_PER_RUN} calls)`);
                return "";
            }
            runHealCounts.set(runId, count + 1);
        }

        // Truncate DOM snapshot to limit token usage
        const truncatedDom = domSnapshot.length > DOM_SNAPSHOT_MAX_CHARS
            ? domSnapshot.substring(0, DOM_SNAPSHOT_MAX_CHARS) + '\n<!-- DOM truncated -->'
            : domSnapshot;

        const cacheKey = `${brokenSelector}||${truncatedDom.substring(0, 200)}`;
        const cached = lruGet(cacheKey);
        if (cached !== undefined) {
            console.log(`[AI] Cache hit for selector: ${brokenSelector}`);
            return cached;
        }

        try {
            const response = await openai.chat.completions.create({
                model: "gpt-4o",
                messages: [
                    {
                        role: "user",
                        content: `The selector '${brokenSelector}' failed to find an element.\nHere is the DOM snapshot:\n${truncatedDom}\n\nFind the element that most likely corresponds to the broken selector.\nReturn ONLY the new selector.`
                    }
                ]
            });
            const healed = response.choices[0].message.content?.trim() || "";
            lruSet(cacheKey, healed);
            return healed;
        } catch (e) {
            console.error("AI Healing failed:", e);
            return "";
        }
    }

    /** Call after a run completes to free the per-run counter. */
    clearRunState(runId: number): void {
        runHealCounts.delete(runId);
    }
}

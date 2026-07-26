import { provider } from './llm-provider';

// Max characters to send to the LLM (~5 000 tokens at ~4 chars/token)
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
    const val = healCache.get(key)!;
    healCache.delete(key);
    healCache.set(key, val);
    return val;
}

function lruSet(key: string, value: string): void {
    if (healCache.has(key)) healCache.delete(key);
    if (healCache.size >= CACHE_MAX_SIZE) {
        const oldest = healCache.keys().next().value;
        if (oldest !== undefined) healCache.delete(oldest);
    }
    healCache.set(key, value);
}

export class AIEngine {
    async healSelector(brokenSelector: string, domSnapshot: string, runId?: number): Promise<string> {
        if (provider.name === 'null') return '';

        if (runId !== undefined) {
            const count = runHealCounts.get(runId) ?? 0;
            if (count >= MAX_HEALS_PER_RUN) {
                console.warn(`[AI] Heal cap reached for run ${runId} (${MAX_HEALS_PER_RUN} calls)`);
                return '';
            }
            runHealCounts.set(runId, count + 1);
        }

        const truncatedDom = domSnapshot.length > DOM_SNAPSHOT_MAX_CHARS
            ? domSnapshot.substring(0, DOM_SNAPSHOT_MAX_CHARS) + '\n<!-- DOM truncated -->'
            : domSnapshot;

        const cacheKey = `${brokenSelector}||${truncatedDom.substring(0, 200)}`;
        const cached = lruGet(cacheKey);
        if (cached !== undefined) {
            console.log(`[AI] Cache hit for selector: ${brokenSelector}`);
            return cached;
        }

        const prompt = `The selector '${brokenSelector}' failed to find an element.\nHere is the DOM snapshot:\n${truncatedDom}\n\nFind the element that most likely corresponds to the broken selector.\nReturn ONLY the new selector.`;
        const healed = await provider.complete(prompt, { maxTokens: 128, feature: 'selector_heal', runId });
        lruSet(cacheKey, healed);
        return healed;
    }

    /**
     * Mobile analogue of healSelector: propose a replacement Appium locator
     * from the native page source (XML). Shares the same LRU cache and
     * per-run call cap. Returns '' when no proposal.
     */
    async healMobileLocator(brokenLocator: string, pageSourceXml: string, runId?: number): Promise<string> {
        if (provider.name === 'null') return '';

        if (runId !== undefined) {
            const count = runHealCounts.get(runId) ?? 0;
            if (count >= MAX_HEALS_PER_RUN) {
                console.warn(`[AI] Heal cap reached for run ${runId} (${MAX_HEALS_PER_RUN} calls)`);
                return '';
            }
            runHealCounts.set(runId, count + 1);
        }

        const truncated = pageSourceXml.length > DOM_SNAPSHOT_MAX_CHARS
            ? pageSourceXml.substring(0, DOM_SNAPSHOT_MAX_CHARS) + '\n<!-- page source truncated -->'
            : pageSourceXml;

        const cacheKey = `mobile:${brokenLocator}||${truncated.substring(0, 200)}`;
        const cached = lruGet(cacheKey);
        if (cached !== undefined) {
            console.log(`[AI] Cache hit for mobile locator: ${brokenLocator}`);
            return cached;
        }

        const prompt = `The Appium locator '${brokenLocator}' failed to find an element in a native mobile app.
Here is the current native page source (XML):
${truncated}

Find the element that most likely corresponds to the broken locator and return a replacement locator, using EXACTLY one of these forms:
  ~ACCESSIBILITY_ID          (content-desc / accessibility id — prefer this when available)
  id=RESOURCE_ID             (Android resource-id or iOS name)
  xpath=//XPATH_EXPRESSION   (over the page source above)

Return ONLY the new locator, nothing else.`;
        const healed = await provider.complete(prompt, { maxTokens: 128, feature: 'selector_heal', runId });
        lruSet(cacheKey, healed);
        return healed;
    }

    /** Call after a run completes to free the per-run counter. */
    clearRunState(runId: number): void {
        runHealCounts.delete(runId);
    }
}

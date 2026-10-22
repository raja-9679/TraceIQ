import type { LLMUsageSummary } from '@/api/llmUsage';

// What each LLM usage `feature` id means, in words. Shared by the AI usage and
// Costs pages so neither shows a raw id.
//
// Ids come from llm_call_context(feature=...) in the backend
// (app/ai/engine.py, app/services/assistant.py, app/api/case_generation.py,
// app/services/generation_proposals.py) and from the worker's usage reports.

export const FEATURE_LABELS: Record<string, string> = {
    failure_analysis: 'Failure analysis',
    selector_heal: 'Selector healing',
    case_generation: 'Test generation',
    assistant: 'Assistant',
    requirement_generation: 'Tests from requirements',
    incident_regression: 'Regression tests from incidents',
    unknown: 'Other',
};

/** A feature id in words; an unknown id is de-underscored and capitalised. */
export function featureLabel(id: string | null | undefined): string {
    if (!id) return FEATURE_LABELS.unknown;
    if (FEATURE_LABELS[id]) return FEATURE_LABELS[id];
    const words = id.replace(/_/g, ' ');
    return words.charAt(0).toUpperCase() + words.slice(1);
}

type DailyPoint = LLMUsageSummary['daily'][number];

/** The API returns only days that had calls; give every day of the window a
 *  slot (UTC dates, like the API's) so the axis spans the whole period. */
export function fillDays(daily: DailyPoint[], days: number, today = new Date()): DailyPoint[] {
    const byDate = new Map(daily.map((d) => [d.date, d]));
    const iso = (t: number) => new Date(t).toISOString().slice(0, 10);
    const dayMs = 86_400_000;
    const end = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
    let start = end - (days - 1) * dayMs;
    // The API's window starts `days` back to the minute, so it can hold one
    // more calendar day than `days`; never drop a day that has data.
    const first = daily[0]?.date;
    if (first && first < iso(start)) start = Date.parse(`${first}T00:00:00Z`);
    const out: DailyPoint[] = [];
    for (let t = start; t <= end; t += dayMs) {
        const date = iso(t);
        out.push(byDate.get(date) ?? { date, calls: 0, total_tokens: 0 });
    }
    // Data dated after "today" (clock skew) is kept rather than hidden.
    for (const d of daily) if (d.date > iso(end)) out.push(d);
    return out;
}

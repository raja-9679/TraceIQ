import type { HealCandidate, HealProposal, HealRecheck, HealStrategy } from '@/api/proposals';
import type { Tone } from '@/components/redesign/kinds';

export const STRATEGY_LABEL: Record<HealStrategy, string> = {
    'test-id': 'Test id',
    id: 'Element id',
    role: 'Role + name',
    label: 'Label / placeholder / name',
    text: 'Visible text',
    'css-path': 'CSS path',
    llm: 'AI suggestion',
};

export const BREAKDOWN_LABEL: Record<keyof HealCandidate['breakdown'], string> = {
    stability: 'Stability',
    similarity: 'Similarity',
    llm: 'AI',
    uniqueness: 'Unique',
};

/** Candidates for display; a pre-W5 proposal shows its single selector. */
export function candidatesOf(p: HealProposal): HealCandidate[] {
    if (p.candidates && p.candidates.length) return p.candidates;
    return [{
        selector: p.new_selector, strategy: 'llm', source: 'llm', matches: 0, applicable: false,
        score: p.confidence, breakdown: { stability: 0, similarity: 0, llm: p.confidence, uniqueness: 0 },
    }];
}

export function defaultPick(p: HealProposal): string {
    return p.chosen_selector || p.new_selector;
}

export function matchesLabel(c: HealCandidate, legacy: boolean): string {
    if (legacy) return 'not counted';
    if (c.matches === 1) return '1 match';
    return `${c.matches} matches`;
}

export function recheckTone(r: HealRecheck): Tone {
    if (r.status === 'unique') return 'mint';
    if (r.status === 'unavailable') return 'butter';
    return 'pink';
}

export function recheckLabel(r: HealRecheck): string {
    switch (r.status) {
        case 'unique': return 'Unique in snapshot';
        case 'not_unique': return `${r.matches} matches in snapshot`;
        case 'no_match': return 'No match in snapshot';
        case 'unsupported': return 'Cannot be checked';
        default: return 'Not rechecked';
    }
}

export const pct = (n: number) => `${Math.round(n * 100)}%`;

import type { ManualOutcome, ManualRunSummary } from '@/api/manualTesting';

// Manual steps are stored as ordinary TestCase steps:
// { type: 'manual-step', value: <instruction>, params: { expected } }.

export interface ManualStepDraft {
    id: string;
    instruction: string;
    expected: string;
}

export const MANUAL_STEP = 'manual-step';

export const OUTCOMES: { value: ManualOutcome; label: string; tone: 'mint' | 'peach' | 'butter' | 'neutral' }[] = [
    { value: 'passed', label: 'Passed', tone: 'mint' },
    { value: 'failed', label: 'Failed', tone: 'peach' },
    { value: 'blocked', label: 'Blocked', tone: 'butter' },
    { value: 'skipped', label: 'Skipped', tone: 'neutral' },
];

export function outcomeMeta(o: ManualOutcome | null | undefined) {
    return OUTCOMES.find((x) => x.value === o) ?? { value: null, label: 'Not recorded', tone: 'neutral' as const };
}

export function newStepId(): string {
    return typeof crypto !== 'undefined' && 'randomUUID' in crypto
        ? crypto.randomUUID()
        : `s-${Math.random().toString(36).slice(2)}`;
}

interface StoredStep { id?: string; type?: string; value?: string; params?: { expected?: string } | null }

export function draftsFromSteps(steps: StoredStep[] | null | undefined): ManualStepDraft[] {
    return (steps || [])
        .filter((s) => s?.type === MANUAL_STEP)
        .map((s) => ({ id: s.id || newStepId(), instruction: s.value || '', expected: s.params?.expected || '' }));
}

export function stepsFromDrafts(drafts: ManualStepDraft[]) {
    return drafts
        .filter((d) => d.instruction.trim() || d.expected.trim())
        .map((d) => ({ id: d.id, type: MANUAL_STEP, value: d.instruction.trim(), params: { expected: d.expected.trim() } }));
}

/** First problem that would make the server refuse the case, or null. */
export function draftError(name: string, drafts: ManualStepDraft[]): string | null {
    if (!name.trim()) return 'Give the test a name';
    const steps = stepsFromDrafts(drafts);
    if (steps.length === 0) return 'Add at least one step';
    const missing = steps.findIndex((s) => !s.value);
    if (missing >= 0) return `Step ${missing + 1} needs an instruction`;
    return null;
}

export function summaryLine(s: ManualRunSummary): string {
    const parts = [`${s.counts.passed} passed`, `${s.counts.failed} failed`, `${s.counts.blocked} blocked`, `${s.counts.skipped} skipped`];
    if (s.unrecorded) parts.push(`${s.unrecorded} to do`);
    return parts.join(' · ');
}

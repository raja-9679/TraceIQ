// Pure helpers for the Value report page and the hypothesis rating buttons.
import type { HypothesisFeedbackRow, HypothesisVerdict, ValueMetric } from '@/api/valueReport';
import { fmtHours, fmtPct } from '@/components/reports/chartMath';

export const KIND_LABEL = { measured: 'Measured', estimated: 'Estimate' } as const;

export const SIGNAL_LABEL: Record<string, string> = {
    triage: 'moved to investigating / resolved',
    ticket: 'ticket filed',
    quarantine: 'case quarantined',
    fixed: 'passed again on a new commit',
};

/** Minutes as "40 s" / "45 min" / "3.2 h" / "—". Under a minute shows
 *  seconds, so a real saving never reads "0 min". */
export function fmtMinutes(m: number | null | undefined): string {
    if (m === null || m === undefined) return '—';
    if (m > 0 && m < 1) return `${Math.max(1, Math.round(m * 60))} s`;
    if (m < 60) return `${Math.round(m)} min`;
    return `${(m / 60).toFixed(1)} h`;
}

/** The big number on a metric card. A metric with no inputs has no number:
 *  the card shows its `missing` text instead, never a zero. */
export function headline(m: ValueMetric): string | null {
    if (m.value === null) return null;
    switch (m.key) {
        case 'time_to_diagnosis':
            return fmtHours(m.value.median_hours);
        case 'analysis_accuracy':
            return fmtPct(m.value.percent);
        case 'targeted_time_saved':
            return fmtMinutes(m.value.saved_browser_minutes);
        case 'proposal_outcomes': {
            const d = m.value.case.decided + m.value.heal.decided;
            const ok = m.value.case.accepted + m.value.case.auto_applied + m.value.heal.accepted + m.value.heal.auto_applied;
            return d ? fmtPct((ok * 100) / d) : '—';
        }
    }
}

/** What the headline number is, in words. */
export function headlineCaption(m: ValueMetric): string {
    switch (m.key) {
        case 'time_to_diagnosis': return 'median';
        case 'analysis_accuracy': return 'of rated top hypotheses correct';
        case 'targeted_time_saved': return 'browser time saved (estimate)';
        case 'proposal_outcomes': return 'of decided proposals applied';
    }
}

export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** Clicking the verdict you already gave clears it; anything else sets it. */
export function nextVote(current: HypothesisVerdict | null, clicked: HypothesisVerdict): HypothesisVerdict | null {
    return current === clicked ? null : clicked;
}

/** Rows for one result, applying an optimistic vote change. */
export function applyVote(rows: HypothesisFeedbackRow[], index: number, verdict: HypothesisVerdict | null): HypothesisFeedbackRow[] {
    return rows.map((r) => {
        if (r.index !== index) return r;
        const out = { ...r };
        if (r.mine) out[r.mine] = Math.max(0, out[r.mine] - 1);
        if (verdict) out[verdict] += 1;
        out.mine = verdict;
        return out;
    });
}

import { describe, it, expect } from 'vitest';
import type { AccuracyMetric, DiagnosisMetric, HypothesisFeedbackRow, ProposalMetric, SavingsMetric } from '@/api/valueReport';
import { applyVote, fmtMinutes, headline, headlineCaption, nextVote } from './valueHelpers';

const base = { title: 't', kind: 'measured' as const, definition: 'd', data_source: 's', sample_size: 0, sample_label: 'x' };

describe('headline', () => {
    it('is null when the metric has no inputs (never zero)', () => {
        const m = { ...base, key: 'analysis_accuracy', value: null, missing: 'Not enough ratings: 2 rated, at least 5 needed.',
            min_sample: 5, rated: 2, split: 0 } as AccuracyMetric;
        expect(headline(m)).toBeNull();
    });
    it('formats each metric', () => {
        const d = { ...base, key: 'time_to_diagnosis', missing: null, clusters_considered: 3,
            value: { median_hours: 0.5, p90_hours: 3, diagnosed: 2, undiagnosed: 1, by_signal: { triage: 1, ticket: 1, quarantine: 0, fixed: 0 } } } as DiagnosisMetric;
        expect(headline(d)).toBe('30 min');
        expect(headlineCaption(d)).toBe('median');
        const s = { ...base, kind: 'estimated', key: 'targeted_time_saved', missing: null, formula: 'f', runs: [],
            measured_inputs: { targeted_runs: 1, estimated: 1, no_baseline: 0, not_estimable: 0 },
            value: { saved_browser_minutes: 90, saved_wall_minutes: null, wall_samples: 0, cases_skipped: 3, cases_ran: 1 } } as SavingsMetric;
        expect(headline(s)).toBe('1.5 h');
        const counts = { total: 2, pending: 0, accepted: 1, auto_applied: 0, rejected: 1, other: 0, decided: 2,
            accept_rate: 50, auto_apply_rate: 0, reject_rate: 50 };
        const p = { ...base, key: 'proposal_outcomes', missing: null, sub_definitions: { revert: '', repair_validation: '' },
            value: { case: counts, heal: { ...counts, accepted: 2, rejected: 0 },
                revert: { reverted: 0, kept: 0, not_measurable: 0, revert_rate: null },
                repair_validation: { validation_runs: 0, passed_matched: 0, passed_unmatched: 0, failed: 0, success_rate: null,
                    decisions: 0, decisions_validated: 0, validated_share: null } } } as ProposalMetric;
        expect(headline(p)).toBe('75.0%');
    });
});

describe('fmtMinutes', () => {
    it('handles missing, minutes and hours', () => {
        expect(fmtMinutes(null)).toBe('—');
        expect(fmtMinutes(12.4)).toBe('12 min');
        expect(fmtMinutes(150)).toBe('2.5 h');
    });
});

describe('votes', () => {
    const rows: HypothesisFeedbackRow[] = [
        { index: 0, hypothesis_key: 'a', correct: 1, incorrect: 0, mine: 'correct' },
        { index: 1, hypothesis_key: 'b', correct: 0, incorrect: 0, mine: null },
    ];
    it('toggles a repeated click off', () => {
        expect(nextVote('correct', 'correct')).toBeNull();
        expect(nextVote('correct', 'incorrect')).toBe('incorrect');
        expect(nextVote(null, 'incorrect')).toBe('incorrect');
    });
    it('moves my vote between the counts', () => {
        const changed = applyVote(rows, 0, 'incorrect');
        expect(changed[0]).toMatchObject({ correct: 0, incorrect: 1, mine: 'incorrect' });
        expect(changed[1]).toBe(rows[1]);
        expect(applyVote(rows, 0, null)[0]).toMatchObject({ correct: 0, incorrect: 0, mine: null });
    });
});

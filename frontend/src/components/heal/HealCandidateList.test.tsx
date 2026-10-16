// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { HealCandidate, HealProposal } from '@/api/proposals';
import HealCandidateList from './HealCandidateList';
import { candidatesOf, defaultPick, recheckLabel } from './healCandidates';

afterEach(cleanup);

const cands: HealCandidate[] = [
    { selector: '[data-testid="go"]', strategy: 'test-id', source: 'both', matches: 1, applicable: true, score: 0.91,
      breakdown: { stability: 1, similarity: 0.8, llm: 0.6, uniqueness: 1 } },
    { selector: 'text="Go"', strategy: 'text', source: 'deterministic', matches: 3, applicable: false, score: 0.4,
      breakdown: { stability: 0.5, similarity: 0.8, llm: 0, uniqueness: 0 } },
];

describe('HealCandidateList', () => {
    it('shows each candidate with strategy, match count and score, and lets a reviewer pick another', async () => {
        const onPick = vi.fn();
        render(<HealCandidateList candidates={cands} picked='[data-testid="go"]' onPick={onPick} legacy={false} editable />);
        expect(screen.getByText('Test id')).toBeTruthy();
        expect(screen.getByText('AI agrees')).toBeTruthy();
        expect(screen.getByText('1 match')).toBeTruthy();
        expect(screen.getByText('3 matches')).toBeTruthy();
        expect(screen.getByText('Score 91%')).toBeTruthy();
        await userEvent.click(screen.getByLabelText('Use text="Go"'));
        expect(onPick).toHaveBeenCalledWith('text="Go"');
    });

    it('offers a labelled uniqueness check and shows its result', async () => {
        const onRecheck = vi.fn();
        render(<HealCandidateList candidates={cands} picked='text="Go"' onPick={() => undefined} legacy={false} editable
            onRecheck={onRecheck}
            recheck={{ status: 'not_unique', matches: 3, selector: 'text="Go"', detail: 'Matches 3 elements.' }} />);
        await userEvent.click(screen.getByRole('button', { name: 'Check uniqueness' }));
        expect(onRecheck).toHaveBeenCalled();
        expect(screen.getByRole('status').textContent).toContain('3 matches in snapshot');
    });

    it('is read-only once the proposal is decided', () => {
        render(<HealCandidateList candidates={cands} picked='[data-testid="go"]' onPick={() => undefined} legacy={false} editable={false} onRecheck={() => undefined} />);
        expect((screen.getByLabelText('Use text="Go"') as HTMLInputElement).disabled).toBe(true);
        expect(screen.queryByRole('button', { name: 'Check uniqueness' })).toBeNull();
    });
});

describe('heal helpers', () => {
    const base = {
        id: 1, test_case_id: 2, step_id: 's', old_selector: '#a', new_selector: '#b', intent: null,
        confidence: 0.5, rationale: null, source_run_id: null, status: 'pending', created_at: '',
    } as HealProposal;

    it('falls back to the single selector for a pre-candidate proposal', () => {
        const [only] = candidatesOf(base);
        expect(only.selector).toBe('#b');
        expect(defaultPick(base)).toBe('#b');
        expect(defaultPick({ ...base, chosen_selector: '#c' })).toBe('#c');
    });

    it('labels recheck outcomes', () => {
        expect(recheckLabel({ status: 'unique', matches: 1, selector: '', detail: '' })).toBe('Unique in snapshot');
        expect(recheckLabel({ status: 'unavailable', matches: null, selector: '', detail: '' })).toBe('Not rechecked');
    });
});

// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AccuracyMetric, SavingsMetric } from '@/api/valueReport';

vi.mock('@/api/valueReport', () => ({
    hypothesisFeedbackApi: {
        forRun: vi.fn(),
        rate: vi.fn().mockResolvedValue({}),
        clear: vi.fn().mockResolvedValue({}),
    },
}));
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

import { hypothesisFeedbackApi } from '@/api/valueReport';
import { ValueMetricCard } from './ValueMetricCard';
import { HypothesisRating } from './HypothesisRating';

afterEach(() => { cleanup(); vi.clearAllMocks(); });

const wrap = (ui: React.ReactNode) => render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
);

describe('ValueMetricCard', () => {
    it('shows the missing text, not a zero, and keeps the definition visible', () => {
        const m: AccuracyMetric = {
            key: 'analysis_accuracy', title: 'AI failure-analysis accuracy', kind: 'measured',
            definition: 'Share of rated top-ranked hypotheses.', data_source: 'Thumbs on the run page.',
            sample_size: 2, sample_label: 'rated top hypotheses (reviewed sample, not all runs)',
            value: null, missing: 'Not enough ratings: 2 rated, at least 5 needed.', min_sample: 5, rated: 2, split: 0,
        };
        wrap(<ValueMetricCard m={m} />);
        expect(screen.getByText('Measured')).toBeTruthy();
        expect(screen.getByText(/Not enough ratings/)).toBeTruthy();
        expect(screen.queryByText('0%')).toBeNull();
        expect(screen.getByText(/reviewed sample, not all runs/)).toBeTruthy();
        expect(screen.getByText(/Share of rated top-ranked/)).toBeTruthy();
    });

    it('labels an estimate and prints its formula', () => {
        const m: SavingsMetric = {
            key: 'targeted_time_saved', title: 'CI time saved by targeted runs', kind: 'estimated',
            definition: 'd', data_source: 's', sample_size: 1, sample_label: 'targeted runs with a comparable full run',
            missing: null, formula: 'saved = baseline_duration × (1 − cases_ran / cases_total)', runs: [],
            measured_inputs: { targeted_runs: 2, estimated: 1, no_baseline: 1, not_estimable: 0 },
            value: { saved_browser_minutes: 6, saved_wall_minutes: 4.5, wall_samples: 1, cases_skipped: 3, cases_ran: 1 },
        };
        wrap(<ValueMetricCard m={m} />);
        expect(screen.getByText('Estimate')).toBeTruthy();
        expect(screen.getByText('6 min')).toBeTruthy();
        expect(screen.getByText(/baseline_duration/)).toBeTruthy();
        expect(screen.getByText(/1 without a comparable full run/)).toBeTruthy();
    });
});

describe('HypothesisRating', () => {
    const feedback = (canRate: boolean, mine: 'correct' | 'incorrect' | null = null) => ({
        run_id: 7, can_rate: canRate, is_validation: false,
        results: { '5': [{ index: 0, hypothesis_key: 'abcdef0123456789', correct: mine === 'correct' ? 1 : 0, incorrect: 0, mine }] },
    });

    it('sends the shown hypothesis key and toggles off on a second click', async () => {
        vi.mocked(hypothesisFeedbackApi.forRun).mockResolvedValue(feedback(true));
        wrap(<HypothesisRating runId={7} resultId={5} index={0} />);
        const up = await screen.findByRole('button', { name: /Correct/ });
        await userEvent.click(up);
        await waitFor(() => expect(hypothesisFeedbackApi.rate).toHaveBeenCalledWith(
            7, 5, { index: 0, hypothesis_key: 'abcdef0123456789', verdict: 'correct' }));

        cleanup();
        vi.mocked(hypothesisFeedbackApi.forRun).mockResolvedValue(feedback(true, 'correct'));
        wrap(<HypothesisRating runId={7} resultId={5} index={0} />);
        const pressed = await screen.findByRole('button', { name: /Correct \(1\)/ });
        expect(pressed.getAttribute('aria-pressed')).toBe('true');
        await userEvent.click(pressed);
        await waitFor(() => expect(hypothesisFeedbackApi.clear).toHaveBeenCalledWith(7, 5, 0, 'abcdef0123456789'));
    });

    it('is read-only for people who cannot rate, and hidden when nobody has', async () => {
        vi.mocked(hypothesisFeedbackApi.forRun).mockResolvedValue(feedback(false, null));
        const { container } = wrap(<HypothesisRating runId={7} resultId={5} index={0} />);
        await waitFor(() => expect(hypothesisFeedbackApi.forRun).toHaveBeenCalled());
        expect(container.querySelector('button')).toBeNull();

        cleanup();
        const withVotes = feedback(false, null);
        withVotes.results['5'][0].correct = 2;
        vi.mocked(hypothesisFeedbackApi.forRun).mockResolvedValue(withVotes);
        wrap(<HypothesisRating runId={7} resultId={5} index={0} />);
        const btn = await screen.findByRole('button', { name: /Correct \(2\)/ });
        expect((btn as HTMLButtonElement).disabled).toBe(true);
    });
});

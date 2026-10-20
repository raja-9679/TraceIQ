import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import type { ValueMetric } from '@/api/valueReport';
import { Pill } from '@/components/redesign/primitives';
import { fmtHours, fmtPct } from '@/components/reports/chartMath';
import { KIND_LABEL, SIGNAL_LABEL, fmtMinutes, headline, headlineCaption, plural } from './valueHelpers';

// One Value report metric: a Measured / Estimate badge, the number (or the
// reason there is none), its sample size, and — always visible, not hidden
// behind a toggle — what it means and where it comes from.

function Facts({ children }: { children: ReactNode }) {
    return <ul className="flex flex-col gap-0.5 text-xs leading-relaxed text-ink-soft">{children}</ul>;
}

function Detail({ m }: { m: ValueMetric }) {
    if (m.value === null) return null;
    switch (m.key) {
        case 'time_to_diagnosis': {
            const v = m.value;
            return (
                <Facts>
                    <li>90th percentile: <span className="font-semibold text-ink">{fmtHours(v.p90_hours)}</span></li>
                    <li>{plural(v.undiagnosed, 'cluster')} not diagnosed yet (left out of the median)</li>
                    {Object.entries(v.by_signal).filter(([, n]) => n > 0).map(([s, n]) => (
                        <li key={s}>First signal {SIGNAL_LABEL[s] ?? s}: {n}</li>
                    ))}
                </Facts>
            );
        }
        case 'analysis_accuracy': {
            const v = m.value;
            return (
                <Facts>
                    <li>{v.correct} correct, {v.incorrect} incorrect{v.split ? `, ${v.split} split (left out)` : ''} · {plural(v.votes, 'vote')}</li>
                    {Object.entries(v.by_source).map(([src, c]) => (
                        <li key={src}>{src === 'llm' ? 'AI model' : 'Heuristic'}: {c.correct} correct, {c.incorrect} incorrect</li>
                    ))}
                </Facts>
            );
        }
        case 'targeted_time_saved': {
            const v = m.value;
            const i = m.measured_inputs;
            return (
                <Facts>
                    <li>Wall clock (estimate): <span className="font-semibold text-ink">{fmtMinutes(v.saved_wall_minutes)}</span>
                        {v.saved_wall_minutes !== null && ` from ${plural(v.wall_samples, 'run')}`}</li>
                    <li>Measured inputs: {plural(i.targeted_runs, 'targeted run')}, {v.cases_ran} cases ran, {v.cases_skipped} skipped
                        {i.no_baseline ? ` · ${i.no_baseline} without a comparable full run` : ''}
                        {i.not_estimable ? ` · ${i.not_estimable} without a suite size` : ''}</li>
                </Facts>
            );
        }
        case 'proposal_outcomes': {
            const v = m.value;
            const row = (label: string, c: typeof v.case) => (
                <li>{label}: {c.total} created · accepted {fmtPct(c.accept_rate)}, auto-applied {fmtPct(c.auto_apply_rate)},
                    rejected {fmtPct(c.reject_rate)} of {c.decided} decided · {c.pending} pending</li>
            );
            return (
                <Facts>
                    {row('Case proposals', v.case)}
                    {row('Heal proposals', v.heal)}
                    <li>Reverted later: {fmtPct(v.revert.revert_rate)} ({v.revert.reverted} of {v.revert.reverted + v.revert.kept} measurable;
                        {' '}{v.revert.not_measurable} not measurable)</li>
                    <li>Repair validation: {fmtPct(v.repair_validation.success_rate)} passed under the failing run's conditions
                        ({plural(v.repair_validation.validation_runs, 'validation run')}) · {fmtPct(v.repair_validation.validated_share)} of
                        {' '}{v.repair_validation.decisions} accepted repairs were validated first</li>
                </Facts>
            );
        }
    }
}

export function ValueMetricCard({ m }: { m: ValueMetric }) {
    const big = headline(m);
    return (
        <section aria-label={m.title} className="flex min-w-0 flex-col gap-3 rounded-3xl border border-border bg-white p-5">
            <span className="flex items-center justify-between gap-2">
                <h2 className="text-sm font-semibold text-ink">{m.title}</h2>
                <Pill tone={m.kind === 'measured' ? 'mint' : 'butter'}>{KIND_LABEL[m.kind]}</Pill>
            </span>
            {big !== null ? (
                <span className="flex flex-wrap items-baseline gap-2">
                    <span className="text-[28px] font-bold leading-tight tracking-tight tabular-nums text-ink">{big}</span>
                    <span className="text-xs text-ink-soft">{headlineCaption(m)}</span>
                </span>
            ) : (
                <p className="rounded-2xl bg-secondary px-3 py-2 text-sm text-ink">{m.missing}</p>
            )}
            <p className="text-xs text-ink-soft">
                Sample: <span className="font-semibold text-ink">{m.sample_size}</span> {m.sample_label}
            </p>
            <Detail m={m} />
            <div className="mt-auto flex flex-col gap-1 border-t border-[#F0EEE8] pt-3 text-xs leading-relaxed text-ink-soft">
                <p><span className="font-semibold text-ink">Definition.</span> {m.definition}</p>
                {m.key === 'targeted_time_saved' && (
                    <p><span className="font-semibold text-ink">Formula.</span> <span className="font-mono">{m.formula}</span></p>
                )}
                {m.key === 'proposal_outcomes' && (
                    <>
                        <p><span className="font-semibold text-ink">Revert.</span> {m.sub_definitions.revert}</p>
                        <p><span className="font-semibold text-ink">Repair validation.</span> {m.sub_definitions.repair_validation}</p>
                    </>
                )}
                <p><span className="font-semibold text-ink">Data source.</span> {m.data_source}</p>
            </div>
        </section>
    );
}

export function ValueLinks({ links }: { links: { label: string; path: string; reason: string }[] }) {
    return (
        <section aria-label="Related reports" className="rounded-3xl border border-border bg-white p-5">
            <h2 className="text-sm font-semibold text-ink">Reported elsewhere</h2>
            <ul className="mt-2 flex flex-col gap-1.5 text-sm">
                {links.map((l) => (
                    <li key={l.path}>
                        <Link to={l.path} className="font-semibold text-ink underline-offset-2 hover:underline">{l.label}</Link>
                        <span className="text-ink-soft"> — {l.reason}</span>
                    </li>
                ))}
            </ul>
        </section>
    );
}

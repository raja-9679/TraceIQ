import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { BudgetPageSummary, BudgetSample, frontendQualityApi } from '@/api/frontendQuality';
import { DetailCard, EmptyState, Eyebrow, ListGroup, ListRow, Pill, SplitView } from '@/components/redesign/primitives';
import { formatMetric } from './matrixHelpers';

const TREND_METRICS = ['lcp_ms', 'cls', 'inp_ms', 'ttfb_ms', 'page_weight_kb', 'request_count'] as const;

// A metric card is ~100px wide; more bars than this shrank each one to
// nothing (59 fixed-width spans squeezed into the card).
const MAX_BARS = 16;

/** Bars oldest → newest (the latest MAX_BARS samples); red where that sample
 *  was over its budget for the metric. */
function MetricBars({ samples: all, metric }: { samples: BudgetSample[]; metric: string }) {
    const samples = all.slice(-MAX_BARS);
    const values = samples.map(s => s.metrics?.[metric]);
    const max = Math.max(...values.filter((v): v is number => typeof v === 'number'), 0);
    if (!max) return <span className="text-xs text-ink-soft">no data</span>;
    return (
        <div className="mt-2 flex h-8 items-end gap-px" role="img"
            aria-label={`${metric} over the last ${samples.length} runs, latest ${formatMetric(metric, values[values.length - 1])}`}>
            {samples.map((s, i) => {
                const v = values[i];
                const over = s.checks?.some(c => c.metric === metric && !c.passed);
                return (
                    <span key={s.id} title={`run #${s.test_run_id}: ${formatMetric(metric, v)}`}
                        className={`block min-w-[2px] max-w-1.5 flex-1 rounded-sm ${over ? 'bg-[#E0603A]' : 'bg-pastel-ink-lavender/40'}`}
                        style={{ height: `${typeof v === 'number' ? Math.max(6, (v / max) * 100) : 6}%` }} />
                );
            })}
        </div>
    );
}

function Trend({ projectId, page }: { projectId: number; page: BudgetPageSummary }) {
    const trend = useQuery({
        queryKey: ['budget-trend', projectId, page.page_key, page.variant],
        queryFn: () => frontendQualityApi.budgetTrend(projectId, page.page_key, page.variant),
    });
    const samples = trend.data || [];
    const latest = page.latest;
    return (
        <div className="space-y-4">
            <div className="grid grid-cols-[repeat(auto-fit,minmax(120px,1fr))] gap-3">
                {TREND_METRICS.map(m => (
                    <div key={m} className="rounded-2xl border border-border p-3">
                        <p className="text-[11px] font-semibold uppercase tracking-[0.04em] text-ink-soft">{m.replace(/_/g, ' ')}</p>
                        <p className="text-lg font-bold tabular-nums text-ink">{formatMetric(m, latest.metrics?.[m])}</p>
                        <MetricBars samples={samples} metric={m} />
                    </div>
                ))}
            </div>
            <div className="grid gap-4 md:grid-cols-2 [&>div]:rounded-2xl [&>div]:bg-background [&>div]:p-4">
                <div>
                    <h4 className="mb-2 text-xs font-semibold uppercase tracking-[0.04em] text-ink-soft">Budgets (latest run)</h4>
                    <ul className="text-sm space-y-1">
                        {(latest.checks || []).map(c => (
                            <li key={c.metric} className={c.passed ? 'text-ink' : 'font-semibold text-pastel-ink-peach'}>
                                {c.passed ? 'within' : 'over'}: {c.metric} {formatMetric(c.metric, c.value)} / budget {formatMetric(c.metric, c.budget)}
                            </li>
                        ))}
                        {!latest.checks?.length && <li className="text-ink-soft">No thresholds set on this step.</li>}
                    </ul>
                </div>
                <div>
                    <h4 className="mb-2 text-xs font-semibold uppercase tracking-[0.04em] text-ink-soft">SEO checks (latest run)</h4>
                    <ul className="text-sm space-y-1">
                        {(latest.seo || []).map(c => (
                            <li key={c.check} className={c.passed ? 'text-ink' : c.severity === 'error' ? 'text-pastel-ink-peach' : 'text-pastel-ink-butter'}>
                                {c.passed ? 'ok' : c.severity}: {c.check.replace(/_/g, ' ')} — {c.detail}
                            </li>
                        ))}
                    </ul>
                </div>
            </div>
            <p className="text-xs text-ink-soft">
                Latest from <Link className="font-semibold text-ink underline underline-offset-2" to={`/runs/${latest.test_run_id}`}>run #{latest.test_run_id}</Link>
                {latest.test_name ? ` (${latest.test_name})` : ''}.
            </p>
        </div>
    );
}

export default function BudgetsPanel({ projectId }: { projectId: number }) {
    const summary = useQuery({ queryKey: ['budget-summary', projectId], queryFn: () => frontendQualityApi.budgetSummary(projectId) });
    const [selected, setSelected] = useState<string | null>(null);
    const pages = summary.data || [];
    const key = (p: BudgetPageSummary) => `${p.page_key}|${p.variant ?? ''}`;
    const current = pages.find(p => key(p) === selected) || pages[0];
    return (
        <div className="flex flex-col gap-5">
            <p className="rounded-3xl border border-border bg-white px-5 py-4 text-sm leading-relaxed text-ink-soft">
                Add a <code>check-page-budget</code> step to a case to measure Core Web Vitals, page weight and SEO checks on that page
                every run. Pages over budget in the last 30 days are listed first.
            </p>
            {summary.isLoading && <div className="h-40 animate-pulse rounded-3xl bg-white/70" aria-busy="true" aria-label="Loading" />}
            {pages.length === 0 && !summary.isLoading && <EmptyState title="No page-budget samples yet">They appear after a run that includes a check-page-budget step.</EmptyState>}
            {pages.length > 0 && (
                <SplitView
                    list={
                        <ListGroup title="Pages" count={pages.length}>
                            {pages.map(p => (
                                <ListRow
                                    key={key(p)}
                                    selected={!!current && key(current) === key(p)}
                                    onSelect={() => setSelected(key(p))}
                                    title={<span className="break-all">{p.page_key}</span>}
                                    meta={`${p.variant || 'default'} · ${p.samples} sample(s)`}
                                    trailing={p.failed > 0 ? <Pill tone="peach">{p.failed} over budget</Pill> : undefined}
                                />
                            ))}
                        </ListGroup>
                    }
                    detail={current && (
                        <DetailCard label={`Page budget for ${current.page_key}`}>
                            <div className="flex flex-col gap-1.5">
                                <Eyebrow>{current.variant || 'default'} · {current.samples} sample(s)</Eyebrow>
                                <h2 className="break-all text-[22px] font-bold leading-tight tracking-[-0.02em] text-ink">{current.page_key}</h2>
                            </div>
                            <Trend projectId={projectId} page={current} />
                        </DetailCard>
                    )}
                />
            )}
        </div>
    );
}

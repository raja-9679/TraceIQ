import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { BudgetPageSummary, BudgetSample, frontendQualityApi } from '@/api/frontendQuality';
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
    if (!max) return <span className="text-xs text-slate-300">no data</span>;
    return (
        <div className="mt-2 flex h-8 items-end gap-px" role="img"
            aria-label={`${metric} over the last ${samples.length} runs, latest ${formatMetric(metric, values[values.length - 1])}`}>
            {samples.map((s, i) => {
                const v = values[i];
                const over = s.checks?.some(c => c.metric === metric && !c.passed);
                return (
                    <span key={s.id} title={`run #${s.test_run_id}: ${formatMetric(metric, v)}`}
                        className={`block min-w-[2px] max-w-1.5 flex-1 rounded-sm ${over ? 'bg-rose-400' : 'bg-indigo-300'}`}
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
            <div className="grid sm:grid-cols-3 lg:grid-cols-6 gap-3">
                {TREND_METRICS.map(m => (
                    <div key={m} className="border border-slate-200 rounded-xl p-3">
                        <p className="text-[10px] uppercase tracking-wide text-slate-400 font-bold">{m.replace(/_/g, ' ')}</p>
                        <p className="text-lg font-bold text-slate-800">{formatMetric(m, latest.metrics?.[m])}</p>
                        <MetricBars samples={samples} metric={m} />
                    </div>
                ))}
            </div>
            <div className="grid md:grid-cols-2 gap-4">
                <div>
                    <h4 className="text-sm font-semibold text-slate-700 mb-2">Budgets (latest run)</h4>
                    <ul className="text-sm space-y-1">
                        {(latest.checks || []).map(c => (
                            <li key={c.metric} className={c.passed ? 'text-slate-600' : 'text-rose-600 font-semibold'}>
                                {c.passed ? 'within' : 'over'}: {c.metric} {formatMetric(c.metric, c.value)} / budget {formatMetric(c.metric, c.budget)}
                            </li>
                        ))}
                        {!latest.checks?.length && <li className="text-slate-400">No thresholds set on this step.</li>}
                    </ul>
                </div>
                <div>
                    <h4 className="text-sm font-semibold text-slate-700 mb-2">SEO checks (latest run)</h4>
                    <ul className="text-sm space-y-1">
                        {(latest.seo || []).map(c => (
                            <li key={c.check} className={c.passed ? 'text-slate-600' : c.severity === 'error' ? 'text-rose-600' : 'text-amber-600'}>
                                {c.passed ? 'ok' : c.severity}: {c.check.replace(/_/g, ' ')} — {c.detail}
                            </li>
                        ))}
                    </ul>
                </div>
            </div>
            <p className="text-xs text-slate-500">
                Latest from <Link className="underline" to={`/runs/${latest.test_run_id}`}>run #{latest.test_run_id}</Link>
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
        <div className="space-y-4">
            <p className="text-sm text-slate-500 max-w-3xl">
                Add a <code>check-page-budget</code> step to a case to measure Core Web Vitals, page weight and SEO checks on that page
                every run. Pages over budget in the last 30 days are listed first.
            </p>
            {summary.isLoading && <p className="text-sm text-slate-500">Loading…</p>}
            {pages.length === 0 && !summary.isLoading && <p className="text-sm text-slate-500">No page-budget samples yet.</p>}
            {pages.length > 0 && (
                <div className="grid lg:grid-cols-[minmax(16rem,22rem)_1fr] gap-4">
                    <ul className="bg-white border border-slate-200 rounded-2xl divide-y divide-slate-100 self-start">
                        {pages.map(p => (
                            <li key={key(p)}>
                                <button type="button" onClick={() => setSelected(key(p))}
                                    className={`w-full text-left px-4 py-3 text-sm ${current && key(current) === key(p) ? 'bg-indigo-50' : 'hover:bg-slate-50'}`}>
                                    <span className="block break-all font-medium text-slate-800">{p.page_key}</span>
                                    <span className="text-xs text-slate-500">{p.variant || 'default'} · {p.samples} sample(s)</span>
                                    {p.failed > 0 && <span className="ml-2 text-xs font-semibold text-rose-600">{p.failed} over budget</span>}
                                </button>
                            </li>
                        ))}
                    </ul>
                    {current && (
                        <div className="bg-white border border-slate-200 rounded-2xl p-5">
                            <Trend projectId={projectId} page={current} />
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

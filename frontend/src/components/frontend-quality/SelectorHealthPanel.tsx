import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Copy } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { frontendQualityApi } from '@/api/frontendQuality';
import { DetailCard, EmptyState, Eyebrow, ListGroup, ListRow, SplitView } from '@/components/redesign/primitives';
import { WindowSelect } from '@/components/reports/ReportControls';

export default function SelectorHealthPanel({ projectId }: { projectId: number }) {
    const [days, setDays] = useState(90);
    const [selected, setSelected] = useState<string | null>(null);
    const report = useQuery({
        queryKey: ['selector-health', projectId, days],
        queryFn: () => frontendQualityApi.selectorHealth(projectId, days),
    });
    const entries = report.data?.entries || [];
    const keyOf = (e: { test_case_id: number; selector: string }) => `${e.test_case_id}|${e.selector}`;
    const e = entries.find(x => keyOf(x) === selected) || entries[0];
    const peak = e ? Math.max(1, ...e.weeks.map(w => w.heals + w.failures)) : 1;

    return (
        <div className="flex flex-col gap-5">
            <div className="flex flex-wrap items-center gap-4 rounded-3xl border border-border bg-white px-5 py-4">
                <div className="flex min-w-0 flex-1 basis-[420px] flex-col gap-1">
                    <p className="text-sm leading-relaxed text-ink-soft">
                        Selectors that keep needing repair (heal proposals, runtime heals) or keep failing, worst first. Suggested
                        locators come from the DOM captured at the failure and are never applied for you — edit the case to adopt one.
                    </p>
                    {report.data && (
                        <p className="text-xs font-semibold text-ink">{report.data.totals.selectors} selector(s), {report.data.totals.heals} heal(s), {report.data.totals.failures} failure(s)</p>
                    )}
                </div>
                <WindowSelect id="sh-days" value={days} options={[30, 90, 180, 365]} onChange={setDays} />
            </div>
            {report.isLoading && <div className="h-40 animate-pulse rounded-3xl bg-white/70" aria-busy="true" aria-label="Loading" />}
            {entries.length === 0 && !report.isLoading && <EmptyState title="No selector trouble in this window" />}
            {entries.length > 0 && e && (
                <SplitView
                    list={
                        <ListGroup title="Selectors, worst first" count={entries.length}>
                            {entries.map(x => (
                                <ListRow
                                    key={keyOf(x)}
                                    selected={keyOf(x) === keyOf(e)}
                                    onSelect={() => setSelected(keyOf(x))}
                                    title={<code className="break-all text-[13px]">{x.selector}</code>}
                                    meta={`${x.case_name || `case #${x.test_case_id}`} · ${x.failure_count} failed · ${x.heal_count} heal proposals`}
                                />
                            ))}
                        </ListGroup>
                    }
                    detail={
                        <DetailCard label={`Selector ${e.selector}`}>
                            <div className="flex flex-col gap-1.5">
                                <code className="break-all text-lg font-semibold text-ink">{e.selector}</code>
                                <p className="text-sm text-ink-soft">
                                    {e.suite_id ? (
                                        <Link className="font-semibold text-ink underline underline-offset-2" to={`/suites/${e.suite_id}/cases/${e.test_case_id}/edit`}>{e.case_name || `case #${e.test_case_id}`}</Link>
                                    ) : (e.case_name || `case #${e.test_case_id}`)}
                                    {e.step_ids.length ? ` · step ${e.step_ids.join(', ')}` : ''}
                                    {e.last_seen ? ` · last ${new Date(e.last_seen).toLocaleDateString()}` : ''}
                                </p>
                            </div>
                            <div className="grid grid-cols-3 gap-3">
                                {[
                                    { label: 'Failed', value: e.failure_count, tone: 'text-pastel-ink-peach' },
                                    { label: 'Heal proposals', value: e.heal_count, tone: 'text-pastel-ink-butter' },
                                    { label: 'Runtime-healed', value: e.runtime_healed_count, tone: 'text-pastel-ink-lavender' },
                                ].map(t => (
                                    <div key={t.label} className="rounded-2xl border border-border p-3">
                                        <p className="text-[11px] font-semibold uppercase tracking-[0.04em] text-ink-soft">{t.label}</p>
                                        <p className={`text-2xl font-bold tabular-nums ${t.tone}`}>{t.value}</p>
                                    </div>
                                ))}
                            </div>
                            <div className="flex flex-col gap-2">
                                <Eyebrow>Heals and failures per week</Eyebrow>
                                <div className="flex h-10 items-end gap-1" role="img" aria-label={`Weekly heals and failures: ${e.weeks.map(w => `${w.week} ${w.heals + w.failures}`).join(', ')}`}>
                                    {e.weeks.map(w => (
                                        <span key={w.week} title={`week of ${w.week}: ${w.failures} failed, ${w.heals} heals`}
                                            className="inline-block w-3 rounded-sm bg-[#E9B949]" style={{ height: `${Math.max(10, ((w.heals + w.failures) / peak) * 100)}%` }} />
                                    ))}
                                </div>
                            </div>
                            {e.fragility.length > 0 && (
                                <div className="flex flex-col gap-2 rounded-2xl bg-background p-4">
                                    <Eyebrow>Why it is fragile</Eyebrow>
                                    <ul className="ml-5 list-disc text-sm text-ink">
                                        {e.fragility.map(r => <li key={r}>{r}</li>)}
                                    </ul>
                                </div>
                            )}
                            {e.suggestions.length > 0 && (
                                <div className="flex flex-col gap-2">
                                    <Eyebrow>Sturdier locators</Eyebrow>
                                    <ul className="divide-y divide-[#F0EEE8] overflow-hidden rounded-2xl border border-border">
                                        {e.suggestions.map(s => (
                                            <li key={s.locator} className="flex flex-wrap items-center gap-2 px-3 py-2 text-xs">
                                                <code className="break-all rounded-lg bg-background px-2 py-1 text-ink">{s.locator}</code>
                                                <span className="text-ink-soft">{s.reason}</span>
                                                <Button size="sm" variant="outline" className="ml-auto" aria-label={`Copy locator ${s.locator}`}
                                                    onClick={() => { navigator.clipboard.writeText(s.locator); toast.success('Locator copied'); }}>
                                                    <Copy className="h-3.5 w-3.5" /> Copy
                                                </Button>
                                            </li>
                                        ))}
                                    </ul>
                                </div>
                            )}
                            {e.recent_run_ids.length > 0 && (
                                <p className="text-xs text-ink-soft">Recent runs: {e.recent_run_ids.map((id, i) => (
                                    <span key={id}>{i ? ', ' : ''}<Link className="font-semibold text-ink underline underline-offset-2" to={`/runs/${id}`}>#{id}</Link></span>
                                ))}</p>
                            )}
                        </DetailCard>
                    }
                />
            )}
        </div>
    );
}

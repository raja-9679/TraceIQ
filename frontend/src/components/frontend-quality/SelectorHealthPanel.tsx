import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Copy } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { frontendQualityApi } from '@/api/frontendQuality';

export default function SelectorHealthPanel({ projectId }: { projectId: number }) {
    const [days, setDays] = useState(90);
    const report = useQuery({
        queryKey: ['selector-health', projectId, days],
        queryFn: () => frontendQualityApi.selectorHealth(projectId, days),
    });
    const entries = report.data?.entries || [];
    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
                <p className="text-sm text-slate-500 max-w-3xl">
                    Selectors that keep needing repair (heal proposals, runtime heals) or keep failing, worst first. Suggested
                    locators come from the DOM captured at the failure and are never applied for you — edit the case to adopt one.
                </p>
                <label htmlFor="sh-days" className="ml-auto text-sm text-slate-600">Window</label>
                <select id="sh-days" className="h-9 rounded-lg border border-slate-200 text-sm px-2" value={days}
                    onChange={(e) => setDays(Number(e.target.value))}>
                    {[30, 90, 180, 365].map(d => <option key={d} value={d}>Last {d} days</option>)}
                </select>
            </div>
            {report.data && (
                <p className="text-xs text-slate-500">{report.data.totals.selectors} selector(s), {report.data.totals.heals} heal(s), {report.data.totals.failures} failure(s)</p>
            )}
            {report.isLoading && <p className="text-sm text-slate-500">Loading…</p>}
            {entries.length === 0 && !report.isLoading && <p className="text-sm text-slate-500">No selector trouble in this window.</p>}
            {entries.map(e => {
                const peak = Math.max(1, ...e.weeks.map(w => w.heals + w.failures));
                return (
                    <section key={`${e.test_case_id}|${e.selector}`} className="bg-white border border-slate-200 rounded-2xl p-4 space-y-3">
                        <div className="flex flex-wrap gap-3 items-start">
                            <div className="min-w-0">
                                <code className="text-sm break-all text-slate-800">{e.selector}</code>
                                <p className="text-xs text-slate-500">
                                    {e.suite_id ? (
                                        <Link className="underline" to={`/suites/${e.suite_id}/cases/${e.test_case_id}/edit`}>{e.case_name || `case #${e.test_case_id}`}</Link>
                                    ) : (e.case_name || `case #${e.test_case_id}`)}
                                    {e.step_ids.length ? ` · step ${e.step_ids.join(', ')}` : ''}
                                    {e.last_seen ? ` · last ${new Date(e.last_seen).toLocaleDateString()}` : ''}
                                </p>
                            </div>
                            <div className="ml-auto flex gap-3 text-xs">
                                <span><strong className="text-rose-600">{e.failure_count}</strong> failed</span>
                                <span><strong className="text-amber-600">{e.heal_count}</strong> heal proposals</span>
                                <span><strong className="text-indigo-600">{e.runtime_healed_count}</strong> runtime-healed</span>
                            </div>
                        </div>
                        <div className="flex items-end gap-1 h-8" role="img" aria-label={`Weekly heals and failures: ${e.weeks.map(w => `${w.week} ${w.heals + w.failures}`).join(', ')}`}>
                            {e.weeks.map(w => (
                                <span key={w.week} title={`week of ${w.week}: ${w.failures} failed, ${w.heals} heals`}
                                    className="inline-block w-3 rounded-sm bg-amber-300" style={{ height: `${Math.max(10, ((w.heals + w.failures) / peak) * 100)}%` }} />
                            ))}
                        </div>
                        {e.fragility.length > 0 && (
                            <ul className="text-xs text-slate-600 list-disc ml-5">
                                {e.fragility.map(r => <li key={r}>{r}</li>)}
                            </ul>
                        )}
                        {e.suggestions.length > 0 && (
                            <div className="space-y-1">
                                <p className="text-xs font-semibold text-slate-700">Sturdier locators</p>
                                {e.suggestions.map(s => (
                                    <div key={s.locator} className="flex items-center gap-2 text-xs">
                                        <code className="bg-slate-50 border border-slate-200 rounded px-2 py-1 break-all">{s.locator}</code>
                                        <span className="text-slate-500">{s.reason}</span>
                                        <Button size="sm" variant="ghost" className="h-7 ml-auto" aria-label={`Copy locator ${s.locator}`}
                                            onClick={() => { navigator.clipboard.writeText(s.locator); toast.success('Locator copied'); }}>
                                            <Copy className="w-3.5 h-3.5 mr-1" /> Copy
                                        </Button>
                                    </div>
                                ))}
                            </div>
                        )}
                        {e.recent_run_ids.length > 0 && (
                            <p className="text-xs text-slate-500">Recent runs: {e.recent_run_ids.map((id, i) => (
                                <span key={id}>{i ? ', ' : ''}<Link className="underline" to={`/runs/${id}`}>#{id}</Link></span>
                            ))}</p>
                        )}
                    </section>
                );
            })}
        </div>
    );
}

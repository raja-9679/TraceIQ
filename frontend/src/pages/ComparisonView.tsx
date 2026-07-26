import { useQuery } from '@tanstack/react-query';
import { useParams, Link } from 'react-router-dom';
import {
    ArrowLeft, ArrowDownRight, ArrowUpRight, Minus, CheckCircle, XCircle,
    Activity, Globe, GitCompareArrows, ExternalLink,
} from 'lucide-react';
import { getRun, getComparison, ComparisonDelta } from '@/lib/api';

const ACTIVE_STATUSES = ['pending', 'running'];

function StatusChip({ status }: { status: string | null }) {
    if (!status) {
        return <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-bold uppercase bg-slate-100 text-slate-400 border border-slate-200">absent</span>;
    }
    const passed = status === 'passed';
    return (
        <span className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-bold uppercase border ${passed
            ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
            : 'bg-rose-50 text-rose-700 border-rose-200'}`}>
            {passed ? <CheckCircle size={11} /> : <XCircle size={11} />}
            {status}
        </span>
    );
}

function DurationDelta({ delta }: { delta: ComparisonDelta }) {
    const b = delta.baseline_duration_ms;
    const c = delta.candidate_duration_ms;
    if (b == null || c == null) return <span className="text-slate-400">—</span>;
    const diff = c - b;
    const pct = b > 0 ? Math.round((diff / b) * 100) : 0;
    const slower = diff > 0;
    const negligible = Math.abs(pct) < 5;
    return (
        <span className={`inline-flex items-center gap-1 text-sm font-medium ${negligible ? 'text-slate-500' : slower ? 'text-amber-600' : 'text-emerald-600'}`}>
            {negligible ? <Minus size={13} /> : slower ? <ArrowUpRight size={13} /> : <ArrowDownRight size={13} />}
            {(c / 1000).toFixed(2)}s ({pct > 0 ? '+' : ''}{pct}%)
        </span>
    );
}

export default function ComparisonView() {
    const { runId: idParam } = useParams<{ runId: string }>();
    const runId = parseInt(idParam || '0');

    // Poll the candidate run until it finishes, then fetch the diff.
    const { data: run } = useQuery({
        queryKey: ['run', runId],
        queryFn: () => getRun(runId),
        enabled: runId > 0,
        refetchInterval: (query) =>
            ACTIVE_STATUSES.includes(query.state.data?.status ?? 'pending') ? 3000 : false,
    });
    const finished = !!run && !ACTIVE_STATUSES.includes(run.status);

    const { data: report, isLoading: reportLoading, error } = useQuery({
        queryKey: ['comparison', runId],
        queryFn: () => getComparison(runId),
        enabled: runId > 0 && finished,
    });

    if (!runId) return <div className="p-4">Invalid run ID</div>;

    const summary = report?.summary;
    const verdictRegressed = (summary?.regressed ?? 0) > 0;

    // Regressions first, then recoveries, then everything else.
    const deltas = [...(report?.deltas || [])].sort((a, b) =>
        Number(b.regressed) - Number(a.regressed) || Number(b.recovered) - Number(a.recovered) || a.test_name.localeCompare(b.test_name));

    return (
        <div className="max-w-[1100px] mx-auto pb-16 space-y-6">
            <div className="pt-2 pb-4 border-b border-slate-200/60">
                <div className="flex items-center gap-2 text-sm text-slate-500 mb-2">
                    <Link to={`/runs/${runId}`} className="hover:text-indigo-600 flex items-center gap-1 transition-colors">
                        <ArrowLeft size={14} /> Run #{runId}
                    </Link>
                    <span className="text-slate-300">/</span>
                    <span className="font-medium text-slate-700">Deployment comparison</span>
                </div>
                <h1 className="text-3xl font-extrabold tracking-tight text-slate-900 flex items-center gap-3">
                    <GitCompareArrows className="text-indigo-500" size={28} />
                    Deployment Comparison
                </h1>
                {report && (
                    <div className="flex flex-wrap items-center gap-3 mt-3 text-sm text-slate-600">
                        <span>
                            Baseline{' '}
                            <Link to={`/runs/${report.baseline_run_id}`} className="font-semibold text-indigo-600 hover:underline">
                                run #{report.baseline_run_id} <ExternalLink size={12} className="inline" />
                            </Link>
                        </span>
                        <span className="text-slate-300">vs</span>
                        <span>
                            Candidate{' '}
                            <Link to={`/runs/${report.candidate_run_id}`} className="font-semibold text-indigo-600 hover:underline">
                                run #{report.candidate_run_id} <ExternalLink size={12} className="inline" />
                            </Link>
                        </span>
                        {report.target_url && (
                            <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-slate-100 border border-slate-200 font-mono text-xs">
                                <Globe size={12} className="text-slate-400" /> {report.target_url}
                            </span>
                        )}
                    </div>
                )}
            </div>

            {!finished ? (
                <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl">
                    <Activity className="w-10 h-10 text-amber-500 mx-auto mb-4 animate-spin" />
                    <h3 className="text-lg font-bold text-slate-800 mb-1">Candidate run in progress…</h3>
                    <p className="text-slate-500 text-sm">
                        Re-running the baseline's suite against the new deployment. This page updates automatically.
                    </p>
                </div>
            ) : error ? (
                <div className="p-6 bg-rose-50 border border-rose-200 rounded-2xl text-rose-700 text-sm">
                    {(error as any)?.response?.data?.detail || 'Failed to load the comparison — is this a comparison run?'}
                </div>
            ) : reportLoading || !report ? (
                <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl text-slate-400">Loading comparison…</div>
            ) : (
                <>
                    {/* Verdict banner */}
                    <div className={`rounded-2xl border p-5 flex items-center gap-4 ${verdictRegressed
                        ? 'bg-rose-50 border-rose-200'
                        : 'bg-emerald-50 border-emerald-200'}`}>
                        {verdictRegressed
                            ? <XCircle className="text-rose-600 shrink-0" size={28} />
                            : <CheckCircle className="text-emerald-600 shrink-0" size={28} />}
                        <div>
                            <h2 className={`text-lg font-bold ${verdictRegressed ? 'text-rose-800' : 'text-emerald-800'}`}>
                                {verdictRegressed
                                    ? `${summary!.regressed} test${summary!.regressed === 1 ? '' : 's'} regressed on this deployment`
                                    : 'No regressions — safe to promote'}
                            </h2>
                            <p className={`text-sm ${verdictRegressed ? 'text-rose-600' : 'text-emerald-700'}`}>
                                {summary!.recovered > 0 && `${summary!.recovered} recovered · `}
                                {summary!.unchanged} unchanged out of {deltas.length} compared tests.
                            </p>
                        </div>
                    </div>

                    {/* Summary tiles */}
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                        {[
                            { label: 'Regressed', value: summary!.regressed, toneClass: 'text-rose-600', desc: 'passed on baseline, failing on candidate' },
                            { label: 'Recovered', value: summary!.recovered, toneClass: 'text-emerald-600', desc: 'failing on baseline, passing on candidate' },
                            { label: 'Unchanged', value: summary!.unchanged, toneClass: 'text-slate-500', desc: 'same outcome on both deployments' },
                        ].map((t) => (
                            <div key={t.label} className="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm">
                                <h3 className={`text-sm font-semibold uppercase tracking-wider mb-1 ${t.toneClass}`}>{t.label}</h3>
                                <p className="text-3xl font-extrabold text-slate-900">{t.value}</p>
                                <p className="text-xs text-slate-400 mt-1">{t.desc}</p>
                            </div>
                        ))}
                    </div>

                    {/* Delta table */}
                    <div className="bg-white border border-slate-200 rounded-2xl overflow-hidden">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="text-left text-xs font-bold text-slate-400 uppercase tracking-widest border-b border-slate-100">
                                    <th className="px-5 py-3">Test</th>
                                    <th className="px-5 py-3">Baseline</th>
                                    <th className="px-5 py-3">Candidate</th>
                                    <th className="px-5 py-3">Candidate duration</th>
                                    <th className="px-5 py-3">Change</th>
                                </tr>
                            </thead>
                            <tbody>
                                {deltas.map((d) => (
                                    <tr key={d.test_name} className={`border-b border-slate-50 last:border-0 ${d.regressed ? 'bg-rose-50/50' : d.recovered ? 'bg-emerald-50/50' : ''}`}>
                                        <td className="px-5 py-3 font-medium text-slate-800">{d.test_name}</td>
                                        <td className="px-5 py-3"><StatusChip status={d.baseline_status} /></td>
                                        <td className="px-5 py-3"><StatusChip status={d.candidate_status} /></td>
                                        <td className="px-5 py-3"><DurationDelta delta={d} /></td>
                                        <td className="px-5 py-3">
                                            {d.regressed ? (
                                                <span className="text-xs font-bold text-rose-600 uppercase">Regressed</span>
                                            ) : d.recovered ? (
                                                <span className="text-xs font-bold text-emerald-600 uppercase">Recovered</span>
                                            ) : (
                                                <span className="text-xs text-slate-400">—</span>
                                            )}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                        {deltas.length === 0 && (
                            <div className="p-10 text-center text-slate-400 text-sm">No comparable results between the two runs.</div>
                        )}
                    </div>
                </>
            )}
        </div>
    );
}

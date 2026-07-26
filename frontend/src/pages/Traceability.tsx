import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
    GitMerge, RefreshCw, AlertCircle, Layers, CheckCircle2, XCircle, MinusCircle, HelpCircle, Link2, ExternalLink,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { getProjects } from '@/lib/api';
import { traceApi, RequirementCoverage } from '@/api/traceability';

const errDetail = (e: any): string => e?.response?.data?.detail || e?.message || 'Unknown error';
const STATUS: Record<string, { tone: string; icon: any }> = {
    passing: { tone: 'bg-emerald-50 text-emerald-700 border-emerald-200', icon: CheckCircle2 },
    failing: { tone: 'bg-rose-50 text-rose-700 border-rose-200', icon: XCircle },
    mixed: { tone: 'bg-amber-50 text-amber-700 border-amber-200', icon: MinusCircle },
    unknown: { tone: 'bg-slate-50 text-slate-500 border-slate-200', icon: HelpCircle },
};

function CoverageRow({ c }: { c: RequirementCoverage }) {
    const [open, setOpen] = useState(false);
    const S = STATUS[c.status] || STATUS.unknown;
    return (
        <div className="border border-slate-200 rounded-xl overflow-hidden">
            <button className="w-full flex items-center gap-3 p-4 hover:bg-slate-50/50 text-left" onClick={() => setOpen(!open)}>
                <div className="min-w-0 flex-1">
                    <div className="font-semibold text-slate-800 text-sm flex items-center gap-2">
                        {c.url ? <a href={c.url} target="_blank" rel="noreferrer" className="text-indigo-600 hover:underline" onClick={(e) => e.stopPropagation()}>{c.ref} <ExternalLink className="w-3 h-3 inline" /></a> : c.ref}
                        {c.title && <span className="text-slate-400 font-normal">— {c.title}</span>}
                    </div>
                    <div className="text-xs text-slate-400 mt-0.5">{c.test_count} test(s) · {c.passing} passing · {c.failing} failing · {c.untested} untested</div>
                </div>
                <Badge variant="outline" className={`rounded-md text-[10px] font-bold uppercase shrink-0 ${S.tone}`}><S.icon className="w-3 h-3 mr-1" /> {c.status}</Badge>
            </button>
            {open && (
                <div className="border-t border-slate-100 p-4 bg-slate-50/30">
                    <div className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Covered by</div>
                    <div className="flex flex-wrap gap-1.5">
                        {c.test_names.map((n) => <Badge key={n} variant="outline" className="rounded-md bg-white text-slate-600 border-slate-200">{n}</Badge>)}
                    </div>
                </div>
            )}
        </div>
    );
}

export default function Traceability() {
    const qc = useQueryClient();
    const [projectId, setProjectId] = useState<number | null>(() => { const s = localStorage.getItem('activeProjectId'); return s ? parseInt(s) : null; });
    useEffect(() => {
        const h = () => { const s = localStorage.getItem('activeProjectId'); setProjectId(s ? parseInt(s) : null); };
        window.addEventListener('projectChanged', h);
        return () => window.removeEventListener('projectChanged', h);
    }, []);
    const { data: projects } = useQuery({ queryKey: ['projects'], queryFn: () => getProjects() });
    const selectProject = (idStr: string) => { const id = parseInt(idStr); setProjectId(id); localStorage.setItem('activeProjectId', id.toString()); window.dispatchEvent(new Event('projectChanged')); };

    const reqs = useQuery({ queryKey: ['requirements', projectId], queryFn: () => traceApi.requirements(projectId!), enabled: !!projectId });
    const gaps = useQuery({ queryKey: ['trace-gaps', projectId], queryFn: () => traceApi.gaps(projectId!), enabled: !!projectId });

    const [linkRefs, setLinkRefs] = useState<Record<number, string>>({});
    const link = useMutation({
        mutationFn: ({ caseId, ref }: { caseId: number; ref: string }) => traceApi.addLink(caseId, { ref }),
        onSuccess: () => { toast.success('Requirement linked'); qc.invalidateQueries({ queryKey: ['requirements', projectId] }); qc.invalidateQueries({ queryKey: ['trace-gaps', projectId] }); },
        onError: (e) => toast.error(errDetail(e)),
    });

    const tracedPct = gaps.data && gaps.data.total_cases ? Math.round((gaps.data.traced_cases / gaps.data.total_cases) * 100) : 0;

    return (
        <div className="max-w-[1200px] mx-auto pb-16">
            <div className="pt-2 pb-6 mb-6 border-b border-slate-200/60">
                <div className="flex items-center text-sm text-slate-400 gap-2 mb-3">
                    <span className="font-bold text-slate-700 bg-white px-2.5 py-1 rounded-md border border-slate-200 shadow-sm">Traceability</span>
                </div>
                <div className="flex flex-col sm:flex-row sm:items-end gap-4">
                    <div>
                        <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">Requirements Traceability</h1>
                        <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
                            Link tests to requirements or tickets (e.g. <code className="text-sm bg-slate-100 px-1 rounded">JIRA-123</code>) to answer
                            "is this covered and passing?" and see what's untested.
                        </p>
                    </div>
                    <div className="sm:ml-auto shrink-0">
                        <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Project</p>
                        <Select value={projectId?.toString() ?? ''} onValueChange={selectProject}>
                            <SelectTrigger className="w-[220px] h-10 rounded-xl bg-white border-slate-200"><div className="flex items-center gap-2 min-w-0"><Layers className="w-4 h-4 text-indigo-500 shrink-0" /><SelectValue placeholder="Select a project" /></div></SelectTrigger>
                            <SelectContent>{(projects || []).map((p) => <SelectItem key={p.id} value={p.id.toString()}>{p.name}</SelectItem>)}</SelectContent>
                        </Select>
                    </div>
                </div>
            </div>

            {!projectId ? (
                <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl"><AlertCircle className="w-10 h-10 text-slate-300 mx-auto mb-4" /><p className="text-slate-500 text-sm">Select a project.</p></div>
            ) : (
                <div className="space-y-6">
                    {gaps.data && (
                        <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
                            <div className="flex items-center justify-between mb-2">
                                <h3 className="font-bold text-slate-800">Coverage</h3>
                                <span className="text-sm text-slate-500 tabular-nums">{gaps.data.traced_cases} / {gaps.data.total_cases} tests linked ({tracedPct}%)</span>
                            </div>
                            <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
                                <div className="h-full rounded-full bg-indigo-500" style={{ width: `${Math.max(2, tracedPct)}%` }} />
                            </div>
                        </div>
                    )}

                    <div>
                        <h3 className="font-bold text-slate-800 mb-3 flex items-center gap-2"><GitMerge className="w-4 h-4 text-slate-500" /> Requirements</h3>
                        {reqs.isLoading ? <div className="p-8 flex justify-center"><RefreshCw className="animate-spin w-5 h-5 text-slate-400" /></div>
                            : reqs.error ? <p className="text-sm text-rose-600">{errDetail(reqs.error)}</p>
                                : !reqs.data || reqs.data.length === 0 ? (
                                    <div className="p-10 text-center bg-white border border-slate-200 rounded-2xl">
                                        <GitMerge className="w-9 h-9 text-slate-300 mx-auto mb-3" />
                                        <p className="text-slate-500 text-sm">No requirements linked yet. Link some untraced tests below.</p>
                                    </div>
                                ) : (
                                    <div className="space-y-2">{reqs.data.map((c) => <CoverageRow key={c.ref} c={c} />)}</div>
                                )}
                    </div>

                    {gaps.data && gaps.data.untraced_cases.length > 0 && (
                        <div>
                            <h3 className="font-bold text-slate-800 mb-3 flex items-center gap-2"><Link2 className="w-4 h-4 text-slate-500" /> Untraced tests ({gaps.data.untraced_cases.length})</h3>
                            <div className="bg-white border border-slate-200 rounded-2xl divide-y divide-slate-100 max-h-[420px] overflow-y-auto">
                                {gaps.data.untraced_cases.slice(0, 200).map((c) => (
                                    <div key={c.id} className="flex items-center gap-2 px-4 py-2">
                                        <span className="text-sm text-slate-600 flex-1 truncate">{c.name}</span>
                                        <Input value={linkRefs[c.id] || ''} onChange={(e) => setLinkRefs((m) => ({ ...m, [c.id]: e.target.value }))} placeholder="REQ / ticket ref" className="h-8 w-44" />
                                        <Button size="sm" variant="outline" className="h-8 text-xs" disabled={!(linkRefs[c.id] || '').trim() || link.isPending}
                                            onClick={() => link.mutate({ caseId: c.id, ref: (linkRefs[c.id] || '').trim() })}>Link</Button>
                                    </div>
                                ))}
                                {gaps.data.untraced_cases.length > 200 && <div className="px-4 py-2 text-xs text-slate-400">…and {gaps.data.untraced_cases.length - 200} more</div>}
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

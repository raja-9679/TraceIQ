import { useState, useEffect } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { FileCode2, Layers, Upload, Info, AlertCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { api, getProjects, getTestSuites } from '@/lib/api';

const errDetail = (e: any): string => e?.response?.data?.detail || e?.message || 'Unknown error';

const SAMPLE = `import { test, expect } from '@playwright/test';

test('homepage has expected title', async ({ page }) => {
  await page.goto('https://example.com');
  await expect(page).toHaveTitle(/Example/);
});`;

export default function ImportPlaywright() {
    const navigate = useNavigate();
    const [projectId, setProjectId] = useState<number | null>(() => {
        const s = localStorage.getItem('activeProjectId'); return s ? parseInt(s) : null;
    });
    useEffect(() => {
        const h = () => { const s = localStorage.getItem('activeProjectId'); setProjectId(s ? parseInt(s) : null); };
        window.addEventListener('projectChanged', h);
        return () => window.removeEventListener('projectChanged', h);
    }, []);

    const [suiteId, setSuiteId] = useState<string>('');
    const [name, setName] = useState('');
    const [script, setScript] = useState('');

    const { data: projects } = useQuery({ queryKey: ['projects'], queryFn: () => getProjects() });
    const { data: suites } = useQuery({
        queryKey: ['suites', projectId],
        queryFn: () => getTestSuites(projectId!),
        enabled: !!projectId,
    });
    const selectProject = (idStr: string) => {
        const id = parseInt(idStr); setProjectId(id); setSuiteId('');
        localStorage.setItem('activeProjectId', id.toString());
        window.dispatchEvent(new Event('projectChanged'));
    };

    const importMut = useMutation({
        mutationFn: async () => {
            const r = await api.post('/cases/import-playwright', {
                suite_id: parseInt(suiteId), name, script,
            });
            return r.data;
        },
        onSuccess: (c) => {
            toast.success(`Imported "${c.name}" as a raw Playwright case`);
            if (suiteId) navigate(`/suites/${suiteId}`);
        },
        onError: (e) => toast.error(`Import failed: ${errDetail(e)}`),
    });

    return (
        <div className="max-w-[900px] mx-auto pb-16">
            <div className="pt-2 pb-6 mb-6 border-b border-slate-200/60">
                <div className="flex items-center text-sm text-slate-400 gap-2 mb-3">
                    <span className="font-bold text-slate-700 bg-white px-2.5 py-1 rounded-md border border-slate-200 shadow-sm">Import</span>
                </div>
                <div className="flex flex-col sm:flex-row sm:items-end gap-4">
                    <div>
                        <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">Import Playwright Spec</h1>
                        <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
                            Bring an existing Playwright <code className="text-sm bg-slate-100 px-1 rounded">.spec.ts</code> in as a
                            test case. It runs verbatim via the Playwright runner (not the step interpreter).
                        </p>
                    </div>
                    <div className="sm:ml-auto shrink-0">
                        <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Project</p>
                        <Select value={projectId?.toString() ?? ''} onValueChange={selectProject}>
                            <SelectTrigger className="w-[220px] h-10 rounded-xl bg-white border-slate-200">
                                <div className="flex items-center gap-2 min-w-0">
                                    <Layers className="w-4 h-4 text-indigo-500 shrink-0" />
                                    <SelectValue placeholder="Select a project" />
                                </div>
                            </SelectTrigger>
                            <SelectContent>
                                {(projects || []).map((p) => <SelectItem key={p.id} value={p.id.toString()}>{p.name}</SelectItem>)}
                            </SelectContent>
                        </Select>
                    </div>
                </div>
            </div>

            <div className="flex items-start gap-2 text-xs text-amber-700 bg-amber-50/70 border border-amber-100 rounded-xl px-3 py-2.5 mb-5">
                <Info className="w-4 h-4 text-amber-500 shrink-0 mt-px" />
                <p>
                    Raw specs are opaque to step-level AI heal and the trace timeline. Execution runs arbitrary code and
                    is <span className="font-semibold">gated by <code>RAW_PLAYWRIGHT_ENABLED</code></span> on a sandboxed worker —
                    importing here only stores the script.
                </p>
            </div>

            {!projectId ? (
                <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl">
                    <AlertCircle className="w-10 h-10 text-slate-300 mx-auto mb-4" />
                    <p className="text-slate-500 text-sm">Select a project to choose a target suite.</p>
                </div>
            ) : (
                <div className="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm space-y-4">
                    <div className="grid sm:grid-cols-2 gap-4">
                        <div>
                            <label className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Target suite</label>
                            <Select value={suiteId} onValueChange={setSuiteId}>
                                <SelectTrigger className="h-10 mt-1"><SelectValue placeholder="Select a suite" /></SelectTrigger>
                                <SelectContent>
                                    {(suites || []).map((s: any) => <SelectItem key={s.id} value={s.id.toString()}>{s.name}</SelectItem>)}
                                </SelectContent>
                            </Select>
                        </div>
                        <div>
                            <label className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Case name</label>
                            <Input className="h-10 mt-1" placeholder="checkout-flow.spec" value={name} onChange={(e) => setName(e.target.value)} />
                        </div>
                    </div>
                    <div>
                        <div className="flex items-center justify-between mb-1">
                            <label className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Playwright spec</label>
                            <button className="text-xs text-indigo-500 hover:underline" onClick={() => setScript(SAMPLE)}>insert sample</button>
                        </div>
                        <textarea
                            className="w-full h-72 font-mono text-xs border border-slate-200 rounded-xl p-3 focus:outline-none focus:ring-2 focus:ring-indigo-200"
                            placeholder="Paste your .spec.ts here…" value={script} onChange={(e) => setScript(e.target.value)} />
                    </div>
                    <div className="flex items-center gap-2">
                        <FileCode2 className="w-4 h-4 text-slate-400" />
                        <span className="text-xs text-slate-400">{script.length} chars</span>
                        <Button className="ml-auto h-10 rounded-lg" disabled={importMut.isPending || !suiteId || !name.trim() || !script.trim()}
                            onClick={() => importMut.mutate()}>
                            <Upload className="w-4 h-4 mr-1.5" /> Import case
                        </Button>
                    </div>
                </div>
            )}
        </div>
    );
}

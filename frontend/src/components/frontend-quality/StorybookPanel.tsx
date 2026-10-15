import { useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { getTestSuites } from '@/lib/api';
import { Story, frontendQualityApi } from '@/api/frontendQuality';
import { errorDetail, toggle } from './matrixHelpers';


export default function StorybookPanel({ projectId }: { projectId: number }) {
    const [url, setUrl] = useState('');
    const [stories, setStories] = useState<Story[]>([]);
    const [picked, setPicked] = useState<string[]>([]);
    const [filter, setFilter] = useState('');
    const [suiteId, setSuiteId] = useState('');
    const [visual, setVisual] = useState(true);
    const [a11y, setA11y] = useState(true);
    const [impact, setImpact] = useState('serious');
    const suites = useQuery({ queryKey: ['suites', projectId], queryFn: () => getTestSuites(projectId) });

    const load = useMutation({
        mutationFn: () => frontendQualityApi.stories(projectId, url),
        onSuccess: (d) => { setStories(d.stories); setPicked([]); toast.success(`${d.stories.length} stories found`); },
        onError: (e) => toast.error(errorDetail(e)),
    });
    const generate = useMutation({
        mutationFn: () => frontendQualityApi.generateStoryCases(projectId, {
            url, suite_id: Number(suiteId), story_ids: picked, visual, a11y, a11y_impact: impact,
        }),
        onSuccess: (d) => toast.success(`${d.created.length} case(s) created, ${d.updated.length} updated`),
        onError: (e) => toast.error(errorDetail(e)),
    });
    const visible = useMemo(() => {
        const f = filter.trim().toLowerCase();
        return f ? stories.filter(s => `${s.title} ${s.name} ${s.id}`.toLowerCase().includes(f)) : stories;
    }, [stories, filter]);

    return (
        <div className="space-y-4">
            <p className="text-sm text-slate-500 max-w-3xl">
                Point at a Storybook on one of this project's hosts, pick stories, and get one case per story that opens its isolated
                iframe and runs a visual match and/or an axe accessibility check. Each story keeps its own component baseline, named
                after the story id; re-generating updates the existing case.
            </p>
            <form className="flex flex-wrap gap-2 items-end" onSubmit={(e) => { e.preventDefault(); load.mutate(); }}>
                <div className="space-y-1 flex-1 min-w-[18rem]">
                    <label htmlFor="sb-url" className="text-sm font-medium text-slate-700">Storybook URL</label>
                    <Input id="sb-url" type="url" required placeholder="https://storybook.example.com/" value={url}
                        onChange={(e) => setUrl(e.target.value)} />
                </div>
                <Button type="submit" disabled={load.isPending}>Load stories</Button>
            </form>
            {stories.length > 0 && (
                <div className="bg-white border border-slate-200 rounded-2xl p-4 space-y-3">
                    <div className="flex flex-wrap items-center gap-2">
                        <label htmlFor="sb-filter" className="sr-only">Filter stories</label>
                        <Input id="sb-filter" className="w-64 h-9" placeholder="Filter stories" value={filter} onChange={(e) => setFilter(e.target.value)} />
                        <Button type="button" size="sm" variant="outline" onClick={() => setPicked([...new Set([...picked, ...visible.map(s => s.id)])])}>Select all shown</Button>
                        <Button type="button" size="sm" variant="outline" onClick={() => setPicked([])}>Clear selection</Button>
                        <span className="text-xs text-slate-500">{picked.length} selected</span>
                    </div>
                    <ul className="max-h-80 overflow-y-auto divide-y divide-slate-100 border border-slate-100 rounded-xl">
                        {visible.map(s => (
                            <li key={s.id}>
                                <label htmlFor={`story-${s.id}`} className="flex items-center gap-2 px-3 py-2 text-sm cursor-pointer">
                                    <input id={`story-${s.id}`} type="checkbox" checked={picked.includes(s.id)}
                                        onChange={(e) => setPicked(toggle(picked, s.id, e.target.checked))} />
                                    <span className="font-medium text-slate-800">{s.title}</span>
                                    <span className="text-slate-500">/ {s.name}</span>
                                    <code className="ml-auto text-[11px] text-slate-400">{s.id}</code>
                                </label>
                            </li>
                        ))}
                    </ul>
                    <div className="flex flex-wrap gap-4 items-end">
                        <div className="space-y-1">
                            <label htmlFor="sb-suite" className="text-sm font-medium text-slate-700">Add cases to suite</label>
                            <select id="sb-suite" className="h-9 rounded-lg border border-slate-200 text-sm px-2 min-w-[14rem]"
                                value={suiteId} onChange={(e) => setSuiteId(e.target.value)}>
                                <option value="">Choose a suite</option>
                                {(suites.data as { id: number; name: string }[] | undefined || []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                            </select>
                        </div>
                        <label htmlFor="sb-visual" className="flex items-center gap-2 text-sm">
                            <input id="sb-visual" type="checkbox" checked={visual} onChange={(e) => setVisual(e.target.checked)} /> Visual match
                        </label>
                        <label htmlFor="sb-a11y" className="flex items-center gap-2 text-sm">
                            <input id="sb-a11y" type="checkbox" checked={a11y} onChange={(e) => setA11y(e.target.checked)} /> Accessibility
                        </label>
                        <div className="space-y-1">
                            <label htmlFor="sb-impact" className="text-sm font-medium text-slate-700">Fail at impact</label>
                            <select id="sb-impact" className="h-9 rounded-lg border border-slate-200 text-sm px-2" value={impact}
                                onChange={(e) => setImpact(e.target.value)} disabled={!a11y}>
                                {['minor', 'moderate', 'serious', 'critical'].map(i => <option key={i} value={i}>{i}</option>)}
                            </select>
                        </div>
                        <Button type="button" disabled={!picked.length || !suiteId || (!visual && !a11y) || generate.isPending}
                            onClick={() => generate.mutate()}>
                            Generate {picked.length} case{picked.length === 1 ? '' : 's'}
                        </Button>
                        {suiteId && generate.isSuccess && <Link className="text-sm underline" to={`/suites/${suiteId}`}>Open suite</Link>}
                    </div>
                </div>
            )}
        </div>
    );
}

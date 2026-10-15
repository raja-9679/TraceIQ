import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Play, Plus, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
    CRAWL_CATEGORIES, CRAWL_ELEMENTS, CrawlCategory, CrawlRun, SiteCrawl, SiteCrawlInput, frontendQualityApi,
} from '@/api/frontendQuality';
import { errorDetail, toggle } from './matrixHelpers';


const CATEGORY_LABEL: Record<CrawlCategory, string> = {
    broken: 'Broken (4xx/5xx)', network: 'Network error', slow: 'Slow', redirect_loop: 'Redirect loop',
    redirect_chain: 'Long redirect chain', mixed_content: 'Mixed content',
};

const BLANK: SiteCrawlInput = {
    name: '', start_url: '', max_depth: 3, max_pages: 100, extra_hosts: [], respect_robots: true,
    rate_limit_ms: 250, slow_threshold_ms: 3000, check_external: true, cron_expression: null, is_active: true,
};

function CrawlForm({ projectId, onDone }: { projectId: number; onDone: () => void }) {
    const [form, setForm] = useState<SiteCrawlInput>(BLANK);
    const [hostsText, setHostsText] = useState('');
    const hosts = useQuery({ queryKey: ['frontend-hosts', projectId], queryFn: () => frontendQualityApi.hosts(projectId) });
    const qc = useQueryClient();
    const create = useMutation({
        mutationFn: () => frontendQualityApi.createCrawl(projectId, {
            ...form, extra_hosts: hostsText.split(/[\s,]+/).filter(Boolean),
            cron_expression: form.cron_expression?.trim() || null,
        }),
        onSuccess: () => { toast.success('Crawl saved'); qc.invalidateQueries({ queryKey: ['site-crawls', projectId] }); onDone(); },
        onError: (e) => toast.error(errorDetail(e)),
    });
    const num = (k: keyof SiteCrawlInput) => (e: React.ChangeEvent<HTMLInputElement>) =>
        setForm({ ...form, [k]: Number(e.target.value) });
    return (
        <form className="bg-white border border-slate-200 rounded-2xl p-5 space-y-4"
            onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
            <div className="grid md:grid-cols-2 gap-4">
                <div className="space-y-1">
                    <label htmlFor="crawl-name" className="text-sm font-medium text-slate-700">Name</label>
                    <Input id="crawl-name" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
                </div>
                <div className="space-y-1">
                    <label htmlFor="crawl-start" className="text-sm font-medium text-slate-700">Start URL</label>
                    <Input id="crawl-start" required type="url" placeholder="https://www.example.com/" value={form.start_url}
                        onChange={(e) => setForm({ ...form, start_url: e.target.value })} />
                    <p className="text-xs text-slate-400">
                        Must be one of this project's hosts{hosts.data?.length ? `: ${hosts.data.join(', ')}` : ' (add an environment base URL or a suite allowed domain first)'}.
                    </p>
                </div>
                <div className="space-y-1">
                    <label htmlFor="crawl-depth" className="text-sm font-medium text-slate-700">Max depth</label>
                    <Input id="crawl-depth" type="number" min={0} max={10} value={form.max_depth} onChange={num('max_depth')} />
                </div>
                <div className="space-y-1">
                    <label htmlFor="crawl-pages" className="text-sm font-medium text-slate-700">Max pages</label>
                    <Input id="crawl-pages" type="number" min={1} max={1000} value={form.max_pages} onChange={num('max_pages')} />
                </div>
                <div className="space-y-1">
                    <label htmlFor="crawl-rate" className="text-sm font-medium text-slate-700">Delay between requests to one host (ms)</label>
                    <Input id="crawl-rate" type="number" min={50} max={10000} value={form.rate_limit_ms} onChange={num('rate_limit_ms')} />
                </div>
                <div className="space-y-1">
                    <label htmlFor="crawl-slow" className="text-sm font-medium text-slate-700">Slow threshold (ms)</label>
                    <Input id="crawl-slow" type="number" min={100} max={60000} value={form.slow_threshold_ms} onChange={num('slow_threshold_ms')} />
                </div>
                <div className="space-y-1">
                    <label htmlFor="crawl-hosts" className="text-sm font-medium text-slate-700">Also crawl these project hosts</label>
                    <Input id="crawl-hosts" placeholder="blog.example.com" value={hostsText} onChange={(e) => setHostsText(e.target.value)} />
                </div>
                <div className="space-y-1">
                    <label htmlFor="crawl-cron" className="text-sm font-medium text-slate-700">Schedule (cron, UTC, optional)</label>
                    <Input id="crawl-cron" placeholder="0 3 * * *" value={form.cron_expression || ''}
                        onChange={(e) => setForm({ ...form, cron_expression: e.target.value })} />
                </div>
            </div>
            <div className="flex flex-wrap gap-4 text-sm text-slate-700">
                <label htmlFor="crawl-robots" className="flex items-center gap-2">
                    <input id="crawl-robots" type="checkbox" checked={form.respect_robots}
                        onChange={(e) => setForm({ ...form, respect_robots: e.target.checked })} />
                    Respect robots.txt
                </label>
                <label htmlFor="crawl-external" className="flex items-center gap-2">
                    <input id="crawl-external" type="checkbox" checked={form.check_external}
                        onChange={(e) => setForm({ ...form, check_external: e.target.checked })} />
                    Check links to other sites (status only, never crawled)
                </label>
            </div>
            <div className="flex gap-2">
                <Button type="submit" disabled={create.isPending}>Save crawl</Button>
                <Button type="button" variant="outline" onClick={onDone}>Cancel</Button>
            </div>
        </form>
    );
}

function IssuesTable({ run }: { run: CrawlRun }) {
    const [categories, setCategories] = useState<CrawlCategory[]>([]);
    const [elements, setElements] = useState<string[]>([]);
    const [q, setQ] = useState('');
    const [offset, setOffset] = useState(0);
    const limit = 100;
    const issues = useQuery({
        queryKey: ['crawl-issues', run.id, categories, elements, q, offset],
        queryFn: () => frontendQualityApi.crawlIssues(run.id, { category: categories, element: elements, q, limit, offset }),
    });
    return (
        <div className="space-y-3">
            <fieldset className="flex flex-wrap gap-2 items-center">
                <legend className="sr-only">Filter by problem</legend>
                {CRAWL_CATEGORIES.map(c => (
                    <label key={c} htmlFor={`cat-${run.id}-${c}`} className="flex items-center gap-1.5 text-xs border border-slate-200 rounded-full px-2.5 py-1 cursor-pointer">
                        <input id={`cat-${run.id}-${c}`} type="checkbox" checked={categories.includes(c)}
                            onChange={(e) => { setOffset(0); setCategories(toggle(categories, c, e.target.checked)); }} />
                        {CATEGORY_LABEL[c]} ({run.issue_counts?.[c] ?? 0})
                    </label>
                ))}
            </fieldset>
            <fieldset className="flex flex-wrap gap-2 items-center">
                <legend className="sr-only">Filter by element</legend>
                {CRAWL_ELEMENTS.map(el => (
                    <label key={el} htmlFor={`el-${run.id}-${el}`} className="flex items-center gap-1.5 text-xs border border-slate-200 rounded-full px-2.5 py-1 cursor-pointer">
                        <input id={`el-${run.id}-${el}`} type="checkbox" checked={elements.includes(el)}
                            onChange={(e) => { setOffset(0); setElements(toggle(elements, el, e.target.checked)); }} />
                        {el === 'a' ? 'links' : el === 'css' ? 'CSS url()' : el}
                    </label>
                ))}
                <label htmlFor={`q-${run.id}`} className="sr-only">Search URLs</label>
                <Input id={`q-${run.id}`} className="h-8 w-60 text-xs" placeholder="Search URLs" value={q}
                    onChange={(e) => { setOffset(0); setQ(e.target.value); }} />
            </fieldset>
            <div className="overflow-x-auto border border-slate-200 rounded-xl">
                <table className="w-full text-xs">
                    <thead className="bg-slate-50 text-slate-500">
                        <tr>
                            <th className="text-left p-2">Problem</th><th className="text-left p-2">Target</th>
                            <th className="text-left p-2">Element</th><th className="text-left p-2">Status</th>
                            <th className="text-left p-2">Time</th><th className="text-left p-2">Found on</th>
                        </tr>
                    </thead>
                    <tbody>
                        {(issues.data?.items || []).map(i => (
                            <tr key={i.id} className="border-t border-slate-100 align-top">
                                <td className="p-2 font-semibold">{CATEGORY_LABEL[i.category] || i.category}
                                    {i.detail && <div className="font-normal text-slate-500">{i.detail}</div>}
                                    {i.redirect_chain && i.redirect_chain.length > 1 && (
                                        <ol className="font-normal text-slate-500 list-decimal ml-4">
                                            {i.redirect_chain.map((h, n) => <li key={n} className="break-all">{h.status ?? '—'} {h.url}</li>)}
                                        </ol>
                                    )}
                                </td>
                                <td className="p-2 break-all max-w-xs">{i.target_url}</td>
                                <td className="p-2">{i.element}</td>
                                <td className="p-2">{i.status_code ?? '—'}</td>
                                <td className="p-2">{i.duration_ms != null ? `${i.duration_ms} ms` : '—'}</td>
                                <td className="p-2 break-all max-w-xs">{i.page_url}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
                {issues.data && issues.data.total === 0 && <p className="p-4 text-sm text-slate-500">No problems match.</p>}
            </div>
            {issues.data && issues.data.total > limit && (
                <div className="flex items-center gap-2 text-xs">
                    <Button size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>Previous page</Button>
                    <span>{offset + 1}–{Math.min(offset + limit, issues.data.total)} of {issues.data.total}</span>
                    <Button size="sm" variant="outline" disabled={offset + limit >= issues.data.total} onClick={() => setOffset(offset + limit)}>Next page</Button>
                </div>
            )}
        </div>
    );
}

function CrawlCard({ crawl, projectId }: { crawl: SiteCrawl; projectId: number }) {
    const qc = useQueryClient();
    const runs = useQuery({
        queryKey: ['crawl-runs', crawl.id],
        queryFn: () => frontendQualityApi.crawlRuns(crawl.id),
        refetchInterval: (q) => (q.state.data || []).some(r => r.status === 'pending' || r.status === 'running') ? 5000 : false,
    });
    const [runId, setRunId] = useState<number | null>(null);
    const current = (runs.data || []).find(r => r.id === runId) || (runs.data || [])[0];
    const start = useMutation({
        mutationFn: () => frontendQualityApi.runCrawl(crawl.id),
        onSuccess: () => { toast.success('Crawl started'); qc.invalidateQueries({ queryKey: ['crawl-runs', crawl.id] }); },
        onError: (e) => toast.error(errorDetail(e)),
    });
    const remove = useMutation({
        mutationFn: () => frontendQualityApi.deleteCrawl(crawl.id),
        onSuccess: () => qc.invalidateQueries({ queryKey: ['site-crawls', projectId] }),
        onError: (e) => toast.error(errorDetail(e)),
    });
    return (
        <section className="bg-white border border-slate-200 rounded-2xl p-5 space-y-4">
            <div className="flex flex-wrap items-start gap-3">
                <div className="min-w-0">
                    <h3 className="font-bold text-slate-800">{crawl.name}</h3>
                    <p className="text-xs text-slate-500 break-all">{crawl.start_url} · depth {crawl.max_depth} · up to {crawl.max_pages} pages
                        {crawl.cron_expression ? ` · scheduled ${crawl.cron_expression}` : ''}{crawl.respect_robots ? '' : ' · ignores robots.txt'}</p>
                </div>
                <div className="ml-auto flex gap-2">
                    <Button size="sm" onClick={() => start.mutate()} disabled={start.isPending}><Play className="w-3.5 h-3.5 mr-1" /> Run now</Button>
                    <Button size="sm" variant="outline" onClick={() => { if (confirm(`Delete crawl "${crawl.name}" and its history?`)) remove.mutate(); }}>
                        <Trash2 className="w-3.5 h-3.5 mr-1" /> Delete
                    </Button>
                </div>
            </div>
            {(runs.data || []).length > 0 && (
                <div className="flex items-center gap-2">
                    <label htmlFor={`run-pick-${crawl.id}`} className="text-xs text-slate-500">Run</label>
                    <select id={`run-pick-${crawl.id}`} className="h-8 rounded-lg border border-slate-200 text-xs px-2"
                        value={current?.id ?? ''} onChange={(e) => setRunId(Number(e.target.value))}>
                        {(runs.data || []).map(r => (
                            <option key={r.id} value={r.id}>#{r.id} · {new Date(r.created_at).toLocaleString()} · {r.status}</option>
                        ))}
                    </select>
                    {current && current.status === 'completed' && (
                        <span className="text-xs text-slate-500">{current.pages_crawled} pages, {current.links_checked} links checked
                            {current.truncated ? ' (stopped at a limit)' : ''}{current.robots?.skipped ? `, ${current.robots.skipped} skipped by robots.txt` : ''}</span>
                    )}
                    {current?.status === 'error' && <span className="text-xs text-rose-600">{current.error}</span>}
                    {(current?.status === 'pending' || current?.status === 'running') && <span className="text-xs text-indigo-600">{current.status}…</span>}
                </div>
            )}
            {current && current.status === 'completed' && <IssuesTable run={current} />}
        </section>
    );
}

export default function CrawlerPanel({ projectId }: { projectId: number }) {
    const [adding, setAdding] = useState(false);
    const crawls = useQuery({ queryKey: ['site-crawls', projectId], queryFn: () => frontendQualityApi.listCrawls(projectId) });
    return (
        <div className="space-y-4">
            <div className="flex items-center gap-3">
                <p className="text-sm text-slate-500 max-w-3xl">
                    Crawls your site from a start URL (same origin by default, robots.txt respected, requests spaced per host) and checks
                    every link, image, script, stylesheet and CSS <code>url()</code> asset for broken responses, slow answers, redirect
                    loops and mixed content.
                </p>
                {!adding && <Button className="ml-auto" onClick={() => setAdding(true)}><Plus className="w-4 h-4 mr-1" /> New crawl</Button>}
            </div>
            {adding && <CrawlForm projectId={projectId} onDone={() => setAdding(false)} />}
            {crawls.isLoading && <p className="text-sm text-slate-500">Loading…</p>}
            {(crawls.data || []).map(c => <CrawlCard key={c.id} crawl={c} projectId={projectId} />)}
            {crawls.data && crawls.data.length === 0 && !adding && (
                <p className="text-sm text-slate-500">No crawls yet.</p>
            )}
        </div>
    );
}

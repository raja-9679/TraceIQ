import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Play, Plus, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
    CRAWL_CATEGORIES, CRAWL_ELEMENTS, CrawlCategory, CrawlRun, SiteCrawl, SiteCrawlInput, frontendQualityApi,
} from '@/api/frontendQuality';
import { DetailCard, EmptyState, ListGroup, ListRow, Pill, SplitView } from '@/components/redesign/primitives';
import type { Tone } from '@/components/redesign/kinds';
import { errorDetail, toggle } from './matrixHelpers';


const CATEGORY_LABEL: Record<CrawlCategory, string> = {
    broken: 'Broken (4xx/5xx)', network: 'Network error', slow: 'Slow', redirect_loop: 'Redirect loop',
    redirect_chain: 'Long redirect chain', mixed_content: 'Mixed content',
    not_checked: 'Not checked (private address)',
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
        <form className="flex flex-col gap-4"
            onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
            <div className="grid md:grid-cols-2 gap-4">
                <div className="space-y-1">
                    <label htmlFor="crawl-name" className="text-sm font-semibold text-ink">Name</label>
                    <Input id="crawl-name" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
                </div>
                <div className="space-y-1">
                    <label htmlFor="crawl-start" className="text-sm font-semibold text-ink">Start URL</label>
                    <Input id="crawl-start" required type="url" placeholder="https://www.example.com/" value={form.start_url}
                        onChange={(e) => setForm({ ...form, start_url: e.target.value })} />
                    <p className="text-xs text-ink-soft">
                        Must be one of this project's hosts{hosts.data?.length ? `: ${hosts.data.join(', ')}` : ' (add an environment base URL or a suite allowed domain first)'}.
                    </p>
                </div>
                <div className="space-y-1">
                    <label htmlFor="crawl-depth" className="text-sm font-semibold text-ink">Max depth</label>
                    <Input id="crawl-depth" type="number" min={0} max={10} value={form.max_depth} onChange={num('max_depth')} />
                </div>
                <div className="space-y-1">
                    <label htmlFor="crawl-pages" className="text-sm font-semibold text-ink">Max pages</label>
                    <Input id="crawl-pages" type="number" min={1} max={1000} value={form.max_pages} onChange={num('max_pages')} />
                </div>
                <div className="space-y-1">
                    <label htmlFor="crawl-rate" className="text-sm font-semibold text-ink">Delay between requests to one host (ms)</label>
                    <Input id="crawl-rate" type="number" min={50} max={10000} value={form.rate_limit_ms} onChange={num('rate_limit_ms')} />
                </div>
                <div className="space-y-1">
                    <label htmlFor="crawl-slow" className="text-sm font-semibold text-ink">Slow threshold (ms)</label>
                    <Input id="crawl-slow" type="number" min={100} max={60000} value={form.slow_threshold_ms} onChange={num('slow_threshold_ms')} />
                </div>
                <div className="space-y-1">
                    <label htmlFor="crawl-hosts" className="text-sm font-semibold text-ink">Also crawl these project hosts</label>
                    <Input id="crawl-hosts" placeholder="blog.example.com" value={hostsText} onChange={(e) => setHostsText(e.target.value)} />
                </div>
                <div className="space-y-1">
                    <label htmlFor="crawl-cron" className="text-sm font-semibold text-ink">Schedule (cron, UTC, optional)</label>
                    <Input id="crawl-cron" placeholder="0 3 * * *" value={form.cron_expression || ''}
                        onChange={(e) => setForm({ ...form, cron_expression: e.target.value })} />
                </div>
            </div>
            <div className="flex flex-wrap gap-4 text-sm text-ink">
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
                    <label key={c} htmlFor={`cat-${run.id}-${c}`} className="flex cursor-pointer items-center gap-1.5 rounded-full border border-border bg-white px-2.5 py-1 text-xs text-ink">
                        <input id={`cat-${run.id}-${c}`} type="checkbox" checked={categories.includes(c)}
                            onChange={(e) => { setOffset(0); setCategories(toggle(categories, c, e.target.checked)); }} />
                        {CATEGORY_LABEL[c]} ({run.issue_counts?.[c] ?? 0})
                    </label>
                ))}
            </fieldset>
            <fieldset className="flex flex-wrap gap-2 items-center">
                <legend className="sr-only">Filter by element</legend>
                {CRAWL_ELEMENTS.map(el => (
                    <label key={el} htmlFor={`el-${run.id}-${el}`} className="flex cursor-pointer items-center gap-1.5 rounded-full border border-border bg-white px-2.5 py-1 text-xs text-ink">
                        <input id={`el-${run.id}-${el}`} type="checkbox" checked={elements.includes(el)}
                            onChange={(e) => { setOffset(0); setElements(toggle(elements, el, e.target.checked)); }} />
                        {el === 'a' ? 'links' : el === 'css' ? 'CSS url()' : el}
                    </label>
                ))}
                <label htmlFor={`q-${run.id}`} className="sr-only">Search URLs</label>
                <Input id={`q-${run.id}`} className="h-8 w-60 text-xs" placeholder="Search URLs" value={q}
                    onChange={(e) => { setOffset(0); setQ(e.target.value); }} />
            </fieldset>
            <div className="overflow-x-auto rounded-2xl border border-border">
                <table className="w-full text-xs text-ink">
                    <thead className="bg-background text-[11px] uppercase tracking-[0.04em] text-ink-soft">
                        <tr>
                            <th className="text-left p-2">Problem</th><th className="text-left p-2">Target</th>
                            <th className="text-left p-2">Element</th><th className="text-left p-2">Status</th>
                            <th className="text-left p-2">Time</th><th className="text-left p-2">Found on</th>
                        </tr>
                    </thead>
                    <tbody>
                        {(issues.data?.items || []).map(i => (
                            <tr key={i.id} className="border-t border-[#F0EEE8] align-top">
                                <td className="p-2 font-semibold">{CATEGORY_LABEL[i.category] || i.category}
                                    {i.detail && <div className="font-normal text-ink-soft">{i.detail}</div>}
                                    {i.redirect_chain && i.redirect_chain.length > 1 && (
                                        <ol className="ml-4 list-decimal font-normal text-ink-soft">
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
                {issues.data && issues.data.total === 0 && <p className="p-4 text-sm text-ink-soft">No problems match.</p>}
            </div>
            {issues.data && issues.data.total > limit && (
                <div className="flex items-center gap-2 text-xs text-ink-soft">
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
        <DetailCard label={`Crawl ${crawl.name}`}>
            <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="flex min-w-0 flex-col gap-1.5">
                    <h2 className="break-words text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{crawl.name}</h2>
                    <p className="break-all text-sm text-ink-soft"><span className="font-mono">{crawl.start_url}</span> · depth {crawl.max_depth} · up to {crawl.max_pages} pages
                        {crawl.cron_expression ? ` · scheduled ${crawl.cron_expression}` : ''}{crawl.respect_robots ? '' : ' · ignores robots.txt'}</p>
                </div>
                <div className="flex flex-wrap gap-2">
                    <Button onClick={() => start.mutate()} disabled={start.isPending}><Play className="h-4 w-4" /> Run now</Button>
                    <Button variant="outline" onClick={() => { if (confirm(`Delete crawl "${crawl.name}" and its history?`)) remove.mutate(); }}>
                        <Trash2 className="h-4 w-4" /> Delete
                    </Button>
                </div>
            </div>
            {(runs.data || []).length > 0 ? (
                <div className="flex flex-wrap items-center gap-2 rounded-2xl bg-background p-3">
                    <label htmlFor={`run-pick-${crawl.id}`} className="text-xs font-semibold uppercase tracking-[0.04em] text-ink-soft">Run</label>
                    <select id={`run-pick-${crawl.id}`} className="h-9 rounded-full border border-border bg-white px-3 text-xs text-ink"
                        value={current?.id ?? ''} onChange={(e) => setRunId(Number(e.target.value))}>
                        {(runs.data || []).map(r => (
                            <option key={r.id} value={r.id}>#{r.id} · {new Date(r.created_at).toLocaleString()} · {r.status}</option>
                        ))}
                    </select>
                    {current && current.status === 'completed' && (
                        <span className="text-xs text-ink-soft">{current.pages_crawled} pages, {current.links_checked} links checked
                            {current.truncated ? ' (stopped at a limit)' : ''}{current.robots?.skipped ? `, ${current.robots.skipped} skipped by robots.txt` : ''}</span>
                    )}
                    {current?.status === 'error' && <span className="text-xs text-pastel-ink-peach">{current.error}</span>}
                    {(current?.status === 'pending' || current?.status === 'running') && <Pill tone="lavender">{current.status}…</Pill>}
                </div>
            ) : runs.data && (
                <p className="rounded-2xl bg-background p-4 text-sm text-ink-soft">This crawl has not run yet. Use “Run now”.</p>
            )}
            {current && current.status === 'completed' && <IssuesTable run={current} />}
        </DetailCard>
    );
}

const STATUS_TONE: Record<string, Tone> = { completed: 'mint', error: 'peach', running: 'lavender', pending: 'lavender' };

export default function CrawlerPanel({ projectId }: { projectId: number }) {
    const [adding, setAdding] = useState(false);
    const [selected, setSelected] = useState<number | null>(null);
    const crawls = useQuery({ queryKey: ['site-crawls', projectId], queryFn: () => frontendQualityApi.listCrawls(projectId) });
    const list = crawls.data || [];
    const current = list.find(c => c.id === selected) || list[0];
    return (
        <div className="flex flex-col gap-5">
            <div className="flex flex-wrap items-center gap-4 rounded-3xl border border-border bg-white px-5 py-4">
                <p className="min-w-0 flex-1 basis-[420px] text-sm leading-relaxed text-ink-soft">
                    Crawls your site from a start URL (same origin by default, robots.txt respected, requests spaced per host) and checks
                    every link, image, script, stylesheet and CSS <code>url()</code> asset for broken responses, slow answers, redirect
                    loops and mixed content.
                </p>
                {!adding && <Button onClick={() => setAdding(true)}><Plus className="h-4 w-4" /> New crawl</Button>}
            </div>
            {adding && (
                <DetailCard label="New crawl">
                    <h2 className="text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">New crawl</h2>
                    <CrawlForm projectId={projectId} onDone={() => setAdding(false)} />
                </DetailCard>
            )}
            {crawls.isLoading && <div className="h-40 animate-pulse rounded-3xl bg-white/70" aria-busy="true" aria-label="Loading" />}
            {crawls.data && list.length === 0 && !adding && (
                <EmptyState title="No crawls yet">Use “New crawl” to check a site for broken links and assets.</EmptyState>
            )}
            {list.length > 0 && (
                <SplitView
                    list={
                        <ListGroup title="Crawls" count={list.length}>
                            {list.map(c => (
                                <ListRow
                                    key={c.id}
                                    selected={current?.id === c.id}
                                    onSelect={() => setSelected(c.id)}
                                    title={<span className="break-words">{c.name}</span>}
                                    meta={<span className="break-all">{c.start_url}{c.cron_expression ? ` · ${c.cron_expression}` : ''}</span>}
                                    trailing={<Pill tone={c.last_run ? STATUS_TONE[c.last_run.status] ?? 'neutral' : 'neutral'}>{c.last_run ? c.last_run.status : 'not run'}</Pill>}
                                />
                            ))}
                        </ListGroup>
                    }
                    detail={current && <CrawlCard key={current.id} crawl={current} projectId={projectId} />}
                />
            )}
        </div>
    );
}

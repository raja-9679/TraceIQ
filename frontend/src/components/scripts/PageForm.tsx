import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { pagesApi, type MonitoredPage } from '@/api/estates';
import { Button } from '@/components/ui/button';
import { errDetail } from '@/lib/errors';
import { formatElementChecks, pageInput, parseElementChecks, splitList } from '@/components/estate/helpers';

interface Props {
    projectId: number;
    page: MonitoredPage | null;
    onSaved: (p: MonitoredPage) => void;
}

export default function PageForm({ projectId, page, onSaved }: Props) {
    const qc = useQueryClient();
    const init = pageInput(page);
    const [form, setForm] = useState(init);
    const [keywords, setKeywords] = useState((init.forbidden_keywords || []).join('\n'));
    const [elements, setElements] = useState(formatElementChecks(init.element_checks));
    const [emails, setEmails] = useState((init.alert_emails || []).join(', '));
    const set = <K extends keyof typeof form>(k: K, v: (typeof form)[K]) => setForm(f => ({ ...f, [k]: v }));

    const save = useMutation({
        mutationFn: () => {
            const body = { ...form, forbidden_keywords: splitList(keywords),
                element_checks: parseElementChecks(elements), alert_emails: splitList(emails) };
            return page ? pagesApi.update(page.id, body) : pagesApi.create(projectId, body);
        },
        onSuccess: (p) => { qc.invalidateQueries({ queryKey: ['monitored-pages', projectId] }); onSaved(p); },
    });

    const field = 'mt-1 h-10 w-full rounded-xl border border-border bg-white px-3 text-sm text-ink';
    return (
        <form className="flex flex-col gap-4"
            onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
            {page ? <h3 className="text-xs font-semibold uppercase tracking-[0.04em] text-ink-soft">Page settings</h3> : <h3 className="sr-only">Monitor a page</h3>}
            <div className="grid gap-3 sm:grid-cols-2">
                <label className="block text-xs font-semibold text-ink-soft">Name
                    <input className={field} value={form.name} onChange={e => set('name', e.target.value)} required />
                </label>
                <label className="block text-xs font-semibold text-ink-soft">URL (host must be allowlisted)
                    <input className={field} type="url" value={form.url} onChange={e => set('url', e.target.value)} required />
                </label>
                <label className="block text-xs font-semibold text-ink-soft">Check schedule (cron, UTC; PCI 11.6.1 wants at least weekly)
                    <input className={field} value={form.cron_expression} onChange={e => set('cron_expression', e.target.value)} />
                </label>
                <label className="block text-xs font-semibold text-ink-soft">Alert emails
                    <input className={field} value={emails} onChange={e => setEmails(e.target.value)} />
                </label>
            </div>
            <div className="flex flex-wrap gap-4 text-sm text-ink">
                <label className="flex items-center gap-2"><input type="checkbox" checked={form.schedule_enabled}
                    onChange={e => set('schedule_enabled', e.target.checked)} />Scheduled checks</label>
                <label className="flex items-center gap-2"><input type="checkbox" checked={form.track_scripts}
                    onChange={e => set('track_scripts', e.target.checked)} />Script inventory</label>
                <label className="flex items-center gap-2"><input type="checkbox" checked={form.track_content}
                    onChange={e => set('track_content', e.target.checked)} />Defacement checks</label>
            </div>
            {form.track_content && (
                <fieldset className="space-y-3 rounded-2xl border border-border p-4">
                    <legend className="px-1 text-sm font-semibold text-ink">Defacement</legend>
                    <label className="block text-xs font-semibold text-ink-soft">
                        Alert when text similarity to the baseline drops below {Math.round(form.similarity_threshold * 100)}%
                        <input type="range" min={0} max={1} step={0.05} className="mt-1 w-full" value={form.similarity_threshold}
                            onChange={e => set('similarity_threshold', Number(e.target.value))} />
                    </label>
                    <label className="block text-xs font-semibold text-ink-soft">Forbidden keywords (one per line, plain text)
                        <textarea rows={3} className="mt-1 w-full rounded-xl border border-border bg-white p-2 text-sm text-ink"
                            value={keywords} onChange={e => setKeywords(e.target.value)} placeholder="hacked by" />
                    </label>
                    <label className="block text-xs font-semibold text-ink-soft">
                        Element checks — one per line: <code>selector | must contain | forbidden; forbidden</code>
                        <textarea rows={3} className="mt-1 w-full rounded-xl border border-border bg-white p-2 font-mono text-xs text-ink"
                            value={elements} onChange={e => setElements(e.target.value)} placeholder="h1 | Checkout" />
                    </label>
                </fieldset>
            )}
            {save.error && <p role="alert" className="text-sm text-pastel-ink-peach">{errDetail(save.error)}</p>}
            <Button type="submit" className="self-start" disabled={save.isPending}>{save.isPending ? 'Saving…' : 'Save page'}</Button>
        </form>
    );
}

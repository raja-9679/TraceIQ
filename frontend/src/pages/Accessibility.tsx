import { useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { accessibilityApi, IMPACTS, type A11yViolation, type A11yWaiver, type WaiverInput } from '@/api/accessibility';
import { flakesApi } from '@/api/flakes';
import { EmptyState, Eyebrow, PageHeader, Pill } from '@/components/redesign/primitives';
import { BarStrip, Legend } from '@/components/reports/charts';
import { MetricTile, WindowSelect } from '@/components/reports/ReportControls';
import { errorDetail } from '@/components/flakes/flakeHelpers';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ImpactChart } from '@/components/accessibility/ImpactChart';
import {
    IMPACT_COLOUR, IMPACT_TONE, MAX_WAIVER_DAYS, validateWaiver, waiverDraftFor, waiverLabel,
} from '@/components/accessibility/a11yModel';

const NEW = '#E0603A';
const FIXED = '#3BA776';

function Card({ title, children, right }: { title: string; children: ReactNode; right?: ReactNode }) {
    return (
        <section aria-label={title} className="flex min-w-0 flex-col gap-3 rounded-3xl border border-border bg-white p-6">
            <Eyebrow right={right}>{title}</Eyebrow>
            {children}
        </section>
    );
}

function WaiverForm({ projectId, draft, onDone }: { projectId: number; draft: WaiverInput; onDone: () => void }) {
    const qc = useQueryClient();
    const [form, setForm] = useState<WaiverInput>(draft);
    const [ownerId, setOwnerId] = useState<string>('');
    const owners = useQuery({
        queryKey: ['flake-owner-candidates', projectId],
        queryFn: () => flakesApi.ownerCandidates(projectId),
    });
    const problem = validateWaiver(form);
    const save = useMutation({
        mutationFn: () => accessibilityApi.createWaiver(projectId, {
            ...form, rule_id: form.rule_id.trim().toLowerCase(),
            owner_user_id: ownerId ? Number(ownerId) : undefined,
        }),
        onSuccess: () => {
            toast.success('Waiver added');
            qc.invalidateQueries({ queryKey: ['a11y-waivers', projectId] });
            qc.invalidateQueries({ queryKey: ['a11y-trend', projectId] });
            onDone();
        },
        onError: (err: unknown) => toast.error(`Could not add the waiver: ${errorDetail(err)}`),
    });
    const field = 'flex flex-col gap-1 text-xs font-semibold text-ink-soft';
    return (
        <form aria-label="New waiver" className="flex flex-col gap-3 rounded-2xl border border-border bg-background p-4"
            onSubmit={(e) => { e.preventDefault(); if (!problem) save.mutate(); }}>
            <label className={field}>Rule
                <Input value={form.rule_id} onChange={(e) => setForm({ ...form, rule_id: e.target.value })} placeholder="color-contrast" />
            </label>
            <label className={field}>Selector pattern (* matches anything)
                <Input value={form.selector_pattern ?? ''} onChange={(e) => setForm({ ...form, selector_pattern: e.target.value })} placeholder="#footer *" />
            </label>
            <label className={field}>Reason
                <Input value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} placeholder="Vendor widget, fix scheduled" />
            </label>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <label className={field}>Length (days, max {MAX_WAIVER_DAYS})
                    <Input type="number" min={1} max={MAX_WAIVER_DAYS} value={form.days ?? 30}
                        onChange={(e) => setForm({ ...form, days: parseInt(e.target.value) || 0 })} />
                </label>
                <label className={field}>Owner
                    <select aria-label="Owner" className="h-10 rounded-xl border border-border bg-white px-2 text-sm text-ink"
                        value={ownerId} onChange={(e) => setOwnerId(e.target.value)}>
                        <option value="">Me</option>
                        {(owners.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.full_name || p.email}</option>)}
                    </select>
                </label>
            </div>
            {problem && <p className="text-xs text-ink-soft">{problem}</p>}
            <div className="flex gap-2">
                <Button type="submit" disabled={!!problem || save.isPending}>Add waiver</Button>
                <Button type="button" variant="outline" onClick={onDone}>Cancel</Button>
            </div>
        </form>
    );
}

function WaiverRow({ w, projectId }: { w: A11yWaiver; projectId: number }) {
    const qc = useQueryClient();
    const refresh = () => {
        qc.invalidateQueries({ queryKey: ['a11y-waivers', projectId] });
        qc.invalidateQueries({ queryKey: ['a11y-trend', projectId] });
    };
    const renew = useMutation({
        mutationFn: () => accessibilityApi.renewWaiver(w.id, 30),
        onSuccess: () => { toast.success('Waiver renewed for 30 days'); refresh(); },
        onError: (err: unknown) => toast.error(`Could not renew: ${errorDetail(err)}`),
    });
    const remove = useMutation({
        mutationFn: () => accessibilityApi.deleteWaiver(w.id),
        onSuccess: () => { toast.success('Waiver removed'); refresh(); },
        onError: (err: unknown) => toast.error(`Could not remove: ${errorDetail(err)}`),
    });
    return (
        <li className="flex flex-wrap items-start justify-between gap-3 border-t border-[#F0EEE8] py-3 first:border-t-0">
            <div className="flex min-w-0 flex-col gap-1">
                <span className="font-mono text-sm text-ink">{w.rule_id} <span className="text-ink-soft">on</span> {w.selector_pattern}</span>
                <span className="text-xs text-ink-soft">{w.reason} · owner {w.owner?.full_name || w.owner?.email || 'none'}</span>
            </div>
            <div className="flex items-center gap-2">
                <Pill tone={w.state === 'expired' ? 'peach' : 'mint'}>{waiverLabel(w)}</Pill>
                <Button size="sm" variant="outline" onClick={() => renew.mutate()} disabled={renew.isPending}>Renew 30 days</Button>
                <Button size="sm" variant="outline" onClick={() => remove.mutate()} disabled={remove.isPending}>Remove</Button>
            </div>
        </li>
    );
}

function ViolationTable({ items, onWaive }: { items: A11yViolation[]; onWaive: (v: A11yViolation) => void }) {
    if (items.length === 0) return <p className="text-sm text-ink-soft">No open violations in the latest scans.</p>;
    return (
        <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
                <thead className="text-xs uppercase tracking-[0.04em] text-ink-soft">
                    <tr><th className="py-2 pr-3">Impact</th><th className="py-2 pr-3">Rule</th><th className="py-2 pr-3">Target</th>
                        <th className="py-2 pr-3">Page · case</th><th className="py-2 pr-3">Nodes</th><th className="py-2">Status</th></tr>
                </thead>
                <tbody>
                    {items.map((v) => (
                        <tr key={v.id} className="border-t border-[#F0EEE8] align-top">
                            <td className="py-2 pr-3"><Pill tone={IMPACT_TONE[v.impact]}>{v.impact}</Pill></td>
                            <td className="py-2 pr-3 font-mono">
                                {v.help_url ? <a className="underline" href={v.help_url} target="_blank" rel="noreferrer noopener">{v.rule_id}</a> : v.rule_id}
                            </td>
                            <td className="max-w-[280px] break-all py-2 pr-3 font-mono text-xs">{v.target || '—'}</td>
                            <td className="max-w-[240px] break-all py-2 pr-3 text-xs text-ink-soft">{v.page_key || '—'} · {v.test_name || 'case'}</td>
                            <td className="py-2 pr-3 tabular-nums">{v.node_count}</td>
                            <td className="py-2">
                                <div className="flex flex-wrap items-center gap-2">
                                    {v.new && <Pill tone="peach">new</Pill>}
                                    {v.waived ? <Pill tone="mint">waived</Pill>
                                        : <Button size="sm" variant="outline" onClick={() => onWaive(v)}>Waive</Button>}
                                </div>
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

export default function Accessibility() {
    const projectId = useActiveProjectId();
    const [days, setDays] = useState(30);
    const [draft, setDraft] = useState<WaiverInput | null>(null);
    const trend = useQuery({
        queryKey: ['a11y-trend', projectId, days],
        queryFn: () => accessibilityApi.trend(projectId!, days),
        enabled: !!projectId,
    });
    const waivers = useQuery({
        queryKey: ['a11y-waivers', projectId],
        queryFn: () => accessibilityApi.waivers(projectId!),
        enabled: !!projectId,
    });
    const t = trend.data;

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Accessibility"
                subtitle="Every check-accessibility step's outcome: rule, impact, element selector and node count — never page text. Known issues can be waived for up to 90 days."
                toolbar={projectId ? <WindowSelect id="a11y-days" label="Window" value={days} options={[7, 30, 90, 180]} onChange={setDays} /> : undefined}
            />
            {!projectId ? (
                <EmptyState title="Pick a project">Choose a project in the sidebar to see its accessibility register.</EmptyState>
            ) : trend.isLoading ? (
                <div className="h-[200px] animate-pulse rounded-3xl bg-white" aria-busy="true" aria-label="Loading" />
            ) : trend.error ? (
                <EmptyState title="Could not load">{errorDetail(trend.error)}</EmptyState>
            ) : t && t.checks === 0 ? (
                <EmptyState title="No accessibility scans yet">
                    Add a check-accessibility step to a case; each run's results appear here.
                </EmptyState>
            ) : t && (
                <>
                    <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
                        {IMPACTS.map((i) => (
                            <MetricTile key={i} label={i} value={t.totals[i]}>open in the latest scans</MetricTile>
                        ))}
                        <MetricTile label="Waived" value={t.totals.waived}>{t.totals.new} new since the previous run</MetricTile>
                    </div>
                    <Card title="Violations by impact" right={<Legend items={IMPACTS.map((i) => ({ colour: IMPACT_COLOUR[i], label: i }))} />}>
                        <ImpactChart series={t.series} />
                        <p className="text-xs text-ink-soft">{t.runs} runs, {t.checks} scans in the last {t.days} days.</p>
                    </Card>
                    <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
                        <Card title="New per day" right={<Legend items={[{ colour: NEW, label: 'New' }]} />}>
                            <BarStrip label="New violations per day" values={t.series.map((d) => d.new)} colour={NEW} />
                        </Card>
                        <Card title="Fixed per day" right={<Legend items={[{ colour: FIXED, label: 'Fixed' }]} />}>
                            <BarStrip label="Fixed violations per day" values={t.series.map((d) => d.fixed)} colour={FIXED} />
                        </Card>
                    </div>
                    <Card title="Open violations">
                        {draft && <WaiverForm key={`${draft.rule_id}|${draft.selector_pattern}`} projectId={projectId} draft={draft} onDone={() => setDraft(null)} />}
                        <ViolationTable items={t.open} onWaive={(v) => setDraft(waiverDraftFor(v))} />
                    </Card>
                </>
            )}
            {projectId && (
                <Card title="Waivers" right={!draft ? (
                    <Button size="sm" variant="outline" onClick={() => setDraft({ rule_id: '', selector_pattern: '*', reason: '', days: 30 })}>New waiver</Button>
                ) : undefined}>
                    <p className="text-xs text-ink-soft">
                        A waiver excludes matching violations from the "no new serious violations" gate check until it expires. Expired waivers count again and are never renewed automatically.
                    </p>
                    {draft && t?.checks === 0 && <WaiverForm projectId={projectId} draft={draft} onDone={() => setDraft(null)} />}
                    {waivers.isLoading ? <p className="text-sm text-ink-soft">Loading…</p>
                        : (waivers.data ?? []).length === 0 ? <p className="text-sm text-ink-soft">No waivers.</p>
                            : <ul aria-label="Waivers">{waivers.data!.map((w) => <WaiverRow key={w.id} w={w} projectId={projectId} />)}</ul>}
                </Card>
            )}
        </div>
    );
}

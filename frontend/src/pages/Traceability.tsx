import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ExternalLink } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { toast } from 'sonner';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { traceApi, linksForRef, RequirementCoverage, type RefLink } from '@/api/traceability';
import { useProjectAccess } from '@/hooks/useProjectAccess';
import {
    DetailCard, EmptyState, Eyebrow, KindChip, ListGroup, ListRow, PageHeader, Pill, SplitView,
} from '@/components/redesign/primitives';
import type { Kind, Tone } from '@/components/redesign/kinds';
import { SkeletonRows } from '@/components/redesign-local/groupB/bits';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const errDetail = (e: any): string => e?.response?.data?.detail || e?.message || 'Unknown error';

const STATUS: Record<RequirementCoverage['status'], { kind: Kind; tone: Tone; label: string; group: string }> = {
    failing: { kind: 'failure', tone: 'peach', label: 'Failing', group: 'Failing' },
    mixed: { kind: 'flaky', tone: 'butter', label: 'Mixed', group: 'Mixed results' },
    passing: { kind: 'pass', tone: 'mint', label: 'Passing', group: 'Passing' },
    unknown: { kind: 'skip', tone: 'neutral', label: 'Not run yet', group: 'Not run yet' },
};
const look = (s: string) => STATUS[s as RequirementCoverage['status']] ?? STATUS.unknown;

function Count({ label, value }: { label: string; value: number }) {
    return (
        <div className="flex flex-col gap-1 rounded-2xl bg-background p-4">
            <span className="text-xs text-ink-soft">{label}</span>
            <span className="text-2xl font-bold tracking-tight text-ink tabular-nums">{value}</span>
        </div>
    );
}

function RequirementDetail({ c, projectId, canEdit }: { c: RequirementCoverage; projectId: number; canEdit: boolean }) {
    const S = look(c.status);
    const qc = useQueryClient();
    // Only editors can unlink, so only they pay for resolving the link ids.
    const links = useQuery({
        queryKey: ['ref-links', projectId, c.ref, c.test_names],
        queryFn: () => linksForRef(projectId, c.ref, c.test_names),
        enabled: canEdit && c.test_names.length > 0,
    });
    const unlink = useMutation({
        mutationFn: (l: RefLink) => traceApi.removeLink(l.caseId, l.linkId),
        onSuccess: () => {
            toast.success('Link removed');
            qc.invalidateQueries({ queryKey: ['requirements', projectId] });
            qc.invalidateQueries({ queryKey: ['trace-gaps', projectId] });
            qc.invalidateQueries({ queryKey: ['ref-links', projectId, c.ref] });
        },
        onError: (e) => toast.error(errDetail(e)),
    });
    return (
        <DetailCard label="Selected requirement">
            <div className="flex flex-col gap-2">
                <Pill tone={S.tone} className="self-start">{S.label}</Pill>
                <h2 className="text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">
                    {c.url ? (
                        <a href={c.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-2 hover:underline">
                            {c.ref} <ExternalLink className="h-4 w-4 text-ink-soft" aria-label="opens in a new tab" />
                        </a>
                    ) : c.ref}
                </h2>
                {c.title && <span className="text-sm text-ink-soft">{c.title}</span>}
            </div>
            <div className="grid grid-cols-[repeat(auto-fit,minmax(120px,1fr))] gap-3">
                <Count label="Tests" value={c.test_count} />
                <Count label="Passing" value={c.passing} />
                <Count label="Failing" value={c.failing} />
                <Count label="Not run yet" value={c.untested} />
            </div>
            <div className="flex flex-col gap-2">
                <Eyebrow>Covered by</Eyebrow>
                {c.test_names.length === 0 ? (
                    <p className="text-sm text-ink-soft">No tests linked.</p>
                ) : canEdit && links.data ? (
                    <ul className="overflow-hidden rounded-2xl border border-border">
                        {links.data.map((l, i) => (
                            <li key={l.linkId} className={`flex min-h-12 items-center gap-3 px-4 py-2 ${i > 0 ? 'border-t border-[#F0EEE8]' : ''}`}>
                                <span className="min-w-0 flex-1 truncate text-sm text-ink">{l.caseName}</span>
                                <Button size="sm" variant="outline" disabled={unlink.isPending}
                                    onClick={() => { if (window.confirm(`Remove the link between "${l.caseName}" and ${c.ref}?`)) unlink.mutate(l); }}>
                                    Remove link
                                </Button>
                            </li>
                        ))}
                    </ul>
                ) : (
                    <ul className="flex flex-wrap gap-1.5">
                        {c.test_names.map((n) => (
                            <li key={n} className="rounded-full border border-border px-3 py-1 text-[13px] text-ink">{n}</li>
                        ))}
                    </ul>
                )}
            </div>
        </DetailCard>
    );
}

export default function Traceability() {
    const qc = useQueryClient();
    const projectId = useActiveProjectId();
    const [selectedRef, setSelectedRef] = useState<string | null>(null);
    const { canEdit } = useProjectAccess(projectId);

    const reqs = useQuery({ queryKey: ['requirements', projectId], queryFn: () => traceApi.requirements(projectId!), enabled: !!projectId });
    const gaps = useQuery({ queryKey: ['trace-gaps', projectId], queryFn: () => traceApi.gaps(projectId!), enabled: !!projectId });

    const [linkRefs, setLinkRefs] = useState<Record<number, string>>({});
    const link = useMutation({
        mutationFn: ({ caseId, ref }: { caseId: number; ref: string }) => traceApi.addLink(caseId, { ref }),
        onSuccess: () => { toast.success('Requirement linked'); qc.invalidateQueries({ queryKey: ['requirements', projectId] }); qc.invalidateQueries({ queryKey: ['trace-gaps', projectId] }); },
        onError: (e) => toast.error(errDetail(e)),
    });

    const tracedPct = gaps.data && gaps.data.total_cases ? Math.round((gaps.data.traced_cases / gaps.data.total_cases) * 100) : 0;

    const list = reqs.data ?? [];
    const groups = (['failing', 'mixed', 'passing', 'unknown'] as const)
        .map((s) => ({ name: STATUS[s].group, items: list.filter((c) => (STATUS[c.status] ? c.status : 'unknown') === s) }))
        .filter((g) => g.items.length > 0);
    const selected = list.find((c) => c.ref === selectedRef) ?? groups[0]?.items[0] ?? null;

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Traceability"
                subtitle={<>Link tests to requirements or tickets (like <code className="rounded bg-secondary px-1 text-[13px]">JIRA-123</code>) to see what is covered, passing, or untested.</>}
            />

            {!projectId ? (
                <EmptyState title="Pick a project">Choose a project in the sidebar to see its requirements.</EmptyState>
            ) : (
                <>
                    {gaps.data && (
                        <div className="flex flex-col gap-2 rounded-2xl border border-border bg-white px-5 py-4">
                            <div className="flex flex-wrap items-baseline justify-between gap-2">
                                <span className="text-[13px] text-ink-soft">Tests linked to a requirement</span>
                                <span className="text-sm text-ink-soft tabular-nums">{gaps.data.traced_cases} of {gaps.data.total_cases}</span>
                            </div>
                            <span className="text-[26px] font-bold leading-tight tracking-tight text-ink tabular-nums">{tracedPct}%</span>
                            <div
                                className="h-2 overflow-hidden rounded-full bg-secondary"
                                role="progressbar" aria-valuenow={tracedPct} aria-valuemin={0} aria-valuemax={100} aria-label="Share of tests linked"
                            >
                                <div className="h-full rounded-full bg-ink" style={{ width: `${Math.max(2, tracedPct)}%` }} />
                            </div>
                        </div>
                    )}

                    {reqs.isLoading ? (
                        <SkeletonRows count={3} className="max-w-[440px]" />
                    ) : reqs.error ? (
                        <EmptyState title="Couldn't load requirements">{errDetail(reqs.error)}</EmptyState>
                    ) : list.length === 0 ? (
                        <EmptyState title="No requirements linked yet">Link some untraced tests below.</EmptyState>
                    ) : (
                        <SplitView
                            list={groups.map((g) => (
                                <ListGroup key={g.name} title={g.name} count={g.items.length}>
                                    {g.items.map((c) => (
                                        <ListRow
                                            key={c.ref}
                                            selected={selected?.ref === c.ref}
                                            onSelect={() => setSelectedRef(c.ref)}
                                            leading={<KindChip kind={look(c.status).kind} size="sm" />}
                                            title={<>{c.ref}{c.title && <span className="font-normal text-ink-soft"> — {c.title}</span>}</>}
                                            meta={`${c.test_count} test${c.test_count === 1 ? '' : 's'} · ${c.passing} passing · ${c.failing} failing · ${c.untested} untested`}
                                        />
                                    ))}
                                </ListGroup>
                            ))}
                            detail={selected && projectId && <RequirementDetail key={selected.ref} c={selected} projectId={projectId} canEdit={canEdit} />}
                        />
                    )}

                    {gaps.data && gaps.data.untraced_cases.length > 0 && (
                        <section className="flex flex-col gap-3 rounded-3xl border border-border bg-white p-6">
                            <div className="flex flex-col gap-1">
                                <h2 className="text-base font-semibold text-ink">Untraced tests · {gaps.data.untraced_cases.length}</h2>
                                <p className="text-[13px] text-ink-soft">Type a requirement or ticket reference next to a test and link it.</p>
                            </div>
                            <ul className="max-h-[420px] overflow-y-auto">
                                {gaps.data.untraced_cases.slice(0, 200).map((c, i) => (
                                    <li key={c.id} className={`flex min-h-12 flex-wrap items-center gap-2 py-2 ${i > 0 ? 'border-t border-[#F0EEE8]' : ''}`}>
                                        <span className="min-w-0 flex-1 truncate text-sm text-ink">{c.name}</span>
                                        <Input
                                            value={linkRefs[c.id] || ''}
                                            onChange={(e) => setLinkRefs((m) => ({ ...m, [c.id]: e.target.value }))}
                                            placeholder="REQ / ticket ref"
                                            aria-label={`Requirement reference for ${c.name}`}
                                            className="h-10 w-44 rounded-full"
                                        />
                                        <Button
                                            variant="outline"
                                            disabled={!(linkRefs[c.id] || '').trim() || link.isPending}
                                            onClick={() => link.mutate({ caseId: c.id, ref: (linkRefs[c.id] || '').trim() })}
                                        >
                                            Link
                                        </Button>
                                    </li>
                                ))}
                            </ul>
                            {gaps.data.untraced_cases.length > 200 && <p className="text-xs text-ink-soft">…and {gaps.data.untraced_cases.length - 200} more</p>}
                        </section>
                    )}
                </>
            )}
        </div>
    );
}

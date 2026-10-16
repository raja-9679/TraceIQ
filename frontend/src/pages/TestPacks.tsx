import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { EmptyState, Eyebrow, PageHeader, Pill } from '@/components/redesign/primitives';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { packsApi, type PackSummary } from '@/api/solution';
import PackImportDialog from '@/components/test-packs/PackImportDialog';

function PackCard({ pack, onImport }: { pack: PackSummary; onImport: () => void }) {
    const [open, setOpen] = useState(false);
    const secrets = pack.variables.filter((v) => v.secret).length;
    return (
        <article aria-label={pack.title} className="flex min-w-0 flex-col gap-4 rounded-3xl border border-border bg-white p-6">
            <div className="flex flex-col gap-2">
                <div className="flex flex-wrap items-center gap-2">
                    <h2 className="text-lg font-semibold text-ink">{pack.title}</h2>
                    <Pill tone="neutral">v{pack.version}</Pill>
                    {pack.category && <Pill tone="mint">{pack.category}</Pill>}
                </div>
                <p className="text-[13px] leading-relaxed text-ink-soft">{pack.description}</p>
            </div>
            <p className="text-xs text-ink-soft">
                {pack.case_count} cases · {pack.variables.length} variables{secrets ? ` (${secrets} secret)` : ''}
            </p>
            <div className="flex flex-col gap-2">
                <Button variant="outline" size="sm" className="self-start" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
                    {open ? 'Hide cases' : 'Show cases'}
                </Button>
                {open && pack.suites.map((s) => (
                    <div key={s.name} className="flex flex-col gap-2">
                        <Eyebrow>{s.name}</Eyebrow>
                        <ul className="flex flex-col divide-y divide-[#F0EEE8] rounded-2xl border border-border">
                            {s.cases.map((c) => (
                                <li key={c.name} className="flex flex-col gap-1 px-4 py-3">
                                    <span className="text-sm font-medium text-ink">{c.name}</span>
                                    {c.description && <span className="text-xs leading-relaxed text-ink-soft">{c.description}</span>}
                                    <span className="font-mono text-[11px] text-ink-soft">{c.step_types.join(' → ')}</span>
                                </li>
                            ))}
                        </ul>
                    </div>
                ))}
            </div>
            <div className="mt-auto border-t border-border pt-4">
                <Button onClick={onImport}>Import into a project</Button>
            </div>
        </article>
    );
}

/** Gallery of the versioned integration test packs shipped with TraceIQ (decision C7). */
export default function TestPacks() {
    const packs = useQuery({ queryKey: ['test-packs'], queryFn: packsApi.list });
    const activeProjectId = useActiveProjectId();
    const [importing, setImporting] = useState<PackSummary | null>(null);

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Test packs"
                subtitle="Ready-made suites for common integrations — SSO, SCIM provisioning, outbound webhooks. Import one, fill its variables, then run it against your own system."
            />
            {packs.isLoading && <div className="h-40 animate-pulse rounded-3xl bg-white/70" aria-busy="true" aria-label="Loading" />}
            {packs.isError && <EmptyState title="Test packs could not be loaded" />}
            {packs.data && packs.data.length === 0 && <EmptyState title="No test packs are installed" />}
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2 2xl:grid-cols-3">
                {(packs.data || []).map((p) => <PackCard key={p.id} pack={p} onImport={() => setImporting(p)} />)}
            </div>
            {importing && (
                <PackImportDialog key={importing.id} pack={importing} open onOpenChange={(o) => { if (!o) setImporting(null); }}
                    defaultProjectId={activeProjectId} />
            )}
        </div>
    );
}

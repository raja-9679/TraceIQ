import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Download, Loader2 } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Eyebrow, Pill } from '@/components/redesign/primitives';
import {
    defaultDiffPair, getSpecDiff, getSpecDiffSarif, groupByOperation, listSpecVersions, ruleLabel,
    type SpecChange, type SpecVersion,
} from '@/api/openapiSpec';
import { shortDate } from '@/components/ownership/ownershipHelpers';

/**
 * Version history of the project's OpenAPI spec and a breaking-change diff
 * between any two versions (WP16b). Breaking changes first, each operation
 * naming the tests whose expect_schema references it.
 */

interface ApiError extends Error {
    response?: { data?: { detail?: string } };
}
const detailOf = (err: unknown) =>
    (err as ApiError).response?.data?.detail || (err as Error).message || 'Unknown error';

const versionLabel = (v: SpecVersion) =>
    `v${v.version} · ${shortDate(v.created_at)}${v.spec_version ? ` · ${v.spec_version}` : ''}${v.is_current ? ' (current)' : ''}`;

function ChangeRow({ change }: { change: SpecChange }) {
    return (
        <li className="flex flex-col gap-0.5 px-4 py-2.5 text-sm">
            <span className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-ink">{ruleLabel(change.rule)}</span>
                {change.status && <span className="font-mono text-xs text-ink-soft">{change.status}</span>}
            </span>
            <span className="text-ink-soft">{change.message}</span>
            {change.promoted_reason && <span className="text-xs text-pastel-ink-peach">Breaking because {change.promoted_reason}.</span>}
            <span className="break-all font-mono text-[11px] text-ink-soft">{change.location}</span>
        </li>
    );
}

export function OpenApiSpecHistory({ projectId }: { projectId: number }) {
    const versions = useQuery({ queryKey: ['openapi-spec-versions', projectId], queryFn: () => listSpecVersions(projectId) });
    const list = versions.data?.versions ?? [];
    const [pair, setPair] = useState<{ from: number; to: number } | null>(null);
    const [showCompatible, setShowCompatible] = useState(false);

    // Follow the newest pair until the person picks their own (and fall back
    // to it if a picked version has been pruned since).
    const has = (n: number) => list.some((v) => v.version === n);
    const chosen = pair && has(pair.from) && has(pair.to) ? pair : defaultDiffPair(list);

    const diff = useQuery({
        queryKey: ['openapi-spec-diff', projectId, chosen?.from, chosen?.to],
        queryFn: () => getSpecDiff(projectId, chosen!.from, chosen!.to),
        enabled: !!chosen && chosen.from !== chosen.to,
    });

    const download = async () => {
        if (!chosen) return;
        try {
            const blob = await getSpecDiffSarif(projectId, chosen.from, chosen.to);
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `openapi-diff-v${chosen.from}-v${chosen.to}.sarif`;
            a.click();
            URL.revokeObjectURL(url);
        } catch (err) {
            toast.error(`Could not export SARIF: ${detailOf(err)}`);
        }
    };

    if (versions.isLoading) return <Loader2 className="h-4 w-4 animate-spin" aria-label="Loading spec history" />;
    if (versions.error) return <p className="text-sm text-ink-soft">Could not load the spec history: {detailOf(versions.error)}</p>;
    if (list.length === 0) return null;

    const groups = diff.data ? groupByOperation(diff.data.changes) : [];
    const shown = showCompatible ? groups : groups.filter((g) => g.breaking.length > 0);

    return (
        <div className="flex flex-col gap-4 border-t border-border pt-4" aria-labelledby={`spec-history-${projectId}`}>
            <div className="flex flex-col gap-1">
                <h4 id={`spec-history-${projectId}`} className="text-sm font-semibold text-ink">Version history</h4>
                <p className="text-sm text-ink-soft">
                    Each saved spec is kept as a version ({list.length} of at most {versions.data?.max_versions}).
                    Compare two to see what changed for API clients and which tests it affects.
                </p>
            </div>

            {list.length < 2 ? (
                <p className="text-sm text-ink-soft">Only one version so far. Save a changed spec to compare.</p>
            ) : (
                <>
                    <div className="flex flex-wrap items-end gap-3">
                        {(['from', 'to'] as const).map((side) => (
                            <label key={side} className="flex min-w-[200px] flex-1 flex-col gap-1 text-xs font-semibold text-ink-soft">
                                {side === 'from' ? 'Compare from (older)' : 'To (newer)'}
                                <select
                                    className="h-10 rounded-xl border border-border bg-white px-3 text-sm font-normal text-ink"
                                    value={chosen?.[side] ?? ''}
                                    onChange={(e) => setPair({ ...(chosen ?? { from: 0, to: 0 }), [side]: Number(e.target.value) })}
                                >
                                    {list.map((v) => <option key={v.version} value={v.version}>{versionLabel(v)}</option>)}
                                </select>
                            </label>
                        ))}
                        <Button type="button" variant="outline" onClick={download} disabled={!chosen || chosen.from === chosen.to}>
                            <Download className="mr-1 h-4 w-4" /> Export SARIF
                        </Button>
                    </div>

                    {chosen && chosen.from === chosen.to ? (
                        <p className="text-sm text-ink-soft">Pick two different versions.</p>
                    ) : diff.isLoading ? (
                        <Loader2 className="h-4 w-4 animate-spin" aria-label="Comparing versions" />
                    ) : diff.error ? (
                        <p className="text-sm text-ink-soft">Could not compare: {detailOf(diff.error)}</p>
                    ) : diff.data && (
                        <div className="flex flex-col gap-3">
                            <div className="flex flex-wrap items-center gap-2">
                                <Pill tone={diff.data.breaking_count ? 'peach' : 'mint'}>
                                    {diff.data.breaking_count} breaking
                                </Pill>
                                <Pill tone="neutral">{diff.data.non_breaking_count} compatible</Pill>
                                {diff.data.affected_tests.length > 0 && (
                                    <Pill tone="lavender">{diff.data.affected_tests.length} test{diff.data.affected_tests.length === 1 ? '' : 's'} affected</Pill>
                                )}
                                {diff.data.truncated && <Pill tone="butter">Diff truncated — spec too large to compare fully</Pill>}
                                <label className="ml-auto flex items-center gap-2 text-xs text-ink-soft">
                                    <input type="checkbox" checked={showCompatible} onChange={(e) => setShowCompatible(e.target.checked)} />
                                    Show compatible changes
                                </label>
                            </div>

                            {diff.data.changes.length === 0 ? (
                                <p className="text-sm text-ink-soft">No differences between these versions.</p>
                            ) : shown.length === 0 ? (
                                <p className="text-sm text-ink-soft">No breaking changes. {diff.data.non_breaking_count} compatible change{diff.data.non_breaking_count === 1 ? '' : 's'} hidden.</p>
                            ) : (
                                <ul className="flex flex-col gap-3">
                                    {shown.map((g) => (
                                        <li key={g.key} className="overflow-hidden rounded-2xl border border-border">
                                            <div className="flex flex-wrap items-center gap-2 border-b border-[#F0EEE8] bg-background px-4 py-2.5">
                                                <span className="font-mono text-sm font-semibold text-ink">{g.method}</span>
                                                <span className="break-all font-mono text-sm text-ink">{g.path}</span>
                                                {g.breaking.length > 0 && <Pill tone="peach">{g.breaking.length} breaking</Pill>}
                                            </div>
                                            <ul className="divide-y divide-[#F0EEE8]">
                                                {g.breaking.map((c) => <ChangeRow key={c.id} change={c} />)}
                                                {showCompatible && g.nonBreaking.map((c) => <ChangeRow key={c.id} change={c} />)}
                                            </ul>
                                            {g.cases.length > 0 && (
                                                <div className="flex flex-col gap-1 border-t border-[#F0EEE8] px-4 py-2.5">
                                                    <Eyebrow>Tests that check this operation</Eyebrow>
                                                    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
                                                        {g.cases.map((t) => (
                                                            <li key={t.case_id}>
                                                                {t.test_suite_id ? (
                                                                    <Link className="underline underline-offset-2" to={`/suites/${t.test_suite_id}/cases/${t.case_id}/edit`}>
                                                                        {t.name || `Case #${t.case_id}`}
                                                                    </Link>
                                                                ) : (t.name || `Case #${t.case_id}`)}
                                                            </li>
                                                        ))}
                                                    </ul>
                                                </div>
                                            )}
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </div>
                    )}
                </>
            )}
        </div>
    );
}

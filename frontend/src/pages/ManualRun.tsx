import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { errorDetail } from '@/api/idpCatalog';
import { Button } from '@/components/ui/button';
import { EmptyState, ListCard, ListRow, PageHeader, Pill, SplitView } from '@/components/redesign/primitives';
import { manualTestingApi, ManualResult, ManualRun as ManualRunT } from '@/api/manualTesting';
import ManualCaseRecorder from '@/components/manual-testing/ManualCaseRecorder';
import { outcomeMeta, summaryLine } from '@/components/manual-testing/manualSteps';

/** Tester execution screen: work through a manual run case by case. */
export default function ManualRun() {
    const { runId } = useParams();
    const id = Number(runId);
    const queryClient = useQueryClient();
    const [selected, setSelected] = useState<number | null>(null);

    const run = useQuery({ queryKey: ['manual-run', id], queryFn: () => manualTestingApi.get(id), enabled: !!id });

    const complete = useMutation({
        mutationFn: (skip: boolean) => manualTestingApi.complete(id, skip),
        onSuccess: (r) => {
            queryClient.setQueryData(['manual-run', id], r);
            toast.success(`Manual run completed: ${r.status}`);
        },
        onError: (err) => toast.error(errorDetail(err, 'Could not complete the run')),
    });

    if (run.isLoading) {
        return <p className="flex items-center gap-2 text-sm text-ink-soft"><Loader2 className="h-4 w-4 animate-spin" /> Loading the manual run…</p>;
    }
    if (run.isError || !run.data) {
        return <EmptyState title="Manual run not available">It does not exist, or you do not have access to its project.</EmptyState>;
    }

    const r = run.data;
    const open = r.status === 'pending' || r.status === 'running';
    const current: ManualResult | undefined = r.results.find((x) => x.id === selected)
        ?? r.results.find((x) => !x.outcome) ?? r.results[0];
    const onSaved = (res: ManualResult) => {
        queryClient.setQueryData<ManualRunT>(['manual-run', id], (old) => old && {
            ...old,
            results: old.results.map((x) => (x.id === res.id ? res : x)),
        });
        queryClient.invalidateQueries({ queryKey: ['manual-run', id] });
        // Move on to the next case still to do.
        const next = r.results.find((x) => x.id !== res.id && !x.outcome);
        if (next) setSelected(next.id);
    };

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title={r.suite_name || `Manual run #${r.id}`}
                badge={<><Pill tone="lavender">Manual</Pill><Pill tone={open ? 'neutral' : r.status === 'passed' ? 'mint' : 'peach'}>{open ? 'In progress' : r.status}</Pill></>}
                crumbs={<><Link to="/runs" className="hover:underline">Runs</Link><span aria-hidden="true">/</span><span>#{r.id}</span></>}
                subtitle={<>
                    {summaryLine(r.summary)}
                    {r.release_label && <> · release <strong className="text-ink">{r.release_label}</strong></>}
                    {r.user_name && <> · started by {r.user_name}</>}
                </>}
                actions={
                    <>
                        <Button variant="outline" asChild><Link to="/release-signoff">Release sign-off</Link></Button>
                        {open && (
                            <Button onClick={() => complete.mutate(r.summary.unrecorded > 0)} disabled={complete.isPending}>
                                {complete.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                                {r.summary.unrecorded > 0 ? `Complete run (${r.summary.unrecorded} left as skipped)` : 'Complete run'}
                            </Button>
                        )}
                    </>
                }
            />
            {!open && (
                <p className="rounded-2xl border border-border bg-white px-4 py-3 text-sm text-ink-soft">
                    This run is completed and read-only. Its results appear in run history and the quality gate like any other run.
                </p>
            )}
            <SplitView
                list={
                    <ListCard title="Cases" count={r.results.length}>
                        {r.results.map((x) => {
                            const m = outcomeMeta(x.outcome);
                            return (
                                <ListRow
                                    key={x.id}
                                    selected={current?.id === x.id}
                                    onSelect={() => setSelected(x.id)}
                                    title={x.test_name}
                                    meta={`${x.steps.length} step${x.steps.length === 1 ? '' : 's'}${x.tester_name ? ` · ${x.tester_name}` : ''}`}
                                    trailing={<Pill tone={m.tone}>{m.label}</Pill>}
                                />
                            );
                        })}
                    </ListCard>
                }
                detail={current
                    ? <ManualCaseRecorder key={`${current.id}-${current.recorded_at ?? ''}-${current.attachments.length}-${open}`} runId={r.id} result={current} readOnly={!open} onSaved={onSaved} />
                    : <EmptyState title="No cases in this run" />}
            />
        </div>
    );
}

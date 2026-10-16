import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, ShieldCheck } from 'lucide-react';
import { toast } from 'sonner';
import { errorDetail } from '@/api/idpCatalog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { getProjects } from '@/lib/api';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { EmptyState, PageHeader, Pill } from '@/components/redesign/primitives';
import { FieldRow, SettingsCard, checkboxClass, inputClass, textareaClass } from '@/components/redesign-local/groupD/settingsForm';
import { cn } from '@/lib/utils';
import { manualTestingApi, SignoffVerdict } from '@/api/manualTesting';

/** Sign-off panel: pick the manual runs a release was tested with, approve or
 * reject it. The server enforces editor access and, when the workspace asks
 * for it, that the signer did not execute those runs. */
export default function ReleaseSignoff() {
    const projectId = useActiveProjectId();
    const queryClient = useQueryClient();
    const projects = useQuery({ queryKey: ['projects'], queryFn: () => getProjects() });
    const project = projects.data?.find((p) => p.id === projectId);

    const runs = useQuery({
        queryKey: ['manual-runs', projectId],
        queryFn: () => manualTestingApi.list(projectId!),
        enabled: !!projectId,
    });
    const signoffs = useQuery({
        queryKey: ['signoffs', projectId],
        queryFn: () => manualTestingApi.signoffs(projectId!),
        enabled: !!projectId,
    });

    const [picked, setPicked] = useState<number[]>([]);
    const [label, setLabel] = useState('');
    const [verdict, setVerdict] = useState<SignoffVerdict>('approve');
    const [note, setNote] = useState('');

    const sign = useMutation({
        mutationFn: () => manualTestingApi.signOff(projectId!, {
            release_label: label.trim(), run_ids: picked, verdict, note: note.trim() || undefined,
        }),
        onSuccess: (s) => {
            toast.success(`${s.verdict === 'approve' ? 'Approved' : 'Rejected'} ${s.release_label}`);
            setPicked([]); setNote('');
            queryClient.invalidateQueries({ queryKey: ['signoffs', projectId] });
        },
        onError: (err) => toast.error(errorDetail(err, 'Could not record the sign-off')),
    });

    const toggle = (id: number) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
    const pickedRuns = (runs.data || []).filter((r) => picked.includes(r.id));
    const live = pickedRuns.filter((r) => r.status === 'pending' || r.status === 'running');
    const problem = !label.trim() ? 'Name the release' : picked.length === 0 ? 'Pick at least one run'
        : verdict === 'approve' && live.length ? 'Complete the selected runs before approving' : null;

    if (!projectId) {
        return (
            <div className="flex flex-col gap-5">
                <PageHeader title="Release sign-off" subtitle="Select a project in the sidebar." />
            </div>
        );
    }

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Release sign-off"
                subtitle={<>Approve or reject a release of <strong className="text-ink">{project?.name ?? 'this project'}</strong> on the evidence of its manual runs. Every sign-off is audited.</>}
            />

            <SettingsCard
                labelledBy="signoff-runs"
                title="Manual runs"
                description="Choose the runs this release was tested with. Start a manual run from a suite that has manual tests."
            >
                {runs.isLoading ? (
                    <p className="flex items-center gap-2 text-sm text-ink-soft"><Loader2 className="h-4 w-4 animate-spin" /> Loading…</p>
                ) : (runs.data || []).length === 0 ? (
                    <p className="text-sm text-ink-soft">No manual runs in this project yet.</p>
                ) : (
                    <ul className="flex flex-col divide-y divide-border">
                        {(runs.data || []).map((r) => {
                            const open = r.status === 'pending' || r.status === 'running';
                            return (
                                <li key={r.id} className="flex flex-wrap items-center gap-3 py-2">
                                    <input id={`pick-run-${r.id}`} type="checkbox" className={checkboxClass}
                                        checked={picked.includes(r.id)} onChange={() => toggle(r.id)} />
                                    <label htmlFor={`pick-run-${r.id}`} className="min-w-0 flex-1 cursor-pointer text-sm">
                                        <span className="font-medium text-ink">#{r.id} {r.suite_name}</span>
                                        <span className="block text-xs text-ink-soft">
                                            {new Date(r.created_at).toLocaleString()} · {r.passed_tests} passed, {r.failed_tests} failed or blocked of {r.total_tests}
                                            {r.release_label ? ` · ${r.release_label}` : ''}
                                        </span>
                                    </label>
                                    <Pill tone={open ? 'neutral' : r.status === 'passed' ? 'mint' : 'peach'}>{open ? 'In progress' : r.status}</Pill>
                                    <Link to={`/manual-runs/${r.id}`} className="text-sm font-medium text-ink underline underline-offset-2">
                                        {open ? 'Continue testing' : 'Open'}
                                    </Link>
                                </li>
                            );
                        })}
                    </ul>
                )}
            </SettingsCard>

            <SettingsCard
                labelledBy="signoff-form"
                title="Sign off"
                icon={<ShieldCheck className="h-4 w-4" aria-hidden="true" />}
                description="Needs editor access. When the workspace requires a separate approver, whoever started or recorded these runs cannot sign."
                footer={
                    <>
                        <Button onClick={() => sign.mutate()} disabled={!!problem || sign.isPending}>
                            {sign.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                            {verdict === 'approve' ? 'Approve release' : 'Reject release'}
                        </Button>
                        {problem && <span className="text-xs text-ink-soft" role="status">{problem}</span>}
                    </>
                }
            >
                <div className="flex flex-col divide-y divide-border">
                    <FieldRow label="Release" htmlFor="signoff-label" hint="Version or release name, e.g. 2.4.0." wide>
                        <Input id="signoff-label" className={inputClass} value={label} maxLength={120} onChange={(e) => setLabel(e.target.value)} />
                    </FieldRow>
                    <FieldRow label="Verdict">
                        <div role="radiogroup" aria-label="Verdict" className="flex gap-2">
                            {(['approve', 'reject'] as SignoffVerdict[]).map((v) => (
                                <button key={v} type="button" role="radio" aria-checked={verdict === v} onClick={() => setVerdict(v)}
                                    className={cn('h-10 rounded-full border px-4 text-sm font-medium',
                                        verdict === v ? 'border-ink bg-ink text-white' : 'border-border bg-white text-ink hover:bg-background')}>
                                    {v === 'approve' ? 'Approve' : 'Reject'}
                                </button>
                            ))}
                        </div>
                    </FieldRow>
                    <label className="flex flex-col gap-1.5 pt-3 text-sm font-medium text-ink">
                        Note
                        <textarea className={`${textareaClass} font-sans`} rows={3} value={note} onChange={(e) => setNote(e.target.value)}
                            placeholder="Known issues accepted, scope, conditions" />
                    </label>
                </div>
            </SettingsCard>

            <SettingsCard labelledBy="signoff-history" title="Sign-off history">
                {(signoffs.data || []).length === 0 ? (
                    <EmptyState title="No sign-offs yet" />
                ) : (
                    <ul className="flex flex-col divide-y divide-border">
                        {(signoffs.data || []).map((s) => (
                            <li key={s.id} className="flex flex-wrap items-start gap-3 py-3">
                                <Pill tone={s.verdict === 'approve' ? 'mint' : 'peach'}>{s.verdict === 'approve' ? 'Approved' : 'Rejected'}</Pill>
                                <div className="min-w-0 flex-1 text-sm">
                                    <span className="font-semibold text-ink">{s.release_label}</span>
                                    <span className="block text-xs text-ink-soft">
                                        {s.signed_by_name || `user ${s.signed_by_id}`} · {new Date(s.signed_at).toLocaleString()} · runs {s.run_ids.map((r) => `#${r}`).join(', ')}
                                        {s.git_commit ? ` · ${s.git_commit.slice(0, 12)}` : ''}
                                    </span>
                                    {s.note && <p className="mt-1 whitespace-pre-wrap text-ink">{s.note}</p>}
                                </div>
                            </li>
                        ))}
                    </ul>
                )}
            </SettingsCard>
        </div>
    );
}

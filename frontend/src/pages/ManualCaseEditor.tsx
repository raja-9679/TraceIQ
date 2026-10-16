import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowDown, ArrowUp, Loader2, Plus, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { errorDetail } from '@/api/idpCatalog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { api, getTestCase, updateTestCase } from '@/lib/api';
import { PageHeader, Pill } from '@/components/redesign/primitives';
import { FieldRow, SettingsCard, inputClass, textareaClass } from '@/components/redesign-local/groupD/settingsForm';
import {
    ManualStepDraft, draftError, draftsFromSteps, newStepId, stepsFromDrafts,
} from '@/components/manual-testing/manualSteps';

const PRIORITIES = ['', 'critical', 'high', 'medium', 'low'];

interface StoredCase { name?: string; priority?: string | null; tags?: string[] | null; steps?: Parameters<typeof draftsFromSteps>[0]; executor?: string }

/** Create or edit a manual test case: written steps a person follows, each
 * with the result they should see. Saved as an ordinary case whose steps are
 * `manual-step`, which the server turns into executor=manual. */
export default function ManualCaseEditor() {
    const { suiteId, caseId } = useParams();
    const editing = !!caseId;
    const existing = useQuery<StoredCase>({
        queryKey: ['testCase', caseId],
        queryFn: () => getTestCase(Number(caseId)),
        enabled: editing,
    });
    const suite = useQuery<{ name: string }>({
        queryKey: ['suite', suiteId],
        queryFn: () => api.get(`/suites/${suiteId}`).then((r) => r.data),
        enabled: !!suiteId,
    });

    if (editing && existing.isLoading) {
        return <p className="flex items-center gap-2 text-sm text-ink-soft"><Loader2 className="h-4 w-4 animate-spin" /> Loading the test…</p>;
    }
    if (editing && existing.data && existing.data.executor !== 'manual') {
        return (
            <SettingsCard title="Not a manual test" description="This case is automated. Edit it in the test builder.">
                <Link to={`/suites/${suiteId}/cases/${caseId}/edit`} className="text-sm font-medium text-ink underline underline-offset-2">Open the test builder</Link>
            </SettingsCard>
        );
    }
    return <ManualCaseForm key={caseId ?? 'new'} suiteId={suiteId!} caseId={caseId} initial={existing.data} suiteName={suite.data?.name} />;
}

function ManualCaseForm({ suiteId, caseId, initial, suiteName }: {
    suiteId: string; caseId?: string; initial?: StoredCase; suiteName?: string;
}) {
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const editing = !!caseId;
    const [name, setName] = useState(initial?.name || '');
    const [priority, setPriority] = useState(initial?.priority || '');
    const [tags, setTags] = useState((initial?.tags || []).join(', '));
    const [steps, setSteps] = useState<ManualStepDraft[]>(() => {
        const drafts = draftsFromSteps(initial?.steps);
        return drafts.length ? drafts : [{ id: newStepId(), instruction: '', expected: '' }];
    });

    const save = useMutation({
        mutationFn: async () => {
            const body = {
                name: name.trim(),
                steps: stepsFromDrafts(steps),
                priority: priority || null,
                tags: tags.split(',').map((t) => t.trim()).filter(Boolean),
            };
            if (editing) return updateTestCase(Number(caseId), body);
            return (await api.post(`/suites/${suiteId}/cases`, { ...body, executor: 'manual' })).data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['suite', suiteId] });
            queryClient.invalidateQueries({ queryKey: ['testCase', caseId] });
            toast.success(editing ? 'Manual test saved' : 'Manual test created');
            navigate(`/suites/${suiteId}`);
        },
        onError: (err) => toast.error(errorDetail(err, 'Could not save the manual test')),
    });

    const problem = draftError(name, steps);
    const update = (id: string, patch: Partial<ManualStepDraft>) =>
        setSteps((s) => s.map((x) => (x.id === id ? { ...x, ...patch } : x)));
    const move = (i: number, by: number) => setSteps((s) => {
        const j = i + by;
        if (j < 0 || j >= s.length) return s;
        const next = [...s];
        [next[i], next[j]] = [next[j], next[i]];
        return next;
    });

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title={editing ? 'Edit manual test' : 'New manual test'}
                badge={<Pill tone="lavender">Manual</Pill>}
                crumbs={suiteName ? <><Link to={`/suites/${suiteId}`} className="hover:underline">{suiteName}</Link><span aria-hidden="true">/</span><span>{editing ? name || 'Test' : 'New'}</span></> : undefined}
                subtitle="Written steps a tester follows by hand. Manual tests never run on workers; start a manual run from the suite to record results."
                actions={
                    <>
                        <Button variant="outline" onClick={() => navigate(`/suites/${suiteId}`)}>Cancel</Button>
                        <Button onClick={() => save.mutate()} disabled={!!problem || save.isPending}>
                            {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Save manual test
                        </Button>
                    </>
                }
            />

            <SettingsCard labelledBy="manual-case-details" title="Details">
                <div className="flex flex-col divide-y divide-border">
                    <FieldRow label="Name" htmlFor="manual-name" wide>
                        <Input id="manual-name" className={inputClass} value={name} onChange={(e) => setName(e.target.value)} placeholder="Invoice PDF shows VAT" />
                    </FieldRow>
                    <FieldRow label="Priority" htmlFor="manual-priority" hint="Critical cases count for release readiness.">
                        <select id="manual-priority" className="h-10 w-full rounded-full border border-input bg-white px-4 text-sm" value={priority} onChange={(e) => setPriority(e.target.value)}>
                            {PRIORITIES.map((p) => <option key={p} value={p}>{p ? p[0].toUpperCase() + p.slice(1) : 'None'}</option>)}
                        </select>
                    </FieldRow>
                    <FieldRow label="Tags" htmlFor="manual-tags" hint="Comma-separated." wide>
                        <Input id="manual-tags" className={inputClass} value={tags} onChange={(e) => setTags(e.target.value)} placeholder="billing, release" />
                    </FieldRow>
                </div>
            </SettingsCard>

            <SettingsCard
                labelledBy="manual-case-steps"
                title={`Steps · ${steps.length}`}
                description="Each step says what to do and what the tester should see."
                footer={
                    <>
                        <Button variant="outline" onClick={() => setSteps((s) => [...s, { id: newStepId(), instruction: '', expected: '' }])}>
                            <Plus className="h-4 w-4" /> Add step
                        </Button>
                        {problem && <span className="text-xs text-ink-soft" role="status">{problem}</span>}
                    </>
                }
            >
                <ol className="flex flex-col gap-4">
                    {steps.map((s, i) => (
                        <li key={s.id} className="flex flex-col gap-3 rounded-2xl border border-border p-4">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <span className="text-sm font-semibold text-ink">Step {i + 1}</span>
                                <div className="flex flex-wrap gap-2">
                                    <Button size="sm" variant="outline" disabled={i === 0} onClick={() => move(i, -1)}><ArrowUp className="h-3.5 w-3.5" /> Move up</Button>
                                    <Button size="sm" variant="outline" disabled={i === steps.length - 1} onClick={() => move(i, 1)}><ArrowDown className="h-3.5 w-3.5" /> Move down</Button>
                                    <Button size="sm" variant="outline" disabled={steps.length === 1} onClick={() => setSteps((x) => x.filter((y) => y.id !== s.id))}><Trash2 className="h-3.5 w-3.5" /> Remove</Button>
                                </div>
                            </div>
                            <label className="flex flex-col gap-1.5 text-sm font-medium text-ink">
                                Instruction
                                <textarea className={`${textareaClass} font-sans`} rows={2} value={s.instruction}
                                    onChange={(e) => update(s.id, { instruction: e.target.value })} placeholder="Open the order confirmation email and download the invoice" />
                            </label>
                            <label className="flex flex-col gap-1.5 text-sm font-medium text-ink">
                                Expected result
                                <textarea className={`${textareaClass} font-sans`} rows={2} value={s.expected}
                                    onChange={(e) => update(s.id, { expected: e.target.value })} placeholder="The PDF shows the order number and the VAT breakdown" />
                            </label>
                        </li>
                    ))}
                </ol>
            </SettingsCard>
        </div>
    );
}

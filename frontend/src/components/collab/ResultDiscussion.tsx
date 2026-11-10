import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Check, MessageSquare, RotateCcw, Trash2, UserPlus, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Eyebrow, Pill } from '@/components/redesign/primitives';
import { collabApi, type Discussion } from '@/api/collaboration';
import { errDetail } from '@/lib/errors';
import { useAuth } from '@/context/AuthContext';
import { resultLink } from './text';

/**
 * Who is looking at this result, and the conversation about it. Sits above
 * the result's details on the run page.
 *
 * Comments are plain text: they are rendered as text (never as HTML), with
 * line breaks kept.
 */
export function ResultDiscussion({ runId, resultId, projectId, failed }: {
    runId: number; resultId: number; projectId: number | null; failed: boolean;
}) {
    const queryClient = useQueryClient();
    const me = useAuth().user?.id ?? null;
    const key = ['discussion', runId, resultId];
    const { data, isLoading, error } = useQuery<Discussion>({
        queryKey: key,
        queryFn: () => collabApi.discussion(runId, resultId),
    });
    const [draft, setDraft] = useState('');
    const [picking, setPicking] = useState(false);
    const [pick, setPick] = useState('');

    const candidates = useQuery({
        queryKey: ['assignee-candidates', projectId],
        queryFn: () => collabApi.candidates(projectId!),
        enabled: picking && !!projectId,
        staleTime: 60_000,
    });

    const refresh = () => {
        queryClient.invalidateQueries({ queryKey: key });
        queryClient.invalidateQueries({ queryKey: ['run-discussion', runId] });
    };
    const fail = (what: string) => (e: unknown) => toast.error(what, { description: errDetail(e) });

    const addComment = useMutation({
        mutationFn: (body: string) => collabApi.addComment(runId, resultId, body),
        onSuccess: () => { setDraft(''); refresh(); },
        onError: fail('Could not add the comment'),
    });
    const deleteComment = useMutation({
        mutationFn: (id: number) => collabApi.deleteComment(runId, resultId, id),
        onSuccess: refresh,
        onError: fail('Could not delete the comment'),
    });
    const assign = useMutation({
        mutationFn: (userId: number) => collabApi.assign(runId, resultId, userId),
        onSuccess: (a) => {
            setPicking(false);
            setPick('');
            refresh();
            // You are not notified about your own action.
            toast.success(a.assignee.id === me ? 'Assigned to you.' : `Assigned to ${a.assignee.name}. They have been notified.`);
        },
        onError: fail('Could not assign this result'),
    });
    const unassign = useMutation({
        mutationFn: () => collabApi.unassign(runId, resultId),
        onSuccess: refresh,
        onError: fail('Could not remove the assignment'),
    });
    const setDone = useMutation({
        mutationFn: (done: boolean) => collabApi.setDone(runId, resultId, done),
        onSuccess: refresh,
        onError: fail('Could not update the assignment'),
    });

    if (isLoading) return <div className="h-24 animate-pulse rounded-3xl bg-white/70" aria-busy="true" aria-label="Loading discussion" />;
    if (error || !data) return null; // the result itself is still shown below

    const a = data.assignment;
    const own = a && !a.carried;
    // "asked by" only when someone else did the asking.
    const askedBy = a?.assigned_by && a.assigned_by.id !== a.assignee.id ? a.assigned_by.name : null;
    // Nothing to show and nothing to do: a passing result nobody has
    // commented on does not need an empty box above it.
    if (!failed && !a && data.comments.length === 0) return null;

    const submit = (e: FormEvent) => {
        e.preventDefault();
        const body = draft.trim();
        if (body) addComment.mutate(body);
    };

    return (
        <section aria-label="Discussion" className="flex flex-col gap-4 rounded-3xl border border-border bg-white p-5 sm:p-6">
            <div className="flex flex-col gap-2">
                <Eyebrow>Who is looking at this</Eyebrow>
                {a ? (
                    <div className="flex flex-wrap items-center gap-2">
                        <Pill tone={a.status === 'done' ? 'mint' : 'lavender'}>{a.status === 'done' ? 'Done' : 'Assigned'}</Pill>
                        <span className="text-sm font-semibold text-ink">{a.assignee.name}</span>
                        <span className="text-sm text-ink-soft">
                            {askedBy ? `asked by ${askedBy}` : ''}
                            {a.carried && (
                                <>
                                    {askedBy ? ' · ' : ''}
                                    from an earlier failure of this test,{' '}
                                    <Link to={resultLink(a.run_id, a.result_id)} className="font-semibold text-ink underline underline-offset-2">run #{a.run_id}</Link>
                                </>
                            )}
                        </span>
                    </div>
                ) : (
                    <p className="text-sm text-ink-soft">Nobody yet.</p>
                )}

                {data.can_write && (
                    picking ? (
                        <form className="flex flex-wrap items-end gap-2"
                            onSubmit={(e) => { e.preventDefault(); if (pick) assign.mutate(Number(pick)); }}>
                            <label className="flex min-w-[220px] flex-1 flex-col gap-1 text-sm font-semibold text-ink">
                                Assign to
                                <select value={pick} onChange={(e) => setPick(e.target.value)}
                                    className="h-10 rounded-xl border border-input bg-white px-3 text-sm font-normal text-ink">
                                    <option value="">{candidates.isLoading ? 'Loading…' : 'Choose a person'}</option>
                                    {(candidates.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                                </select>
                            </label>
                            <Button type="submit" size="sm" disabled={!pick || assign.isPending}>Assign</Button>
                            <Button type="button" size="sm" variant="outline" onClick={() => { setPicking(false); setPick(''); }}>Cancel</Button>
                            <p className="basis-full text-xs text-ink-soft">People who can edit tests in this project. They get a notification.</p>
                        </form>
                    ) : (
                        <div className="flex flex-wrap gap-2">
                            <Button type="button" size="sm" variant="outline" onClick={() => setPicking(true)}>
                                <UserPlus className="h-4 w-4" /> {own ? 'Assign to someone else' : a ? 'Assign this result' : 'Assign'}
                            </Button>
                            {own && a.status === 'open' && (
                                <Button type="button" size="sm" variant="outline" disabled={setDone.isPending} onClick={() => setDone.mutate(true)}>
                                    <Check className="h-4 w-4" /> Mark done
                                </Button>
                            )}
                            {own && a.status === 'done' && (
                                <Button type="button" size="sm" variant="outline" disabled={setDone.isPending} onClick={() => setDone.mutate(false)}>
                                    <RotateCcw className="h-4 w-4" /> Reopen
                                </Button>
                            )}
                            {own && (
                                <Button type="button" size="sm" variant="outline" disabled={unassign.isPending} onClick={() => unassign.mutate()}>
                                    <X className="h-4 w-4" /> Remove assignment
                                </Button>
                            )}
                        </div>
                    )
                )}
            </div>

            <div className="flex flex-col gap-2">
                <Eyebrow right={data.comments.length ? `${data.comments.length}` : undefined}>Comments</Eyebrow>
                {data.comments.length === 0 ? (
                    <p className="text-sm text-ink-soft">No comments yet.</p>
                ) : (
                    <ol className="flex flex-col divide-y divide-[#F0EEE8] overflow-hidden rounded-2xl border border-border">
                        {data.comments.map((c) => (
                            <li key={c.id} className="flex flex-col gap-1 px-4 py-3">
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                    <span className="text-sm">
                                        <span className="font-semibold text-ink">{c.author?.name ?? 'Former member'}</span>{' '}
                                        <time className="text-xs text-ink-soft" dateTime={c.created_at}>{new Date(c.created_at).toLocaleString()}</time>
                                    </span>
                                    {c.can_delete && (
                                        <Button type="button" size="sm" variant="ghost" className="h-8 px-2 text-xs text-ink-soft"
                                            disabled={deleteComment.isPending}
                                            onClick={() => { if (window.confirm('Delete this comment?')) deleteComment.mutate(c.id); }}>
                                            <Trash2 className="h-3.5 w-3.5" /> Delete
                                        </Button>
                                    )}
                                </div>
                                <p className="whitespace-pre-wrap break-words text-sm text-ink">{c.body}</p>
                            </li>
                        ))}
                    </ol>
                )}
                {data.can_write && (
                    <form onSubmit={submit} className="flex flex-col gap-2">
                        <label htmlFor={`comment-${resultId}`} className="sr-only">Add a comment</label>
                        <textarea id={`comment-${resultId}`} value={draft} onChange={(e) => setDraft(e.target.value)}
                            rows={2} maxLength={4000} placeholder="Add a comment for your team"
                            className="min-h-[72px] rounded-2xl border border-input bg-white px-4 py-3 text-sm text-ink outline-none focus-visible:ring-2 focus-visible:ring-ink/20" />
                        <div className="flex flex-wrap items-center justify-between gap-2">
                            <span className="text-xs text-ink-soft">Plain text. The person assigned and everyone in this thread are notified.</span>
                            <Button type="submit" size="sm" disabled={!draft.trim() || addComment.isPending}>
                                <MessageSquare className="h-4 w-4" /> Comment
                            </Button>
                        </div>
                    </form>
                )}
            </div>
        </section>
    );
}

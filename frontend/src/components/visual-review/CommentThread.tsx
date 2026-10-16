import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Pill } from '@/components/redesign/primitives';
import { visualReviewApi, type CommentTarget, type ReviewComment } from '@/api/visual';
import { textareaClass } from '@/components/redesign-local/groupD/settingsForm';
import { cn } from '@/lib/utils';

const when = (iso: string) =>
    new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });

/**
 * Comment thread on a review item or a group. Bodies are plain text and are
 * rendered as text (React escapes them) — never as HTML or markdown.
 */
export function CommentThread({ projectId, target, canEdit, title, onChanged }: {
    projectId: number;
    target: CommentTarget;
    canEdit: boolean;
    title: string;
    onChanged?: () => void;
}) {
    const queryClient = useQueryClient();
    const [draft, setDraft] = useState('');
    const key = ['visual-review-comments', projectId, target];
    const { data: comments = [], isLoading, error } = useQuery<ReviewComment[]>({
        queryKey: key,
        queryFn: () => visualReviewApi.comments(projectId, target),
    });
    const refresh = () => {
        queryClient.invalidateQueries({ queryKey: key });
        onChanged?.();
    };
    const fail = (err: Error & { response?: { data?: { detail?: string } } }) =>
        toast.error(err.response?.data?.detail || err.message);

    const add = useMutation({
        mutationFn: () => visualReviewApi.addComment(projectId, target, draft.trim()),
        onSuccess: () => { setDraft(''); refresh(); },
        onError: fail,
    });
    const resolve = useMutation({
        mutationFn: ({ id, resolved }: { id: number; resolved: boolean }) =>
            visualReviewApi.resolveComment(projectId, id, resolved),
        onSuccess: refresh,
        onError: fail,
    });
    const inputId = `comment-${'comparisonId' in target ? `c${target.comparisonId}` : `${target.runId}-${target.groupKey}`}`;

    return (
        <section className="flex flex-col gap-3" aria-label={title}>
            <h3 className="text-sm font-semibold text-ink">{title}</h3>
            {isLoading && <div className="h-12 animate-pulse rounded-2xl bg-background" aria-busy="true" aria-label="Loading comments" />}
            {error && <p className="text-sm text-pastel-ink-peach">Couldn't load comments.</p>}
            {!isLoading && !error && comments.length === 0 && (
                <p className="text-sm text-ink-soft">No comments yet.</p>
            )}
            {comments.length > 0 && (
                <ul className="overflow-hidden rounded-2xl border border-border">
                    {comments.map((c, i) => (
                        <li key={c.id} className={cn('flex flex-col gap-1.5 px-4 py-3', i > 0 && 'border-t border-[#F0EEE8]', c.resolved && 'bg-background')}>
                            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-soft">
                                <span className="font-semibold text-ink">{c.author?.name ?? 'Former member'}</span>
                                <span>{when(c.created_at)}</span>
                                {c.resolved && <Pill tone="mint">Resolved</Pill>}
                                {canEdit && (
                                    <Button
                                        size="sm"
                                        variant="outline"
                                        className="ml-auto h-8 px-3"
                                        disabled={resolve.isPending}
                                        onClick={() => resolve.mutate({ id: c.id, resolved: !c.resolved })}
                                    >
                                        {c.resolved ? 'Reopen' : 'Resolve'}
                                    </Button>
                                )}
                            </div>
                            <p className={cn('whitespace-pre-wrap break-words text-sm', c.resolved ? 'text-ink-soft' : 'text-ink')}>{c.body}</p>
                        </li>
                    ))}
                </ul>
            )}
            {canEdit ? (
                <form
                    className="flex flex-col gap-2"
                    onSubmit={(e) => { e.preventDefault(); if (draft.trim()) add.mutate(); }}
                >
                    <label htmlFor={inputId} className="text-xs font-semibold text-ink-soft">Add a comment</label>
                    <textarea
                        id={inputId}
                        value={draft}
                        maxLength={4000}
                        rows={3}
                        onChange={(e) => setDraft(e.target.value)}
                        className={cn(textareaClass, 'font-sans')}
                        placeholder="What changed, and is it intended?"
                    />
                    <Button type="submit" size="sm" className="self-start" disabled={!draft.trim() || add.isPending}>
                        {add.isPending ? 'Posting…' : 'Post comment'}
                    </Button>
                </form>
            ) : (
                <p className="text-xs text-ink-soft">Commenting needs the editor role on this project.</p>
            )}
        </section>
    );
}

import { useState, type FormEvent, type KeyboardEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { History, Loader2, Plus, Send, Sparkles, Trash2, X } from 'lucide-react';
import { assistantApi, type AssistantMessage, type Citation } from '@/api/assistant';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { Pill } from '@/components/redesign/primitives';
import { warnCalloutClass } from '@/components/redesign-local/groupD/settingsForm';
import { cn } from '@/lib/utils';
import {
    SUGGESTIONS, citationHref, citationKindLabel, citationLabel, formatWhen, isSystemNotice,
    splitInlineRefs,
} from './citations';

interface Props {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    workspaceId: number | null;
    projectId: number | null;
    /** Switch the app's active project (per-project pages read it). */
    onSelectProject: (id: number) => void;
}

const DEFAULT_DISCLAIMER = 'AI can be wrong; check the cited records.';

/** "Ask TraceIQ": a read-only chat over the caller's own data (WP14a). */
export default function AssistantDrawer({ open, onOpenChange, workspaceId, projectId, onSelectProject }: Props) {
    const qc = useQueryClient();
    const navigate = useNavigate();
    const [conversationId, setConversationId] = useState<number | null>(null);
    const [showHistory, setShowHistory] = useState(false);
    const [draft, setDraft] = useState('');
    const [pending, setPending] = useState<string | null>(null);

    // A conversation belongs to one workspace: switching workspace starts over.
    const [lastWs, setLastWs] = useState(workspaceId);
    if (lastWs !== workspaceId) {
        setLastWs(workspaceId);
        setConversationId(null);
    }

    const enabled = open && !!workspaceId;
    const status = useQuery({
        queryKey: ['assistant', 'status', workspaceId],
        queryFn: () => assistantApi.status(workspaceId!),
        enabled,
    });
    const conversations = useQuery({
        queryKey: ['assistant', 'conversations', workspaceId],
        queryFn: () => assistantApi.list(workspaceId!),
        enabled,
    });
    const thread = useQuery({
        queryKey: ['assistant', 'conversation', conversationId],
        queryFn: () => assistantApi.get(conversationId!),
        enabled: open && !!conversationId,
    });

    const send = useMutation({
        mutationFn: async (text: string) => {
            let id = conversationId;
            if (!id) {
                const created = await assistantApi.create(workspaceId!, projectId);
                id = created.id;
                setConversationId(id);
            }
            return assistantApi.send(id, workspaceId!, text, projectId);
        },
        onMutate: (text) => { setPending(text); setDraft(''); },
        onSuccess: (res) => {
            qc.invalidateQueries({ queryKey: ['assistant', 'conversation', res.conversation.id] });
            qc.invalidateQueries({ queryKey: ['assistant', 'conversations', workspaceId] });
        },
        onError: (err: unknown, text) => {
            setDraft(text);
            const status = (err as { response?: { status?: number } })?.response?.status;
            toast.error(status === 429 ? 'Too many questions in a minute. Wait a moment and try again.'
                : 'The assistant could not answer. Please try again.');
        },
        onSettled: () => setPending(null),
    });

    const remove = useMutation({
        mutationFn: (id: number) => assistantApi.remove(id),
        onSuccess: (_r, id) => {
            if (id === conversationId) setConversationId(null);
            qc.invalidateQueries({ queryKey: ['assistant', 'conversations', workspaceId] });
            qc.removeQueries({ queryKey: ['assistant', 'conversation', id] });
            toast.success('Conversation deleted');
        },
        onError: () => toast.error('Could not delete the conversation'),
    });

    const ask = (text: string) => {
        const t = text.trim();
        if (!t || send.isPending || !workspaceId) return;
        send.mutate(t);
    };
    const onSubmit = (e: FormEvent) => { e.preventDefault(); ask(draft); };
    const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(draft); }
    };

    const openCitation = (c: Citation) => {
        if (c.project_id && c.project_id !== projectId) onSelectProject(c.project_id);
        onOpenChange(false);
        navigate(citationHref(c));
    };

    const disclaimer = status.data?.disclaimer || DEFAULT_DISCLAIMER;
    const off = status.data && !status.data.enabled;
    const messages: AssistantMessage[] = thread.data?.messages ?? [];
    const list = conversations.data ?? [];

    return (
        <Sheet open={open} onOpenChange={onOpenChange}>
            <SheetContent
                side="right"
                className="flex w-full flex-col gap-0 p-0 sm:max-w-xl [&>button.absolute]:hidden"
                aria-describedby="assistant-desc"
            >
                <SheetHeader className="space-y-1 border-b border-border px-5 py-4 text-left">
                    <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                            <SheetTitle className="flex items-center gap-2 text-base font-semibold text-ink">
                                <Sparkles className="h-4 w-4" aria-hidden="true" /> Ask TraceIQ
                            </SheetTitle>
                            <SheetDescription id="assistant-desc" className="text-[13px] text-ink-soft">
                                Read-only answers about your runs, results, flaky tests and findings.
                                It only sees what you can see.
                            </SheetDescription>
                        </div>
                        <Button type="button" variant="outline" size="sm" onClick={() => onOpenChange(false)}>
                            <X aria-hidden="true" /> Close assistant
                        </Button>
                    </div>
                    <div className="flex flex-wrap gap-2 pt-2">
                        <Button type="button" size="sm" onClick={() => { setConversationId(null); setShowHistory(false); }}>
                            <Plus aria-hidden="true" /> New conversation
                        </Button>
                        <Button type="button" variant="outline" size="sm" aria-expanded={showHistory}
                            aria-controls="assistant-history" onClick={() => setShowHistory((v) => !v)}>
                            <History aria-hidden="true" /> History ({list.length})
                        </Button>
                        {conversationId && (
                            <Button type="button" variant="outline" size="sm" disabled={remove.isPending}
                                onClick={() => remove.mutate(conversationId)}>
                                <Trash2 aria-hidden="true" /> Delete conversation
                            </Button>
                        )}
                    </div>
                </SheetHeader>

                {showHistory && (
                    <section id="assistant-history" aria-label="Your conversations"
                        className="max-h-64 overflow-y-auto border-b border-border bg-secondary/40 px-5 py-3">
                        {list.length === 0 ? (
                            <p className="text-[13px] text-ink-soft">No conversations yet.</p>
                        ) : (
                            <ul className="flex flex-col gap-1.5">
                                {list.map((c) => (
                                    <li key={c.id} className={cn(
                                        'flex items-center gap-2 rounded-2xl border bg-white px-3 py-2',
                                        c.id === conversationId ? 'border-ink/30' : 'border-border')}>
                                        <button type="button" className="min-w-0 flex-1 text-left"
                                            onClick={() => { setConversationId(c.id); setShowHistory(false); }}>
                                            <span className="block truncate text-sm font-medium text-ink">{c.title}</span>
                                            <span className="block text-xs text-ink-soft">
                                                {formatWhen(c.updated_at)}
                                            </span>
                                        </button>
                                        <Button type="button" variant="ghost" size="sm" disabled={remove.isPending}
                                            onClick={() => remove.mutate(c.id)} aria-label={`Delete conversation ${c.title}`}>
                                            Delete
                                        </Button>
                                    </li>
                                ))}
                            </ul>
                        )}
                        <p className="mt-2 text-xs text-ink-soft">
                            Only you can see these. They are deleted after {status.data?.retention_days ?? 30} days.
                        </p>
                    </section>
                )}

                <div className="flex-1 overflow-y-auto px-5 py-4" aria-live="polite">
                    <p className={cn(warnCalloutClass, 'mb-4 text-[13px]')}>{disclaimer}</p>
                    {off && (
                        <p className="mb-4 rounded-2xl border border-border bg-white px-4 py-3 text-sm text-ink">
                            {status.data?.message}
                        </p>
                    )}
                    {!workspaceId && (
                        <p className="text-sm text-ink-soft">Select a project to ask about its workspace.</p>
                    )}
                    {conversationId && thread.isLoading && (
                        <p className="flex items-center gap-2 text-sm text-ink-soft">
                            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> Loading conversation…
                        </p>
                    )}
                    {!conversationId && !pending && workspaceId && !off && (
                        <div className="flex flex-col gap-2">
                            <p className="text-sm font-medium text-ink">Try asking</p>
                            <div className="flex flex-wrap gap-2">
                                {SUGGESTIONS.map((s) => (
                                    <Button key={s} type="button" variant="outline" size="sm" onClick={() => ask(s)}>
                                        {s}
                                    </Button>
                                ))}
                            </div>
                        </div>
                    )}
                    <ol className="flex flex-col gap-3">
                        {messages.map((m) => (
                            <MessageItem key={m.id} message={m} onOpenCitation={openCitation} />
                        ))}
                        {pending && (
                            <>
                                <li className="ml-auto max-w-[85%] rounded-2xl bg-ink px-4 py-2.5 text-sm text-white">{pending}</li>
                                <li className="flex items-center gap-2 text-sm text-ink-soft">
                                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                                    Looking up records…
                                </li>
                            </>
                        )}
                    </ol>
                </div>

                <form onSubmit={onSubmit} className="border-t border-border px-5 py-4">
                    <label htmlFor="assistant-question" className="mb-1.5 block text-[13px] font-medium text-ink">
                        Your question
                    </label>
                    <textarea
                        id="assistant-question"
                        value={draft}
                        onChange={(e) => setDraft(e.target.value)}
                        onKeyDown={onKey}
                        rows={2}
                        maxLength={2000}
                        disabled={!workspaceId || !!off}
                        placeholder="e.g. Why did checkout fail last night?"
                        className="w-full resize-none rounded-2xl border border-input bg-white px-3 py-2.5 text-sm text-ink focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-60"
                    />
                    <div className="mt-2 flex items-center justify-between gap-3">
                        <span className="text-xs text-ink-soft">Enter to send, Shift+Enter for a new line.</span>
                        <Button type="submit" size="sm" disabled={!draft.trim() || send.isPending || !workspaceId || !!off}>
                            {send.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : <Send aria-hidden="true" />}
                            Ask
                        </Button>
                    </div>
                </form>
            </SheetContent>
        </Sheet>
    );
}

function MessageItem({ message: m, onOpenCitation }: { message: AssistantMessage; onOpenCitation: (c: Citation) => void }) {
    if (m.role === 'user') {
        return <li className="ml-auto max-w-[85%] whitespace-pre-wrap rounded-2xl bg-ink px-4 py-2.5 text-sm text-white">{m.content}</li>;
    }
    const byRef = new Map(m.citations.map((c) => [c.ref, c]));
    return (
        <li className="rounded-2xl border border-border bg-white px-4 py-3 text-sm text-ink">
            <AnswerText text={m.content} byRef={byRef} onOpenCitation={onOpenCitation} />
            {!m.hidden && !isSystemNotice(m.status) && (
                <div className="mt-3 flex flex-col gap-2">
                    {m.citations.length > 0 && (
                        <div>
                            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-ink-soft">Sources</p>
                            <div className="flex flex-wrap gap-1.5">
                                {m.citations.map((c) => (
                                    <CitationChip key={c.ref} citation={c} onOpen={onOpenCitation} />
                                ))}
                            </div>
                        </div>
                    )}
                    <div className="flex flex-wrap gap-1.5">
                        {m.uncited && <Pill tone="butter">No sources cited: treat as unverified</Pill>}
                        {m.dropped_citations > 0 && (
                            <Pill tone="neutral">
                                {m.dropped_citations} reference{m.dropped_citations === 1 ? '' : 's'} removed (not in the data)
                            </Pill>
                        )}
                    </div>
                    {m.tool_calls.length > 0 && (
                        <p className="text-xs text-ink-soft">
                            Looked up: {m.tool_calls.map((t) => `${t.tool.replace(/_/g, ' ')}${t.error ? ' (refused)' : ` (${t.items})`}`).join(', ')}
                        </p>
                    )}
                </div>
            )}
        </li>
    );
}

function AnswerText({ text, byRef, onOpenCitation }: {
    text: string; byRef: Map<string, Citation>; onOpenCitation: (c: Citation) => void;
}) {
    return (
        <p className="whitespace-pre-wrap leading-relaxed">
            {splitInlineRefs(text).map((part, i) => {
                const c = part.ref ? byRef.get(part.ref) : undefined;
                if (!c) return <span key={i}>{part.text}</span>;
                return (
                    <button key={i} type="button" onClick={() => onOpenCitation(c)}
                        className="mx-0.5 inline rounded-full bg-secondary px-1.5 text-xs font-medium text-ink underline-offset-2 hover:underline"
                        aria-label={`Open ${citationKindLabel(c)}: ${citationLabel(c)}`}>
                        {citationKindLabel(c)} #{c.id}
                    </button>
                );
            })}
        </p>
    );
}

function CitationChip({ citation: c, onOpen }: { citation: Citation; onOpen: (c: Citation) => void }) {
    return (
        <button type="button" onClick={() => onOpen(c)}
            className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-white px-2.5 py-1 text-xs text-ink hover:bg-secondary"
            title={citationLabel(c)}>
            <span className="font-semibold">{citationKindLabel(c)}</span>
            <span className="truncate">{citationLabel(c)}</span>
        </button>
    );
}


import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { PanelTabs, Pill } from '@/components/redesign/primitives';
import { cn } from '@/lib/utils';
import { collabApi } from '@/api/collaboration';
import { notificationText, resultLink } from './text';

type Tab = 'notifications' | 'assigned';

/** The bell's drawer: what happened to your work, and what is assigned to you. */
export function NotificationsDrawer({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
    const queryClient = useQueryClient();
    const [tab, setTab] = useState<Tab>('notifications');

    const list = useQuery({ queryKey: ['notifications'], queryFn: collabApi.notifications, enabled: open });
    const mine = useQuery({ queryKey: ['assignments-mine'], queryFn: collabApi.myAssignments, enabled: open });

    const refresh = () => {
        queryClient.invalidateQueries({ queryKey: ['notifications'] });
        queryClient.invalidateQueries({ queryKey: ['notifications-unread'] });
    };
    const markRead = useMutation({ mutationFn: collabApi.markRead, onSuccess: refresh });

    const items = list.data?.items ?? [];
    const unread = list.data?.unread ?? 0;
    const assignments = mine.data ?? [];

    return (
        <Sheet open={open} onOpenChange={onOpenChange}>
            <SheetContent side="right" className="flex w-full flex-col gap-0 overflow-hidden bg-white p-0 sm:max-w-md">
                <SheetHeader className="px-5 pb-3 pt-5 text-left">
                    <SheetTitle className="text-xl font-bold tracking-[-0.02em] text-ink">Notifications</SheetTitle>
                    <SheetDescription className="text-sm text-ink-soft">
                        Failed results you were asked to look at, and replies in threads you are part of.
                    </SheetDescription>
                </SheetHeader>
                <div className="flex items-center justify-between gap-2 border-y border-[#F0EEE8] px-3 py-2">
                    <PanelTabs<Tab> label="Notification lists" value={tab} onChange={setTab} tabs={[
                        { id: 'notifications', label: 'Notifications', count: unread || undefined },
                        { id: 'assigned', label: 'Assigned to me', count: assignments.length || undefined },
                    ]} />
                    {tab === 'notifications' && unread > 0 && (
                        <Button type="button" variant="outline" size="sm" disabled={markRead.isPending}
                            onClick={() => markRead.mutate({ all: true })}>
                            Mark all read
                        </Button>
                    )}
                </div>

                <div className="min-h-0 flex-1 overflow-y-auto">
                    {tab === 'notifications' ? (
                        list.isLoading ? <p role="status" className="px-5 py-6 text-sm text-ink-soft">Loading…</p>
                            : items.length === 0 ? (
                                <p className="px-5 py-8 text-center text-sm text-ink-soft">
                                    Nothing yet. You are told here when someone assigns you a failed result or replies in a thread you are in.
                                </p>
                            ) : (
                                <ul className="flex flex-col divide-y divide-[#F0EEE8]">
                                    {items.map((n) => (
                                        <li key={n.id}>
                                            <Link to={resultLink(n.run_id, n.result_id)}
                                                onClick={() => { if (!n.read) markRead.mutate({ ids: [n.id] }); onOpenChange(false); }}
                                                className={cn('flex min-h-[60px] items-start gap-3 px-5 py-3 hover:bg-background', !n.read && 'bg-accent/60')}>
                                                <span aria-hidden="true" className={cn('mt-1.5 h-2 w-2 flex-none rounded-full', n.read ? 'bg-transparent' : 'bg-ink')} />
                                                <span className="flex min-w-0 flex-col gap-0.5">
                                                    <span className={cn('text-sm text-ink [overflow-wrap:anywhere]', !n.read && 'font-semibold')}>
                                                        {notificationText(n)}<span className="sr-only">{n.read ? '' : ' (unread)'}</span>
                                                    </span>
                                                    <span className="text-xs text-ink-soft">
                                                        {n.project_name} · run #{n.run_id} · {new Date(n.created_at).toLocaleString()}
                                                    </span>
                                                </span>
                                            </Link>
                                        </li>
                                    ))}
                                </ul>
                            )
                    ) : mine.isLoading ? <p role="status" className="px-5 py-6 text-sm text-ink-soft">Loading…</p>
                        : assignments.length === 0 ? (
                            <p className="px-5 py-8 text-center text-sm text-ink-soft">No open failures are assigned to you.</p>
                        ) : (
                            <ul className="flex flex-col divide-y divide-[#F0EEE8]">
                                {assignments.map((a) => (
                                    <li key={a.result_id}>
                                        <Link to={resultLink(a.run_id, a.result_id)} onClick={() => onOpenChange(false)}
                                            className="flex min-h-[60px] items-center justify-between gap-3 px-5 py-3 hover:bg-background">
                                            <span className="flex min-w-0 flex-col gap-0.5">
                                                <span className="text-sm font-semibold text-ink [overflow-wrap:anywhere]">{a.test_name}</span>
                                                <span className="text-xs text-ink-soft">
                                                    {a.project_name} · run #{a.run_id}{a.assigned_by ? ` · asked by ${a.assigned_by.name}` : ''}
                                                </span>
                                            </span>
                                            <Pill tone="lavender">Open</Pill>
                                        </Link>
                                    </li>
                                ))}
                            </ul>
                        )}
                </div>
            </SheetContent>
        </Sheet>
    );
}

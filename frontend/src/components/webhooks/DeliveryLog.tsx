import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { RotateCcw } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Eyebrow, Pill } from '@/components/redesign/primitives';
import type { Tone } from '@/components/redesign/kinds';
import { Segmented } from '@/components/redesign-local/groupB/bits';
import { webhooksApi, type WebhookDelivery } from '@/api/webhooks';

// Delivery log of one webhook (WP15f): every attempt, newest first, with a
// redeliver action. The payload and URL are never shown — the API does not
// return them.

const STATUS_TONE: Record<WebhookDelivery['status'], Tone> = {
    ok: 'mint', failed: 'peach', pending: 'lavender', skipped: 'neutral',
};

const when = (iso: string | null) =>
    iso ? new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—';

export function DeliveryLog({ workspaceId, webhookId }: { workspaceId: number; webhookId: number }) {
    const [filter, setFilter] = useState<'' | 'failed'>('');
    const queryClient = useQueryClient();
    const key = ['webhook-deliveries', workspaceId, webhookId, filter];
    const { data, isLoading, error } = useQuery({
        queryKey: key,
        queryFn: () => webhooksApi.deliveries(workspaceId, webhookId, filter || undefined),
        // A retry may be scheduled; keep the log fresh while it is open.
        refetchInterval: 15000,
    });
    const redeliver = useMutation({
        mutationFn: (rowId: number) => webhooksApi.redeliver(workspaceId, webhookId, rowId),
        onSuccess: () => {
            toast.success('Redelivery queued', { description: 'It appears below as a new manual attempt.' });
            setTimeout(() => queryClient.invalidateQueries({ queryKey: ['webhook-deliveries', workspaceId, webhookId] }), 1500);
            queryClient.invalidateQueries({ queryKey: ['webhooks', workspaceId] });
        },
        onError: (err: unknown) => {
            const e = err as { response?: { data?: { detail?: string } }; message?: string };
            toast.error('Could not redeliver', { description: e?.response?.data?.detail || e?.message });
        },
    });

    const rows = data?.deliveries ?? [];
    return (
        <div className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <Eyebrow>Recent deliveries</Eyebrow>
                <Segmented
                    label="Filter deliveries"
                    value={filter}
                    onChange={(v) => setFilter(v as '' | 'failed')}
                    options={[{ id: '', label: 'All' }, { id: 'failed', label: 'Failed' }]}
                />
            </div>
            {isLoading ? (
                <p className="text-sm text-ink-soft">Loading deliveries…</p>
            ) : error ? (
                <p className="text-sm text-ink-soft">Couldn't load the delivery log.</p>
            ) : rows.length === 0 ? (
                <p className="text-sm text-ink-soft">
                    {filter ? 'No failed deliveries' : 'No deliveries yet'} in the last {data?.retention_days ?? 30} days.
                </p>
            ) : (
                <ul className="flex flex-col divide-y divide-border rounded-2xl border border-border bg-white">
                    {rows.map((d) => (
                        <li key={d.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-sm">
                            <Pill tone={STATUS_TONE[d.status] ?? 'neutral'}>
                                {d.status === 'ok' ? `OK ${d.status_code ?? ''}`.trim() : d.status === 'failed' ? 'Failed' : d.status}
                            </Pill>
                            <span className="font-mono text-xs text-ink">{d.event}</span>
                            {d.run_id && <Link to={`/runs/${d.run_id}`} className="text-xs text-ink-soft underline-offset-2 hover:underline">run #{d.run_id}</Link>}
                            <span className="text-xs text-ink-soft">
                                {d.manual ? 'manual redelivery' : `attempt ${d.attempt}`} · {when(d.completed_at || d.created_at)}
                                {d.duration_ms != null ? ` · ${d.duration_ms} ms` : ''}
                            </span>
                            {d.error && <span className="w-full text-xs text-pastel-ink-peach">{d.error}</span>}
                            {d.next_retry_at && <span className="w-full text-xs text-ink-soft">Next automatic retry {when(d.next_retry_at)}</span>}
                            <span className="ml-auto flex items-center gap-2">
                                <span className="hidden font-mono text-[11px] text-ink-soft md:inline" title="X-TraceIQ-Delivery">{d.delivery_id.slice(0, 8)}</span>
                                <Button size="sm" variant="outline" disabled={redeliver.isPending}
                                    onClick={() => redeliver.mutate(d.id)}>
                                    <RotateCcw className="h-3.5 w-3.5" /> Redeliver
                                </Button>
                            </span>
                        </li>
                    ))}
                </ul>
            )}
            <p className="text-xs leading-relaxed text-ink-soft">
                Failed deliveries are retried after 1, 5 and 30 minutes. A redelivery sends the same body with the same{' '}
                <code className="font-mono">X-TraceIQ-Delivery</code> id, freshly signed — a receiver that already processed it can drop it.
            </p>
        </div>
    );
}

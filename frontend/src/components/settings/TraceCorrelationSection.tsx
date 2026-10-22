import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Network } from 'lucide-react';
import { toast } from 'sonner';

import { tracesApi, type TraceSettings } from '@/api/traces';
import { Pill } from '@/components/redesign/primitives';
import { CheckRow, SettingsCard, warnCalloutClass } from '@/components/redesign-local/groupD/settingsForm';

/**
 * Trace correlation (WP16a): workers add a W3C traceparent to requests to the
 * project's own hosts, and the run page shows the spans the application
 * recorded under each step. Off by default; project admin to change.
 */
export function TraceCorrelationSection({ projectId, canEdit }: { projectId: number; canEdit: boolean }) {
    const qc = useQueryClient();
    const query = useQuery({
        queryKey: ['trace-settings', projectId],
        queryFn: () => tracesApi.getSettings(projectId),
        enabled: !!projectId,
    });
    const save = useMutation({
        mutationFn: (body: Partial<Pick<TraceSettings, 'enabled' | 'cross_origin'>>) => tracesApi.putSettings(projectId, body),
        onSuccess: (data) => {
            qc.setQueryData(['trace-settings', projectId], data);
            toast.success(data.enabled ? 'Trace correlation is on for new runs' : 'Trace correlation is off');
        },
        onError: (e: { response?: { data?: { detail?: string } } }) =>
            toast.error(e?.response?.data?.detail ?? 'Could not save trace correlation'),
    });
    const data = query.data;

    return (
        <SettingsCard
            labelledBy="ps-traces"
            icon={<Network className="h-4 w-4" aria-hidden="true" />}
            title="Backend traces"
            description={<>
                When on, workers add a W3C <code>traceparent</code> header to requests your tests make to this
                project&apos;s own hosts — environment base URLs, suite allowed domains and the security allowlist,
                never third parties — and each result shows the spans your application recorded for every step.
                Nothing is shown for a step the backend has no spans for, and nothing is guessed.
            </>}
            actions={data ? <Pill tone={data.enabled ? 'mint' : 'neutral'}>{data.enabled ? 'On' : 'Off'}</Pill> : null}
        >
            {query.isLoading ? (
                <p className="flex items-center gap-2 text-sm text-ink-soft"><Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> Loading…</p>
            ) : query.isError || !data ? (
                <p className="text-sm text-destructive" role="alert">Could not load trace correlation for this project.</p>
            ) : (
                <>
                    {!data.backend_configured && (
                        <p className={warnCalloutClass}>
                            No trace backend is configured on this instance yet. Headers are still sent when this is on,
                            but spans can only be shown once an instance admin sets the Jaeger or Tempo URL in
                            Setup → Instance settings → Observability.
                        </p>
                    )}
                    <div className="flex flex-col">
                        <CheckRow
                            id="trace-enabled"
                            label="Send trace headers to this project's own hosts"
                            hint="Applies to new runs. Not sent at capture level “none”, by local or mobile workers, or outside a step."
                            checked={data.enabled}
                            disabled={!canEdit || save.isPending}
                            onChange={(v) => save.mutate({ enabled: v })}
                        />
                        <CheckRow
                            id="trace-cross-origin"
                            label="Also on cross-origin browser requests"
                            hint="Off: the browser sends the header only on page loads and same-origin requests, because a custom header on a cross-origin fetch can trigger a CORS preflight your API may refuse. Turn on only if your API's CORS policy allows traceparent. API steps always send it to own hosts."
                            checked={data.cross_origin}
                            disabled={!canEdit || !data.enabled || save.isPending}
                            onChange={(v) => save.mutate({ cross_origin: v })}
                        />
                    </div>
                </>
            )}
        </SettingsCard>
    );
}

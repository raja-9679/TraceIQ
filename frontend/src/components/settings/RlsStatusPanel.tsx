import { useMutation, useQuery } from '@tanstack/react-query';
import { Loader2, RotateCcw } from 'lucide-react';
import { toast } from 'sonner';
import { errDetail } from '@/lib/errors';
import { Button } from '@/components/ui/button';
import { rlsApi } from '@/api/rls';
import { mechanismLabel, violationSummary } from '@/components/settings/rlsStatus';

// Rendered inside the "Tenant isolation" instance-settings card: whether this
// database can actually enforce, and what log mode has counted so far.
export default function RlsStatusPanel() {
    const status = useQuery({ queryKey: ['rls-status'], queryFn: rlsApi.status, refetchInterval: 30000, retry: false });
    const reset = useMutation({
        mutationFn: rlsApi.resetViolations,
        onSuccess: () => { toast.success('Violation count reset'); status.refetch(); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const s = status.data;
    if (status.isError) return <div className="text-xs text-rose-600">{errDetail(status.error)}</div>;
    if (!s) return <Loader2 className="h-4 w-4 animate-spin" aria-label="Loading status" />;
    const top = Object.entries(s.violations.by_table).sort((a, b) => b[1] - a[1]).slice(0, 5);
    return (
        <div className="space-y-2 text-xs text-ink-soft">
            <div>
                Mode: <span className="font-medium text-ink">{s.mode}</span>
                {s.configured.trim().toLowerCase() !== s.mode && <> (configured value “{s.configured}” is not recognised, so it is treated as off)</>}
                {' · '}
                {s.enforcing
                    ? <span className="text-emerald-700">Postgres is enforcing</span>
                    : s.mode === 'enforce'
                        ? <span className="text-rose-600">set to enforce, but NOT enforcing</span>
                        : <span>nothing is refused</span>}
            </div>
            <div>Mechanism: <span className="text-ink">{mechanismLabel(s.mechanism, s.role)}</span></div>
            {s.mechanism_detail && <div className="text-amber-700">{s.mechanism_detail}</div>}
            {!s.policies_installed && (
                <div className="text-rose-600">
                    Policies missing on: {s.tables.filter((t) => !t.policy || !t.enabled).map((t) => t.table).join(', ')}
                </div>
            )}
            <div>{violationSummary(s.violations)}</div>
            {top.length > 0 && (
                <ul className="list-disc pl-5">
                    {top.map(([table, n]) => <li key={table}>{table}: {n}</li>)}
                </ul>
            )}
            <Button variant="outline" size="sm" disabled={reset.isPending} onClick={() => reset.mutate()}>
                {reset.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
                Reset violation count
            </Button>
        </div>
    );
}

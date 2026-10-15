import { useMutation, useQuery } from '@tanstack/react-query';
import { Loader2, Send } from 'lucide-react';
import { toast } from 'sonner';
import { errDetail } from '@/lib/errors';
import { Button } from '@/components/ui/button';
import { siemApi } from '@/api/securityOps';

// Rendered inside the SIEM instance-settings card: delivery health plus a
// test event that goes straight to the saved sink.
export default function SiemStatusPanel() {
    const status = useQuery({ queryKey: ['siem-status'], queryFn: siemApi.status, refetchInterval: 15000, retry: false });
    const test = useMutation({
        mutationFn: siemApi.test,
        onSuccess: (d) => { toast.success(`Test event delivered via ${d.sink_type}`); status.refetch(); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const s = status.data;
    return (
        <div className="space-y-2">
            {s && (
                <div className="text-xs text-ink-soft space-y-0.5">
                    {s.config_error && <div className="text-rose-600">Configuration problem: {s.config_error}</div>}
                    <div>
                        Queue: <span className="font-medium text-ink">{s.pending}</span> event(s) waiting
                        {s.oldest_pending_at && <> · oldest from {new Date(s.oldest_pending_at).toLocaleString()}</>}
                    </div>
                    {s.last_error && <div className="text-amber-700">Retrying after: {s.last_error}</div>}
                </div>
            )}
            <div className="flex flex-wrap items-center gap-3">
                <Button variant="outline" disabled={test.isPending} onClick={() => test.mutate()}>
                    {test.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                    Send test event
                </Button>
                <span className="text-xs text-ink-soft">Save first — the test uses saved values.</span>
            </div>
        </div>
    );
}

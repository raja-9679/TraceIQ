import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ShieldCheck } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { api } from '@/lib/api';
import { detailText } from '@/components/proposals/repairValidation';

// WP16c (decision V4): a protected suite never auto-applies agent proposals,
// and heal / case-edit proposals touching it (or a module below it) must pass
// a validation run before anyone can accept them. Editor-only, audited, and
// not something an API key can change.
export function ProtectedSuiteCard({ suiteId, protectedSuite }: { suiteId: number; protectedSuite: boolean }) {
    const queryClient = useQueryClient();
    const mutation = useMutation({
        mutationFn: async (next: boolean) => (await api.put(`/suites/${suiteId}/protected`, { protected: next })).data,
        onSuccess: (out: { protected: boolean }) => {
            queryClient.invalidateQueries({ queryKey: ['suite', String(suiteId)] });
            queryClient.invalidateQueries({ queryKey: ['suite', suiteId] });
            toast.success(out.protected ? 'Module protected' : 'Module no longer protected');
        },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        onError: (err: any) => toast.error('Failed to change protection', {
            description: detailText(err.response?.data?.detail),
        }),
    });
    return (
        <div className="bg-white rounded-3xl border border-slate-200 shadow-sm p-6 lg:p-8 flex flex-col md:flex-row md:items-center justify-between gap-6 hover:border-slate-300 transition-colors">
            <div className="space-y-1">
                <h3 className="text-base font-bold text-slate-900 flex items-center gap-2">
                    <ShieldCheck className="h-4 w-4 text-indigo-500" />
                    Protected module
                </h3>
                <p className="text-sm text-slate-500 max-w-xl">
                    Agent proposals that touch this module or anything below it are never applied
                    automatically, and selector heals and test edits must pass a validation run before
                    they can be accepted. Changes are recorded in the audit log.
                </p>
            </div>
            <Button
                variant={protectedSuite ? 'default' : 'outline'}
                disabled={mutation.isPending}
                aria-pressed={protectedSuite}
                onClick={() => mutation.mutate(!protectedSuite)}
                className="shrink-0 h-11 px-6 rounded-xl font-bold tracking-wide"
            >
                {protectedSuite ? 'Protected' : 'Protect module'}
            </Button>
        </div>
    );
}

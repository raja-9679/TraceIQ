import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Ban } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { cancelRun } from '@/lib/api';

// Cancel a pending or running run (WP15f). The backend is the authority on
// who may (editor on the project) and when (a finished run answers 409); its
// message is shown as given.
export function CancelRunButton({ runId, onCancelled }: { runId: number; onCancelled?: () => void }) {
    const [open, setOpen] = useState(false);
    const queryClient = useQueryClient();
    const mutation = useMutation({
        mutationFn: () => cancelRun(runId),
        onSuccess: (out) => {
            if (out.workers_notified === false) {
                toast.warning('Run cancelled', {
                    description: 'The server could not reach its workers, so queued cases may still run. The run stays cancelled.',
                });
            } else {
                toast.success('Run cancelled');
            }
            queryClient.invalidateQueries({ queryKey: ['run', runId] });
            queryClient.invalidateQueries({ queryKey: ['runs'] });
            setOpen(false);
            onCancelled?.();
        },
        onError: (error: unknown) => {
            const detail = (error as { response?: { data?: { detail?: string | { message?: string } } } })
                ?.response?.data?.detail;
            toast.error('Could not cancel the run', {
                description: typeof detail === 'string' ? detail : detail?.message || 'An error occurred',
            });
        },
    });

    return (
        <>
            <Button variant="outline" onClick={() => setOpen(true)}>
                <Ban size={16} /> Cancel run
            </Button>
            <AlertDialog open={open} onOpenChange={setOpen}>
                <AlertDialogContent className="max-w-md rounded-3xl p-6 md:p-8">
                    <AlertDialogHeader>
                        <AlertDialogTitle className="text-xl font-bold text-ink">Cancel run #{runId}?</AlertDialogTitle>
                        <AlertDialogDescription className="leading-relaxed text-ink-soft">
                            Queued cases will not start. A case that is running stops before its next step and
                            still runs its suite teardown and clean-up. The run ends as <strong>Cancelled</strong>:
                            it counts neither as a pass nor a failure, and nobody is notified.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter className="gap-2">
                        <AlertDialogCancel className="mt-0 rounded-full">Keep running</AlertDialogCancel>
                        <AlertDialogAction
                            onClick={(e) => { e.preventDefault(); mutation.mutate(); }}
                            disabled={mutation.isPending}
                            className="rounded-full bg-ink text-white hover:bg-ink/90"
                        >
                            {mutation.isPending ? 'Cancelling…' : 'Cancel run'}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </>
    );
}

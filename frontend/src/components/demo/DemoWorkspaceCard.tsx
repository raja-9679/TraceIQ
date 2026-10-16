import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import {
    AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
    AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { SettingsCard } from '@/components/redesign-local/groupD/settingsForm';
import { errorText } from '@/api/solution';
import DemoBadge from './DemoBadge';
import { switchToProject, useDemoWorkspace } from './useDemoWorkspace';

/**
 * Create / open / delete the caller's seeded demo workspace (decision C5).
 * One per person; deleting it is the ordinary workspace purge.
 */
export default function DemoWorkspaceCard() {
    const { demo, create, remove } = useDemoWorkspace();
    const [confirming, setConfirming] = useState(false);
    const d = demo.data;

    return (
        <SettingsCard
            labelledBy="demo-ws-title"
            title={<>Demo workspace <DemoBadge /></>}
            description={d
                ? 'A separate workspace with generated history: two projects, suites, environments, personas, a paused monitor, requirements and 30 days of runs. It never sends notifications.'
                : 'Explore TraceIQ with lived-in data: a separate workspace with two projects and 30 days of generated run history (a flaky test, a failure cluster, a security finding). Nothing is run and nothing is contacted; it never sends notifications. One per person.'}
            footer={d ? (
                <>
                    <Button onClick={() => { if (d.projects[0]) switchToProject(d.projects[0].id); toast.success('Switched to the demo'); }}>
                        Open the demo
                    </Button>
                    <Button variant="outline" className="text-destructive" onClick={() => setConfirming(true)} disabled={remove.isPending}>
                        {remove.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Delete demo workspace
                    </Button>
                </>
            ) : (
                <Button
                    onClick={() => create.mutate(undefined, {
                        onSuccess: (r) => toast.success('Demo workspace created', { description: `${r.runs_seeded ?? 0} runs of history seeded.` }),
                        onError: (e) => toast.error(errorText(e, 'The demo workspace could not be created')),
                    })}
                    disabled={create.isPending || demo.isLoading}
                >
                    {create.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                    {create.isPending ? 'Seeding the demo…' : 'Create demo workspace'}
                </Button>
            )}
        >
            {d && (
                <ul className="flex flex-col gap-1 text-sm text-ink">
                    {d.projects.map((p) => (
                        <li key={p.id} className="flex items-center gap-2">
                            <DemoBadge /> <span className="truncate">{p.name}</span>
                        </li>
                    ))}
                </ul>
            )}
            <AlertDialog open={confirming} onOpenChange={setConfirming}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Delete the demo workspace?</AlertDialogTitle>
                        <AlertDialogDescription>
                            Removes the demo workspace and everything in it, including anything you added there. Your real workspaces are not touched.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Keep it</AlertDialogCancel>
                        <Button
                            variant="destructive"
                            onClick={() => remove.mutate(undefined, {
                                onSuccess: () => { setConfirming(false); toast.success('Demo workspace deleted'); },
                                onError: (e) => toast.error(errorText(e, 'The demo workspace could not be deleted')),
                            })}
                            disabled={remove.isPending}
                        >
                            Delete demo workspace
                        </Button>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </SettingsCard>
    );
}

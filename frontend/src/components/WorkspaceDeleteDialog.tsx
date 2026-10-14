import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import {
    AlertDialog,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { deleteWorkspace, previewWorkspaceDeletion } from '@/lib/api';

// DELETE /workspaces/{id} purges everything reachable from the workspace
// (app/services/purge.py) and so refuses without confirm=<exact name>. This
// dialog shows the dry-run counts first, then asks for the name.

const SUMMARY: [table: string, one: string, many: string][] = [
    ['project', 'project', 'projects'],
    ['testsuite', 'suite', 'suites'],
    ['testcase', 'test case', 'test cases'],
    ['testrun', 'run', 'runs'],
    ['team', 'team', 'teams'],
];

const errorDetail = (e: unknown) =>
    (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'The workspace could not be deleted.';

export default function WorkspaceDeleteDialog({ workspace, open, onOpenChange, onDeleted }: {
    workspace: { id: number; name: string };
    open: boolean;
    onOpenChange: (open: boolean) => void;
    onDeleted: () => void;
}) {
    const [typed, setTyped] = useState('');
    const preview = useQuery({
        queryKey: ['workspace-delete-preview', workspace.id],
        queryFn: () => previewWorkspaceDeletion(workspace.id),
        enabled: open,
        staleTime: 0,
    });
    const remove = useMutation({
        mutationFn: () => deleteWorkspace(workspace.id, typed),
        onSuccess: onDeleted,
    });
    const rows: Record<string, number> = preview.data?.rows_deleted ?? {};
    const parts = SUMMARY.filter(([t]) => rows[t]).map(([t, one, many]) => `${rows[t]} ${rows[t] === 1 ? one : many}`);
    const matches = typed === workspace.name;

    return (
        <AlertDialog open={open} onOpenChange={(o) => { if (!o) setTyped(''); onOpenChange(o); }}>
            <AlertDialogContent>
                <AlertDialogHeader>
                    <AlertDialogTitle>Delete workspace “{workspace.name}”?</AlertDialogTitle>
                    <AlertDialogDescription asChild>
                        <div className="space-y-2">
                            <p>This permanently deletes everything in the workspace, including stored screenshots, videos and other artifacts. It cannot be undone.</p>
                            {preview.isLoading && <p className="flex items-center gap-2"><Loader2 className="h-3.5 w-3.5 animate-spin" /> Checking what will be removed…</p>}
                            {preview.data && (
                                <p>
                                    Will remove {parts.length ? parts.join(', ') : 'the workspace itself'}
                                    {' '}({preview.data.total_rows_deleted} records in total). Audit history is kept.
                                </p>
                            )}
                            {preview.isError && <p className="text-destructive">{errorDetail(preview.error)}</p>}
                        </div>
                    </AlertDialogDescription>
                </AlertDialogHeader>

                <div className="space-y-1.5">
                    <label htmlFor="ws-delete-confirm" className="text-sm font-medium">
                        Type the workspace name, <strong>{workspace.name}</strong>, to confirm
                    </label>
                    <Input
                        id="ws-delete-confirm"
                        value={typed}
                        onChange={(e) => setTyped(e.target.value)}
                        autoComplete="off"
                    />
                    {remove.isError && <p className="text-sm text-destructive">{errorDetail(remove.error)}</p>}
                </div>

                <AlertDialogFooter>
                    <AlertDialogCancel>Cancel</AlertDialogCancel>
                    <Button
                        variant="destructive"
                        disabled={!matches || remove.isPending}
                        onClick={() => remove.mutate()}
                    >
                        {remove.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                        Delete workspace
                    </Button>
                </AlertDialogFooter>
            </AlertDialogContent>
        </AlertDialog>
    );
}

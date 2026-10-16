import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { FieldRow, SettingsCard } from '@/components/redesign-local/groupD/settingsForm';
import { getDataPolicy } from '@/api/dataPolicy';
import {
    NOTIFY_MODE_LABELS, listMyProjectNotifications, setMyProjectNotification, type ProjectNotifyMode,
} from '@/api/projectNotifications';

const linkClass = 'font-medium text-ink underline underline-offset-2';

/**
 * The selected project's capture level, read-only. It is the ceiling for the
 * personal recording choices on this page, but it belongs to the project.
 */
export function CaptureLevelPointer({ projectId }: { projectId: number | null }) {
    const policy = useQuery({
        queryKey: ['data-policy', projectId],
        queryFn: () => getDataPolicy(projectId as number),
        enabled: !!projectId,
    });
    const level = policy.data?.effective.capture_level;
    return (
        <SettingsCard
            labelledBy="settings-capture-pointer"
            title="What the project allows"
            description={projectId
                ? <>This project&apos;s capture level is <strong className="text-ink">{level ?? '…'}</strong>
                    {level && level !== 'full' && ', so no video or Playwright traces are recorded, whatever you choose below'}
                    . It is set for the whole project in{' '}
                    <Link to="/project-settings" className={linkClass}>Project settings → Data capture and retention</Link>.</>
                : 'Select a project in the sidebar to see what its runs may record.'}
        />
    );
}

/** The person's choice for each project they can see; changes save at once. */
export function ProjectChoicesCard() {
    const queryClient = useQueryClient();
    const list = useQuery({ queryKey: ['my-project-notifications'], queryFn: listMyProjectNotifications });
    const save = useMutation({
        mutationFn: ({ projectId, mode }: { projectId: number; mode: ProjectNotifyMode }) => setMyProjectNotification(projectId, mode),
        onSuccess: (_data, vars) => {
            queryClient.invalidateQueries({ queryKey: ['my-project-notifications'] });
            queryClient.invalidateQueries({ queryKey: ['my-project-notification', vars.projectId] });
            toast.success('Saved');
        },
        onError: (e: any) => toast.error(e?.response?.data?.detail || 'Failed to save'),
    });
    return (
        <SettingsCard
            labelledBy="settings-project-choices"
            title="Per project"
            description="Override your default for a single project. Applies only to projects that email you (your runs, your schedules, or where the project admin added you)."
        >
            <div className="flex flex-col">
                {list.isLoading && <p className="text-sm text-ink-soft">Loading projects…</p>}
                {list.data?.length === 0 && <p className="text-sm text-ink-soft">You are not on any project yet.</p>}
                {list.data?.map((p) => (
                    <FieldRow key={p.project_id} label={p.project_name} htmlFor={`pc-${p.project_id}`}>
                        <Select value={p.mode} disabled={save.isPending}
                            onValueChange={(v) => save.mutate({ projectId: p.project_id, mode: v as ProjectNotifyMode })}>
                            <SelectTrigger id={`pc-${p.project_id}`} className="h-10 w-full rounded-full"><SelectValue /></SelectTrigger>
                            <SelectContent>
                                {(Object.keys(NOTIFY_MODE_LABELS) as ProjectNotifyMode[]).map((m) => (
                                    <SelectItem key={m} value={m}>{NOTIFY_MODE_LABELS[m]}</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </FieldRow>
                ))}
            </div>
        </SettingsCard>
    );
}

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
    CheckRow, FieldRow, RowGroup, SettingsCard, checkboxClass, inputClass, textareaClass,
} from '@/components/redesign-local/groupD/settingsForm';
import {
    NOTIFY_MODE_LABELS, getMyProjectNotification, getNotificationRecipients, getProjectNotifications,
    setMyProjectNotification, updateProjectNotifications,
    type ProjectNotificationPatch, type ProjectNotificationSettings, type ProjectNotifyMode,
} from '@/api/projectNotifications';
import {
    chatDraftFrom, chatPatch, emailDraftFrom, emailPatch, isEmpty, type Chat, type ChatDraft, type EmailDraft,
} from './notificationHelpers';

const errDetail = (e: any) => e?.response?.data?.detail || e?.message || 'Request failed';

/**
 * Who hears about THIS project's runs, and where. Project admins edit; other
 * members see it read-only (the server enforces the same). Each card saves
 * only its own fields.
 */
export function ProjectNotificationsSection({ projectId, canEdit }: { projectId: number; canEdit: boolean }) {
    const query = useQuery({
        queryKey: ['project-notifications', projectId],
        queryFn: () => getProjectNotifications(projectId),
    });
    if (query.isLoading) {
        return (
            <section className="rounded-3xl border border-border bg-white p-6">
                <p className="flex items-center gap-2 text-sm text-ink-soft"><Loader2 className="h-4 w-4 animate-spin" /> Loading notification settings…</p>
            </section>
        );
    }
    if (query.isError || !query.data) {
        return (
            <section className="rounded-3xl border border-border bg-white p-6">
                <p className="text-sm text-destructive" role="alert">Could not load this project&apos;s notification settings.</p>
            </section>
        );
    }
    const saved = query.data;
    // Re-key on the saved content so drafts re-seed after a save or project switch.
    const key = `${projectId}-${JSON.stringify(saved)}`;
    return (
        <>
            <MyProjectNotificationCard projectId={projectId} />
            <EmailCard key={`e-${key}`} projectId={projectId} saved={saved} canEdit={canEdit} />
            <ChatCard key={`s-${key}`} projectId={projectId} saved={saved} canEdit={canEdit} which="slack" />
            <ChatCard key={`t-${key}`} projectId={projectId} saved={saved} canEdit={canEdit} which="teams" />
        </>
    );
}

function useSaveProjectNotifications(projectId: number) {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: (patch: ProjectNotificationPatch) => updateProjectNotifications(projectId, patch),
        onSuccess: (data) => {
            queryClient.setQueryData(['project-notifications', projectId], data);
            toast.success('Notification settings saved');
        },
        onError: (e) => toast.error(errDetail(e)),
    });
}

function CardFooter({ dirty, pending, onSave, onDiscard, canEdit }: {
    dirty: boolean; pending: boolean; onSave: () => void; onDiscard: () => void; canEdit: boolean;
}) {
    if (!canEdit) {
        return <span className="text-xs text-ink-soft">Only project admins can change this.</span>;
    }
    return (
        <>
            <Button onClick={onSave} disabled={pending || !dirty}>
                {pending && <Loader2 className="h-4 w-4 animate-spin" />}
                {pending ? 'Saving…' : 'Save'}
            </Button>
            {dirty && <Button variant="outline" onClick={onDiscard} disabled={pending}>Discard</Button>}
            <span className="text-xs text-ink-soft" aria-live="polite">{dirty ? 'Unsaved changes.' : 'Saved.'}</span>
        </>
    );
}

function MyProjectNotificationCard({ projectId }: { projectId: number }) {
    const queryClient = useQueryClient();
    const mine = useQuery({ queryKey: ['my-project-notification', projectId], queryFn: () => getMyProjectNotification(projectId) });
    const save = useMutation({
        mutationFn: (mode: ProjectNotifyMode) => setMyProjectNotification(projectId, mode),
        onSuccess: (data) => {
            queryClient.setQueryData(['my-project-notification', projectId], data);
            queryClient.invalidateQueries({ queryKey: ['my-project-notifications'] });
            toast.success('Your choice for this project is saved');
        },
        onError: (e) => toast.error(errDetail(e)),
    });
    return (
        <SettingsCard
            labelledBy="pn-mine"
            title="Your notifications for this project"
            description="Only matters when you are one of this project's email recipients: you started the run, or the project admin added you below."
        >
            <FieldRow label="Email me about" htmlFor="pn-mine-mode" hint="'My default' follows My settings → Notifications.">
                <Select
                    value={mine.data?.mode ?? 'default'}
                    onValueChange={(v) => save.mutate(v as ProjectNotifyMode)}
                    disabled={!mine.data || save.isPending}
                >
                    <SelectTrigger id="pn-mine-mode" className="h-10 w-full rounded-full"><SelectValue /></SelectTrigger>
                    <SelectContent>
                        {(Object.keys(NOTIFY_MODE_LABELS) as ProjectNotifyMode[]).map((m) => (
                            <SelectItem key={m} value={m}>{NOTIFY_MODE_LABELS[m]}</SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            </FieldRow>
        </SettingsCard>
    );
}

function EmailCard({ projectId, saved, canEdit }: { projectId: number; saved: ProjectNotificationSettings; canEdit: boolean }) {
    const [draft, setDraft] = useState<EmailDraft>(() => emailDraftFrom(saved));
    const people = useQuery({ queryKey: ['notification-recipients', projectId], queryFn: () => getNotificationRecipients(projectId) });
    const save = useSaveProjectNotifications(projectId);
    const patch = emailPatch(saved, draft);
    const set = (p: Partial<EmailDraft>) => setDraft((d) => ({ ...d, ...p }));
    const toggle = (id: number, on: boolean) => set({
        recipient_user_ids: on ? [...draft.recipient_user_ids, id] : draft.recipient_user_ids.filter((x) => x !== id),
    });
    return (
        <SettingsCard
            labelledBy="pn-email"
            title="Email"
            description="Who is emailed about this project's runs. Only people with access to this project can be picked, and anyone who loses access stops getting mail."
            footer={<CardFooter dirty={!isEmpty(patch)} pending={save.isPending} canEdit={canEdit}
                onSave={() => save.mutate(patch)} onDiscard={() => setDraft(emailDraftFrom(saved))} />}
        >
            <div className="flex flex-col">
                <CheckRow id="pn-email-on" label="Send email about this project's runs" checked={draft.email_enabled}
                    disabled={!canEdit} onChange={(v) => set({ email_enabled: v })} />
                <CheckRow id="pn-failures" label="Only for failed runs" hint="Skip runs where every test passed."
                    checked={draft.failures_only} disabled={!canEdit} onChange={(v) => set({ failures_only: v })} />
                <CheckRow id="pn-case-owners" label="Tell a test's owner when it starts failing"
                    hint="One email to the owner (or the backup owner) the first time a test fails after passing."
                    checked={draft.notify_case_owners} disabled={!canEdit} onChange={(v) => set({ notify_case_owners: v })} />
                <RowGroup title="People">
                    <p className="mb-1 text-xs leading-relaxed text-ink-soft">
                        The person who started a run is always included. Tick anyone else who should hear about every run;
                        each of them can still turn it down for themselves.
                    </p>
                    {people.isLoading && <p className="text-xs text-ink-soft">Loading members…</p>}
                    {people.data?.length === 0 && <p className="text-xs text-ink-soft">No members yet.</p>}
                    {people.data?.map((u) => (
                        <label key={u.id} htmlFor={`pn-person-${u.id}`}
                            className={`flex min-h-10 items-center justify-between gap-4 py-1 text-sm ${canEdit ? 'cursor-pointer' : 'cursor-not-allowed opacity-60'}`}>
                            <span className="min-w-0">
                                <span className="block font-medium text-ink">{u.full_name || u.email}</span>
                                {u.full_name && <span className="block text-xs text-ink-soft">{u.email}</span>}
                            </span>
                            <input id={`pn-person-${u.id}`} type="checkbox" className={checkboxClass} disabled={!canEdit}
                                checked={draft.recipient_user_ids.includes(u.id)} onChange={(e) => toggle(u.id, e.target.checked)} />
                        </label>
                    ))}
                </RowGroup>
                <RowGroup title="Other addresses">
                    <label htmlFor="pn-extra" className="mb-1 text-xs leading-relaxed text-ink-soft">
                        Lists or people outside TraceIQ, one per line (up to 20).
                    </label>
                    <textarea id="pn-extra" rows={3} className={textareaClass} disabled={!canEdit}
                        value={draft.extraText} onChange={(e) => set({ extraText: e.target.value })}
                        placeholder={'qa-team@yourcompany.com'} />
                </RowGroup>
            </div>
        </SettingsCard>
    );
}

const CHAT_COPY: Record<Chat, { title: string; placeholder: string; hint: string }> = {
    slack: {
        title: 'Slack',
        placeholder: 'https://hooks.slack.com/services/…',
        hint: 'An incoming-webhook URL for this project\'s channel.',
    },
    teams: {
        title: 'Microsoft Teams',
        placeholder: 'https://….webhook.office.com/…',
        hint: 'An incoming-webhook URL for this project\'s channel.',
    },
};

function ChatCard({ projectId, saved, canEdit, which }: {
    projectId: number; saved: ProjectNotificationSettings; canEdit: boolean; which: Chat;
}) {
    const [draft, setDraft] = useState<ChatDraft>(() => chatDraftFrom(saved, which));
    const save = useSaveProjectNotifications(projectId);
    const patch = chatPatch(saved, which, draft);
    const copy = CHAT_COPY[which];
    const isSet = which === 'slack' ? saved.slack_webhook_set : saved.teams_webhook_set;
    const hint = which === 'slack' ? saved.slack_webhook_hint : saved.teams_webhook_hint;
    const id = `pn-${which}`;
    return (
        <SettingsCard
            labelledBy={id}
            title={copy.title}
            description="Posts go only to this project's own webhook, never to a channel shared with other projects."
            footer={<CardFooter dirty={!isEmpty(patch)} pending={save.isPending} canEdit={canEdit}
                onSave={() => save.mutate(patch)} onDiscard={() => setDraft(chatDraftFrom(saved, which))} />}
        >
            <div className="flex flex-col">
                <CheckRow id={`${id}-on`} label={`Post this project's runs to ${copy.title}`}
                    hint={!isSet && !draft.newUrl ? 'Add a webhook below first.' : undefined}
                    checked={draft.enabled && !draft.remove} disabled={!canEdit || draft.remove}
                    onChange={(v) => setDraft((d) => ({ ...d, enabled: v }))} />
                <FieldRow label="Webhook" htmlFor={`${id}-url`} wide
                    hint={isSet && !draft.remove ? `Saved: ${hint ?? 'set'}. Paste a new one to replace it.` : copy.hint}>
                    <Input id={`${id}-url`} type="url" className={inputClass} disabled={!canEdit || draft.remove}
                        value={draft.newUrl} placeholder={isSet ? 'Keep the saved webhook' : copy.placeholder}
                        onChange={(e) => setDraft((d) => ({ ...d, newUrl: e.target.value }))} autoComplete="off" />
                </FieldRow>
                {isSet && canEdit && (
                    <div className="flex items-center gap-3 py-1">
                        <Button variant="outline" onClick={() => setDraft((d) => ({ ...d, remove: !d.remove, newUrl: '' }))}>
                            {draft.remove ? 'Keep the webhook' : 'Remove the webhook'}
                        </Button>
                        {draft.remove && <span className="text-xs text-ink-soft">Save to remove it; posting turns off.</span>}
                    </div>
                )}
            </div>
        </SettingsCard>
    );
}

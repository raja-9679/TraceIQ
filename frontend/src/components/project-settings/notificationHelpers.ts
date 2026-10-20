import type { ProjectNotificationPatch, ProjectNotificationSettings } from '@/api/projectNotifications';

/** Textarea → address list: one per line (commas and semicolons also split), trimmed, de-duplicated. */
export function emailsFromText(text: string): string[] {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const raw of text.split(/[\n,;]+/)) {
        const v = raw.trim();
        if (v && !seen.has(v.toLowerCase())) {
            seen.add(v.toLowerCase());
            out.push(v);
        }
    }
    return out;
}

export interface EmailDraft {
    email_enabled: boolean;
    failures_only: boolean;
    notify_case_owners: boolean;
    recipient_user_ids: number[];
    extraText: string;
}

export const emailDraftFrom = (s: ProjectNotificationSettings): EmailDraft => ({
    email_enabled: s.email_enabled,
    failures_only: s.failures_only,
    notify_case_owners: !!s.notify_case_owners,
    recipient_user_ids: [...s.recipient_user_ids],
    extraText: s.extra_emails.join('\n'),
});

const sameSet = (a: number[], b: number[]) => a.length === b.length && a.every((x) => b.includes(x));
const sameList = (a: string[], b: string[]) => a.length === b.length && a.every((x, i) => x === b[i]);

/** Only what changed, so saving the email card never touches the webhooks. */
export function emailPatch(saved: ProjectNotificationSettings, draft: EmailDraft): ProjectNotificationPatch {
    const patch: ProjectNotificationPatch = {};
    if (draft.email_enabled !== saved.email_enabled) patch.email_enabled = draft.email_enabled;
    if (draft.failures_only !== saved.failures_only) patch.failures_only = draft.failures_only;
    if (draft.notify_case_owners !== !!saved.notify_case_owners) patch.notify_case_owners = draft.notify_case_owners;
    if (!sameSet(draft.recipient_user_ids, saved.recipient_user_ids)) patch.recipient_user_ids = draft.recipient_user_ids;
    const extras = emailsFromText(draft.extraText);
    if (!sameList(extras, saved.extra_emails)) patch.extra_emails = extras;
    return patch;
}

export type Chat = 'slack' | 'teams';

export interface ChatDraft {
    enabled: boolean;
    /** A new URL typed in; '' means keep what is stored. */
    newUrl: string;
    remove: boolean;
}

export const chatDraftFrom = (s: ProjectNotificationSettings, which: Chat): ChatDraft => ({
    enabled: which === 'slack' ? s.slack_enabled : s.teams_enabled,
    newUrl: '',
    remove: false,
});

export function chatPatch(saved: ProjectNotificationSettings, which: Chat, draft: ChatDraft): ProjectNotificationPatch {
    const patch: ProjectNotificationPatch = {};
    const enabledKey = which === 'slack' ? 'slack_enabled' : 'teams_enabled';
    const urlKey = which === 'slack' ? 'slack_webhook' : 'teams_webhook';
    const isSet = which === 'slack' ? saved.slack_webhook_set : saved.teams_webhook_set;
    if (draft.remove && isSet) patch[urlKey] = '';
    else if (draft.newUrl.trim()) patch[urlKey] = draft.newUrl.trim();
    // Removing the webhook also switches the channel off, so it can't be left
    // "enabled" with nowhere to post.
    const enabled = draft.remove ? false : draft.enabled;
    if (enabled !== saved[enabledKey]) patch[enabledKey] = enabled;
    return patch;
}

export const isEmpty = (p: object) => Object.keys(p).length === 0;

import { describe, expect, it } from 'vitest';
import type { ProjectNotificationSettings } from '@/api/projectNotifications';
import { chatDraftFrom, chatPatch, emailDraftFrom, emailPatch, emailsFromText, isEmpty } from './notificationHelpers';

const saved: ProjectNotificationSettings = {
    email_enabled: true, recipient_user_ids: [2, 5], extra_emails: ['list@corp.example.com'],
    failures_only: false, slack_enabled: true, teams_enabled: false,
    slack_webhook_set: true, slack_webhook_hint: 'https://hooks.slack.com/…abcd',
    teams_webhook_set: false, teams_webhook_hint: null,
};

describe('project notification helpers', () => {
    it('splits addresses on lines, commas and semicolons, de-duplicating', () => {
        expect(emailsFromText(' a@x.com\nb@x.com, A@x.com;c@x.com \n\n')).toEqual(['a@x.com', 'b@x.com', 'c@x.com']);
    });

    it('the owner-notification switch is sent only when changed (absent on old servers = off)', () => {
        expect(isEmpty(emailPatch(saved, emailDraftFrom(saved)))).toBe(true);
        expect(emailPatch(saved, { ...emailDraftFrom(saved), notify_case_owners: true })).toEqual({ notify_case_owners: true });
    });

    it('an untouched email card sends nothing', () => {
        expect(isEmpty(emailPatch(saved, emailDraftFrom(saved)))).toBe(true);
        // Same recipients in a different order is not a change.
        expect(isEmpty(emailPatch(saved, { ...emailDraftFrom(saved), recipient_user_ids: [5, 2] }))).toBe(true);
    });

    it('the email card sends only what changed, never webhooks', () => {
        const draft = { ...emailDraftFrom(saved), failures_only: true, extraText: 'list@corp.example.com\nqa@corp.example.com' };
        expect(emailPatch(saved, draft)).toEqual({ failures_only: true, extra_emails: ['list@corp.example.com', 'qa@corp.example.com'] });
    });

    it('chat: keep, replace, remove', () => {
        expect(isEmpty(chatPatch(saved, 'slack', chatDraftFrom(saved, 'slack')))).toBe(true);
        expect(chatPatch(saved, 'slack', { enabled: true, newUrl: ' https://hooks.slack.com/new ', remove: false }))
            .toEqual({ slack_webhook: 'https://hooks.slack.com/new' });
        // Removing also switches the channel off.
        expect(chatPatch(saved, 'slack', { enabled: true, newUrl: '', remove: true }))
            .toEqual({ slack_webhook: '', slack_enabled: false });
    });

    it('chat: turning Teams on with a new URL', () => {
        expect(chatPatch(saved, 'teams', { enabled: true, newUrl: 'https://x.webhook.office.com/a', remove: false }))
            .toEqual({ teams_webhook: 'https://x.webhook.office.com/a', teams_enabled: true });
    });
});

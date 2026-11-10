import { describe, expect, it } from 'vitest';
import { countLabel, notificationText, resultLink, summaryLabel } from './text';

describe('notificationText', () => {
    const actor = { id: 1, name: 'Maya Rao' };
    it('has a sentence for each kind', () => {
        expect(notificationText({ kind: 'assigned', actor, test_name: 'Sign in' })).toBe('Maya Rao asked you to look at “Sign in”');
        expect(notificationText({ kind: 'comment', actor, test_name: 'Sign in' })).toBe('Maya Rao commented on “Sign in”');
        expect(notificationText({ kind: 'assignment_done', actor, test_name: 'Sign in' })).toBe('Maya Rao marked “Sign in” as done');
    });
    it('copes with a missing actor and an unknown kind', () => {
        expect(notificationText({ kind: 'comment', actor: null, test_name: 'A' })).toBe('Someone commented on “A”');
        expect(notificationText({ kind: 'something-new', actor, test_name: 'A' })).toBe('Maya Rao updated “A”');
    });
});

describe('summaryLabel', () => {
    const sam = { id: 2, name: 'Sam' };
    it('joins who is on it and how many comments', () => {
        expect(summaryLabel({ comments: 2, assignee: sam, status: 'open', carried: false })).toBe('Sam · 2 comments');
        expect(summaryLabel({ comments: 1, assignee: null, status: null, carried: false })).toBe('1 comment');
        expect(summaryLabel({ comments: 0, assignee: sam, status: 'done', carried: false })).toBe('Sam (done)');
    });
    it('says nothing when there is nothing', () => {
        expect(summaryLabel(undefined)).toBeNull();
        expect(summaryLabel({ comments: 0, assignee: null, status: null, carried: false })).toBeNull();
    });
});

describe('countLabel and resultLink', () => {
    it('hides zero and caps at 99+', () => {
        expect(countLabel(0)).toBeNull();
        expect(countLabel(7)).toBe('7');
        expect(countLabel(100)).toBe('99+');
    });
    it('links to the run, opened on the result', () => {
        expect(resultLink(812, 4031)).toBe('/runs/812?result=4031');
    });
});

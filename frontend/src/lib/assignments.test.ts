import { describe, expect, it } from 'vitest';
import { assignmentOutcomes, describeOutcome } from './assignments';

describe('tenant-admin assignment outcomes', () => {
    it('reads a bare list or a wrapped one, and nothing else', () => {
        const row = { workspace_id: 1, status: 'updated', role: 'admin', previous_role: 'member' };
        expect(assignmentOutcomes([row])).toHaveLength(1);
        expect(assignmentOutcomes({ results: [row] })).toHaveLength(1);
        expect(assignmentOutcomes({ message: 'ok' })).toEqual([]);
    });
    it('says what happened', () => {
        expect(describeOutcome({ status: 'updated', role: 'admin', previous_role: 'member' })).toBe('Member → Admin');
        expect(describeOutcome({ status: 'unchanged', role: 'Workspace Member' })).toBe('Already Member — unchanged');
        expect(describeOutcome({ status: 'error', message: 'last admin' })).toBe('last admin');
    });
});

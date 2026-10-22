import { describe, expect, it } from 'vitest';
import { isWorkspaceAdminRole, workspaceRoleLabel } from './roles';

describe('workspace role labels', () => {
    it('reads both spellings', () => {
        expect(isWorkspaceAdminRole('Workspace Admin')).toBe(true);
        expect(isWorkspaceAdminRole('admin')).toBe(true);
        expect(isWorkspaceAdminRole('Workspace Member')).toBe(false);
        expect(workspaceRoleLabel('Workspace Admin')).toBe('Admin');
        expect(workspaceRoleLabel('member')).toBe('Member');
        expect(workspaceRoleLabel('Tenant Admin')).toBe('Tenant Admin');
    });
});

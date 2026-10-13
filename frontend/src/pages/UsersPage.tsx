import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
    getWorkspaces,
    getWorkspaceMembersDetailed,
    getWorkspaceInvitations,
    inviteUserToWorkspace,
    createWorkspace,
    removeUserFromWorkspace,
    getAdminUsers,
    getRoles,
    getProjects,
    inviteUserToProject,
    Workspace,
    DetailedMember,
    Project,
    Role
} from '@/lib/api';
import { usePermission } from '@/hooks/usePermission';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from "@/components/ui/dialog";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Trash2, Search, UserPlus } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { toast } from 'sonner';
import {
    DetailCard, EmptyState, ListGroup, ListRow, PageHeader, Panel, Pill, SplitView,
} from '@/components/redesign/primitives';
import { Avatar, Facts, Field, ListSkeleton } from '@/components/redesign-local/groupE/bits';

// The tenant-wide ("all") listing adds the workspace name to each row.
const workspaceOf = (m: DetailedMember): string | undefined => (m as DetailedMember & { workspace?: string }).workspace;
const isAdminRole = (role?: string) => role === 'Workspace Admin' || role === 'admin';
const inputCls = 'h-10 rounded-full px-4';

export default function UsersPage() {
    const queryClient = useQueryClient();
    const { can } = usePermission();

    // Check for Tenant Admin scope
    const isTenantAdmin = can('tenant:manage_settings');

    // selectedWorkspaceId can be number or 'all'
    const [pickedWorkspaceId, setSelectedWorkspaceId] = useState<number | 'all' | null>(null);
    const [selectedKey, setSelectedKey] = useState<string | null>(null);
    const [searchTerm, setSearchTerm] = useState('');
    const [showInviteDialog, setShowInviteDialog] = useState(false);
    const [inviteEmail, setInviteEmail] = useState('');
    const [inviteRole, setInviteRole] = useState('member'); // Default fall back
    const [inviteTargetWorkspaceId, setInviteTargetWorkspaceId] = useState<string>('current');
    const [newWorkspaceName, setNewWorkspaceName] = useState('');
    const [deleteTarget, setDeleteTarget] = useState<DetailedMember | null>(null);
    const [inviteScope, setInviteScope] = useState<'workspace' | 'project'>('workspace');
    const [inviteProjectId, setInviteProjectId] = useState<string>('');
    const [inviteProjectRole, setInviteProjectRole] = useState('viewer');

    // Fetch Workspaces
    const { data: workspaces } = useQuery<Workspace[]>({
        queryKey: ['workspaces'],
        queryFn: getWorkspaces,
    });

    // Until the user picks a scope: Tenant Admins default to "all", everyone
    // else to their first workspace. Derived rather than set in an effect.
    const selectedWorkspaceId: number | 'all' | null = pickedWorkspaceId
        ?? (workspaces && workspaces.length > 0 ? (isTenantAdmin ? 'all' : workspaces[0].id) : null);

    // Fetch Roles
    const { data: roles } = useQuery<Role[]>({
        queryKey: ['roles'],
        queryFn: getRoles,
    });

    // Filter for Workspace roles
    const workspaceRoles = roles?.filter(r => r.name.startsWith('Workspace')) || [];

    // Fetch Members
    const { data: members, isLoading: membersLoading } = useQuery<DetailedMember[]>({
        queryKey: ['workspaceMembersDetailed', selectedWorkspaceId],
        queryFn: () => {
            if (selectedWorkspaceId === 'all') {
                return getAdminUsers();
            }
            return getWorkspaceMembersDetailed(selectedWorkspaceId as number);
        },
        enabled: !!selectedWorkspaceId,
    });

    // Fetch Invitations
    const { data: invitations, isLoading: invitationsLoading } = useQuery<DetailedMember[]>({
        queryKey: ['workspaceInvitations', selectedWorkspaceId],
        queryFn: () => getWorkspaceInvitations(selectedWorkspaceId as number),
        enabled: !!selectedWorkspaceId && selectedWorkspaceId !== 'all', // Disable for All view
    });

    // Fetch Projects for Invite (if scope is project)
    // We need projects of the *target* workspace.
    // If inviteTargetWorkspaceId is set, fetch projects for that workspace.
    const targetWorkspaceForProjects = inviteTargetWorkspaceId === 'current' ? (selectedWorkspaceId !== 'all' ? selectedWorkspaceId : null) : (inviteTargetWorkspaceId === 'new' ? null : parseInt(inviteTargetWorkspaceId));

    const { data: projects } = useQuery<Project[]>({
        queryKey: ['workspaceProjects', targetWorkspaceForProjects],
        queryFn: () => getProjects(targetWorkspaceForProjects as number),
        enabled: !!targetWorkspaceForProjects && inviteScope === 'project'
    });

    // Remove Mutation
    const removeMutation = useMutation({
        mutationFn: (userId: number) => removeUserFromWorkspace(selectedWorkspaceId as number, userId),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['workspaceMembersDetailed', selectedWorkspaceId] });
            setDeleteTarget(null);
            toast.success('User removed from workspace');
        },
        onError: (error: any) => {
            toast.error('Failed to remove user: ' + (error.response?.data?.detail || error.message));
        }
    });

    const handleInvite = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!inviteEmail) return;

        try {
            let targetWorkspaceId: number;

            // Handle "Create New" or "Switch Workspace" logic
            if (inviteTargetWorkspaceId === 'new') {
                if (inviteScope === 'project') {
                    toast.error("Cannot create new workspace when inviting to a project.");
                    return;
                }
                if (!newWorkspaceName) {
                    toast.error("Workspace name is required");
                    return;
                }
                const newWorkspace = await createWorkspace({ name: newWorkspaceName });
                targetWorkspaceId = newWorkspace.id;
                queryClient.invalidateQueries({ queryKey: ['workspaces'] });
                setSelectedWorkspaceId(newWorkspace.id);
            } else if (inviteTargetWorkspaceId !== 'current') {
                targetWorkspaceId = parseInt(inviteTargetWorkspaceId);
            } else {
                if (selectedWorkspaceId === 'all') {
                    toast.error("Please select a specific workspace to invite into.");
                    return;
                }
                targetWorkspaceId = selectedWorkspaceId as number;
            }

            if (inviteScope === 'project') {
                if (!inviteProjectId) {
                    toast.error("Please select a project.");
                    return;
                }
                await inviteUserToProject(parseInt(inviteProjectId), inviteEmail, inviteProjectRole);
            } else {
                await inviteUserToWorkspace(targetWorkspaceId, inviteEmail, inviteRole);
            }

            queryClient.invalidateQueries({ queryKey: ['workspaceInvitations', targetWorkspaceId] });
            queryClient.invalidateQueries({ queryKey: ['workspaceMembersDetailed', targetWorkspaceId] });

            setShowInviteDialog(false);
            setInviteEmail('');
            setNewWorkspaceName('');
            setInviteTargetWorkspaceId('current');
            setInviteRole('member');
            setInviteScope('workspace');
            setInviteProjectId('');

            toast.success(inviteScope === 'project' ? 'Project invitation sent!' : 'Workspace invitation sent!');

        } catch (error: any) {
            toast.error('Failed to process invitation: ' + (error.response?.data?.detail || error.message));
        }
    };

    const confirmRemove = () => {
        if (deleteTarget) {
            removeMutation.mutate(deleteTarget.id);
        }
    };

    // Filter members based on search
    const filteredMembers = members?.filter(m =>
        m.full_name?.toLowerCase().includes(searchTerm.toLowerCase()) ||
        m.email.toLowerCase().includes(searchTerm.toLowerCase()) ||
        (workspaceOf(m) || '').toLowerCase().includes(searchTerm.toLowerCase())
    ) || [];

    // Filter invitations
    const filteredInvitations = invitations?.filter(i =>
        i.email.toLowerCase().includes(searchTerm.toLowerCase())
    ) || [];

    const canManageUsers = selectedWorkspaceId !== 'all' && selectedWorkspaceId ? can('workspace:manage_users', { workspaceId: selectedWorkspaceId }) : isTenantAdmin;
    // Tenant Admin can always invite (via dialog select), but specificOrg context required?
    // We updated dialog to allow Org Selection, so yes.

    if (!selectedWorkspaceId && workspaces?.length === 0) {
        return (
            <div className="flex flex-col gap-5">
                <PageHeader title="Users" />
                <EmptyState title="No workspaces found">You need to belong to a workspace to view users.</EmptyState>
            </div>
        );
    }

    const isAll = selectedWorkspaceId === 'all';
    const memberKey = (m: DetailedMember) => `m:${m.id}:${workspaceOf(m) ?? ''}`;
    const inviteKey = (i: DetailedMember) => `i:${i.id}`;
    const selectedMember = filteredMembers.find((m) => memberKey(m) === selectedKey) ?? null;
    const selectedInvite = filteredInvitations.find((i) => inviteKey(i) === selectedKey) ?? null;
    const nothingPicked = !selectedMember && !selectedInvite;
    const member = selectedMember ?? (nothingPicked ? (filteredMembers[0] ?? null) : null);
    const invite = member ? null : (selectedInvite ?? (nothingPicked ? (filteredInvitations[0] ?? null) : null));
    const scopeName = isAll ? 'all workspaces' : workspaces?.find((w) => w.id === selectedWorkspaceId)?.name;

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Users"
                subtitle={
                    members
                        ? `${filteredMembers.length} member${filteredMembers.length === 1 ? '' : 's'}${!isAll ? ` · ${filteredInvitations.length} pending invite${filteredInvitations.length === 1 ? '' : 's'}` : ''}${scopeName ? ` in ${scopeName}` : ''}`
                        : 'People with access to your workspaces.'
                }
                actions={
                    <>
                        <Select
                            value={selectedWorkspaceId?.toString() || ""}
                            onValueChange={(val) => { setSelectedWorkspaceId(val === 'all' ? 'all' : parseInt(val)); setSelectedKey(null); }}
                        >
                            <SelectTrigger className="h-10 w-[230px] rounded-full px-4" aria-label="Workspace scope">
                                <SelectValue placeholder="Select scope" />
                            </SelectTrigger>
                            <SelectContent>
                                {isTenantAdmin && (
                                    <SelectItem value="all" className="font-semibold">All workspaces</SelectItem>
                                )}
                                {workspaces?.map(ws => (
                                    <SelectItem key={ws.id} value={ws.id.toString()}>{ws.name}</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        {canManageUsers && (
                            <Dialog open={showInviteDialog} onOpenChange={setShowInviteDialog}>
                                <DialogTrigger asChild>
                                    <Button>
                                        <UserPlus className="h-4 w-4" />
                                        Invite people
                                    </Button>
                                </DialogTrigger>
                                <DialogContent>
                                    <DialogHeader>
                                        <DialogTitle>Invite people</DialogTitle>
                                        <DialogDescription>
                                            Send an invitation to join a workspace or a single project.
                                        </DialogDescription>
                                    </DialogHeader>
                                    <form onSubmit={handleInvite} className="flex flex-col gap-4 pt-2">
                                        <Field id="invite-email" label="Email address">
                                            <Input
                                                id="invite-email"
                                                type="email"
                                                placeholder="colleague@company.com"
                                                className={inputCls}
                                                value={inviteEmail}
                                                onChange={(e) => setInviteEmail(e.target.value)}
                                                required
                                            />
                                        </Field>
                                        <Field id="invite-scope" label="Invite to">
                                            <Select value={inviteScope} onValueChange={(val) => setInviteScope(val as 'workspace' | 'project')}>
                                                <SelectTrigger id="invite-scope" className={inputCls}><SelectValue /></SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="workspace">Workspace</SelectItem>
                                                    <SelectItem value="project">Project</SelectItem>
                                                </SelectContent>
                                            </Select>
                                        </Field>

                                        <Field id="invite-workspace" label="Workspace">
                                            <Select
                                                value={inviteTargetWorkspaceId}
                                                onValueChange={(val) => setInviteTargetWorkspaceId(val)}
                                            >
                                                <SelectTrigger id="invite-workspace" className={inputCls}>
                                                    <SelectValue />
                                                </SelectTrigger>
                                                <SelectContent>
                                                    {selectedWorkspaceId !== 'all' && selectedWorkspaceId && (
                                                        <SelectItem value="current">Current: {workspaces?.find(o => o.id === selectedWorkspaceId)?.name}</SelectItem>
                                                    )}
                                                    {workspaces?.filter(o => o.id !== selectedWorkspaceId).map(ws => (
                                                        <SelectItem key={ws.id} value={ws.id.toString()}>
                                                            {ws.name}
                                                        </SelectItem>
                                                    ))}
                                                    {inviteScope !== 'project' && (
                                                        <SelectItem value="new" className="font-medium">
                                                            Create a new workspace
                                                        </SelectItem>
                                                    )}
                                                </SelectContent>
                                            </Select>
                                        </Field>

                                        {inviteTargetWorkspaceId === 'new' && (
                                            <Field id="invite-new-ws" label="New workspace name">
                                                <Input
                                                    id="invite-new-ws"
                                                    placeholder="My New Startup"
                                                    className={inputCls}
                                                    value={newWorkspaceName}
                                                    onChange={(e) => setNewWorkspaceName(e.target.value)}
                                                    required
                                                />
                                            </Field>
                                        )}

                                        {inviteScope === 'project' && (
                                            <Field id="invite-project" label="Project">
                                                <Select value={inviteProjectId} onValueChange={setInviteProjectId}>
                                                    <SelectTrigger id="invite-project" className={inputCls}>
                                                        <SelectValue placeholder="Select a project" />
                                                    </SelectTrigger>
                                                    <SelectContent>
                                                        {projects?.map((p) => (
                                                            <SelectItem key={p.id} value={p.id.toString()}>{p.name}</SelectItem>
                                                        ))}
                                                        {projects?.length === 0 && <div className="p-2 text-sm text-ink-soft">No projects found</div>}
                                                    </SelectContent>
                                                </Select>
                                            </Field>
                                        )}

                                        <Field id="invite-role" label="Role">
                                            {inviteScope === 'project' ? (
                                                <Select value={inviteProjectRole} onValueChange={setInviteProjectRole}>
                                                    <SelectTrigger id="invite-role" className={inputCls}><SelectValue /></SelectTrigger>
                                                    <SelectContent>
                                                        <SelectItem value="admin">Project Admin</SelectItem>
                                                        <SelectItem value="editor">Editor</SelectItem>
                                                        <SelectItem value="viewer">Viewer</SelectItem>
                                                    </SelectContent>
                                                </Select>
                                            ) : (
                                                <Select value={inviteRole} onValueChange={setInviteRole}>
                                                    <SelectTrigger id="invite-role" className={inputCls}>
                                                        <SelectValue />
                                                    </SelectTrigger>
                                                    <SelectContent>
                                                        {workspaceRoles.map(role => (
                                                            <SelectItem key={role.id} value={role.name === "Workspace Admin" ? "admin" : "member"}>
                                                                {role.name.replace("Workspace ", "")}
                                                            </SelectItem>
                                                        ))}
                                                        {workspaceRoles.length === 0 && (
                                                            <SelectItem value="member">Member</SelectItem>
                                                        )}
                                                    </SelectContent>
                                                </Select>
                                            )}
                                        </Field>
                                        <DialogFooter>
                                            <Button type="button" variant="outline" onClick={() => setShowInviteDialog(false)}>Cancel</Button>
                                            <Button type="submit">Send invitation</Button>
                                        </DialogFooter>
                                    </form>
                                </DialogContent>
                            </Dialog>
                        )}
                    </>
                }
            />

            {membersLoading && invitationsLoading ? (
                <ListSkeleton rows={4} />
            ) : (
                <SplitView
                    list={
                        <>
                            <div className="relative">
                                <Search className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-soft" aria-hidden="true" />
                                <Input
                                    type="search"
                                    placeholder={isAll ? 'Search by name, email or workspace' : 'Search by name or email'}
                                    aria-label="Search users"
                                    className="h-10 rounded-full bg-white pl-10 pr-4"
                                    value={searchTerm}
                                    onChange={(e) => setSearchTerm(e.target.value)}
                                />
                            </div>
                            {filteredMembers.length === 0 && filteredInvitations.length === 0 ? (
                                <p className="px-2 py-6 text-center text-sm text-ink-soft">No users found.</p>
                            ) : (
                                <>
                                    {filteredMembers.length > 0 && (
                                        <ListGroup title="Members" count={filteredMembers.length}>
                                            {filteredMembers.map((m) => (
                                                <ListRow
                                                    key={memberKey(m)}
                                                    selected={!!member && memberKey(member) === memberKey(m)}
                                                    onSelect={() => setSelectedKey(memberKey(m))}
                                                    leading={<Avatar name={m.full_name || m.email} />}
                                                    title={m.full_name || 'Unknown name'}
                                                    meta={isAll ? `${m.email} · ${workspaceOf(m) || '-'}` : m.email}
                                                    trailing={
                                                        <Pill tone={isAdminRole(m.role) ? 'lavender' : 'neutral'} className="capitalize">
                                                            {m.role?.replace('Workspace ', '')}
                                                        </Pill>
                                                    }
                                                />
                                            ))}
                                        </ListGroup>
                                    )}
                                    {filteredInvitations.length > 0 && (
                                        <ListGroup title="Pending invites" count={filteredInvitations.length}>
                                            {filteredInvitations.map((i) => (
                                                <ListRow
                                                    key={inviteKey(i)}
                                                    selected={!!invite && inviteKey(invite) === inviteKey(i)}
                                                    onSelect={() => setSelectedKey(inviteKey(i))}
                                                    leading={<Avatar name={i.email} />}
                                                    title={i.email}
                                                    meta="Invitation sent"
                                                    trailing={<Pill tone="butter">Pending</Pill>}
                                                />
                                            ))}
                                        </ListGroup>
                                    )}
                                </>
                            )}
                        </>
                    }
                    detail={member ? (
                        <DetailCard label="Selected member">
                            <div className="flex items-start gap-4">
                                <Avatar name={member.full_name || member.email} />
                                <div className="flex min-w-0 flex-col gap-1">
                                    <h2 className="break-words text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{member.full_name || 'Unknown name'}</h2>
                                    <span className="break-all text-sm text-ink-soft">{member.email}</span>
                                </div>
                            </div>
                            <Facts
                                items={[
                                    {
                                        label: 'Role',
                                        value: <Pill tone={isAdminRole(member.role) ? 'lavender' : 'neutral'} className="capitalize">{member.role?.replace('Workspace ', '')}</Pill>,
                                    },
                                    {
                                        label: 'Status',
                                        value: <Pill tone={member.is_active ? 'mint' : 'neutral'} className="capitalize">{member.status}</Pill>,
                                    },
                                    ...(isAll ? [{ label: 'Workspace', value: workspaceOf(member) || '-' }] : []),
                                    {
                                        label: 'Last active',
                                        value: member.last_login_at
                                            ? formatDistanceToNow(new Date(member.last_login_at), { addSuffix: true })
                                            : 'Never',
                                    },
                                ]}
                            />
                            {canManageUsers && selectedWorkspaceId !== 'all' ? (
                                <div className="flex flex-wrap gap-2.5">
                                    <Button size="lg" variant="outline" className="text-pastel-ink-peach" onClick={() => setDeleteTarget(member)}>
                                        <Trash2 className="h-4 w-4" />
                                        Remove from workspace
                                    </Button>
                                </div>
                            ) : isAll ? (
                                <Panel title="Changing access" callout>
                                    Pick a single workspace in the scope menu to remove people from it.
                                </Panel>
                            ) : null}
                        </DetailCard>
                    ) : invite ? (
                        <DetailCard label="Selected invitation">
                            <div className="flex flex-col gap-2">
                                <Pill tone="butter" className="self-start">Pending</Pill>
                                <h2 className="break-all text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{invite.email}</h2>
                                <span className="text-sm text-ink-soft">Invitation sent{invite.created_at ? ` ${formatDistanceToNow(new Date(invite.created_at), { addSuffix: true })}` : ''}</span>
                            </div>
                            <Facts items={[{ label: 'Role', value: <span className="capitalize">{invite.role}</span> }]} />
                            <Panel title="What happens next" callout>
                                They join with this role once they accept. Revoking an invitation isn't available here yet.
                            </Panel>
                        </DetailCard>
                    ) : (
                        <EmptyState title="No one here yet">
                            {canManageUsers ? 'Invite people to give them access.' : 'Nobody matches this scope or search.'}
                        </EmptyState>
                    )}
                />
            )}

            <AlertDialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Remove user</AlertDialogTitle>
                        <AlertDialogDescription>
                            Are you sure you want to remove <strong>{deleteTarget?.full_name || deleteTarget?.email}</strong> from the workspace?
                            This will also remove them from all teams within the workspace. This action cannot be undone.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                            onClick={confirmRemove}
                            className="bg-destructive hover:bg-destructive/90"
                        >
                            Remove user
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}

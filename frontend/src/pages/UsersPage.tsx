import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
    getOrganizations,
    getOrgMembersDetailed,
    getOrgInvitations,
    inviteUserToOrg,
    createOrganization,
    removeUserFromOrg,
    getAdminUsers,
    getRoles,
    getProjects,
    inviteUserToProject,
    Organization,
    DetailedMember,
    Role
} from '@/lib/api';
import { usePermission } from '@/hooks/usePermission';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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
import { Badge } from "@/components/ui/badge";
import {
    User,
    UserPlus,
    Building,
    MoreVertical,
    Trash2,
    Mail,
    Shield,
    Clock,
    Search,
    Loader2
} from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { toast } from 'sonner';

export default function UsersPage() {
    const queryClient = useQueryClient();
    const { can } = usePermission();

    // Check for Tenant Admin scope
    const isTenantAdmin = can('tenant:manage_settings');

    // selectedOrgId can be number or 'all'
    const [selectedOrgId, setSelectedOrgId] = useState<number | 'all' | null>(null);
    const [searchTerm, setSearchTerm] = useState('');
    const [showInviteDialog, setShowInviteDialog] = useState(false);
    const [inviteEmail, setInviteEmail] = useState('');
    const [inviteRole, setInviteRole] = useState('member'); // Default fall back
    const [inviteTargetOrgId, setInviteTargetOrgId] = useState<string>('current');
    const [newOrgName, setNewOrgName] = useState('');
    const [deleteTarget, setDeleteTarget] = useState<DetailedMember | null>(null);
    const [inviteScope, setInviteScope] = useState<'org' | 'project'>('org');
    const [inviteProjectId, setInviteProjectId] = useState<string>('');
    const [inviteProjectRole, setInviteProjectRole] = useState('viewer');

    // Fetch Organizations
    const { data: organizations } = useQuery<Organization[]>({
        queryKey: ['organizations'],
        queryFn: getOrganizations,
    });

    useEffect(() => {
        if (organizations && organizations.length > 0 && !selectedOrgId) {
            // If Tenant Admin, default to "all" if preferred, or just logic
            if (isTenantAdmin) {
                setSelectedOrgId('all');
            } else {
                setSelectedOrgId(organizations[0].id);
            }
        }
    }, [organizations, selectedOrgId, isTenantAdmin]);

    // Fetch Roles
    const { data: roles } = useQuery<Role[]>({
        queryKey: ['roles'],
        queryFn: getRoles,
    });

    // Filter for Org roles
    const orgRoles = roles?.filter(r => r.name.startsWith('Organization')) || [];

    // Fetch Members
    const { data: members, isLoading: membersLoading } = useQuery<DetailedMember[]>({
        queryKey: ['orgMembersDetailed', selectedOrgId],
        queryFn: () => {
            if (selectedOrgId === 'all') {
                return getAdminUsers();
            }
            return getOrgMembersDetailed(selectedOrgId as number);
        },
        enabled: !!selectedOrgId,
    });

    // Fetch Invitations
    const { data: invitations, isLoading: invitationsLoading } = useQuery<DetailedMember[]>({
        queryKey: ['orgInvitations', selectedOrgId],
        queryFn: () => getOrgInvitations(selectedOrgId as number),
        enabled: !!selectedOrgId && selectedOrgId !== 'all', // Disable for All view
    });

    // Fetch Projects for Invite (if scope is project)
    // We need projects of the *target* org.
    // If inviteTargetOrgId is set, fetch projects for that org.
    const targetOrgForProjects = inviteTargetOrgId === 'current' ? (selectedOrgId !== 'all' ? selectedOrgId : null) : (inviteTargetOrgId === 'new' ? null : parseInt(inviteTargetOrgId));

    const { data: projects } = useQuery<any[]>({
        queryKey: ['orgProjects', targetOrgForProjects],
        queryFn: () => getProjects(targetOrgForProjects as number),
        enabled: !!targetOrgForProjects && inviteScope === 'project'
    });

    // Remove Mutation
    const removeMutation = useMutation({
        mutationFn: (userId: number) => removeUserFromOrg(selectedOrgId as number, userId),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['orgMembersDetailed', selectedOrgId] });
            setDeleteTarget(null);
            toast.success('User removed from organization');
        },
        onError: (error: any) => {
            toast.error('Failed to remove user: ' + (error.response?.data?.detail || error.message));
        }
    });

    const handleInvite = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!inviteEmail) return;

        try {
            let targetOrgId: number;

            // Handle "Create New" or "Switch Org" logic
            if (inviteTargetOrgId === 'new') {
                if (inviteScope === 'project') {
                    toast.error("Cannot create new organization when inviting to a project.");
                    return;
                }
                if (!newOrgName) {
                    toast.error("Organization name is required");
                    return;
                }
                const newOrg = await createOrganization({ name: newOrgName });
                targetOrgId = newOrg.id;
                queryClient.invalidateQueries({ queryKey: ['organizations'] });
                setSelectedOrgId(newOrg.id);
            } else if (inviteTargetOrgId !== 'current') {
                targetOrgId = parseInt(inviteTargetOrgId);
            } else {
                if (selectedOrgId === 'all') {
                    toast.error("Please select a specific organization to invite into.");
                    return;
                }
                targetOrgId = selectedOrgId as number;
            }

            if (inviteScope === 'project') {
                if (!inviteProjectId) {
                    toast.error("Please select a project.");
                    return;
                }
                await inviteUserToProject(parseInt(inviteProjectId), inviteEmail, inviteProjectRole);
            } else {
                await inviteUserToOrg(targetOrgId, inviteEmail, inviteRole);
            }

            queryClient.invalidateQueries({ queryKey: ['orgInvitations', targetOrgId] });
            queryClient.invalidateQueries({ queryKey: ['orgMembersDetailed', targetOrgId] });

            setShowInviteDialog(false);
            setInviteEmail('');
            setNewOrgName('');
            setInviteTargetOrgId('current');
            setInviteRole('member');
            setInviteScope('org');
            setInviteProjectId('');

            toast.success(inviteScope === 'project' ? 'Project invitation sent!' : 'Organization invitation sent!');

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
        ((m as any).organization || '').toLowerCase().includes(searchTerm.toLowerCase())
    ) || [];

    // Filter invitations
    const filteredInvitations = invitations?.filter(i =>
        i.email.toLowerCase().includes(searchTerm.toLowerCase())
    ) || [];

    const canManageUsers = selectedOrgId !== 'all' && selectedOrgId ? can('org:manage_users', { orgId: selectedOrgId }) : isTenantAdmin;
    // Tenant Admin can always invite (via dialog select), but specificOrg context required?
    // We updated dialog to allow Org Selection, so yes.

    if (!selectedOrgId && organizations?.length === 0) {
        return (
            <div className="flex flex-col items-center justify-center h-[50vh]">
                <h2 className="text-xl font-semibold mb-2">No Organizations Found</h2>
                <p className="text-muted-foreground">You need to belong to an organization to view users.</p>
            </div>
        );
    }

    return (
        <div className="space-y-6 max-w-7xl mx-auto">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Users</h1>
                    <div className="flex items-center gap-2 mt-2">
                        <Select
                            value={selectedOrgId?.toString() || ""}
                            onValueChange={(val) => setSelectedOrgId(val === 'all' ? 'all' : parseInt(val))}
                        >
                            <SelectTrigger className="w-[250px]">
                                <SelectValue placeholder="Select Scope" />
                            </SelectTrigger>
                            <SelectContent>
                                {isTenantAdmin && (
                                    <SelectItem value="all" className="font-semibold">All Organizations</SelectItem>
                                )}
                                {organizations?.map(org => (
                                    <SelectItem key={org.id} value={org.id.toString()}>{org.name}</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    {canManageUsers && (
                        <Dialog open={showInviteDialog} onOpenChange={setShowInviteDialog}>
                            <DialogTrigger asChild>
                                <Button className="gap-2">
                                    <UserPlus className="h-4 w-4" />
                                    Invite User
                                </Button>
                            </DialogTrigger>
                            <DialogContent>
                                <DialogHeader>
                                    <DialogTitle>Invite User</DialogTitle>
                                    <DialogDescription>
                                        Send an invitation to join an organization.
                                    </DialogDescription>
                                </DialogHeader>
                                <form onSubmit={handleInvite} className="space-y-4 pt-4">
                                    <div className="space-y-2">
                                        <label className="text-sm font-medium">Email Address</label>
                                        <div className="relative">
                                            <Mail className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                                            <Input
                                                type="email"
                                                placeholder="colleague@company.com"
                                                className="pl-9"
                                                value={inviteEmail}
                                                onChange={(e) => setInviteEmail(e.target.value)}
                                                required
                                            />
                                        </div>
                                    </div>
                                    <div className="space-y-2">
                                        <label className="text-sm font-medium">Invite To</label>
                                        <Select value={inviteScope} onValueChange={(val: any) => setInviteScope(val)}>
                                            <SelectTrigger><SelectValue /></SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="org">Organization</SelectItem>
                                                <SelectItem value="project">Project</SelectItem>
                                            </SelectContent>
                                        </Select>
                                    </div>

                                    <div className="space-y-2">
                                        <label className="text-sm font-medium">Organization</label>
                                        <Select
                                            value={inviteTargetOrgId}
                                            onValueChange={(val) => setInviteTargetOrgId(val)}
                                        >
                                            <SelectTrigger>
                                                <SelectValue />
                                            </SelectTrigger>
                                            <SelectContent>
                                                {selectedOrgId !== 'all' && selectedOrgId && (
                                                    <SelectItem value="current">Current: {organizations?.find(o => o.id === selectedOrgId)?.name}</SelectItem>
                                                )}
                                                {organizations?.filter(o => o.id !== selectedOrgId).map(org => (
                                                    <SelectItem key={org.id} value={org.id.toString()}>
                                                        {org.name}
                                                    </SelectItem>
                                                ))}
                                                {inviteScope !== 'project' && (
                                                    <SelectItem value="new" className="text-primary font-medium">
                                                        + Create New Organization
                                                    </SelectItem>
                                                )}
                                            </SelectContent>
                                        </Select>
                                    </div>

                                    {inviteTargetOrgId === 'new' && (
                                        <div className="space-y-2 animate-in fade-in slide-in-from-top-2">
                                            <label className="text-sm font-medium">New Organization Name</label>
                                            <div className="relative">
                                                <Building className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                                                <Input
                                                    placeholder="My New Startup"
                                                    className="pl-9"
                                                    value={newOrgName}
                                                    onChange={(e) => setNewOrgName(e.target.value)}
                                                    required
                                                />
                                            </div>
                                        </div>
                                    )}

                                    {inviteScope === 'project' && (
                                        <div className="space-y-2">
                                            <label className="text-sm font-medium">Project</label>
                                            <Select value={inviteProjectId} onValueChange={setInviteProjectId}>
                                                <SelectTrigger>
                                                    <SelectValue placeholder="Select Project" />
                                                </SelectTrigger>
                                                <SelectContent>
                                                    {projects?.map((p: any) => (
                                                        <SelectItem key={p.id} value={p.id.toString()}>{p.name}</SelectItem>
                                                    ))}
                                                    {projects?.length === 0 && <div className="p-2 text-sm text-muted-foreground">No projects found</div>}
                                                </SelectContent>
                                            </Select>
                                        </div>
                                    )}

                                    <div className="space-y-2">
                                        <label className="text-sm font-medium">Role</label>
                                        {inviteScope === 'project' ? (
                                            <Select value={inviteProjectRole} onValueChange={setInviteProjectRole}>
                                                <SelectTrigger><SelectValue /></SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="admin">Project Admin</SelectItem>
                                                    <SelectItem value="editor">Editor</SelectItem>
                                                    <SelectItem value="viewer">Viewer</SelectItem>
                                                </SelectContent>
                                            </Select>
                                        ) : (
                                            <Select value={inviteRole} onValueChange={setInviteRole}>
                                                <SelectTrigger>
                                                    <SelectValue />
                                                </SelectTrigger>
                                                <SelectContent>
                                                    {orgRoles.map(role => (
                                                        <SelectItem key={role.id} value={role.name === "Organization Admin" ? "admin" : "member"}>
                                                            <div className="flex items-center gap-2">
                                                                {role.name === "Organization Admin" ? <Shield className="h-4 w-4" /> : <User className="h-4 w-4" />}
                                                                <span>{role.name.replace("Organization ", "")}</span>
                                                            </div>
                                                        </SelectItem>
                                                    ))}
                                                    {orgRoles.length === 0 && (
                                                        <SelectItem value="member">Member</SelectItem>
                                                    )}
                                                </SelectContent>
                                            </Select>
                                        )}
                                    </div>
                                    <DialogFooter>
                                        <Button type="button" variant="outline" onClick={() => setShowInviteDialog(false)}>Cancel</Button>
                                        <Button type="submit">
                                            {/*inviteMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />*/}
                                            Send Invitation
                                        </Button>
                                    </DialogFooter>
                                </form>
                            </DialogContent>
                        </Dialog>
                    )}
                </div>
            </div>

            <Card>
                <CardHeader>
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                        <CardTitle>Organization Members</CardTitle>
                        <div className="relative w-full sm:w-64">
                            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                            <Input
                                type="search"
                                placeholder="Search users..."
                                className="pl-9"
                                value={searchTerm}
                                onChange={(e) => setSearchTerm(e.target.value)}
                            />
                        </div>
                    </div>
                </CardHeader>
                <CardContent>
                    {membersLoading && invitationsLoading ? (
                        <div className="flex justify-center p-8">
                            <Loader2 className="h-8 w-8 animate-spin text-primary" />
                        </div>
                    ) : (
                        <div className="rounded-md border">
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>User</TableHead>
                                        <TableHead>Role</TableHead>
                                        {selectedOrgId === 'all' && <TableHead>Organization</TableHead>}
                                        <TableHead>Status</TableHead>
                                        <TableHead>Last Active</TableHead>
                                        <TableHead className="w-[50px]"></TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {/* Pending Invitations */}
                                    {filteredInvitations.map((invite) => (
                                        <TableRow key={`invite-${invite.id}`} className="bg-muted/30">
                                            <TableCell>
                                                <div className="flex flex-col">
                                                    <span className="font-medium text-muted-foreground">{invite.email}</span>
                                                    <span className="text-xs text-muted-foreground">Invitation Sent</span>
                                                </div>
                                            </TableCell>
                                            <TableCell>
                                                <Badge variant="outline" className="capitalize bg-background">
                                                    {invite.role}
                                                </Badge>
                                            </TableCell>
                                            {selectedOrgId === 'all' && <TableCell>-</TableCell>}
                                            <TableCell>
                                                <Badge variant="secondary" className="gap-1">
                                                    <Clock className="h-3 w-3" />
                                                    Pending
                                                </Badge>
                                            </TableCell>
                                            <TableCell className="text-muted-foreground text-sm">
                                                -
                                            </TableCell>
                                            <TableCell>
                                                <Button variant="ghost" size="icon" disabled title="Revoke not implemented">
                                                    <MoreVertical className="h-4 w-4 opacity-50" />
                                                </Button>
                                            </TableCell>
                                        </TableRow>
                                    ))}

                                    {/* Active Members */}
                                    {filteredMembers.map((member) => (
                                        <TableRow key={member.id}>
                                            <TableCell>
                                                <div className="flex items-center gap-3">
                                                    <div className="h-8 w-8 rounded-full bg-primary/10 flex items-center justify-center text-primary font-medium">
                                                        {(member.full_name?.[0] || member.email[0]).toUpperCase()}
                                                    </div>
                                                    <div className="flex flex-col">
                                                        <span className="font-medium">{member.full_name || 'Unknown Name'}</span>
                                                        <span className="text-xs text-muted-foreground">{member.email}</span>
                                                    </div>
                                                </div>
                                            </TableCell>
                                            <TableCell>
                                                <Badge variant={member.role === 'Organization Admin' || member.role === 'admin' ? 'default' : 'outline'} className="capitalize">
                                                    {member.role?.replace('Organization ', '')}
                                                </Badge>
                                            </TableCell>
                                            {selectedOrgId === 'all' && (
                                                <TableCell className="text-muted-foreground">
                                                    {(member as any).organization || '-'}
                                                </TableCell>
                                            )}
                                            <TableCell>
                                                <div className="flex items-center gap-2">
                                                    <span className={`h-2 w-2 rounded-full ${member.is_active ? 'bg-green-500' : 'bg-gray-300'}`} />
                                                    <span className="text-sm capitalize">{member.status}</span>
                                                </div>
                                            </TableCell>
                                            <TableCell className="text-sm text-muted-foreground">
                                                {member.last_login_at
                                                    ? formatDistanceToNow(new Date(member.last_login_at), { addSuffix: true })
                                                    : 'Never'}
                                            </TableCell>
                                            <TableCell>
                                                {canManageUsers && selectedOrgId !== 'all' && (
                                                    <DropdownMenu>
                                                        <DropdownMenuTrigger asChild>
                                                            <Button variant="ghost" size="icon">
                                                                <MoreVertical className="h-4 w-4" />
                                                            </Button>
                                                        </DropdownMenuTrigger>
                                                        <DropdownMenuContent align="end">
                                                            <DropdownMenuItem
                                                                className="text-destructive focus:text-destructive"
                                                                onClick={() => setDeleteTarget(member)}
                                                            >
                                                                <Trash2 className="mr-2 h-4 w-4" />
                                                                Remove from Organization
                                                            </DropdownMenuItem>
                                                        </DropdownMenuContent>
                                                    </DropdownMenu>
                                                )}
                                            </TableCell>
                                        </TableRow>
                                    ))}

                                    {filteredMembers.length === 0 && filteredInvitations.length === 0 && (
                                        <TableRow>
                                            <TableCell colSpan={selectedOrgId === 'all' ? 6 : 5} className="h-24 text-center text-muted-foreground">
                                                No users found.
                                            </TableCell>
                                        </TableRow>
                                    )}
                                </TableBody>
                            </Table>
                        </div>
                    )}
                </CardContent>
            </Card>

            <AlertDialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Remove User</AlertDialogTitle>
                        <AlertDialogDescription>
                            Are you sure you want to remove <strong>{deleteTarget?.full_name || deleteTarget?.email}</strong> from the organization?
                            This will also remove them from all teams within the organization. This action cannot be undone.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                            onClick={confirmRemove}
                            className="bg-destructive hover:bg-destructive/90"
                        >
                            {/*removeMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />*/}
                            Remove User
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}

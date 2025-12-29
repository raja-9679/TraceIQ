import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
    getOrganizations, createOrganization, deleteOrganization,
    getTeams, createTeam, deleteTeam,
    getProjects, createProject, deleteProject,
    Organization, Team, Project, User,
    inviteToTeam, getTeamMembers, linkTeamToProject, getOrganizationMembers,
    removeUserFromTeam, getProjectTeams, getProjectMembers,
    unlinkTeamFromProject, removeUserProjectAccess,
    addTeamToProject, addUserProjectAccess,
    getOrgMembersDetailed, inviteUserToOrg, getOrgInvitations,
} from '@/lib/api';
import type { DetailedMember } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
    Plus, Users, FolderOpen, Building2, Loader2, ArrowRight, Mail, Shield,
    Link as LinkIcon, Trash2, X, UserPlus, AlertTriangle, Clock, ShieldCheck,
    Check, Info, MoreVertical
} from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { toast } from 'sonner';
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

interface ProjectTeam {
    id: number;
    name: string;
    access_level: string;
}

interface ProjectMember {
    id: number;
    full_name: string;
    email: string;
    access_level: string;
}

export default function OrganizationPage() {
    const queryClient = useQueryClient();
    const [selectedOrgId, setSelectedOrgId] = useState<number | null>(null);
    const [selectedTeamId, setSelectedTeamId] = useState<number | null>(null);
    const [showCreateOrg, setShowCreateOrg] = useState(false);
    const [showCreateTeam, setShowCreateTeam] = useState(false);
    const [showCreateProject, setShowCreateProject] = useState(false);
    const [showInviteMember, setShowInviteMember] = useState(false);
    const [showLinkProject, setShowLinkProject] = useState(false);
    const [showProjectAccess, setShowProjectAccess] = useState<{ id: number, name: string } | null>(null);

    // Deletion states
    const [deleteTarget, setDeleteTarget] = useState<{ type: 'org' | 'team' | 'project', id: number, name: string } | null>(null);

    const [newName, setNewName] = useState('');
    const [newDesc, setNewDesc] = useState('');
    const [inviteEmail, setInviteEmail] = useState('');
    const [linkProjectId, setLinkProjectId] = useState<string>('');
    const [linkAccessLevel, setLinkAccessLevel] = useState<string>('editor');

    // Project Access Dialog states
    const [addTeamId, setAddTeamId] = useState<string>('');
    const [addUserId, setAddUserId] = useState<string>('');
    const [addAccessLevel, setAddAccessLevel] = useState<string>('editor');

    // Org Member Management states
    const [showInviteOrgMember, setShowInviteOrgMember] = useState(false);
    const [orgInviteEmail, setOrgInviteEmail] = useState('');
    const [orgInviteRole, setOrgInviteRole] = useState('member');

    // Queries
    const { data: organizations, isLoading: orgsLoading } = useQuery<Organization[]>({
        queryKey: ['organizations'],
        queryFn: getOrganizations,
    });

    useEffect(() => {
        if (organizations && organizations.length > 0 && !selectedOrgId) {
            // Using queueMicrotask to avoid synchronous setState lint error
            queueMicrotask(() => setSelectedOrgId(organizations[0].id));
        }
    }, [organizations, selectedOrgId]);

    const { data: teams, isLoading: teamsLoading } = useQuery<Team[]>({
        queryKey: ['teams', selectedOrgId],
        queryFn: () => getTeams(selectedOrgId!),
        enabled: !!selectedOrgId,
    });

    const { data: projects, isLoading: projectsLoading } = useQuery<Project[]>({
        queryKey: ['projects', selectedOrgId],
        queryFn: () => getProjects(selectedOrgId!),
        enabled: !!selectedOrgId,
    });

    const { data: teamMembers, isLoading: teamMembersLoading } = useQuery<User[]>({
        queryKey: ['teamMembers', selectedTeamId],
        queryFn: () => getTeamMembers(selectedTeamId!),
        enabled: !!selectedTeamId,
    });

    const { data: orgMembersDetailed, isLoading: orgMembersDetailedLoading } = useQuery<DetailedMember[]>({
        queryKey: ['orgMembersDetailed', selectedOrgId],
        queryFn: () => getOrgMembersDetailed(selectedOrgId!),
        enabled: !!selectedOrgId,
    });

    const { data: orgInvitations, isLoading: orgInvitationsLoading } = useQuery<DetailedMember[]>({
        queryKey: ['orgInvitations', selectedOrgId],
        queryFn: () => getOrgInvitations(selectedOrgId!),
        enabled: !!selectedOrgId,
    });

    const { data: orgMembers } = useQuery<User[]>({
        queryKey: ['orgMembers', selectedOrgId],
        queryFn: () => getOrganizationMembers(selectedOrgId!),
        enabled: !!selectedOrgId,
    });

    const { data: projectTeams } = useQuery<ProjectTeam[]>({
        queryKey: ['projectTeams', showProjectAccess?.id],
        queryFn: () => getProjectTeams(showProjectAccess!.id),
        enabled: !!showProjectAccess,
    });

    const { data: projectMembers } = useQuery<ProjectMember[]>({
        queryKey: ['projectMembers', showProjectAccess?.id],
        queryFn: () => getProjectMembers(showProjectAccess!.id),
        enabled: !!showProjectAccess,
    });

    // Mutations
    const orgMutation = useMutation({
        mutationFn: createOrganization,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['organizations'] });
            setShowCreateOrg(false);
            setNewName('');
            setNewDesc('');
            toast.success('Organization created');
        }
    });

    const deleteOrgMutation = useMutation({
        mutationFn: deleteOrganization,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['organizations'] });
            setDeleteTarget(null);
            setSelectedOrgId(null);
            toast.success('Organization deleted');
        }
    });

    const teamMutation = useMutation({
        mutationFn: (data: { name: string, description?: string, initial_project_id?: number, initial_access_level?: string }) => createTeam(selectedOrgId!, data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['teams', selectedOrgId] });
            setShowCreateTeam(false);
            setNewName('');
            setNewDesc('');
            setLinkProjectId('');
            setLinkAccessLevel('editor');
            toast.success('Team created');
        }
    });

    const deleteTeamMutation = useMutation({
        mutationFn: deleteTeam,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['teams', selectedOrgId] });
            setDeleteTarget(null);
            setSelectedTeamId(null);
            toast.success('Team deleted');
        }
    });

    const projectMutation = useMutation({
        mutationFn: (data: { name: string, description?: string }) => createProject({ ...data, organization_id: selectedOrgId! }),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['projects', selectedOrgId] });
            setShowCreateProject(false);
            setNewName('');
            setNewDesc('');
            toast.success('Project created');
        }
    });

    const deleteProjectMutation = useMutation({
        mutationFn: deleteProject,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['projects', selectedOrgId] });
            setDeleteTarget(null);
            toast.success('Project deleted');
        }
    });

    const inviteMutation = useMutation({
        mutationFn: (emailToInvite?: string) => inviteToTeam(selectedTeamId!, emailToInvite || inviteEmail),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['teamMembers', selectedTeamId] });
            setShowInviteMember(false);
            setInviteEmail('');
            toast.success('Invitation sent');
        },
        onError: (error: unknown) => {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const err = error as any;
            toast.error(err.response?.data?.detail || 'Failed to add user');
        }
    });

    const linkMutation = useMutation({
        mutationFn: () => linkTeamToProject(parseInt(linkProjectId), selectedTeamId!, linkAccessLevel),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['teams', selectedOrgId] });
            queryClient.invalidateQueries({ queryKey: ['projects', selectedOrgId] });
            setShowLinkProject(false);
            setLinkProjectId('');
            setLinkAccessLevel('editor');
            toast.success('Team linked to project');
        },
        onError: (error: unknown) => {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const err = error as any;
            toast.error(err.response?.data?.detail || 'Failed to link team');
        }
    });

    const removeMemberMutation = useMutation({
        mutationFn: (userId: number) => removeUserFromTeam(selectedTeamId!, userId),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['teamMembers', selectedTeamId] });
            toast.success('Member removed from team');
        }
    });

    const unlinkTeamMutation = useMutation({
        mutationFn: (teamId: number) => unlinkTeamFromProject(showProjectAccess!.id, teamId),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['projectTeams', showProjectAccess?.id] });
            toast.success('Team unlinked from project');
        },
        onError: (error: unknown) => {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const err = error as any;
            toast.error(err.response?.data?.detail || 'Failed to unlink team');
        }
    });

    const removeUserProjectAccessMutation = useMutation({
        mutationFn: (userId: number) => removeUserProjectAccess(showProjectAccess!.id, userId),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['projectMembers', showProjectAccess?.id] });
            toast.success('User access removed');
        },
        onError: (error: unknown) => {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const err = error as any;
            toast.error(err.response?.data?.detail || 'Failed to remove user access');
        }
    });

    const addTeamToProjectMutation = useMutation({
        mutationFn: (teamId: number) => addTeamToProject(showProjectAccess!.id, teamId, addAccessLevel),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['projectTeams', showProjectAccess?.id] });
            setAddTeamId('');
            toast.success('Team added to project');
        },
        onError: (error: unknown) => {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const err = error as any;
            toast.error(err.response?.data?.detail || 'Failed to add team');
        }
    });

    const addUserProjectAccessMutation = useMutation({
        mutationFn: (userId: number) => addUserProjectAccess(showProjectAccess!.id, userId, addAccessLevel),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['projectMembers', showProjectAccess?.id] });
            setAddUserId('');
            toast.success('User added to project');
        },
        onError: (error: unknown) => {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const err = error as any;
            toast.error(err.response?.data?.detail || 'Failed to add user');
        }
    });

    const inviteOrgMemberMutation = useMutation({
        mutationFn: () => inviteUserToOrg(selectedOrgId!, orgInviteEmail, orgInviteRole),
        onSuccess: (data: any) => {
            queryClient.invalidateQueries({ queryKey: ['orgInvitations', selectedOrgId] });
            queryClient.invalidateQueries({ queryKey: ['orgMembersDetailed', selectedOrgId] });
            setOrgInviteEmail('');
            setShowInviteOrgMember(false);
            toast.success(data.message || 'Invitation sent');
        },
        onError: (error: unknown) => {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const err = error as any;
            toast.error(err.response?.data?.detail || 'Failed to send invitation');
        }
    });

    if (orgsLoading) return <div className="p-8 flex justify-center"><Loader2 className="animate-spin" /></div>;

    const selectedOrg = organizations?.find(o => o.id === selectedOrgId);
    const selectedTeam = teams?.find(t => t.id === selectedTeamId);

    const availableMembers = orgMembers?.filter(om =>
        !teamMembers?.some(tm => tm.id === om.id)
    ) || [];

    const availableTeamsForProject = teams?.filter(t =>
        !projectTeams?.some(pt => pt.id === t.id)
    ) || [];

    const availableUsersForProjectDirect = orgMembers?.filter(om =>
        !projectMembers?.some(pm => pm.id === om.id)
    ) || [];
    return (
        <div className="space-y-8">
            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-3xl font-bold text-foreground">Organization Management</h1>
                    <p className="text-muted-foreground mt-1">Manage your organizations, teams, and projects</p>
                </div>
                <div className="flex gap-2">
                    {selectedOrg && (
                        <Button variant="outline" className="text-destructive hover:bg-destructive/10" onClick={() => setDeleteTarget({ type: 'org', id: selectedOrg.id, name: selectedOrg.name })}>
                            <Trash2 className="mr-2 h-4 w-4" /> Delete Org
                        </Button>
                    )}
                    <Button onClick={() => { setShowCreateOrg(true); setNewName(''); setNewDesc(''); }}>
                        <Plus className="mr-2 h-4 w-4" /> New Organization
                    </Button>
                </div>
            </div>

            <div className="grid grid-cols-12 gap-8">
                {/* Organizations Sidebar */}
                <div className="col-span-3 space-y-4 border-r pr-4">
                    <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">Your Organizations</h3>
                    <div className="space-y-1">
                        {organizations?.map((org) => (
                            <button
                                key={org.id}
                                onClick={() => { setSelectedOrgId(org.id); setSelectedTeamId(null); }}
                                className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-medium transition-all ${selectedOrgId === org.id
                                    ? 'bg-primary text-primary-foreground shadow-lg shadow-primary/20 scale-[1.02]'
                                    : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground'
                                    }`}
                            >
                                <Building2 className="h-4 w-4" />
                                <span className="flex-1 text-left">{org.name}</span>
                            </button>
                        ))}
                    </div>
                </div>

                {/* Main Content */}
                <div className="col-span-9 space-y-8">
                    {selectedOrg ? (
                        <div className="grid grid-cols-2 gap-8">
                            {/* Teams Section */}
                            <div className="space-y-4">
                                <div className="flex justify-between items-center">
                                    <h3 className="text-lg font-semibold flex items-center gap-2">
                                        <Users className="h-5 w-5 text-indigo-500" />
                                        Teams
                                    </h3>
                                    <Button variant="ghost" size="sm" onClick={() => {
                                        setShowCreateTeam(true);
                                        setLinkProjectId('');
                                        setLinkAccessLevel('editor');
                                        setNewName('');
                                        setNewDesc('');
                                    }}>
                                        <Plus className="h-4 w-4" />
                                    </Button>
                                </div>
                                <div className="grid gap-4">
                                    {teamsLoading ? <Loader2 className="animate-spin mx-auto" /> : (
                                        teams?.map((team) => (
                                            <Card
                                                key={team.id}
                                                className={`hover:border-primary/50 transition-colors cursor-pointer relative group ${selectedTeamId === team.id ? 'border-primary ring-1 ring-primary' : ''}`}
                                                onClick={() => setSelectedTeamId(team.id)}
                                            >
                                                <CardContent className="p-4">
                                                    <div className="flex items-center justify-between mb-2">
                                                        <p className="font-bold text-lg">{team.name}</p>
                                                        <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                                                            <Button size="icon" variant="ghost" className="h-8 w-8 text-primary" onClick={(e) => { e.stopPropagation(); setSelectedTeamId(team.id); setShowInviteMember(true); setInviteEmail(''); }} title="Add/Invite Member">
                                                                <UserPlus className="h-4 w-4" />
                                                            </Button>
                                                            <Button size="icon" variant="ghost" className="h-8 w-8 text-indigo-500" onClick={(e) => { e.stopPropagation(); setSelectedTeamId(team.id); setShowLinkProject(true); setLinkProjectId(''); setLinkAccessLevel('editor'); }} title="Link to Project">
                                                                <LinkIcon className="h-4 w-4" />
                                                            </Button>
                                                            <Button size="icon" variant="ghost" className="h-8 w-8 text-destructive" onClick={(e) => { e.stopPropagation(); setDeleteTarget({ type: 'team', id: team.id, name: team.name }); }} title="Delete Team">
                                                                <Trash2 className="h-4 w-4" />
                                                            </Button>
                                                        </div>
                                                    </div>
                                                    {team.description && <p className="text-sm text-muted-foreground line-clamp-2">{team.description}</p>}
                                                </CardContent>
                                            </Card>
                                        ))
                                    )}
                                    {(!teams || teams.length === 0) && (
                                        <p className="text-sm text-muted-foreground text-center py-4 bg-accent/20 rounded-xl border border-dashed">
                                            No teams created yet
                                        </p>
                                    )}
                                </div>
                            </div>

                            {/* Details Pane */}
                            <div className="space-y-8">
                                {/* Team Details (if selected) */}
                                {selectedTeam ? (
                                    <div className="space-y-4 animate-in fade-in slide-in-from-right-4">
                                        <div className="bg-primary/5 border border-primary/10 rounded-2xl p-4">
                                            <div className="flex justify-between items-start mb-4">
                                                <div>
                                                    <h4 className="font-bold text-xl flex items-center gap-2">
                                                        <Shield className="h-5 w-5 text-primary" />
                                                        {selectedTeam.name}
                                                    </h4>
                                                    <p className="text-sm text-muted-foreground mt-1">Manage members and project access</p>
                                                </div>
                                                <div className="flex gap-2">
                                                    <Button size="sm" onClick={() => { setShowInviteMember(true); setInviteEmail(''); }}>
                                                        <Plus className="h-4 w-4 mr-2" /> Add Member
                                                    </Button>
                                                    <Button size="sm" variant="outline" className="text-indigo-600 border-indigo-200 hover:bg-indigo-50" onClick={() => { setShowLinkProject(true); setLinkProjectId(''); setLinkAccessLevel('editor'); }}>
                                                        <LinkIcon className="h-4 w-4 mr-2" /> Link Project
                                                    </Button>
                                                </div>
                                            </div>

                                            <div className="space-y-4">
                                                <div>
                                                    <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">Members ({teamMembers?.length || 0})</p>
                                                    <div className="space-y-2 max-h-[200px] overflow-y-auto pr-2 custom-scrollbar">
                                                        {teamMembersLoading ? <Loader2 className="animate-spin h-4 w-4" /> : (
                                                            teamMembers?.map(member => (
                                                                <div key={member.id} className="flex items-center justify-between text-sm bg-background p-2 rounded-lg border group/member">
                                                                    <div className="flex items-center gap-2">
                                                                        <div className="h-6 w-6 rounded-full bg-primary/20 flex items-center justify-center text-[10px] font-bold">
                                                                            {member.full_name?.charAt(0) || 'U'}
                                                                        </div>
                                                                        <span className="truncate max-w-[120px]" title={member.email}>{member.email}</span>
                                                                    </div>
                                                                    <div className="flex items-center gap-1">
                                                                        <span className="text-[10px] text-muted-foreground mr-1">{member.full_name}</span>
                                                                        <Button
                                                                            size="icon"
                                                                            variant="ghost"
                                                                            className="h-6 w-6 text-destructive opacity-0 group-hover/member:opacity-100 transition-opacity"
                                                                            onClick={() => removeMemberMutation.mutate(member.id)}
                                                                        >
                                                                            <X className="h-3 w-3" />
                                                                        </Button>
                                                                    </div>
                                                                </div>
                                                            ))
                                                        )}
                                                        {(!teamMembers || teamMembers.length === 0) && (
                                                            <p className="text-center py-4 text-xs text-muted-foreground italic">No members in this team</p>
                                                        )}
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                ) : (
                                    <div className="h-40 flex items-center justify-center border-2 border-dashed rounded-2xl text-muted-foreground font-medium">
                                        Select a team to see details
                                    </div>
                                )}

                                {/* All Projects in Org */}
                                <div className="space-y-4">
                                    <div className="flex justify-between items-center">
                                        <h3 className="text-lg font-semibold flex items-center gap-2">
                                            <FolderOpen className="h-5 w-5 text-emerald-500" />
                                            Projects
                                        </h3>
                                        <Button variant="ghost" size="sm" onClick={() => { setShowCreateProject(true); setNewName(''); setNewDesc(''); }}>
                                            <Plus className="h-4 w-4" />
                                        </Button>
                                    </div>
                                    <div className="grid gap-4">
                                        {projectsLoading ? <Loader2 className="animate-spin mx-auto" /> : (
                                            projects?.map((project) => (
                                                <Card key={project.id} className="hover:border-primary/50 transition-colors group relative border-l-4 border-l-emerald-500/30">
                                                    <CardContent className="p-4 flex items-center justify-between text-sm">
                                                        <div className="flex-1 min-w-0">
                                                            <div className="flex items-center gap-2 overflow-hidden">
                                                                <span className="font-bold truncate">{project.name}</span>
                                                                {project.access_level && (
                                                                    <span className="text-[10px] bg-emerald-100 text-emerald-700 font-black px-1.5 py-0.5 rounded uppercase shrink-0">
                                                                        {project.access_level}
                                                                    </span>
                                                                )}
                                                            </div>
                                                            {project.description && <p className="text-xs text-muted-foreground mt-1 truncate max-w-[200px]">{project.description}</p>}
                                                        </div>
                                                        <div className="flex items-center gap-1 shrink-0">
                                                            <Button
                                                                size="sm"
                                                                variant="ghost"
                                                                className="text-primary hover:bg-primary/5 h-8 px-2"
                                                                onClick={() => setShowProjectAccess({ id: project.id, name: project.name })}
                                                            >
                                                                Access
                                                            </Button>
                                                            <Button size="icon" variant="ghost" className="h-8 w-8 text-destructive opacity-0 group-hover:opacity-100 transition-opacity" onClick={() => setDeleteTarget({ type: 'project', id: project.id, name: project.name })}>
                                                                <Trash2 className="h-4 w-4" />
                                                            </Button>
                                                            <ArrowRight className="h-4 w-4 text-muted-foreground" />
                                                        </div>
                                                    </CardContent>
                                                </Card>
                                            ))
                                        )}
                                        {(!projects || projects.length === 0) && (
                                            <p className="text-sm text-muted-foreground text-center py-4 bg-accent/20 rounded-xl border border-dashed">
                                                No projects created yet
                                            </p>
                                        )}
                                    </div>
                                </div>

                                {/* Organization Members */}
                                <div className="space-y-4 pt-8 border-t">
                                    <div className="flex justify-between items-center">
                                        <div className="flex items-center gap-3">
                                            <div className="p-2 bg-indigo-50 rounded-lg">
                                                <Users className="h-5 w-5 text-indigo-600" />
                                            </div>
                                            <div>
                                                <h3 className="text-lg font-bold tracking-tight">Organization Members</h3>
                                                <p className="text-xs text-muted-foreground">Manage organization-wide users and invitations</p>
                                            </div>
                                        </div>
                                        <Button
                                            onClick={() => { setShowInviteOrgMember(true); setOrgInviteEmail(''); setOrgInviteRole('member'); }}
                                            className="bg-indigo-600 hover:bg-indigo-700 text-white shadow-lg shadow-indigo-200"
                                        >
                                            <UserPlus className="h-4 w-4 mr-2" /> Invite Member
                                        </Button>
                                    </div>

                                    <Card className="overflow-hidden border-indigo-100">
                                        <div className="overflow-x-auto">
                                            <table className="w-full text-sm">
                                                <thead className="bg-accent/30 border-b">
                                                    <tr>
                                                        <th className="text-left p-4 font-bold text-muted-foreground uppercase text-[10px] tracking-wider">Member</th>
                                                        <th className="text-left p-4 font-bold text-muted-foreground uppercase text-[10px] tracking-wider">Role</th>
                                                        <th className="text-left p-4 font-bold text-muted-foreground uppercase text-[10px] tracking-wider">Status</th>
                                                        <th className="text-left p-4 font-bold text-muted-foreground uppercase text-[10px] tracking-wider">Last Active</th>
                                                        <th className="p-4"></th>
                                                    </tr>
                                                </thead>
                                                <tbody className="divide-y divide-border">
                                                    {/* Active Members */}
                                                    {orgMembersDetailedLoading ? (
                                                        <tr>
                                                            <td colSpan={5} className="p-8 text-center">
                                                                <Loader2 className="animate-spin h-6 w-6 text-primary mx-auto" />
                                                            </td>
                                                        </tr>
                                                    ) : (
                                                        orgMembersDetailed?.map((member) => (
                                                            <tr key={member.id} className="hover:bg-accent/10 transition-colors group">
                                                                <td className="p-4">
                                                                    <div className="flex items-center gap-3">
                                                                        <div className="h-8 w-8 rounded-full bg-indigo-100 text-indigo-700 flex items-center justify-center font-bold relative">
                                                                            {member.full_name?.charAt(0) || 'U'}
                                                                            {member.is_active && (
                                                                                <span className="absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 bg-emerald-500 border-2 border-white rounded-full"></span>
                                                                            )}
                                                                        </div>
                                                                        <div className="min-w-0">
                                                                            <p className="font-bold truncate" title={member.full_name}>{member.full_name}</p>
                                                                            <p className="text-[10px] text-muted-foreground truncate" title={member.email}>{member.email}</p>
                                                                        </div>
                                                                    </div>
                                                                </td>
                                                                <td className="p-4">
                                                                    <div className="flex items-center gap-1.5">
                                                                        {member.role === 'admin' ? (
                                                                            <ShieldCheck className="h-3.5 w-3.5 text-indigo-600" />
                                                                        ) : (
                                                                            <Users className="h-3.5 w-3.5 text-slate-400" />
                                                                        )}
                                                                        <span className={`capitalize font-medium ${member.role === 'admin' ? 'text-indigo-700' : 'text-slate-600'}`}>
                                                                            {member.role}
                                                                        </span>
                                                                    </div>
                                                                </td>
                                                                <td className="p-4">
                                                                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 text-[10px] font-bold border border-emerald-100 uppercase tracking-tight">
                                                                        <Check className="h-2.5 w-2.5" /> Active
                                                                    </span>
                                                                </td>
                                                                <td className="p-4 text-muted-foreground whitespace-nowrap">
                                                                    {member.last_login_at ? (
                                                                        <div className="flex items-center gap-1.5">
                                                                            <Clock className="h-3.5 w-3.5" />
                                                                            {formatDistanceToNow(new Date(member.last_login_at), { addSuffix: true })}
                                                                        </div>
                                                                    ) : (
                                                                        <span className="text-[10px] italic">Never</span>
                                                                    )}
                                                                </td>
                                                                <td className="p-4 text-right">
                                                                    <DropdownMenu>
                                                                        <DropdownMenuTrigger asChild>
                                                                            <Button variant="ghost" size="icon" className="h-8 w-8 opacity-0 group-hover:opacity-100">
                                                                                <MoreVertical className="h-4 w-4" />
                                                                            </Button>
                                                                        </DropdownMenuTrigger>
                                                                        <DropdownMenuContent align="end" className="w-40">
                                                                            <DropdownMenuLabel>Manage Member</DropdownMenuLabel>
                                                                            <DropdownMenuSeparator />
                                                                            <DropdownMenuItem>Change Role</DropdownMenuItem>
                                                                            <DropdownMenuItem className="text-destructive">Remove from Org</DropdownMenuItem>
                                                                        </DropdownMenuContent>
                                                                    </DropdownMenu>
                                                                </td>
                                                            </tr>
                                                        ))
                                                    )}

                                                    {/* Pending Invitations */}
                                                    {orgInvitations?.map((invite) => (
                                                        <tr key={`invite-${invite.id}`} className="bg-amber-50/30 border-l-4 border-l-amber-400 hover:bg-amber-50/50 transition-colors">
                                                            <td className="p-4">
                                                                <div className="flex items-center gap-3">
                                                                    <div className="h-8 w-8 rounded-full bg-amber-100 text-amber-700 flex items-center justify-center font-bold">
                                                                        <Mail className="h-4 w-4" />
                                                                    </div>
                                                                    <div className="min-w-0">
                                                                        <p className="font-bold truncate opacity-50">Pending Invitation</p>
                                                                        <p className="text-[10px] text-muted-foreground truncate" title={invite.email}>{invite.email}</p>
                                                                    </div>
                                                                </div>
                                                            </td>
                                                            <td className="p-4">
                                                                <span className="capitalize font-medium text-slate-500 opacity-70 italic">{invite.role}</span>
                                                            </td>
                                                            <td className="p-4">
                                                                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 text-[10px] font-bold border border-amber-100 uppercase tracking-tight">
                                                                    <Clock className="h-2.5 w-2.5" /> Invited
                                                                </span>
                                                            </td>
                                                            <td className="p-4 text-muted-foreground whitespace-nowrap">
                                                                <div className="flex items-center gap-1.5 opacity-50">
                                                                    <Info className="h-3.5 w-3.5" />
                                                                    {formatDistanceToNow(new Date(invite.created_at!), { addSuffix: true })}
                                                                </div>
                                                            </td>
                                                            <td className="p-4 text-right">
                                                                <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive">
                                                                    <X className="h-4 w-4" />
                                                                </Button>
                                                            </td>
                                                        </tr>
                                                    ))}

                                                    {(!orgMembersDetailed || orgMembersDetailed.length === 0) && (!orgInvitations || orgInvitations.length === 0) && (
                                                        <tr>
                                                            <td colSpan={5} className="p-12 text-center text-muted-foreground">
                                                                <Users className="h-8 w-8 mx-auto mb-2 opacity-20" />
                                                                <p className="font-medium">No members found</p>
                                                            </td>
                                                        </tr>
                                                    )}
                                                </tbody>
                                            </table>
                                        </div>
                                    </Card>
                                </div>
                            </div>
                        </div>
                    ) : (
                        <div className="flex flex-col items-center justify-center py-20 bg-accent/10 rounded-3xl border border-dashed border-border">
                            <Building2 className="h-12 w-12 text-muted-foreground mb-4" />
                            <h3 className="text-xl font-semibold">No Organization Selected</h3>
                            <p className="text-muted-foreground mt-2">Select an organization from the sidebar or create a new one.</p>
                        </div>
                    )}
                </div>
            </div>

            {/* Modals & Dialogs */}

            {/* Create Org Dialog */}
            {showCreateOrg && (
                <div className="fixed inset-0 bg-background/80 backdrop-blur-md z-[100] flex items-center justify-center p-4">
                    <Card className="w-full max-w-md shadow-2xl scale-in-center">
                        <CardHeader>
                            <CardTitle>Create Organization</CardTitle>
                            <CardDescription>Setup a new workspace for your automation</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="space-y-2">
                                <label className="text-sm font-medium">Name</label>
                                <input
                                    className="w-full p-2 rounded-lg border bg-background"
                                    value={newName}
                                    onChange={(e) => setNewName(e.target.value)}
                                    placeholder="Company Name"
                                />
                            </div>
                            <div className="space-y-2">
                                <label className="text-sm font-medium">Description</label>
                                <textarea
                                    className="w-full p-2 rounded-lg border bg-background h-20"
                                    value={newDesc}
                                    onChange={(e) => setNewDesc(e.target.value)}
                                    placeholder="Optional description"
                                />
                            </div>
                            <div className="flex justify-end gap-2 pt-4">
                                <Button variant="ghost" onClick={() => setShowCreateOrg(false)}>Cancel</Button>
                                <Button onClick={() => orgMutation.mutate({ name: newName, description: newDesc })} disabled={!newName || orgMutation.isPending}>
                                    {orgMutation.isPending && <Loader2 className="animate-spin h-4 w-4 mr-2" />}
                                    Create
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            )}

            {/* Create Team Dialog */}
            {showCreateTeam && (
                <div className="fixed inset-0 bg-background/80 backdrop-blur-md z-[100] flex items-center justify-center p-4">
                    <Card className="w-full max-w-md shadow-2xl scale-in-center">
                        <CardHeader>
                            <CardTitle>Create New Team</CardTitle>
                            <CardDescription>Create a team and optionally link to a project</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="space-y-2">
                                <label className="text-sm font-medium">Team Name</label>
                                <input
                                    className="w-full p-2 rounded-lg border bg-background"
                                    value={newName}
                                    onChange={(e) => setNewName(e.target.value)}
                                    placeholder="Engineering, QA, etc."
                                />
                            </div>
                            <div className="space-y-2">
                                <label className="text-sm font-medium">Description</label>
                                <textarea
                                    className="w-full p-2 rounded-lg border bg-background h-20"
                                    value={newDesc}
                                    onChange={(e) => setNewDesc(e.target.value)}
                                    placeholder="Team goals or focus area"
                                />
                            </div>

                            <div className="border-t pt-4 mt-4 space-y-4">
                                <p className="text-xs font-bold text-muted-foreground uppercase tracking-wider">Project Link (Optional)</p>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Link to Project</label>
                                    <Select value={linkProjectId} onValueChange={setLinkProjectId}>
                                        <SelectTrigger>
                                            <SelectValue placeholder="Select a project" />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {projects?.map(p => (
                                                <SelectItem key={p.id} value={p.id.toString()}>{p.name}</SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                                {linkProjectId && (
                                    <div className="space-y-2">
                                        <label className="text-sm font-medium">Access Level</label>
                                        <Select value={linkAccessLevel} onValueChange={setLinkAccessLevel}>
                                            <SelectTrigger>
                                                <SelectValue />
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="viewer">Viewer</SelectItem>
                                                <SelectItem value="editor">Editor</SelectItem>
                                                <SelectItem value="admin">Admin</SelectItem>
                                            </SelectContent>
                                        </Select>
                                    </div>
                                )}
                            </div>

                            <div className="flex justify-end gap-2 pt-4">
                                <Button variant="ghost" onClick={() => setShowCreateTeam(false)}>Cancel</Button>
                                <Button onClick={() => teamMutation.mutate({
                                    name: newName,
                                    description: newDesc,
                                    initial_project_id: linkProjectId ? parseInt(linkProjectId) : undefined,
                                    initial_access_level: linkProjectId ? linkAccessLevel : undefined
                                })} disabled={!newName || teamMutation.isPending}>
                                    {teamMutation.isPending && <Loader2 className="animate-spin h-4 w-4 mr-2" />}
                                    Create Team
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            )}

            {/* Create Project Dialog */}
            {showCreateProject && (
                <div className="fixed inset-0 bg-background/80 backdrop-blur-md z-[100] flex items-center justify-center p-4">
                    <Card className="w-full max-w-md shadow-2xl scale-in-center">
                        <CardHeader>
                            <CardTitle>New Project</CardTitle>
                            <CardDescription>Create a project to group your test suites</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="space-y-2">
                                <label className="text-sm font-medium">Name</label>
                                <input
                                    className="w-full p-2 rounded-lg border bg-background"
                                    value={newName}
                                    onChange={(e) => setNewName(e.target.value)}
                                    placeholder="Project Name"
                                />
                            </div>
                            <div className="space-y-2">
                                <label className="text-sm font-medium">Description</label>
                                <textarea
                                    className="w-full p-2 rounded-lg border bg-background h-20"
                                    value={newDesc}
                                    onChange={(e) => setNewDesc(e.target.value)}
                                    placeholder="What is this project about?"
                                />
                            </div>
                            <div className="flex justify-end gap-2 pt-4">
                                <Button variant="ghost" onClick={() => setShowCreateProject(false)}>Cancel</Button>
                                <Button onClick={() => projectMutation.mutate({ name: newName, description: newDesc })} disabled={!newName || projectMutation.isPending}>
                                    {projectMutation.isPending && <Loader2 className="animate-spin h-4 w-4 mr-2" />}
                                    Create Project
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            )}

            {/* Invite Member Dialog */}
            {showInviteMember && (
                <div className="fixed inset-0 bg-background/80 backdrop-blur-md z-[100] flex items-center justify-center p-4">
                    <Card className="w-full max-w-md shadow-2xl scale-in-center">
                        <CardHeader>
                            <CardTitle>Add Team Member</CardTitle>
                            <CardDescription>Invite an existing organization member to {selectedTeam?.name}</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="space-y-4">
                                <div className="space-y-2">
                                    <label className="text-sm font-medium text-muted-foreground uppercase tracking-tight">Available Organization Members</label>
                                    <div className="border rounded-xl p-2 space-y-1 max-h-[300px] overflow-y-auto bg-primary/5">
                                        {availableMembers.map(member => (
                                            <div key={member.id} className="flex items-center justify-between p-2 hover:bg-white rounded-lg transition-colors border-transparent border hover:border-border">
                                                <div>
                                                    <p className="text-sm font-bold">{member.full_name}</p>
                                                    <p className="text-xs text-muted-foreground">{member.email}</p>
                                                </div>
                                                <Button size="sm" variant="outline" className="h-7 text-xs bg-white" onClick={() => inviteMutation.mutate(member.email)}>
                                                    Add
                                                </Button>
                                            </div>
                                        ))}
                                        {availableMembers.length === 0 && (
                                            <p className="text-center py-8 text-sm text-muted-foreground italic">
                                                No other organization members found
                                            </p>
                                        )}
                                    </div>
                                </div>

                                <div className="relative py-4">
                                    <div className="absolute inset-0 flex items-center"><span className="w-full border-t"></span></div>
                                    <div className="relative flex justify-center text-xs uppercase"><span className="bg-background px-2 text-muted-foreground font-bold">OR INVITE BY EMAIL</span></div>
                                </div>

                                <div className="space-y-2">
                                    <label className="text-sm font-medium">New User Email</label>
                                    <div className="flex gap-2">
                                        <input
                                            className="flex-1 p-2 rounded-lg border bg-background"
                                            value={inviteEmail}
                                            onChange={(e) => setInviteEmail(e.target.value)}
                                            placeholder="user@example.com"
                                        />
                                        <Button onClick={() => inviteMutation.mutate(inviteEmail)} disabled={!inviteEmail || inviteMutation.isPending}>
                                            <Mail className="h-4 w-4 mr-2" /> Invite
                                        </Button>
                                    </div>
                                </div>
                            </div>
                            <div className="flex justify-end pt-4">
                                <Button variant="ghost" onClick={() => setShowInviteMember(false)}>Close</Button>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            )}

            {/* Link Project Dialog */}
            {showLinkProject && (
                <div className="fixed inset-0 bg-background/80 backdrop-blur-md z-[100] flex items-center justify-center p-4">
                    <Card className="w-full max-w-md shadow-2xl scale-in-center">
                        <CardHeader>
                            <CardTitle>Link Team to Project</CardTitle>
                            <CardDescription>Grant {selectedTeam?.name} access to a project</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="space-y-2">
                                <label className="text-sm font-medium">Select Project</label>
                                <Select value={linkProjectId} onValueChange={setLinkProjectId}>
                                    <SelectTrigger>
                                        <SelectValue placeholder="Choose project..." />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {projects?.map(p => (
                                            <SelectItem key={p.id} value={p.id.toString()}>{p.name}</SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-2">
                                <label className="text-sm font-medium">Access Level</label>
                                <Select value={linkAccessLevel} onValueChange={setLinkAccessLevel}>
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="viewer">Viewer</SelectItem>
                                        <SelectItem value="editor">Editor</SelectItem>
                                        <SelectItem value="admin">Admin</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="flex justify-end gap-2 pt-4">
                                <Button variant="ghost" onClick={() => setShowLinkProject(false)}>Cancel</Button>
                                <Button onClick={() => linkMutation.mutate()} disabled={!linkProjectId || linkMutation.isPending}>
                                    {linkMutation.isPending && <Loader2 className="animate-spin h-4 w-4 mr-2" />}
                                    Link Project
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            )}

            {/* Project Access Dialog */}
            {showProjectAccess && (
                <div className="fixed inset-0 bg-background/80 backdrop-blur-md z-[100] flex items-center justify-center p-4">
                    <Card className="w-full max-w-2xl shadow-2xl animate-in fade-in zoom-in-95 duration-200">
                        <CardHeader className="flex flex-row items-center justify-between border-b pb-4">
                            <div className="min-w-0">
                                <CardTitle className="flex items-center gap-2">
                                    <Shield className="h-5 w-5 text-primary shrink-0" />
                                    <span className="truncate">Access for {showProjectAccess.name}</span>
                                </CardTitle>
                                <CardDescription>Teams and users with access to this project</CardDescription>
                            </div>
                            <Button variant="ghost" size="icon" className="shrink-0" onClick={() => setShowProjectAccess(null)}>
                                <X className="h-4 w-4" />
                            </Button>
                        </CardHeader>
                        <CardContent className="p-6 space-y-6">
                            <div className="grid grid-cols-2 gap-8">
                                {/* Teams */}
                                <div className="space-y-4">
                                    <h4 className="text-sm font-bold flex items-center gap-2 border-b pb-2">
                                        <Users className="h-4 w-4 text-indigo-500" />
                                        Teams
                                    </h4>
                                    <div className="space-y-4">
                                        <div className="space-y-2 max-h-[250px] overflow-y-auto pr-2 custom-scrollbar">
                                            {projectTeams?.map((t: ProjectTeam) => (
                                                <div key={t.id} className="flex items-center justify-between p-2 rounded-lg bg-accent/30 border group/access">
                                                    <div className="flex items-center gap-2 min-w-0 flex-1">
                                                        <span className="text-sm font-medium truncate" title={t.name}>{t.name}</span>
                                                        <span className="text-[10px] bg-indigo-100 text-indigo-700 font-bold px-1.5 py-0.5 rounded uppercase shrink-0">
                                                            {t.access_level}
                                                        </span>
                                                    </div>
                                                    <Button
                                                        size="icon"
                                                        variant="ghost"
                                                        className="h-6 w-6 text-destructive opacity-0 group-hover/access:opacity-100 transition-opacity"
                                                        onClick={() => unlinkTeamMutation.mutate(t.id)}
                                                        title="Unlink Team"
                                                    >
                                                        <X className="h-3 w-3" />
                                                    </Button>
                                                </div>
                                            ))}
                                            {(!projectTeams || projectTeams.length === 0) && (
                                                <p className="text-xs text-muted-foreground italic text-center py-4">No teams linked</p>
                                            )}
                                        </div>

                                        {/* Add Team Input */}
                                        <div className="border-t pt-4 space-y-3">
                                            <p className="text-[10px] font-bold text-muted-foreground uppercase tracking-tight">Grant Team Access</p>
                                            <div className="flex gap-2">
                                                <Select value={addTeamId} onValueChange={setAddTeamId}>
                                                    <SelectTrigger className="h-8 text-xs flex-1">
                                                        <SelectValue placeholder="Select Team" />
                                                    </SelectTrigger>
                                                    <SelectContent>
                                                        {availableTeamsForProject.map(t => (
                                                            <SelectItem key={t.id} value={t.id.toString()}>{t.name}</SelectItem>
                                                        ))}
                                                    </SelectContent>
                                                </Select>
                                                <Select value={addAccessLevel} onValueChange={setAddAccessLevel}>
                                                    <SelectTrigger className="h-8 text-xs w-[90px]">
                                                        <SelectValue />
                                                    </SelectTrigger>
                                                    <SelectContent>
                                                        <SelectItem value="viewer">Viewer</SelectItem>
                                                        <SelectItem value="editor">Editor</SelectItem>
                                                        <SelectItem value="admin">Admin</SelectItem>
                                                    </SelectContent>
                                                </Select>
                                                <Button
                                                    size="sm"
                                                    className="h-8 px-2"
                                                    disabled={!addTeamId || addTeamToProjectMutation.isPending}
                                                    onClick={() => addTeamToProjectMutation.mutate(parseInt(addTeamId))}
                                                >
                                                    <Plus className="h-3 w-3" />
                                                </Button>
                                            </div>
                                        </div>
                                    </div>
                                </div>

                                {/* Direct Members */}
                                <div className="space-y-4">
                                    <h4 className="text-sm font-bold flex items-center gap-2 border-b pb-2">
                                        <Users className="h-4 w-4 text-emerald-500" />
                                        Direct Members
                                    </h4>
                                    <div className="space-y-4">
                                        <div className="space-y-2 max-h-[250px] overflow-y-auto pr-2 custom-scrollbar">
                                            {projectMembers?.map((m: ProjectMember) => (
                                                <div key={m.id} className="flex items-center justify-between p-2 rounded-lg bg-accent/30 border group/useraccess">
                                                    <div className="min-w-0 mr-2 flex-1">
                                                        <div className="flex items-center gap-2">
                                                            <p className="text-sm font-medium truncate" title={m.full_name}>{m.full_name}</p>
                                                            <span className="text-[10px] bg-emerald-100 text-emerald-700 font-bold px-1.5 py-0.5 rounded uppercase shrink-0">
                                                                {m.access_level}
                                                            </span>
                                                        </div>
                                                        <p className="text-[10px] text-muted-foreground truncate" title={m.email}>{m.email}</p>
                                                    </div>
                                                    <Button
                                                        size="icon"
                                                        variant="ghost"
                                                        className="h-6 w-6 text-destructive opacity-0 group-hover/useraccess:opacity-100 transition-opacity"
                                                        onClick={() => removeUserProjectAccessMutation.mutate(m.id)}
                                                        title="Remove User Access"
                                                    >
                                                        <X className="h-3 w-3" />
                                                    </Button>
                                                </div>
                                            ))}
                                            {(!projectMembers || projectMembers.length === 0) && (
                                                <p className="text-xs text-muted-foreground italic text-center py-4">No direct users linked</p>
                                            )}
                                        </div>

                                        {/* Add Member Input */}
                                        <div className="border-t pt-4 space-y-3">
                                            <p className="text-[10px] font-bold text-muted-foreground uppercase tracking-tight">Grant Direct Access</p>
                                            <div className="flex gap-2">
                                                <Select value={addUserId} onValueChange={setAddUserId}>
                                                    <SelectTrigger className="h-8 text-xs flex-1">
                                                        <SelectValue placeholder="Select User" />
                                                    </SelectTrigger>
                                                    <SelectContent>
                                                        {availableUsersForProjectDirect.map(u => (
                                                            <SelectItem key={u.id} value={u.id.toString()}>{u.full_name}</SelectItem>
                                                        ))}
                                                    </SelectContent>
                                                </Select>
                                                <Select value={addAccessLevel} onValueChange={setAddAccessLevel}>
                                                    <SelectTrigger className="h-8 text-xs w-[90px]">
                                                        <SelectValue />
                                                    </SelectTrigger>
                                                    <SelectContent>
                                                        <SelectItem value="viewer">Viewer</SelectItem>
                                                        <SelectItem value="editor">Editor</SelectItem>
                                                        <SelectItem value="admin">Admin</SelectItem>
                                                    </SelectContent>
                                                </Select>
                                                <Button
                                                    size="sm"
                                                    className="h-8 px-2"
                                                    disabled={!addUserId || addUserProjectAccessMutation.isPending}
                                                    onClick={() => addUserProjectAccessMutation.mutate(parseInt(addUserId))}
                                                >
                                                    <Plus className="h-3 w-3" />
                                                </Button>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            )}

            {/* Invite Org Member Dialog */}
            {showInviteOrgMember && (
                <div className="fixed inset-0 bg-background/80 backdrop-blur-md z-[100] flex items-center justify-center p-4">
                    <Card className="w-full max-w-md shadow-2xl scale-in-center overflow-hidden border-indigo-100">
                        <div className="h-2 bg-indigo-600 w-full" />
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                                <UserPlus className="h-5 w-5 text-indigo-600" />
                                Invite to Organization
                            </CardTitle>
                            <CardDescription>Grant a new user access to the entire organization</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="space-y-2">
                                <label className="text-sm font-bold text-slate-700">Email Address</label>
                                <div className="relative">
                                    <Mail className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
                                    <Input
                                        className="pl-10"
                                        type="email"
                                        value={orgInviteEmail}
                                        onChange={(e) => setOrgInviteEmail(e.target.value)}
                                        placeholder="colleague@company.com"
                                    />
                                </div>
                            </div>
                            <div className="space-y-2">
                                <label className="text-sm font-bold text-slate-700">Organization Role</label>
                                <Select value={orgInviteRole} onValueChange={setOrgInviteRole}>
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="member">
                                            <div className="flex items-center gap-2">
                                                <Users className="h-4 w-4 text-slate-400" />
                                                <div className="text-left">
                                                    <p className="font-bold text-xs uppercase tracking-tight">Member</p>
                                                    <p className="text-[10px] text-muted-foreground">Standard access to assigned projects</p>
                                                </div>
                                            </div>
                                        </SelectItem>
                                        <SelectItem value="admin">
                                            <div className="flex items-center gap-2">
                                                <ShieldCheck className="h-4 w-4 text-indigo-600" />
                                                <div className="text-left">
                                                    <p className="font-bold text-xs uppercase tracking-tight">Admin</p>
                                                    <p className="text-[10px] text-muted-foreground">Full control over teams and members</p>
                                                </div>
                                            </div>
                                        </SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="flex justify-end gap-2 pt-4">
                                <Button variant="ghost" onClick={() => setShowInviteOrgMember(false)}>Cancel</Button>
                                <Button
                                    className="bg-indigo-600 hover:bg-indigo-700"
                                    onClick={() => inviteOrgMemberMutation.mutate()}
                                    disabled={!orgInviteEmail || inviteOrgMemberMutation.isPending}
                                >
                                    {inviteOrgMemberMutation.isPending && <Loader2 className="animate-spin h-4 w-4 mr-2" />}
                                    Send Invitation
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            )}

            {/* Deletion Confirmation Dialog */}
            <AlertDialog open={!!deleteTarget} onOpenChange={(open: boolean) => !open && setDeleteTarget(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle className="flex items-center gap-2 text-destructive">
                            <AlertTriangle className="h-5 w-5" />
                            Confirm Deletion
                        </AlertDialogTitle>
                        <AlertDialogDescription>
                            Are you sure you want to delete the {deleteTarget?.type} <strong>{deleteTarget?.name}</strong>?
                            This action cannot be undone and may affect associated data.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                            onClick={() => {
                                if (deleteTarget?.type === 'org') deleteOrgMutation.mutate(deleteTarget.id);
                                if (deleteTarget?.type === 'team') deleteTeamMutation.mutate(deleteTarget.id);
                                if (deleteTarget?.type === 'project') deleteProjectMutation.mutate(deleteTarget.id);
                            }}
                        >
                            Delete
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}

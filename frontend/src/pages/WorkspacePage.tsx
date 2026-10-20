import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
    getWorkspaces, createWorkspace,
    getTeams, createTeam, deleteTeam,
    getProjects, createProject, deleteProject,
    Workspace, Team, Project, User,
    inviteToTeam, getTeamMembers, linkTeamToProject, getWorkspaceMembers,
    removeUserFromTeam, getProjectTeams, getProjectMembers,
    unlinkTeamFromProject, removeUserProjectAccess,
    addTeamToProject, addUserProjectAccess,
    getWorkspaceMembersDetailed, inviteUserToWorkspace, getWorkspaceInvitations,
} from '@/lib/api';
import type { DetailedMember } from '@/lib/api';
import WorkspaceDeleteDialog from '@/components/WorkspaceDeleteDialog';
import DemoBadge from '@/components/demo/DemoBadge';
import DemoWorkspaceCard from '@/components/demo/DemoWorkspaceCard';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
    Plus, Users, FolderOpen, Loader2, Mail, Link as LinkIcon, Trash2, X, UserPlus, Copy,
} from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { toast } from 'sonner';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
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
import {
    DetailCard, EmptyState, Eyebrow, ListGroup, ListRow, PageHeader, PanelTabs, Panel, Pill, SplitView,
} from '@/components/redesign/primitives';
import { Avatar, Facts, Field, ListSkeleton } from '@/components/redesign-local/groupE/bits';
import { cn } from '@/lib/utils';

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

type Section = 'members' | 'teams' | 'projects' | 'danger';

const errDetail = (error: unknown, fallback: string): string =>
    (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail || fallback;

const inputCls = 'h-10 rounded-full px-4';
const textareaCls = 'min-h-20 w-full rounded-2xl border border-input bg-transparent px-4 py-2.5 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring';

function IconChip({ icon: Icon, tone }: { icon: typeof Users; tone: 'lavender' | 'mint' | 'butter' }) {
    const cls = { lavender: 'bg-pastel-lavender text-pastel-ink-lavender', mint: 'bg-pastel-mint text-pastel-ink-mint', butter: 'bg-pastel-butter text-pastel-ink-butter' }[tone];
    return (
        <span aria-hidden="true" className={cn('flex h-8 w-8 flex-none items-center justify-center rounded-[10px]', cls)}>
            <Icon className="h-[15px] w-[15px]" strokeWidth={2.2} />
        </span>
    );
}

function AccessLevelSelect({ id, value, onChange, label }: { id?: string; value: string; onChange: (v: string) => void; label?: string }) {
    return (
        <Select value={value} onValueChange={onChange}>
            <SelectTrigger id={id} aria-label={label} className={cn(inputCls, 'w-[120px] flex-none')}>
                <SelectValue />
            </SelectTrigger>
            <SelectContent>
                <SelectItem value="viewer">Viewer</SelectItem>
                <SelectItem value="editor">Editor</SelectItem>
                <SelectItem value="admin">Admin</SelectItem>
            </SelectContent>
        </Select>
    );
}

export default function WorkspacePage() {
    const queryClient = useQueryClient();
    const [pickedWorkspaceId, setSelectedWorkspaceId] = useState<number | null>(null);
    const [pickedTeamId, setSelectedTeamId] = useState<number | null>(null);
    const [pickedProjectId, setPickedProjectId] = useState<number | null>(null);
    const [pickedMemberKey, setPickedMemberKey] = useState<string | null>(null);
    const [section, setSection] = useState<Section>('members');
    const [showCreateWorkspace, setShowCreateWorkspace] = useState(false);
    const [showCreateTeam, setShowCreateTeam] = useState(false);
    const [showCreateProject, setShowCreateProject] = useState(false);
    const [showInviteMember, setShowInviteMember] = useState(false);
    const [showLinkProject, setShowLinkProject] = useState(false);

    // Deletion states
    const [deleteTarget, setDeleteTarget] = useState<{ type: 'workspace' | 'team' | 'project', id: number, name: string } | null>(null);

    const [newName, setNewName] = useState('');
    const [newDesc, setNewDesc] = useState('');
    const [inviteEmail, setInviteEmail] = useState('');
    const [linkProjectId, setLinkProjectId] = useState<string>('');
    const [linkAccessLevel, setLinkAccessLevel] = useState<string>('editor');

    // Project access states
    const [addTeamId, setAddTeamId] = useState<string>('');
    const [addUserId, setAddUserId] = useState<string>('');
    const [addAccessLevel, setAddAccessLevel] = useState<string>('editor');

    // Workspace Member Management states
    const [showInviteWorkspaceMember, setShowInviteWorkspaceMember] = useState(false);
    const [workspaceInviteEmail, setWorkspaceInviteEmail] = useState('');
    const [workspaceInviteRole, setWorkspaceInviteRole] = useState('member');

    // Queries
    const { data: workspaces, isLoading: workspacesLoading } = useQuery<Workspace[]>({
        queryKey: ['workspaces'],
        queryFn: getWorkspaces,
    });

    // First workspace until the user picks one (derived, not set in an effect).
    const selectedWorkspaceId = pickedWorkspaceId ?? workspaces?.[0]?.id ?? null;

    const { data: teams, isLoading: teamsLoading } = useQuery<Team[]>({
        queryKey: ['teams', selectedWorkspaceId],
        queryFn: () => getTeams(selectedWorkspaceId!),
        enabled: !!selectedWorkspaceId,
    });

    // On the Teams section the first team is shown until one is picked.
    const selectedTeamId = pickedTeamId ?? (section === 'teams' ? teams?.[0]?.id ?? null : null);

    const { data: projects, isLoading: projectsLoading } = useQuery<Project[]>({
        queryKey: ['projects', selectedWorkspaceId],
        queryFn: () => getProjects(selectedWorkspaceId!),
        enabled: !!selectedWorkspaceId,
    });

    // The selected project's access is managed inline on the Projects section.
    const selectedProject = projects?.find(p => p.id === pickedProjectId) ?? projects?.[0] ?? null;
    const showProjectAccess = section === 'projects' && selectedProject ? { id: selectedProject.id, name: selectedProject.name } : null;

    const { data: teamMembers, isLoading: teamMembersLoading } = useQuery<User[]>({
        queryKey: ['teamMembers', selectedTeamId],
        queryFn: () => getTeamMembers(selectedTeamId!),
        enabled: !!selectedTeamId,
    });

    const { data: workspaceMembersDetailed, isLoading: workspaceMembersDetailedLoading } = useQuery<DetailedMember[]>({
        queryKey: ['workspaceMembersDetailed', selectedWorkspaceId],
        queryFn: () => getWorkspaceMembersDetailed(selectedWorkspaceId!),
        enabled: !!selectedWorkspaceId,
    });

    const { data: workspaceInvitations } = useQuery<DetailedMember[]>({
        queryKey: ['workspaceInvitations', selectedWorkspaceId],
        queryFn: () => getWorkspaceInvitations(selectedWorkspaceId!),
        enabled: !!selectedWorkspaceId,
    });

    const { data: workspaceMembers } = useQuery<User[]>({
        queryKey: ['workspaceMembers', selectedWorkspaceId],
        queryFn: () => getWorkspaceMembers(selectedWorkspaceId!),
        enabled: !!selectedWorkspaceId,
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
    const workspaceMutation = useMutation({
        mutationFn: createWorkspace,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['workspaces'] });
            setShowCreateWorkspace(false);
            setNewName('');
            setNewDesc('');
            toast.success('Workspace created');
        }
    });

    // Workspace deletion is a confirmed purge with its own dialog
    // (WorkspaceDeleteDialog); this runs after it succeeds.
    const onWorkspaceDeleted = () => {
        queryClient.invalidateQueries({ queryKey: ['workspaces'] });
        setDeleteTarget(null);
        setSelectedWorkspaceId(null);
        toast.success('Workspace deleted');
    };

    const teamMutation = useMutation({
        mutationFn: (data: { name: string, description?: string, initial_project_id?: number, initial_access_level?: string }) => createTeam(selectedWorkspaceId!, data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['teams', selectedWorkspaceId] });
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
            queryClient.invalidateQueries({ queryKey: ['teams', selectedWorkspaceId] });
            setDeleteTarget(null);
            setSelectedTeamId(null);
            toast.success('Team deleted');
        }
    });

    const projectMutation = useMutation({
        mutationFn: (data: { name: string, description?: string }) => createProject({ ...data, workspace_id: selectedWorkspaceId! }),
        onSuccess: () => {
            // ['projects'] (prefix) also refreshes the sidebar's project switcher,
            // which caches the list under ['projects'] — invalidating only this
            // workspace's key left a new project out of the switcher until reload.
            queryClient.invalidateQueries({ queryKey: ['projects'] });
            setShowCreateProject(false);
            setNewName('');
            setNewDesc('');
            toast.success('Project created');
        }
    });

    const deleteProjectMutation = useMutation({
        mutationFn: deleteProject,
        onSuccess: () => {
            // Prefix key, as above: the switcher must drop a deleted project.
            queryClient.invalidateQueries({ queryKey: ['projects'] });
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
            toast.error(errDetail(error, 'Failed to add user'));
        }
    });

    const linkMutation = useMutation({
        mutationFn: () => linkTeamToProject(parseInt(linkProjectId), selectedTeamId!, linkAccessLevel),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['teams', selectedWorkspaceId] });
            queryClient.invalidateQueries({ queryKey: ['projects', selectedWorkspaceId] });
            setShowLinkProject(false);
            setLinkProjectId('');
            setLinkAccessLevel('editor');
            toast.success('Team linked to project');
        },
        onError: (error: unknown) => {
            toast.error(errDetail(error, 'Failed to link team'));
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
            toast.error(errDetail(error, 'Failed to unlink team'));
        }
    });

    const removeUserProjectAccessMutation = useMutation({
        mutationFn: (userId: number) => removeUserProjectAccess(showProjectAccess!.id, userId),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['projectMembers', showProjectAccess?.id] });
            toast.success('User access removed');
        },
        onError: (error: unknown) => {
            toast.error(errDetail(error, 'Failed to remove user access'));
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
            toast.error(errDetail(error, 'Failed to add team'));
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
            toast.error(errDetail(error, 'Failed to add user'));
        }
    });

    const inviteWorkspaceMemberMutation = useMutation({
        mutationFn: () => inviteUserToWorkspace(selectedWorkspaceId!, workspaceInviteEmail, workspaceInviteRole),
        onSuccess: (data: unknown) => {
            queryClient.invalidateQueries({ queryKey: ['workspaceInvitations', selectedWorkspaceId] });
            queryClient.invalidateQueries({ queryKey: ['workspaceMembersDetailed', selectedWorkspaceId] });
            setWorkspaceInviteEmail('');
            setShowInviteWorkspaceMember(false);
            toast.success((data as { message?: string } | undefined)?.message || 'Invitation sent');
        },
        onError: (error: unknown) => {
            toast.error(errDetail(error, 'Failed to send invitation'));
        }
    });

    if (workspacesLoading) {
        return (
            <div className="flex flex-col gap-5">
                <PageHeader title="Workspace" subtitle="Loading your workspaces…" />
                <ListSkeleton />
            </div>
        );
    }

    const selectedWorkspace = workspaces?.find(o => o.id === selectedWorkspaceId);
    const selectedTeam = teams?.find(t => t.id === selectedTeamId);

    const availableMembers = workspaceMembers?.filter(om =>
        !teamMembers?.some(tm => tm.id === om.id)
    ) || [];

    const availableTeamsForProject = teams?.filter(t =>
        !projectTeams?.some(pt => pt.id === t.id)
    ) || [];

    const availableUsersForProjectDirect = workspaceMembers?.filter(om =>
        !projectMembers?.some(pm => pm.id === om.id)
    ) || [];

    const members = workspaceMembersDetailed ?? [];
    const invites = workspaceInvitations ?? [];
    const memberKey = (m: DetailedMember) => `m:${m.id}`;
    const inviteKey = (i: DetailedMember) => `i:${i.id}`;
    const pickedMember = members.find(m => memberKey(m) === pickedMemberKey) ?? null;
    const pickedInvite = invites.find(i => inviteKey(i) === pickedMemberKey) ?? null;
    const shownMember = pickedMember ?? (!pickedInvite ? members[0] ?? null : null);
    const shownInvite = shownMember ? null : (pickedInvite ?? invites[0] ?? null);

    const openCreateTeam = () => {
        setShowCreateTeam(true);
        setLinkProjectId('');
        setLinkAccessLevel('editor');
        setNewName('');
        setNewDesc('');
    };
    const openInviteToTeam = () => { setShowInviteMember(true); setInviteEmail(''); };
    const openLinkProject = () => { setShowLinkProject(true); setLinkProjectId(''); setLinkAccessLevel('editor'); };

    // ---------------------------------------------------------------------
    // Sections
    // ---------------------------------------------------------------------

    const membersSection = (
        <div className="flex flex-col gap-4">
            {workspaceMembersDetailedLoading ? (
                <ListSkeleton />
            ) : members.length === 0 && invites.length === 0 ? (
                <EmptyState title="No members found">Invite people to give them access to this workspace.</EmptyState>
            ) : (
                <SplitView
                    list={
                        <>
                            {members.length > 0 && (
                                <ListGroup title="Members" count={members.length}>
                                    {members.map((m) => (
                                        <ListRow
                                            key={memberKey(m)}
                                            selected={!!shownMember && memberKey(shownMember) === memberKey(m)}
                                            onSelect={() => setPickedMemberKey(memberKey(m))}
                                            leading={<Avatar name={m.full_name || m.email} />}
                                            title={m.full_name}
                                            meta={m.email}
                                            trailing={<Pill tone={m.role === 'admin' ? 'lavender' : 'neutral'} className="capitalize">{m.role}</Pill>}
                                        />
                                    ))}
                                </ListGroup>
                            )}
                            {invites.length > 0 && (
                                <ListGroup title="Pending invites" count={invites.length}>
                                    {invites.map((i) => (
                                        <ListRow
                                            key={inviteKey(i)}
                                            selected={!!shownInvite && inviteKey(shownInvite) === inviteKey(i)}
                                            onSelect={() => setPickedMemberKey(inviteKey(i))}
                                            leading={<IconChip icon={Mail} tone="butter" />}
                                            title={i.email}
                                            meta={i.created_at ? `Invited ${formatDistanceToNow(new Date(i.created_at), { addSuffix: true })}` : 'Invited'}
                                            trailing={<Pill tone="butter">Invited</Pill>}
                                        />
                                    ))}
                                </ListGroup>
                            )}
                        </>
                    }
                    detail={shownMember ? (
                        <DetailCard label="Selected member">
                            <div className="flex items-start gap-4">
                                <Avatar name={shownMember.full_name || shownMember.email} />
                                <div className="flex min-w-0 flex-col gap-1">
                                    <h2 className="break-words text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{shownMember.full_name}</h2>
                                    <span className="break-all text-sm text-ink-soft">{shownMember.email}</span>
                                </div>
                            </div>
                            <Facts
                                items={[
                                    { label: 'Workspace role', value: <Pill tone={shownMember.role === 'admin' ? 'lavender' : 'neutral'} className="capitalize">{shownMember.role}</Pill> },
                                    { label: 'Status', value: <Pill tone="mint">Active</Pill> },
                                    {
                                        label: 'Last active',
                                        value: shownMember.last_login_at
                                            ? formatDistanceToNow(new Date(shownMember.last_login_at), { addSuffix: true })
                                            : 'Never',
                                    },
                                ]}
                            />
                            <Panel title="Changing access" callout>
                                Project access comes from teams and direct grants — see the Teams and Projects sections.
                                To remove someone from the workspace, use the <Link to="/users" className="font-semibold underline underline-offset-2">Users</Link> page.
                            </Panel>
                        </DetailCard>
                    ) : shownInvite ? (
                        <DetailCard label="Selected invitation">
                            <div className="flex flex-col gap-2">
                                <Pill tone="butter" className="self-start">Invited</Pill>
                                <h2 className="break-all text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{shownInvite.email}</h2>
                                {shownInvite.created_at && (
                                    <span className="text-sm text-ink-soft">Sent {formatDistanceToNow(new Date(shownInvite.created_at), { addSuffix: true })}</span>
                                )}
                            </div>
                            <Facts items={[{ label: 'Workspace role', value: <span className="capitalize">{shownInvite.role}</span> }]} />
                            <Panel title="What happens next" callout>
                                They join with this role once they sign up from the invitation link.
                                {shownInvite.token ? ' You can copy the link and send it yourself.' : ''}
                            </Panel>
                            {shownInvite.token && (
                                <div className="flex flex-wrap gap-2.5">
                                    <Button
                                        size="lg"
                                        variant="outline"
                                        onClick={() => {
                                            const url = `${window.location.origin}/signup?token=${shownInvite.token}`;
                                            navigator.clipboard.writeText(url);
                                            toast.success("Invitation link copied to clipboard");
                                        }}
                                    >
                                        <Copy className="h-4 w-4" /> Copy invitation link
                                    </Button>
                                </div>
                            )}
                        </DetailCard>
                    ) : null}
                />
            )}
        </div>
    );

    const teamsSection = (
        <div className="flex flex-col gap-4">
            {teamsLoading ? (
                <ListSkeleton />
            ) : !teams || teams.length === 0 ? (
                <EmptyState title="No teams created yet">Create a team, then add members and link it to projects.</EmptyState>
            ) : (
                <SplitView
                    list={
                        <ListGroup title="Teams" count={teams.length}>
                            {teams.map((team) => (
                                <ListRow
                                    key={team.id}
                                    selected={selectedTeamId === team.id}
                                    onSelect={() => setSelectedTeamId(team.id)}
                                    leading={<IconChip icon={Users} tone="lavender" />}
                                    title={team.name}
                                    meta={team.description ? <span className="line-clamp-1">{team.description}</span> : undefined}
                                />
                            ))}
                        </ListGroup>
                    }
                    detail={selectedTeam && (
                        <DetailCard label="Selected team">
                            <div className="flex flex-col gap-2">
                                <h2 className="break-words text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{selectedTeam.name}</h2>
                                <span className="text-sm text-ink-soft">{selectedTeam.description || 'Manage members and project access.'}</span>
                            </div>

                            <div className="flex flex-col gap-2">
                                <Eyebrow right={`${teamMembers?.length || 0}`}>Members</Eyebrow>
                                {teamMembersLoading ? (
                                    <div className="h-12 animate-pulse rounded-2xl bg-background" aria-busy="true" aria-label="Loading members" />
                                ) : !teamMembers || teamMembers.length === 0 ? (
                                    <p className="rounded-2xl bg-background px-4 py-5 text-center text-sm text-ink-soft">No members in this team</p>
                                ) : (
                                    <ul className="overflow-hidden rounded-2xl border border-border">
                                        {teamMembers.map((member, i) => (
                                            <li key={member.id} className={cn('flex min-h-12 items-center gap-3 px-4 py-2', i > 0 && 'border-t border-[#F0EEE8]')}>
                                                <Avatar name={member.full_name || member.email} size="sm" />
                                                <span className="flex min-w-0 flex-1 flex-col">
                                                    <span className="truncate text-sm font-medium text-ink">{member.full_name || member.email}</span>
                                                    <span className="truncate text-xs text-ink-soft">{member.email}</span>
                                                </span>
                                                <Button
                                                    size="icon"
                                                    variant="ghost"
                                                    aria-label={`Remove ${member.email} from ${selectedTeam.name}`}
                                                    className="text-ink-soft hover:text-pastel-ink-peach"
                                                    onClick={() => removeMemberMutation.mutate(member.id)}
                                                >
                                                    <X className="h-4 w-4" />
                                                </Button>
                                            </li>
                                        ))}
                                    </ul>
                                )}
                            </div>

                            <div className="flex flex-wrap gap-2.5">
                                <Button size="lg" onClick={openInviteToTeam}><UserPlus className="h-4 w-4" /> Add member</Button>
                                <Button size="lg" variant="outline" onClick={openLinkProject}><LinkIcon className="h-4 w-4" /> Link project</Button>
                                <Button size="lg" variant="outline" className="text-pastel-ink-peach" onClick={() => setDeleteTarget({ type: 'team', id: selectedTeam.id, name: selectedTeam.name })}>
                                    <Trash2 className="h-4 w-4" /> Delete team
                                </Button>
                            </div>
                        </DetailCard>
                    )}
                />
            )}
        </div>
    );

    const projectsSection = (
        <div className="flex flex-col gap-4">
            {projectsLoading ? (
                <ListSkeleton />
            ) : !projects || projects.length === 0 ? (
                <EmptyState title="No projects created yet">Create a project to group your test suites.</EmptyState>
            ) : (
                <SplitView
                    list={
                        <ListGroup title="Projects" count={projects.length}>
                            {projects.map((project) => (
                                <ListRow
                                    key={project.id}
                                    selected={selectedProject?.id === project.id}
                                    onSelect={() => setPickedProjectId(project.id)}
                                    leading={<IconChip icon={FolderOpen} tone="mint" />}
                                    title={project.name}
                                    meta={project.description ? <span className="line-clamp-1">{project.description}</span> : undefined}
                                    trailing={project.access_level ? <Pill tone="neutral" className="capitalize">{project.access_level}</Pill> : undefined}
                                />
                            ))}
                        </ListGroup>
                    }
                    detail={selectedProject && (
                        <DetailCard label="Selected project">
                            <div className="flex flex-col gap-2">
                                {selectedProject.access_level && (
                                    <Pill tone="mint" className="self-start capitalize">Your access: {selectedProject.access_level}</Pill>
                                )}
                                <h2 className="break-words text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{selectedProject.name}</h2>
                                {selectedProject.description && <span className="text-sm text-ink-soft">{selectedProject.description}</span>}
                            </div>

                            <div className="flex flex-col gap-2">
                                <Eyebrow right={`${projectTeams?.length || 0}`}>Teams with access</Eyebrow>
                                {!projectTeams || projectTeams.length === 0 ? (
                                    <p className="rounded-2xl bg-background px-4 py-4 text-center text-sm text-ink-soft">No teams linked</p>
                                ) : (
                                    <ul className="overflow-hidden rounded-2xl border border-border">
                                        {projectTeams.map((t, i) => (
                                            <li key={t.id} className={cn('flex min-h-12 items-center gap-3 px-4 py-2', i > 0 && 'border-t border-[#F0EEE8]')}>
                                                <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink" title={t.name}>{t.name}</span>
                                                <Pill tone="lavender" className="capitalize">{t.access_level}</Pill>
                                                <Button
                                                    size="icon"
                                                    variant="ghost"
                                                    aria-label={`Unlink team ${t.name}`}
                                                    className="text-ink-soft hover:text-pastel-ink-peach"
                                                    onClick={() => unlinkTeamMutation.mutate(t.id)}
                                                >
                                                    <X className="h-4 w-4" />
                                                </Button>
                                            </li>
                                        ))}
                                    </ul>
                                )}
                                <div className="flex flex-wrap gap-2">
                                    <Select value={addTeamId} onValueChange={setAddTeamId}>
                                        <SelectTrigger aria-label="Team to grant access" className={cn(inputCls, 'min-w-[180px] flex-1')}>
                                            <SelectValue placeholder="Select a team" />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {availableTeamsForProject.map(t => (
                                                <SelectItem key={t.id} value={t.id.toString()}>{t.name}</SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                    <AccessLevelSelect value={addAccessLevel} onChange={setAddAccessLevel} label="Team access level" />
                                    <Button
                                        variant="outline"
                                        disabled={!addTeamId || addTeamToProjectMutation.isPending}
                                        onClick={() => addTeamToProjectMutation.mutate(parseInt(addTeamId))}
                                    >
                                        <Plus className="h-4 w-4" /> Grant
                                    </Button>
                                </div>
                            </div>

                            <div className="flex flex-col gap-2">
                                <Eyebrow right={`${projectMembers?.length || 0}`}>Direct members</Eyebrow>
                                {!projectMembers || projectMembers.length === 0 ? (
                                    <p className="rounded-2xl bg-background px-4 py-4 text-center text-sm text-ink-soft">No direct users linked</p>
                                ) : (
                                    <ul className="overflow-hidden rounded-2xl border border-border">
                                        {projectMembers.map((m, i) => (
                                            <li key={m.id} className={cn('flex min-h-12 items-center gap-3 px-4 py-2', i > 0 && 'border-t border-[#F0EEE8]')}>
                                                <Avatar name={m.full_name || m.email} size="sm" />
                                                <span className="flex min-w-0 flex-1 flex-col">
                                                    <span className="truncate text-sm font-medium text-ink" title={m.full_name}>{m.full_name}</span>
                                                    <span className="truncate text-xs text-ink-soft" title={m.email}>{m.email}</span>
                                                </span>
                                                <Pill tone="mint" className="capitalize">{m.access_level}</Pill>
                                                <Button
                                                    size="icon"
                                                    variant="ghost"
                                                    aria-label={`Remove access for ${m.email}`}
                                                    className="text-ink-soft hover:text-pastel-ink-peach"
                                                    onClick={() => removeUserProjectAccessMutation.mutate(m.id)}
                                                >
                                                    <X className="h-4 w-4" />
                                                </Button>
                                            </li>
                                        ))}
                                    </ul>
                                )}
                                <div className="flex flex-wrap gap-2">
                                    <Select value={addUserId} onValueChange={setAddUserId}>
                                        <SelectTrigger aria-label="Person to grant access" className={cn(inputCls, 'min-w-[180px] flex-1')}>
                                            <SelectValue placeholder="Select a person" />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {availableUsersForProjectDirect.map(u => (
                                                <SelectItem key={u.id} value={u.id.toString()}>{u.full_name}</SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                    <AccessLevelSelect value={addAccessLevel} onChange={setAddAccessLevel} label="Direct access level" />
                                    <Button
                                        variant="outline"
                                        disabled={!addUserId || addUserProjectAccessMutation.isPending}
                                        onClick={() => addUserProjectAccessMutation.mutate(parseInt(addUserId))}
                                    >
                                        <Plus className="h-4 w-4" /> Grant
                                    </Button>
                                </div>
                            </div>

                            <div className="flex flex-wrap gap-2.5">
                                <Button size="lg" variant="outline" className="text-pastel-ink-peach" onClick={() => setDeleteTarget({ type: 'project', id: selectedProject.id, name: selectedProject.name })}>
                                    <Trash2 className="h-4 w-4" /> Delete project
                                </Button>
                            </div>
                        </DetailCard>
                    )}
                />
            )}
        </div>
    );

    const dangerSection = selectedWorkspace && (
        <section
            aria-labelledby="danger-title"
            className="flex max-w-3xl flex-col gap-4 rounded-3xl border border-border bg-white p-6 shadow-[inset_4px_0_0_#E0603A]"
        >
            <div className="flex flex-col gap-1">
                <Pill tone="peach" className="self-start">Cannot be undone</Pill>
                <h2 id="danger-title" className="text-base font-semibold text-ink">Delete “{selectedWorkspace.name}”</h2>
                <p className="max-w-[64ch] text-[13px] leading-relaxed text-ink-soft">
                    Deleting a workspace removes its teams, projects and the data associated with them. You will be asked to confirm.
                </p>
            </div>
            <div>
                <Button
                    variant="outline"
                    size="lg"
                    className="text-pastel-ink-peach"
                    onClick={() => setDeleteTarget({ type: 'workspace', id: selectedWorkspace.id, name: selectedWorkspace.name })}
                >
                    <Trash2 className="h-4 w-4" /> Delete workspace
                </Button>
            </div>
        </section>
    );

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title={selectedWorkspace ? selectedWorkspace.name : 'Workspace'}
                badge={selectedWorkspace?.is_demo ? <DemoBadge /> : undefined}
                subtitle={
                    selectedWorkspace
                        ? <>
                            {`${members.length} member${members.length === 1 ? '' : 's'} · ${teams?.length ?? 0} team${teams?.length === 1 ? '' : 's'} · ${projects?.length ?? 0} project${projects?.length === 1 ? '' : 's'}`}
                            {section !== 'danger' && <span className="block pt-0.5">{section === 'members' ? 'People with access to the whole workspace, and invitations not yet accepted.' : section === 'teams' ? 'Teams group people and grant them access to projects together.' : 'Projects hold your suites. Grant access to teams or to individual people.'}</span>}
                        </>
                        : 'Manage your workspaces, teams and projects.'
                }
                actions={
                    <>
                        {(workspaces?.length ?? 0) > 0 && (
                            <Select
                                value={selectedWorkspaceId?.toString() ?? ''}
                                onValueChange={(v) => { setSelectedWorkspaceId(parseInt(v)); setSelectedTeamId(null); setPickedProjectId(null); setPickedMemberKey(null); }}
                            >
                                <SelectTrigger className="h-10 w-[220px] rounded-full px-4" aria-label="Workspace">
                                    <SelectValue placeholder="Select a workspace" />
                                </SelectTrigger>
                                <SelectContent>
                                    {workspaces?.map((ws) => (
                                        <SelectItem key={ws.id} value={ws.id.toString()}>
                                            <span className="flex items-center gap-2">{ws.name}{ws.is_demo && <DemoBadge />}</span>
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        )}
                        <Button variant="outline" onClick={() => { setShowCreateWorkspace(true); setNewName(''); setNewDesc(''); }}>
                            <Plus className="h-4 w-4" /> New workspace
                        </Button>
                    </>
                }
                toolbar={selectedWorkspace ? (<>
                    <PanelTabs<Section>
                        label="Workspace sections"
                        value={section}
                        onChange={setSection}
                        tabs={[
                            { id: 'members', label: 'Members', count: members.length + invites.length },
                            { id: 'teams', label: 'Teams', count: teams?.length },
                            { id: 'projects', label: 'Projects', count: projects?.length },
                            { id: 'danger', label: 'Danger zone' },
                        ]}
                    />
                    <div className="ml-auto flex items-center gap-3">
                        {section === 'members' && <><Button onClick={() => { setShowInviteWorkspaceMember(true); setWorkspaceInviteEmail(''); setWorkspaceInviteRole('member'); }}>
                    <UserPlus className="h-4 w-4" /> Invite people
                </Button></>}
                        {section === 'teams' && <><Button onClick={openCreateTeam}><Plus className="h-4 w-4" /> New team</Button></>}
                        {section === 'projects' && <><Button onClick={() => { setShowCreateProject(true); setNewName(''); setNewDesc(''); }}><Plus className="h-4 w-4" /> New project</Button></>}
                    </div>
                </>) : undefined}
            />

            {selectedWorkspace ? (
                <>
                    <div role="tabpanel" aria-label={section}>
                        {section === 'members' && membersSection}
                        {section === 'teams' && teamsSection}
                        {section === 'projects' && projectsSection}
                        {section === 'danger' && dangerSection}
                    </div>
                </>
            ) : (
                <EmptyState title="No workspace selected">Create a workspace to get started.</EmptyState>
            )}

            <DemoWorkspaceCard />

            {/* Create Workspace Dialog */}
            <Dialog open={showCreateWorkspace} onOpenChange={setShowCreateWorkspace}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>New workspace</DialogTitle>
                        <DialogDescription>Set up a new workspace for your automation.</DialogDescription>
                    </DialogHeader>
                    <div className="flex flex-col gap-4">
                        <Field id="ws-name" label="Name">
                            <Input id="ws-name" className={inputCls} value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Company Name" />
                        </Field>
                        <Field id="ws-desc" label="Description">
                            <textarea id="ws-desc" className={textareaCls} value={newDesc} onChange={(e) => setNewDesc(e.target.value)} placeholder="Optional description" />
                        </Field>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setShowCreateWorkspace(false)}>Cancel</Button>
                        <Button onClick={() => workspaceMutation.mutate({ name: newName, description: newDesc })} disabled={!newName || workspaceMutation.isPending}>
                            {workspaceMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                            Create
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Create Team Dialog */}
            <Dialog open={showCreateTeam} onOpenChange={setShowCreateTeam}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>New team</DialogTitle>
                        <DialogDescription>Create a team and optionally link it to a project.</DialogDescription>
                    </DialogHeader>
                    <div className="flex flex-col gap-4">
                        <Field id="team-name" label="Team name">
                            <Input id="team-name" className={inputCls} value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Engineering, QA, etc." />
                        </Field>
                        <Field id="team-desc" label="Description">
                            <textarea id="team-desc" className={textareaCls} value={newDesc} onChange={(e) => setNewDesc(e.target.value)} placeholder="Team goals or focus area" />
                        </Field>
                        <div className="flex flex-col gap-4 border-t border-border pt-4">
                            <Field id="team-link-project" label="Link to project (optional)">
                                <Select value={linkProjectId} onValueChange={setLinkProjectId}>
                                    <SelectTrigger id="team-link-project" className={inputCls}>
                                        <SelectValue placeholder="Select a project" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {projects?.map(p => (
                                            <SelectItem key={p.id} value={p.id.toString()}>{p.name}</SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </Field>
                            {linkProjectId && (
                                <Field id="team-link-access" label="Access level">
                                    <AccessLevelSelect id="team-link-access" value={linkAccessLevel} onChange={setLinkAccessLevel} />
                                </Field>
                            )}
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setShowCreateTeam(false)}>Cancel</Button>
                        <Button onClick={() => teamMutation.mutate({
                            name: newName,
                            description: newDesc,
                            initial_project_id: linkProjectId ? parseInt(linkProjectId) : undefined,
                            initial_access_level: linkProjectId ? linkAccessLevel : undefined
                        })} disabled={!newName || teamMutation.isPending}>
                            {teamMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                            Create team
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Create Project Dialog */}
            <Dialog open={showCreateProject} onOpenChange={setShowCreateProject}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>New project</DialogTitle>
                        <DialogDescription>Create a project to group your test suites.</DialogDescription>
                    </DialogHeader>
                    <div className="flex flex-col gap-4">
                        <Field id="project-name" label="Name">
                            <Input id="project-name" className={inputCls} value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Project Name" />
                        </Field>
                        <Field id="project-desc" label="Description">
                            <textarea id="project-desc" className={textareaCls} value={newDesc} onChange={(e) => setNewDesc(e.target.value)} placeholder="What is this project about?" />
                        </Field>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setShowCreateProject(false)}>Cancel</Button>
                        <Button onClick={() => projectMutation.mutate({ name: newName, description: newDesc })} disabled={!newName || projectMutation.isPending}>
                            {projectMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                            Create project
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Add Team Member Dialog */}
            <Dialog open={showInviteMember} onOpenChange={setShowInviteMember}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>Add team member</DialogTitle>
                        <DialogDescription>Add a workspace member to {selectedTeam?.name}, or invite someone new by email.</DialogDescription>
                    </DialogHeader>
                    <div className="flex flex-col gap-4">
                        <div className="flex flex-col gap-1.5">
                            <span className="text-[13px] font-semibold text-ink">Workspace members not in this team</span>
                            <ul className="flex max-h-[300px] flex-col overflow-y-auto rounded-2xl border border-border">
                                {availableMembers.map((member, i) => (
                                    <li key={member.id} className={cn('flex min-h-12 items-center gap-3 px-4 py-2', i > 0 && 'border-t border-[#F0EEE8]')}>
                                        <Avatar name={member.full_name || member.email} size="sm" />
                                        <span className="flex min-w-0 flex-1 flex-col">
                                            <span className="truncate text-sm font-medium text-ink">{member.full_name}</span>
                                            <span className="truncate text-xs text-ink-soft">{member.email}</span>
                                        </span>
                                        <Button size="sm" variant="outline" aria-label={`Add ${member.email}`} onClick={() => inviteMutation.mutate(member.email)}>
                                            Add
                                        </Button>
                                    </li>
                                ))}
                                {availableMembers.length === 0 && (
                                    <li className="py-8 text-center text-sm text-ink-soft">No other workspace members found</li>
                                )}
                            </ul>
                        </div>

                        <Field id="team-invite-email" label="Or invite by email">
                            <div className="flex gap-2">
                                <Input
                                    id="team-invite-email"
                                    className={cn(inputCls, 'flex-1')}
                                    value={inviteEmail}
                                    onChange={(e) => setInviteEmail(e.target.value)}
                                    placeholder="user@example.com"
                                />
                                <Button onClick={() => inviteMutation.mutate(inviteEmail)} disabled={!inviteEmail || inviteMutation.isPending}>
                                    <Mail className="h-4 w-4" /> Invite
                                </Button>
                            </div>
                        </Field>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setShowInviteMember(false)}>Close</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Link Project Dialog */}
            <Dialog open={showLinkProject} onOpenChange={setShowLinkProject}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>Link team to project</DialogTitle>
                        <DialogDescription>Grant {selectedTeam?.name} access to a project.</DialogDescription>
                    </DialogHeader>
                    <div className="flex flex-col gap-4">
                        <Field id="link-project" label="Project">
                            <Select value={linkProjectId} onValueChange={setLinkProjectId}>
                                <SelectTrigger id="link-project" className={inputCls}>
                                    <SelectValue placeholder="Choose a project" />
                                </SelectTrigger>
                                <SelectContent>
                                    {projects?.map(p => (
                                        <SelectItem key={p.id} value={p.id.toString()}>{p.name}</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </Field>
                        <Field id="link-access" label="Access level">
                            <AccessLevelSelect id="link-access" value={linkAccessLevel} onChange={setLinkAccessLevel} />
                        </Field>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setShowLinkProject(false)}>Cancel</Button>
                        <Button onClick={() => linkMutation.mutate()} disabled={!linkProjectId || linkMutation.isPending}>
                            {linkMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                            Link project
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Invite Workspace Member Dialog */}
            <Dialog open={showInviteWorkspaceMember} onOpenChange={setShowInviteWorkspaceMember}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>Invite to workspace</DialogTitle>
                        <DialogDescription>Give a new person access to the whole workspace.</DialogDescription>
                    </DialogHeader>
                    <div className="flex flex-col gap-4">
                        <Field id="ws-invite-email" label="Email address">
                            <Input
                                id="ws-invite-email"
                                className={inputCls}
                                type="email"
                                value={workspaceInviteEmail}
                                onChange={(e) => setWorkspaceInviteEmail(e.target.value)}
                                placeholder="colleague@company.com"
                            />
                        </Field>
                        <Field
                            id="ws-invite-role"
                            label="Workspace role"
                            hint={workspaceInviteRole === 'admin' ? 'Full control over teams and members.' : 'Standard access to assigned projects.'}
                        >
                            <Select value={workspaceInviteRole} onValueChange={setWorkspaceInviteRole}>
                                <SelectTrigger id="ws-invite-role" className={inputCls}>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="member">Member</SelectItem>
                                    <SelectItem value="admin">Admin</SelectItem>
                                </SelectContent>
                            </Select>
                        </Field>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setShowInviteWorkspaceMember(false)}>Cancel</Button>
                        <Button
                            onClick={() => inviteWorkspaceMemberMutation.mutate()}
                            disabled={!workspaceInviteEmail || inviteWorkspaceMemberMutation.isPending}
                        >
                            {inviteWorkspaceMemberMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                            Send invitation
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Deletion Confirmation Dialog */}
            {deleteTarget?.type === 'workspace' && (
                <WorkspaceDeleteDialog
                    workspace={{ id: deleteTarget.id, name: deleteTarget.name }}
                    open
                    onOpenChange={(open) => !open && setDeleteTarget(null)}
                    onDeleted={onWorkspaceDeleted}
                />
            )}
            <AlertDialog open={!!deleteTarget && deleteTarget.type !== 'workspace'} onOpenChange={(open: boolean) => !open && setDeleteTarget(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Delete this {deleteTarget?.type}?</AlertDialogTitle>
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

import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import {
    getAdminUsers,
    getAdminWorkspaces,
    assignUserToWorkspaces,
    User,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogFooter,
} from "@/components/ui/dialog";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { Loader2, Search } from "lucide-react";
import {
    DetailCard, EmptyState, ListGroup, ListRow, PageHeader, Panel, Pill, SplitView,
} from "@/components/redesign/primitives";
import type { Tone } from "@/components/redesign/kinds";
import { Avatar, Facts, ListSkeleton } from "@/components/redesign-local/groupE/bits";

const roleTone = (role?: string): Tone => {
    const r = (role || "").toLowerCase();
    if (r.includes("tenant admin")) return "pink";
    if (r.includes("admin")) return "lavender";
    return "neutral";
};

export default function AdminUsersPage() {
    const [search, setSearch] = useState("");
    const [selectedUser, setSelectedUser] = useState<User | null>(null);
    const [isAssignOpen, setIsAssignOpen] = useState(false);
    const [focusedUserId, setFocusedUserId] = useState<number | null>(null);

    // Assignment state
    const [selectedWorkspaces, setSelectedWorkspaces] = useState<number[]>([]);
    const [role, setRole] = useState("member");

    const { data: users, isLoading: isLoadingUsers } = useQuery({
        queryKey: ["admin", "users"],
        queryFn: getAdminUsers,
    });

    const { data: workspaces } = useQuery({
        queryKey: ["admin", "workspaces"],
        queryFn: getAdminWorkspaces,
    });

    const assignMutation = useMutation({
        mutationFn: (data: { userId: number; workspaceIds: number[]; role: string }) =>
            assignUserToWorkspaces(data.userId, data.workspaceIds, data.role),
        onSuccess: () => {
            toast.success("User assigned to workspaces");
            setIsAssignOpen(false);
            setSelectedWorkspaces([]);
        },
        onError: (error: { response?: { data?: { detail?: string } } }) => {
            toast.error(error.response?.data?.detail || "Failed to assign user");
        },
    });

    const filteredUsers = users?.filter((user) =>
        user.email.toLowerCase().includes(search.toLowerCase()) ||
        (user.full_name && user.full_name.toLowerCase().includes(search.toLowerCase()))
    );

    const handleAssignClick = (user: User) => {
        setSelectedUser(user);
        setSelectedWorkspaces([]);
        setRole("member");
        setIsAssignOpen(true);
    };

    const handleWorkspaceToggle = (workspaceId: number) => {
        setSelectedWorkspaces((prev) =>
            prev.includes(workspaceId)
                ? prev.filter((id) => id !== workspaceId)
                : [...prev, workspaceId]
        );
    };

    const handleAssignSubmit = () => {
        if (selectedUser && selectedWorkspaces.length > 0) {
            assignMutation.mutate({
                userId: selectedUser.id,
                workspaceIds: selectedWorkspaces,
                role: role,
            });
        } else {
            toast.error("Please select at least one workspace");
        }
    };

    const list = filteredUsers ?? [];
    const groups = [
        { name: "Active", items: list.filter((u) => u.is_active) },
        { name: "Inactive", items: list.filter((u) => !u.is_active) },
    ].filter((g) => g.items.length > 0);
    const focused = list.find((u) => u.id === focusedUserId) ?? groups[0]?.items[0] ?? null;

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Tenant users"
                subtitle={
                    users
                        ? `${users.length} user${users.length === 1 ? "" : "s"} across ${workspaces?.length ?? 0} workspace${workspaces?.length === 1 ? "" : "s"} in your tenant`
                        : "Manage users across all workspaces in your tenant."
                }
            />

            {isLoadingUsers ? (
                <ListSkeleton rows={4} />
            ) : (
                <SplitView
                    list={
                        <>
                            <div className="relative">
                                <Search className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-soft" aria-hidden="true" />
                                <Input
                                    placeholder="Search by name or email"
                                    aria-label="Search users"
                                    value={search}
                                    onChange={(e) => setSearch(e.target.value)}
                                    className="h-10 rounded-full bg-white pl-10 pr-4"
                                />
                            </div>
                            {list.length === 0 ? (
                                <p className="px-2 py-6 text-center text-sm text-ink-soft">No users found.</p>
                            ) : groups.map((g) => (
                                <ListGroup key={g.name} title={g.name} count={g.items.length}>
                                    {g.items.map((user) => (
                                        <ListRow
                                            key={user.id}
                                            selected={focused?.id === user.id}
                                            onSelect={() => setFocusedUserId(user.id)}
                                            leading={<Avatar name={user.full_name || user.email} />}
                                            title={user.full_name || "N/A"}
                                            meta={user.email}
                                            trailing={<Pill tone={roleTone(user.role)}>{user.role || "Member"}</Pill>}
                                        />
                                    ))}
                                </ListGroup>
                            ))}
                        </>
                    }
                    detail={focused ? (
                        <DetailCard label="Selected user">
                            <div className="flex items-start gap-4">
                                <Avatar name={focused.full_name || focused.email} />
                                <div className="flex min-w-0 flex-col gap-1">
                                    <h2 className="break-words text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{focused.full_name || "N/A"}</h2>
                                    <span className="break-all text-sm text-ink-soft">{focused.email}</span>
                                </div>
                            </div>
                            <Facts
                                items={[
                                    { label: "Role", value: <Pill tone={roleTone(focused.role)}>{focused.role || "Member"}</Pill> },
                                    { label: "Status", value: <Pill tone={focused.is_active ? "mint" : "peach"}>{focused.is_active ? "Active" : "Inactive"}</Pill> },
                                ]}
                            />
                            <Panel title="Workspace access" callout>
                                Add this person to one or more workspaces in the tenant as a member or an admin.
                                Existing memberships are not removed.
                            </Panel>
                            <div className="flex flex-wrap gap-2.5">
                                <Button size="lg" onClick={() => handleAssignClick(focused)}>Assign workspaces</Button>
                            </div>
                        </DetailCard>
                    ) : (
                        <EmptyState title="No user selected">Pick someone from the list to see their access.</EmptyState>
                    )}
                />
            )}

            <Dialog open={isAssignOpen} onOpenChange={setIsAssignOpen}>
                <DialogContent className="sm:max-w-[425px]">
                    <DialogHeader>
                        <DialogTitle>Assign workspaces{selectedUser ? ` to ${selectedUser.full_name || selectedUser.email}` : ""}</DialogTitle>
                    </DialogHeader>
                    <div className="grid gap-4 py-4">
                        <div className="flex flex-col gap-1.5">
                            <label htmlFor="assign-role" className="text-[13px] font-semibold text-ink">Role</label>
                            <Select value={role} onValueChange={setRole}>
                                <SelectTrigger id="assign-role" className="h-10 rounded-full px-4">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="member">Member</SelectItem>
                                    <SelectItem value="admin">Admin</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <fieldset className="flex flex-col gap-1.5">
                            <legend className="mb-1.5 text-[13px] font-semibold text-ink">Workspaces</legend>
                            <div className="flex max-h-[220px] flex-col overflow-y-auto rounded-2xl border border-border">
                                {workspaces?.map((ws) => (
                                    <label
                                        key={ws.id}
                                        htmlFor={`ws-${ws.id}`}
                                        className="flex min-h-11 cursor-pointer items-center gap-3 border-b border-[#F0EEE8] px-4 text-sm text-ink last:border-b-0 hover:bg-background"
                                    >
                                        <input
                                            type="checkbox"
                                            id={`ws-${ws.id}`}
                                            className="h-[18px] w-[18px] accent-ink"
                                            checked={selectedWorkspaces.includes(ws.id)}
                                            onChange={() => handleWorkspaceToggle(ws.id)}
                                        />
                                        {ws.name}
                                    </label>
                                ))}
                                {workspaces?.length === 0 && (
                                    <p className="py-4 text-center text-sm text-ink-soft">
                                        No workspaces found in this tenant.
                                    </p>
                                )}
                            </div>
                        </fieldset>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setIsAssignOpen(false)}>
                            Cancel
                        </Button>
                        <Button
                            onClick={handleAssignSubmit}
                            disabled={assignMutation.isPending || selectedWorkspaces.length === 0}
                        >
                            {assignMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                            Assign
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}

import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import {
    getAdminUsers,
    getAdminOrgs,
    assignUserToOrgs,
    User,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
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
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { Loader2, Search, UserCog } from "lucide-react";

export default function AdminUsersPage() {
    const [search, setSearch] = useState("");
    const [selectedUser, setSelectedUser] = useState<User | null>(null);
    const [isAssignOpen, setIsAssignOpen] = useState(false);

    // Assignment state
    const [selectedOrgs, setSelectedOrgs] = useState<number[]>([]);
    const [role, setRole] = useState("member");

    const { data: users, isLoading: isLoadingUsers } = useQuery({
        queryKey: ["admin", "users"],
        queryFn: getAdminUsers,
    });

    const { data: orgs } = useQuery({
        queryKey: ["admin", "orgs"],
        queryFn: getAdminOrgs,
    });

    const assignMutation = useMutation({
        mutationFn: (data: { userId: number; orgIds: number[]; role: string }) =>
            assignUserToOrgs(data.userId, data.orgIds, data.role),
        onSuccess: () => {
            toast.success("User assigned to organizations");
            setIsAssignOpen(false);
            setSelectedOrgs([]);
        },
        onError: (error: any) => {
            toast.error(error.response?.data?.detail || "Failed to assign user");
        },
    });

    const filteredUsers = users?.filter((user) =>
        user.email.toLowerCase().includes(search.toLowerCase()) ||
        (user.full_name && user.full_name.toLowerCase().includes(search.toLowerCase()))
    );

    const handleAssignClick = (user: User) => {
        setSelectedUser(user);
        setSelectedOrgs([]);
        setRole("member");
        setIsAssignOpen(true);
    };

    const handleOrgToggle = (orgId: number) => {
        setSelectedOrgs((prev) =>
            prev.includes(orgId)
                ? prev.filter((id) => id !== orgId)
                : [...prev, orgId]
        );
    };

    const handleAssignSubmit = () => {
        if (selectedUser && selectedOrgs.length > 0) {
            assignMutation.mutate({
                userId: selectedUser.id,
                orgIds: selectedOrgs,
                role: role,
            });
        } else {
            toast.error("Please select at least one organization");
        }
    };

    if (isLoadingUsers) {
        return (
            <div className="flex justify-center items-center h-full">
                <Loader2 className="w-8 h-8 animate-spin" />
            </div>
        );
    }

    return (
        <div className="p-8 max-w-7xl mx-auto">
            <div className="flex justify-between items-center mb-6">
                <div>
                    <h1 className="text-2xl font-bold flex items-center gap-2">
                        <UserCog className="w-6 h-6" />
                        Tenant User Management
                    </h1>
                    <p className="text-muted-foreground">
                        Manage users across all organizations in your tenant.
                    </p>
                </div>
            </div>

            <div className="flex items-center space-x-2 mb-4">
                <div className="relative flex-1 max-w-sm">
                    <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
                    <Input
                        placeholder="Search users..."
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="pl-8"
                    />
                </div>
            </div>

            <div className="border rounded-md">
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead>User</TableHead>
                            <TableHead>Email</TableHead>
                            <TableHead>Status</TableHead>
                            <TableHead className="text-right">Actions</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {filteredUsers?.map((user) => (
                            <TableRow key={user.id}>
                                <TableCell className="font-medium">{user.full_name || "N/A"}</TableCell>
                                <TableCell>{user.email}</TableCell>
                                <TableCell>
                                    <span
                                        className={`px-2 py-1 rounded-full text-xs ${user.is_active
                                            ? "bg-green-100 text-green-800"
                                            : "bg-red-100 text-red-800"
                                            }`}
                                    >
                                        {user.is_active ? "Active" : "Inactive"}
                                    </span>
                                </TableCell>
                                <TableCell className="text-right">
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => handleAssignClick(user)}
                                    >
                                        Assign Orgs
                                    </Button>
                                </TableCell>
                            </TableRow>
                        ))}
                        {filteredUsers?.length === 0 && (
                            <TableRow>
                                <TableCell colSpan={4} className="text-center h-24">
                                    No users found.
                                </TableCell>
                            </TableRow>
                        )}
                    </TableBody>
                </Table>
            </div>

            <Dialog open={isAssignOpen} onOpenChange={setIsAssignOpen}>
                <DialogContent className="sm:max-w-[425px]">
                    <DialogHeader>
                        <DialogTitle>Assign Organizations</DialogTitle>
                    </DialogHeader>
                    <div className="grid gap-4 py-4">
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Role</label>
                            <Select value={role} onValueChange={setRole}>
                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="member">Member</SelectItem>
                                    <SelectItem value="admin">Admin</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Select Organizations</label>
                            <div className="border rounded-md p-2 max-h-[200px] overflow-y-auto space-y-2">
                                {orgs?.map((org) => (
                                    <div key={org.id} className="flex items-center space-x-2">
                                        <Checkbox
                                            id={`org-${org.id}`}
                                            checked={selectedOrgs.includes(org.id)}
                                            onCheckedChange={() => handleOrgToggle(org.id)}
                                        />
                                        <label
                                            htmlFor={`org-${org.id}`}
                                            className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70"
                                        >
                                            {org.name}
                                        </label>
                                    </div>
                                ))}
                                {orgs?.length === 0 && (
                                    <p className="text-sm text-muted-foreground text-center py-2">
                                        No organizations found in this tenant.
                                    </p>
                                )}
                            </div>
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setIsAssignOpen(false)}>
                            Cancel
                        </Button>
                        <Button
                            onClick={handleAssignSubmit}
                            disabled={assignMutation.isPending || selectedOrgs.length === 0}
                        >
                            {assignMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                            Assign
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}

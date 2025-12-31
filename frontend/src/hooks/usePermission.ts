import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext"; // Import from context

// Define the response type based on backend
interface PermissionsResponse {
    permissions: string[];
    roles: string[];
}

export const usePermission = (projectId?: number) => {
    const { user } = useAuth(); // or however you get user context

    const { data, isLoading } = useQuery({
        queryKey: ["permissions", projectId],
        queryFn: async () => {
            if (!projectId) return { permissions: [], roles: [] };
            // Manually calling the endpoint since it might not be in api.ts yet
            const response = await api.get(`/auth/permissions?project_id=${projectId}`);
            return response.data as PermissionsResponse;
        },
        enabled: !!projectId && !!user,
        staleTime: 5 * 60 * 1000, // Cache for 5 minutes
    });

    const hasPermission = (action: string, resource: string) => {
        if (!data?.permissions) return false;
        // Check specific permission or admin (wildcard?)
        // Our permissions are "resource:action" e.g. "test_case:create"
        return data.permissions.includes(`${resource}:${action}`);
    };

    const hasRole = (roleName: string) => {
        if (!data?.roles) return false;
        return data.roles.includes(roleName);
    };

    return {
        permissions: data?.permissions || [],
        roles: data?.roles || [],
        isLoading,
        hasPermission,
        hasRole,
    };
};

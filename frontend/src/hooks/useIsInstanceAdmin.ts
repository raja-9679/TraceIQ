import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';

/**
 * Whether the signed-in user is an instance admin (the operator: an explicit
 * grant, or admin of the first tenant). The frontend has no flag for it, so
 * this asks an endpoint guarded by exactly that check — 403 means no. Only
 * decides which controls to offer; the server enforces it.
 */
export function useIsInstanceAdmin(): boolean {
    const q = useQuery({
        queryKey: ['is-instance-admin'],
        queryFn: async () => {
            try {
                await api.get('/admin/instance-admins');
                return true;
            } catch {
                return false;
            }
        },
        staleTime: 5 * 60 * 1000,
        retry: false,
    });
    return q.data === true;
}

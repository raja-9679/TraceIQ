import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';

export type Me = { id: number; email: string; full_name: string; mfa_enabled?: boolean; is_instance_admin?: boolean };

/** `GET /auth/me`, shared by everything that needs facts about the caller. */
export function useMe() {
    return useQuery({
        queryKey: ['auth-me'],
        queryFn: async () => (await api.get<Me>('/auth/me')).data,
        staleTime: 5 * 60 * 1000,
    });
}

/**
 * Whether the signed-in user is an instance admin (the operator: an explicit
 * grant, or admin of the first tenant), as reported by /auth/me. Only decides
 * which controls to offer; the server enforces it.
 */
export function useIsInstanceAdmin(): boolean {
    return useMe().data?.is_instance_admin === true;
}

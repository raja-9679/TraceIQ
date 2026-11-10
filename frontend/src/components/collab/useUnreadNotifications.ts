import { useQuery } from '@tanstack/react-query';
import { collabApi } from '@/api/collaboration';

/** How many unread notifications the signed-in person has (for the bell). */
export function useUnreadNotifications(): number {
    const { data } = useQuery({
        queryKey: ['notifications-unread'],
        queryFn: collabApi.unreadCount,
        refetchInterval: 60_000,
        refetchOnWindowFocus: true,
        // A failed poll is not retried: the bell simply shows no count.
        retry: false,
    });
    return data ?? 0;
}

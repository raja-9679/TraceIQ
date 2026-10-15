// The API answers errors as {detail: string} or {detail: {message, ...}}.
export function errDetail(e: unknown): string {
    const err = e as { response?: { data?: { detail?: unknown } }; message?: string } | null;
    const d = err?.response?.data?.detail;
    if (typeof d === 'string') return d;
    if (d && typeof d === 'object' && 'message' in d && typeof (d as { message: unknown }).message === 'string') {
        return (d as { message: string }).message;
    }
    return err?.message || 'Request failed';
}

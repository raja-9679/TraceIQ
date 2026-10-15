import { api } from '@/lib/api';

// Authenticated downloads: the API needs the bearer header, so a plain <a href>
// would arrive unauthenticated. Fetch as a blob, then hand it to the browser.

export async function downloadFromApi(path: string, filename: string): Promise<void> {
    const res = await api.get(path, { responseType: 'blob' });
    const url = window.URL.createObjectURL(res.data as Blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
}

// Print-ready HTML reports open in a new tab so the browser's "Save as PDF" works.
export async function openHtmlFromApi(path: string): Promise<void> {
    const res = await api.get(path, { responseType: 'blob' });
    const blob = new Blob([res.data as Blob], { type: 'text/html' });
    const url = window.URL.createObjectURL(blob);
    window.open(url, '_blank', 'noopener');
    // Give the new tab time to load before releasing the object URL.
    setTimeout(() => window.URL.revokeObjectURL(url), 60_000);
}

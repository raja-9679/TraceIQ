import { api } from '@/lib/api';

// Reproduce-locally bundle (WP11): GET /api/runs/{run}/results/{result}/bundle
// returns a zip. Fetched through the authenticated client (a plain <a href>
// would carry no Authorization header) and handed to the browser as a file.
export async function downloadReproBundle(runId: number, resultId: number): Promise<void> {
    const res = await api.get(`/runs/${runId}/results/${resultId}/bundle`, { responseType: 'blob' });
    const url = URL.createObjectURL(res.data as Blob);
    try {
        const a = document.createElement('a');
        a.href = url;
        a.download = `traceiq-run${runId}-result${resultId}.zip`;
        document.body.appendChild(a);
        a.click();
        a.remove();
    } finally {
        URL.revokeObjectURL(url);
    }
}

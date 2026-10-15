import { useState } from 'react';
import { Download } from 'lucide-react';
import { toast } from 'sonner';
import { downloadReproBundle } from '@/api/developer';

// One click to a zip with the case, resolved settings, artifacts and a
// standalone repro.spec.ts (secrets stay placeholders; see README.txt in it).
export function ReproBundleButton({ runId, resultId }: { runId: number; resultId: number }) {
    const [busy, setBusy] = useState(false);
    return (
        <button type="button" disabled={busy}
            onClick={async (e) => {
                e.stopPropagation();
                setBusy(true);
                try {
                    await downloadReproBundle(runId, resultId);
                } catch {
                    toast.error('Could not build the reproduce-locally bundle');
                } finally {
                    setBusy(false);
                }
            }}
            className="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-full border border-gray-200 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-60">
            <Download size={14} /> {busy ? 'Preparing bundle…' : 'Reproduce locally (.zip)'}
        </button>
    );
}

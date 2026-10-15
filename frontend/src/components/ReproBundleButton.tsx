import { useState } from 'react';
import { Download } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { downloadReproBundle } from '@/api/developer';

// One click to a zip with the case, resolved settings, artifacts and a
// standalone repro.spec.ts (secrets stay placeholders; see README.txt in it).
export function ReproBundleButton({ runId, resultId }: { runId: number; resultId: number }) {
    const [busy, setBusy] = useState(false);
    return (
        <Button type="button" variant="outline" size="sm" disabled={busy}
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
        >
            <Download size={14} /> {busy ? 'Preparing bundle…' : 'Reproduce locally (.zip)'}
        </Button>
    );
}

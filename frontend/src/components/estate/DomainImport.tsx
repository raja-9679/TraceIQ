import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { estatesApi } from '@/api/estates';
import { Button } from '@/components/ui/button';
import { errDetail } from '@/lib/errors';

interface Props {
    estateId: number;
}

export default function DomainImport({ estateId }: Props) {
    const qc = useQueryClient();
    const [text, setText] = useState('');
    const imp = useMutation({
        mutationFn: () => estatesApi.importDomains(estateId, text),
        onSuccess: (r) => {
            if (r.added.length) setText(r.rejected.map(x => x.value).join('\n'));
            qc.invalidateQueries({ queryKey: ['estate', estateId] });
        },
    });
    return (
        <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); imp.mutate(); }}>
            <label htmlFor={`import-${estateId}`} className="block text-xs font-medium text-slate-600">
                Add domains — one per line, comma separated, or paste a CSV (first column). Hosts must be on
                the project's security allowlist.
            </label>
            <textarea id={`import-${estateId}`} rows={4} value={text} onChange={e => setText(e.target.value)}
                className="w-full rounded-lg border border-slate-200 p-2 font-mono text-xs"
                placeholder={'example.com\nshop.example.com'} />
            <Button type="submit" variant="outline" disabled={!text.trim() || imp.isPending}>
                {imp.isPending ? 'Importing…' : 'Import domains'}
            </Button>
            {imp.error && <p role="alert" className="text-sm text-rose-700">{errDetail(imp.error)}</p>}
            {imp.data && (
                <div className="text-xs text-slate-600" role="status">
                    Added {imp.data.added.length}.
                    {imp.data.rejected.length > 0 && (
                        <ul className="mt-1 list-disc pl-5 text-rose-700">
                            {imp.data.rejected.map(r => <li key={r.value}>{r.value}: {r.reason}</li>)}
                        </ul>
                    )}
                </div>
            )}
        </form>
    );
}

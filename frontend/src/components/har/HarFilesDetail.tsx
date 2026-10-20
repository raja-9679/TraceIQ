import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { harFilesApi, MAX_HAR_MB, type HarFile } from '@/api/harFiles';
import { DetailCard, Panel } from '@/components/redesign/primitives';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { errorDetail } from '@/components/flakes/flakeHelpers';
import { formatBytes } from '@/components/accessibility/a11yModel';

/** Uploaded HARs for the har-replay step (WP15d). Lives on the Environments
 * page next to secrets: both are project-level inputs that tests reference. */
export function HarFilesDetail({ projectId }: { projectId: number }) {
    const qc = useQueryClient();
    const fileRef = useRef<HTMLInputElement>(null);
    const [name, setName] = useState('');
    const [file, setFile] = useState<File | null>(null);
    const list = useQuery({ queryKey: ['har-files', projectId], queryFn: () => harFilesApi.list(projectId) });
    const tooBig = !!file && file.size > MAX_HAR_MB * 1024 * 1024;
    const upload = useMutation({
        mutationFn: () => harFilesApi.upload(projectId, file!, name.trim() || undefined),
        onSuccess: (h: HarFile) => {
            toast.success(`Uploaded "${h.name}" (${h.entry_count} requests)`);
            setFile(null); setName('');
            if (fileRef.current) fileRef.current.value = '';
            qc.invalidateQueries({ queryKey: ['har-files', projectId] });
        },
        onError: (err: unknown) => toast.error(`Upload failed: ${errorDetail(err)}`),
    });
    const remove = useMutation({
        mutationFn: (id: number) => harFilesApi.remove(id),
        onSuccess: () => { toast.success('HAR file deleted'); qc.invalidateQueries({ queryKey: ['har-files', projectId] }); },
        onError: (err: unknown) => toast.error(`Delete failed: ${errorDetail(err)}`),
    });
    const rows = list.data ?? [];
    return (
        <DetailCard label="Recorded traffic">
            <h2 className="text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">Recorded traffic (HAR)</h2>
            <Panel title="How replay works" callout>
                <span className="text-sm text-ink-soft">
                    A <code className="font-mono text-[13px] text-ink">har-replay</code> step answers requests from an uploaded HAR instead of the network.
                    Files are redacted when uploaded — cookies, auth headers and secret-looking values are replaced — so replayed credentials read [REDACTED].
                    Up to {MAX_HAR_MB} MB.
                </span>
            </Panel>
            <form aria-label="Upload HAR" className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); if (file && !tooBig) upload.mutate(); }}>
                <label className="flex flex-col gap-1 text-xs font-semibold text-ink-soft">HAR file
                    <input ref={fileRef} type="file" accept=".har,application/json" aria-label="HAR file"
                        className="text-sm text-ink" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
                </label>
                <label className="flex flex-col gap-1 text-xs font-semibold text-ink-soft">Name (optional)
                    <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="checkout-happy-path.har" />
                </label>
                {tooBig && <p className="text-xs text-ink-soft">That file is over {MAX_HAR_MB} MB.</p>}
                <Button type="submit" className="self-start" disabled={!file || tooBig || upload.isPending}>Upload HAR</Button>
            </form>
            {list.isLoading ? <p className="text-sm text-ink-soft">Loading…</p>
                : list.error ? <p className="text-sm text-ink">Couldn't load HAR files: {errorDetail(list.error)}</p>
                    : rows.length === 0 ? <p className="text-sm text-ink-soft">No HAR files yet.</p> : (
                        <ul aria-label="HAR files" className="flex flex-col">
                            {rows.map((h) => (
                                <li key={h.id} className="flex flex-wrap items-start justify-between gap-3 border-t border-[#F0EEE8] py-3 first:border-t-0">
                                    <div className="flex min-w-0 flex-col gap-1">
                                        <span className="text-sm font-semibold text-ink">{h.name} <span className="font-mono text-xs text-ink-soft">id {h.id}</span></span>
                                        <span className="break-all text-xs text-ink-soft">
                                            {h.entry_count} requests · {formatBytes(h.size_bytes)} · {h.hosts.slice(0, 4).join(', ')}{h.hosts.length > 4 ? '…' : ''}
                                        </span>
                                    </div>
                                    <Button size="sm" variant="outline" onClick={() => remove.mutate(h.id)} disabled={remove.isPending}>Delete</Button>
                                </li>
                            ))}
                        </ul>
                    )}
        </DetailCard>
    );
}

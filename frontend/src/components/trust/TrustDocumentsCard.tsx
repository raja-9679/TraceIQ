import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FileLock2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Pill } from '@/components/redesign/primitives';
import { CheckRow, FieldRow, SettingsCard, inputClass } from '@/components/redesign-local/groupD/settingsForm';
import { trustApi, type TrustDocument } from '@/api/trustCenter';
import { shareLinksApi, type CreatedShareLink } from '@/api/reports';
import { ExpirySelect, LinkRows } from '@/components/reports/ShareLinks';
import { shareUrl } from '@/components/reports/reportHelpers';
import { ACCEPTED_DOCUMENTS, errDetail, fmtBytes } from './trustHelpers';

function NewLink({ link }: { link: CreatedShareLink }) {
    const url = shareUrl(link.path);
    return (
        <div className="flex flex-col gap-2 rounded-2xl border border-border bg-white p-4 shadow-[inset_4px_0_0_#8FD3B6]">
            <label htmlFor={`doc-link-${link.id}`} className="text-sm font-medium text-ink">Download link for {link.label}</label>
            <div className="flex flex-wrap gap-2">
                <Input id={`doc-link-${link.id}`} readOnly value={url} className={`${inputClass} min-w-0 flex-1 font-mono text-xs`}
                    onFocus={(e) => e.currentTarget.select()} />
                <Button variant="outline" onClick={() => navigator.clipboard.writeText(url).then(() => toast.success('Copied'), () => toast.error('Copy failed: copy it by hand'))}>Copy link</Button>
            </div>
            <p className="text-xs leading-relaxed text-ink-soft">
                Copy it now: it is not shown again. It works until {new Date(link.expires_at).toLocaleDateString()} unless you revoke
                it, and stops if the trust center is switched off or you stop being a workspace admin. Every download is audited.
            </p>
        </div>
    );
}

function DocumentRow({ doc, workspaceId }: { doc: TrustDocument; workspaceId: number }) {
    const qc = useQueryClient();
    const linksKey = ['trust-doc-links', doc.id];
    const [open, setOpen] = useState(false);
    const [recipient, setRecipient] = useState('');
    const [expires, setExpires] = useState(7);
    const [created, setCreated] = useState<CreatedShareLink | null>(null);
    const links = useQuery({ queryKey: linksKey, queryFn: () => trustApi.links(doc.id), enabled: open });
    const issue = useMutation({
        mutationFn: () => trustApi.createLink(doc.id, recipient.trim(), expires),
        onSuccess: (l) => { setCreated(l); setRecipient(''); qc.invalidateQueries({ queryKey: linksKey }); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const revoke = useMutation({
        mutationFn: (id: number) => shareLinksApi.revoke(id),
        onSuccess: () => { toast.success('Link revoked'); qc.invalidateQueries({ queryKey: linksKey }); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const remove = useMutation({
        mutationFn: () => trustApi.deleteDocument(doc.id),
        onSuccess: () => qc.invalidateQueries({ queryKey: ['trust-docs', workspaceId] }),
        onError: (e) => toast.error(errDetail(e)),
    });
    return (
        <li className="flex flex-col gap-3 border-t border-[#F0EEE8] py-4 first:border-t-0">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex min-w-0 flex-col">
                    <span className="flex flex-wrap items-center gap-2 text-sm font-semibold text-ink">
                        {doc.title}
                        <Pill tone={doc.listed ? 'mint' : 'neutral'}>{doc.listed ? 'Listed' : 'Unlisted'}</Pill>
                    </span>
                    <span className="text-xs text-ink-soft">{doc.filename} · {fmtBytes(doc.size_bytes)} · SHA-256 {doc.sha256.slice(0, 12)}…</span>
                </div>
                <div className="flex flex-wrap gap-2">
                    <Button variant="outline" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? 'Hide links' : 'Recipients and links'}</Button>
                    <Button variant="outline" disabled={remove.isPending}
                        onClick={() => { if (window.confirm(`Delete "${doc.title}"? Its links stop working.`)) remove.mutate(); }}>
                        Delete
                    </Button>
                </div>
            </div>
            {open && (
                <div className="flex flex-col gap-3 rounded-2xl bg-background p-4">
                    <FieldRow label="Recipient" htmlFor={`doc-rcpt-${doc.id}`} hint="Who this link is for; only admins see it." wide>
                        <Input id={`doc-rcpt-${doc.id}`} className={inputClass} maxLength={120} value={recipient}
                            onChange={(e) => setRecipient(e.target.value)} placeholder="Jane Doe, Bigco security" />
                    </FieldRow>
                    <FieldRow label="Expires after" htmlFor={`doc-exp-${doc.id}`}>
                        <ExpirySelect id={`doc-exp-${doc.id}`} value={expires} onChange={setExpires} />
                    </FieldRow>
                    <div><Button onClick={() => issue.mutate()} disabled={!recipient.trim() || issue.isPending}>Issue link</Button></div>
                    {created && <NewLink link={created} />}
                    {links.isLoading ? <p className="text-sm text-ink-soft">Loading…</p>
                        : <LinkRows links={links.data || []} onRevoke={(id) => revoke.mutate(id)} pending={revoke.isPending} />}
                </div>
            )}
        </li>
    );
}

export function TrustDocumentsCard({ workspaceId, configured }: { workspaceId: number; configured: boolean }) {
    const qc = useQueryClient();
    const key = ['trust-docs', workspaceId];
    const docs = useQuery({ queryKey: key, queryFn: () => trustApi.documents(workspaceId) });
    const fileRef = useRef<HTMLInputElement>(null);
    const [file, setFile] = useState<File | null>(null);
    const [title, setTitle] = useState('');
    const [description, setDescription] = useState('');
    const [listed, setListed] = useState(true);
    const upload = useMutation({
        mutationFn: () => trustApi.upload(workspaceId, file!, title.trim(), description.trim(), listed),
        onSuccess: () => {
            setFile(null); setTitle(''); setDescription(''); setListed(true);
            if (fileRef.current) fileRef.current.value = '';
            qc.invalidateQueries({ queryKey: key });
            toast.success('Document uploaded');
        },
        onError: (e) => toast.error(errDetail(e)),
    });
    return (
        <SettingsCard
            labelledBy="trust-docs"
            icon={<FileLock2 className="h-4 w-4" />}
            title="Gated documents"
            description="The public page lists the titles of listed documents. Downloading one needs a link you issue to a named recipient; links expire (at most 90 days) and can be revoked."
            footer={<Button onClick={() => upload.mutate()} disabled={!file || !title.trim() || upload.isPending || !configured}>Upload</Button>}
        >
            {!configured && <p className="text-sm text-ink-soft">Save the public page settings first.</p>}
            <div className="flex flex-col">
                <FieldRow label="File" htmlFor="doc-file" hint="PDF, PNG, JPEG, Word, Excel, PowerPoint, TXT or Markdown; up to 20 MB. The type is checked from the contents." wide>
                    <input id="doc-file" ref={fileRef} type="file" accept={ACCEPTED_DOCUMENTS} className="min-w-0 text-sm text-ink"
                        onChange={(e) => { const f = e.target.files?.[0] ?? null; setFile(f); if (f && !title) setTitle(f.name.replace(/\.[^.]+$/, '')); }} />
                </FieldRow>
                <FieldRow label="Title" htmlFor="doc-title" wide>
                    <Input id="doc-title" className={inputClass} maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="SOC 2 Type II report 2026" />
                </FieldRow>
                <FieldRow label="Description" htmlFor="doc-desc" hint="Optional, shown publicly under the title." wide>
                    <Input id="doc-desc" className={inputClass} maxLength={500} value={description} onChange={(e) => setDescription(e.target.value)} />
                </FieldRow>
                <CheckRow id="doc-listed" label="List the title on the public page" hint="Unlisted documents are reachable only through a link you issue." checked={listed} onChange={setListed} />
            </div>
            <div className="flex flex-col border-t border-border pt-3">
                <h3 className="mb-1 text-sm font-semibold text-ink">Documents</h3>
                {docs.isLoading ? <p className="text-sm text-ink-soft">Loading…</p>
                    : (docs.data || []).length === 0 ? <p className="text-sm text-ink-soft">No documents yet.</p>
                        : <ul className="flex flex-col">{docs.data!.map((d) => <DocumentRow key={d.id} doc={d} workspaceId={workspaceId} />)}</ul>}
            </div>
        </SettingsCard>
    );
}

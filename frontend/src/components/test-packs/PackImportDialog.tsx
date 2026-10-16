import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Pill } from '@/components/redesign/primitives';
import { getProjects } from '@/lib/api';
import { errorText, packsApi, type PackImportResult, type PackSummary } from '@/api/solution';

const selectClass = 'h-10 w-full rounded-full border border-input bg-white px-4 text-sm text-ink focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring';

/**
 * Import one pack into a project: pick the project (and optionally the
 * environment), fill the variables. Secret ones are stored as project
 * secrets and never shown again; nothing is run or contacted.
 */
export default function PackImportDialog({ pack, open, onOpenChange, defaultProjectId }: {
    pack: PackSummary;
    open: boolean;
    onOpenChange: (open: boolean) => void;
    defaultProjectId: number | null;
}) {
    const projects = useQuery({ queryKey: ['projects'], queryFn: () => getProjects(), enabled: open });
    const [projectId, setProjectId] = useState<number | null>(defaultProjectId);
    const pid = projectId ?? projects.data?.[0]?.id ?? null;
    const envs = useQuery({
        queryKey: ['environments', pid], queryFn: () => packsApi.environments(pid!), enabled: open && !!pid, retry: false,
    });
    const [envId, setEnvId] = useState<number | null>(null);
    // Defaults stay placeholders: sent empty, the backend applies them without
    // overwriting a value the environment already has.
    const [values, setValues] = useState<Record<string, string>>({});
    const [result, setResult] = useState<PackImportResult | null>(null);

    const run = useMutation({
        mutationFn: () => packsApi.importPack(pid!, pack.id, {
            variables: Object.fromEntries(Object.entries(values).filter(([, v]) => v !== '')),
            environment_id: envId,
        }),
        onSuccess: setResult,
    });

    const close = (o: boolean) => {
        if (!o) { setResult(null); run.reset(); }
        onOpenChange(o);
    };

    return (
        <Dialog open={open} onOpenChange={close}>
            <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-[640px]">
                <DialogHeader>
                    <DialogTitle>Import “{pack.title}” v{pack.version}</DialogTitle>
                    <DialogDescription>
                        Creates the suite “{pack.suite_name}” with {pack.case_count} cases. Nothing runs and no target is contacted until you run it.
                    </DialogDescription>
                </DialogHeader>

                {result ? (
                    <div className="flex flex-col gap-3 text-sm text-ink">
                        <p>Imported {result.case_count} cases into <strong>{result.suite_name}</strong>.</p>
                        <p className="text-ink-soft">
                            Variables went to the “{result.environment.name}” environment
                            {result.secrets.stored.length > 0 && <>; {result.secrets.stored.length} secret{result.secrets.stored.length === 1 ? '' : 's'} stored encrypted</>}.
                        </p>
                        {result.missing.length > 0 && (
                            <div className="rounded-2xl border border-border bg-white px-4 py-3 shadow-[inset_4px_0_0_#F2C94C]">
                                Still to fill before running: {result.missing.map((m) => <code key={m} className="mr-1.5 font-mono text-xs">{m}</code>)}
                                <span className="block pt-1 text-xs text-ink-soft">Set them on the Environments page (secrets under Secrets).</span>
                            </div>
                        )}
                        <DialogFooter className="gap-2 sm:justify-start">
                            <Button asChild><Link to={`/suites/${result.suite_id}`}>Open the suite</Link></Button>
                            {result.missing.length > 0 && <Button asChild variant="outline"><Link to="/environments">Go to Environments</Link></Button>}
                            <Button variant="ghost" onClick={() => close(false)}>Close</Button>
                        </DialogFooter>
                    </div>
                ) : (
                    <form className="flex flex-col gap-4" onSubmit={(e) => { e.preventDefault(); if (pid) run.mutate(); }}>
                        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                            <div className="flex flex-col gap-1.5">
                                <label htmlFor="pack-project" className="text-sm font-medium text-ink">Project</label>
                                <select id="pack-project" className={selectClass} value={pid ?? ''}
                                    onChange={(e) => { setProjectId(parseInt(e.target.value)); setEnvId(null); }}>
                                    {(projects.data || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                                </select>
                            </div>
                            <div className="flex flex-col gap-1.5">
                                <label htmlFor="pack-env" className="text-sm font-medium text-ink">Environment for the variables</label>
                                <select id="pack-env" className={selectClass} value={envId ?? ''}
                                    onChange={(e) => setEnvId(e.target.value ? parseInt(e.target.value) : null)}>
                                    <option value="">Project default{envs.data && envs.data.length === 0 ? ' (creates “default”)' : ''}</option>
                                    {(envs.data || []).map((en) => <option key={en.id} value={en.id}>{en.name}{en.is_default ? ' (default)' : ''}</option>)}
                                </select>
                            </div>
                        </div>

                        <fieldset className="flex flex-col gap-3">
                            <legend className="mb-1 text-sm font-semibold text-ink">Variables</legend>
                            {pack.variables.map((v) => (
                                <div key={v.name} className="flex flex-col gap-1">
                                    <label htmlFor={`pv-${v.name}`} className="flex flex-wrap items-center gap-2 text-sm font-medium text-ink">
                                        <code className="font-mono text-xs">{v.name}</code>
                                        {v.required && <Pill tone="peach">Required</Pill>}
                                        {v.secret && <Pill tone="lavender">Secret</Pill>}
                                    </label>
                                    <Input id={`pv-${v.name}`} type={v.secret ? 'password' : 'text'} autoComplete={v.secret ? 'new-password' : 'off'}
                                        className="h-10 rounded-full" value={values[v.name] ?? ''}
                                        placeholder={v.secret ? 'Stored as an encrypted project secret' : v.default ? `Default: ${v.default}` : ''}
                                        onChange={(e) => setValues((p) => ({ ...p, [v.name]: e.target.value }))} />
                                    <span className="text-xs leading-relaxed text-ink-soft">{v.description}</span>
                                </div>
                            ))}
                        </fieldset>

                        {run.isError && <p className="text-sm text-destructive">{errorText(run.error, 'The pack could not be imported.')}</p>}
                        <DialogFooter className="gap-2 sm:justify-start">
                            <Button type="submit" disabled={!pid || run.isPending}>
                                {run.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Import pack
                            </Button>
                            <Button type="button" variant="ghost" onClick={() => close(false)}>Cancel</Button>
                        </DialogFooter>
                    </form>
                )}
            </DialogContent>
        </Dialog>
    );
}

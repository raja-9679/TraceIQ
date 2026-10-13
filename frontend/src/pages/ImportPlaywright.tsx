import { useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { FileCode2, Upload, Info } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { api, getTestSuites } from '@/lib/api';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { EmptyState, PageHeader } from '@/components/redesign/primitives';

const errDetail = (e: any): string => e?.response?.data?.detail || e?.message || 'Unknown error';

const SAMPLE = `import { test, expect } from '@playwright/test';

test('homepage has expected title', async ({ page }) => {
  await page.goto('https://example.com');
  await expect(page).toHaveTitle(/Example/);
});`;

export default function ImportPlaywright() {
    const navigate = useNavigate();
    // The sidebar owns project selection.
    const projectId = useActiveProjectId();

    const [pickedSuiteId, setSuiteId] = useState<string>('');
    const [name, setName] = useState('');
    const [script, setScript] = useState('');

    const { data: suites } = useQuery({
        queryKey: ['suites', projectId],
        queryFn: () => getTestSuites(projectId!),
        enabled: !!projectId,
    });
    // A suite picked under another project no longer applies once the sidebar
    // switches project (the old picker reset it explicitly); derived here.
    const suiteId = (suites || []).some((s: { id: number }) => s.id.toString() === pickedSuiteId) ? pickedSuiteId : '';

    const importMut = useMutation({
        mutationFn: async () => {
            const r = await api.post('/cases/import-playwright', {
                suite_id: parseInt(suiteId), name, script,
            });
            return r.data;
        },
        onSuccess: (c) => {
            toast.success(`Imported "${c.name}" as a raw Playwright case`);
            if (suiteId) navigate(`/suites/${suiteId}`);
        },
        onError: (e) => toast.error(`Import failed: ${errDetail(e)}`),
    });

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                crumbs={<><Link to="/suites" className="hover:text-ink">Tests</Link><span>/</span><span>Import</span></>}
                title="Import a Playwright spec"
                subtitle="Bring an existing .spec.ts in as a test case. It runs as written, through the Playwright runner."
            />

            <div className="flex max-w-[900px] items-start gap-3 rounded-2xl border border-white/90 bg-tiq-callout px-4 py-3 text-sm leading-relaxed text-ink">
                <Info className="mt-0.5 h-4 w-4 shrink-0 text-ink-soft" aria-hidden="true" />
                <p>
                    Imported specs can't use step-level AI heal or the trace timeline. Running one executes arbitrary code,
                    so it only happens on a sandboxed worker with <code className="font-mono text-[13px]">RAW_PLAYWRIGHT_ENABLED</code> set.
                    Importing here only stores the script.
                </p>
            </div>

            {!projectId ? (
                <EmptyState title="Pick a project">Choose a project in the sidebar to pick a target suite.</EmptyState>
            ) : (
                <div className="flex max-w-[900px] flex-col gap-5 rounded-3xl border border-border bg-white p-6 sm:p-7">
                    <div className="grid gap-4 sm:grid-cols-2">
                        <div className="flex flex-col gap-1.5">
                            <label htmlFor="import-suite" className="text-[13px] font-semibold text-ink">Target suite</label>
                            <Select value={suiteId} onValueChange={setSuiteId}>
                                <SelectTrigger id="import-suite" className="h-11 rounded-xl"><SelectValue placeholder="Select a suite" /></SelectTrigger>
                                <SelectContent>
                                    {(suites || []).map((s: { id: number; name: string }) => <SelectItem key={s.id} value={s.id.toString()}>{s.name}</SelectItem>)}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="flex flex-col gap-1.5">
                            <label htmlFor="import-name" className="text-[13px] font-semibold text-ink">Case name</label>
                            <Input id="import-name" className="h-11 rounded-xl" placeholder="checkout-flow.spec" value={name} onChange={(e) => setName(e.target.value)} />
                        </div>
                    </div>
                    <div className="flex flex-col gap-1.5">
                        <div className="flex items-center justify-between gap-2">
                            <label htmlFor="import-script" className="text-[13px] font-semibold text-ink">Playwright spec</label>
                            <Button type="button" variant="ghost" size="sm" onClick={() => setScript(SAMPLE)}>Insert sample</Button>
                        </div>
                        <textarea
                            id="import-script"
                            className="h-72 w-full rounded-2xl border border-border bg-background p-4 font-mono text-xs text-ink focus:outline-none focus:ring-2 focus:ring-ring"
                            placeholder="Paste your .spec.ts here…" value={script} onChange={(e) => setScript(e.target.value)} />
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                        <FileCode2 className="h-4 w-4 text-ink-soft" aria-hidden="true" />
                        <span className="text-xs text-ink-soft">{script.length} characters</span>
                        <Button size="lg" className="ml-auto" disabled={importMut.isPending || !suiteId || !name.trim() || !script.trim()}
                            onClick={() => importMut.mutate()}>
                            <Upload className="h-4 w-4" /> Import case
                        </Button>
                    </div>
                </div>
            )}
        </div>
    );
}

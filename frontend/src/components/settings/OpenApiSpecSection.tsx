import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FileJson, Loader2, Trash2, Upload } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
    deleteOpenApiSpec, getOpenApiSpec, parseSpecFile, putOpenApiSpec,
} from '@/api/openapiSpec';
import { OpenApiSpecHistory } from './OpenApiSpecHistory';

/**
 * The project's OpenAPI document. API steps reference it with
 * `expect_schema: {openapi: {operationId}}` so a response contract lives in one
 * place instead of being pasted into every step.
 */

interface ApiError extends Error {
    response?: { data?: { detail?: string } };
}
const detailOf = (err: unknown) =>
    (err as ApiError).response?.data?.detail || (err as Error).message || 'Unknown error';

const SHOWN_OPS = 12;

export function OpenApiSpecSection({ projectId }: { projectId: number }) {
    const queryClient = useQueryClient();
    const fileRef = useRef<HTMLInputElement>(null);
    const [url, setUrl] = useState('');
    const key = ['openapi-spec', projectId];

    const { data, isLoading } = useQuery({ queryKey: key, queryFn: () => getOpenApiSpec(projectId) });

    const save = useMutation({
        mutationFn: (body: { spec?: Record<string, unknown>; url?: string }) => putOpenApiSpec(projectId, body),
        onSuccess: (view) => {
            queryClient.setQueryData(key, view);
            queryClient.invalidateQueries({ queryKey: ['openapi-spec-versions', projectId] });
            setUrl('');
            toast.success(`Spec saved: ${view.operations.length} operations`);
        },
        onError: (err) => toast.error(`Could not save the spec: ${detailOf(err)}`),
    });

    const remove = useMutation({
        mutationFn: () => deleteOpenApiSpec(projectId),
        onSuccess: () => {
            queryClient.setQueryData(key, { exists: false, operations: [] });
            toast.success('Spec removed');
        },
        onError: (err) => toast.error(`Could not remove the spec: ${detailOf(err)}`),
    });

    const onFile = async (file: File | undefined) => {
        if (!file) return;
        try {
            save.mutate({ spec: parseSpecFile(await file.text()) });
        } catch (err) {
            toast.error((err as Error).message);
        } finally {
            if (fileRef.current) fileRef.current.value = '';
        }
    };

    const busy = save.isPending || remove.isPending;

    return (
        <section className="space-y-4 rounded-2xl border border-border bg-white p-5" aria-labelledby="openapi-spec-title">
            <div className="space-y-1">
                <h3 id="openapi-spec-title" className="text-base font-semibold">OpenAPI spec</h3>
                <p className="text-sm text-muted-foreground">
                    API steps can check responses against this document with{' '}
                    <code>expect_schema: {'{'}openapi: {'{'}operationId{'}'}{'}'}</code>.
                    Importing tests from OpenAPI also stores it here.
                </p>
            </div>

            {isLoading ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-label="Loading" />
            ) : data?.exists ? (
                <div className="space-y-3">
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                        <span className="inline-flex items-center gap-1.5 font-medium">
                            <FileJson className="h-4 w-4" /> {data.title || 'Untitled spec'}
                            {data.version ? ` · v${data.version}` : ''}
                        </span>
                        <span className="text-muted-foreground">{data.operations.length} operations</span>
                        {data.source_url && <span className="text-muted-foreground break-all">from {data.source_url}</span>}
                    </div>
                    {data.operations.length > 0 && (
                        <ul className="grid gap-1 font-mono text-xs sm:grid-cols-2">
                            {data.operations.slice(0, SHOWN_OPS).map((op) => (
                                <li key={`${op.method} ${op.path}`} className="truncate" title={`${op.method} ${op.path}`}>
                                    <span className="font-semibold">{op.method}</span> {op.path}
                                    {op.operationId ? <span className="text-muted-foreground"> · {op.operationId}</span> : null}
                                </li>
                            ))}
                            {data.operations.length > SHOWN_OPS && (
                                <li className="text-muted-foreground">+{data.operations.length - SHOWN_OPS} more</li>
                            )}
                        </ul>
                    )}
                </div>
            ) : (
                <p className="text-sm text-muted-foreground">No spec stored for this project.</p>
            )}

            <div className="flex flex-wrap items-end gap-3">
                <div className="space-y-1.5">
                    <Label htmlFor={`openapi-file-${projectId}`}>Upload a JSON file</Label>
                    <input
                        ref={fileRef}
                        id={`openapi-file-${projectId}`}
                        type="file"
                        accept="application/json,.json"
                        className="sr-only"
                        onChange={(e) => onFile(e.target.files?.[0])}
                    />
                    <Button type="button" variant="outline" disabled={busy} onClick={() => fileRef.current?.click()}>
                        <Upload className="mr-1 h-4 w-4" /> Choose file
                    </Button>
                </div>
                <form
                    className="flex min-w-0 flex-1 flex-wrap items-end gap-2"
                    onSubmit={(e) => { e.preventDefault(); if (url.trim()) save.mutate({ url: url.trim() }); }}
                >
                    <div className="min-w-[220px] flex-1 space-y-1.5">
                        <Label htmlFor={`openapi-url-${projectId}`}>Or fetch from a URL</Label>
                        <Input
                            id={`openapi-url-${projectId}`}
                            placeholder="https://api.example.com/openapi.json"
                            value={url}
                            onChange={(e) => setUrl(e.target.value)}
                        />
                    </div>
                    <Button type="submit" disabled={busy || !url.trim()}>
                        {save.isPending ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null} Fetch and save
                    </Button>
                </form>
                {data?.exists && (
                    <Button type="button" variant="outline" disabled={busy} onClick={() => remove.mutate()}>
                        <Trash2 className="mr-1 h-4 w-4" /> Remove spec
                    </Button>
                )}
            </div>

            <OpenApiSpecHistory projectId={projectId} />
        </section>
    );
}

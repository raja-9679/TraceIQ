import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import {
    Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
    Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Loader2, Pencil, Plus, Sparkles, Star, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import {
    LLMProviderCreate, LLMProviderRead, llmProvidersApi, PROVIDER_TYPES,
} from '@/api/llmProviders';

const EMPTY_FORM: LLMProviderCreate = {
    name: '', provider_type: 'anthropic', model: '', base_url: '', api_key: '',
    is_active: true, is_default: false,
};

// Which fields matter per provider type, to keep the form honest.
const NEEDS_KEY = ['anthropic', 'openai', 'gemini', 'openai-compatible'];
const NEEDS_BASE_URL = ['ollama', 'openai-compatible'];

export default function LLMProvidersCard() {
    const queryClient = useQueryClient();
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editing, setEditing] = useState<LLMProviderRead | null>(null);
    const [form, setForm] = useState<LLMProviderCreate>(EMPTY_FORM);

    const { data: providers, isLoading } = useQuery({
        queryKey: ['llm-providers'],
        queryFn: llmProvidersApi.list,
        retry: false,
    });

    const invalidate = () => {
        queryClient.invalidateQueries({ queryKey: ['llm-providers'] });
        queryClient.invalidateQueries({ queryKey: ['llm-providers-active'] });
    };

    const saveMutation = useMutation({
        mutationFn: async () => {
            if (editing) {
                // Empty api_key string = leave the stored key unchanged.
                return llmProvidersApi.update(editing.id, form);
            }
            return llmProvidersApi.create(form);
        },
        onSuccess: () => {
            invalidate();
            setDialogOpen(false);
            toast.success(editing ? 'Provider updated' : 'Provider added');
        },
        onError: (err: any) =>
            toast.error(err.response?.data?.detail || 'Failed to save provider'),
    });

    const deleteMutation = useMutation({
        mutationFn: (id: number) => llmProvidersApi.remove(id),
        onSuccess: () => { invalidate(); toast.success('Provider deleted'); },
        onError: (err: any) =>
            toast.error(err.response?.data?.detail || 'Failed to delete provider'),
    });

    const toggleMutation = useMutation({
        mutationFn: ({ id, patch }: { id: number; patch: { is_active?: boolean; is_default?: boolean } }) =>
            llmProvidersApi.update(id, patch),
        onSuccess: invalidate,
        onError: (err: any) =>
            toast.error(err.response?.data?.detail || 'Failed to update provider'),
    });

    const testMutation = useMutation({
        mutationFn: (id: number) => llmProvidersApi.test(id),
        onSuccess: (data) => toast.success(`${data.provider} ok (${data.model}): "${data.reply}"`),
        onError: (err: any) => toast.error(err.response?.data?.detail || 'Provider test failed'),
    });

    const openCreate = () => { setEditing(null); setForm(EMPTY_FORM); setDialogOpen(true); };
    const openEdit = (p: LLMProviderRead) => {
        setEditing(p);
        setForm({
            name: p.name, provider_type: p.provider_type, model: p.model,
            base_url: p.base_url || '', api_key: '',
            is_active: p.is_active, is_default: p.is_default,
        });
        setDialogOpen(true);
    };

    const canSave = form.name.trim() && form.model.trim()
        && (!NEEDS_BASE_URL.includes(form.provider_type) || form.provider_type === 'ollama' || (form.base_url || '').trim());

    return (
        <Card>
            <CardHeader>
                <div className="flex items-center justify-between">
                    <div>
                        <CardTitle className="text-lg">AI providers</CardTitle>
                        <CardDescription>
                            Save several providers and activate the ones users may pick for failure
                            analysis. The one marked default serves automatic analysis at run finish.
                            With no providers saved, the legacy single-provider settings below apply.
                        </CardDescription>
                    </div>
                    <Button size="sm" onClick={openCreate}>
                        <Plus className="h-4 w-4 mr-1" /> Add provider
                    </Button>
                </div>
            </CardHeader>
            <CardContent>
                {isLoading ? (
                    <div className="flex items-center gap-2 text-gray-500 text-sm">
                        <Loader2 className="h-4 w-4 animate-spin" /> Loading providers…
                    </div>
                ) : !providers || providers.length === 0 ? (
                    <p className="text-sm text-gray-500">
                        No providers saved yet — the legacy single-provider settings below are in effect.
                    </p>
                ) : (
                    <div className="space-y-2">
                        {providers.map((p) => (
                            <div key={p.id}
                                 className="flex items-center justify-between rounded-md border px-3 py-2">
                                <div className="flex items-center gap-3 min-w-0">
                                    <Checkbox
                                        checked={p.is_active}
                                        title={p.is_active ? 'Active — click to deactivate' : 'Inactive — click to activate'}
                                        onCheckedChange={(v) =>
                                            toggleMutation.mutate({ id: p.id, patch: { is_active: v === true } })}
                                    />
                                    <div className="min-w-0">
                                        <div className="flex items-center gap-2">
                                            <span className="text-sm font-medium truncate">{p.name}</span>
                                            {p.is_default && (
                                                <Badge variant="secondary" className="text-xs">default</Badge>
                                            )}
                                            {!p.is_active && (
                                                <Badge variant="outline" className="text-xs">inactive</Badge>
                                            )}
                                        </div>
                                        <p className="text-xs text-gray-500 truncate">
                                            {p.provider_type} · {p.model}
                                            {p.base_url ? ` · ${p.base_url}` : ''}
                                            {NEEDS_KEY.includes(p.provider_type) && !p.api_key_set ? ' · no API key' : ''}
                                        </p>
                                    </div>
                                </div>
                                <div className="flex items-center gap-1 shrink-0">
                                    {!p.is_default && (
                                        <Button variant="ghost" size="sm" title="Make default"
                                                onClick={() => toggleMutation.mutate({ id: p.id, patch: { is_default: true } })}>
                                            <Star className="h-4 w-4" />
                                        </Button>
                                    )}
                                    <Button variant="ghost" size="sm" title="Test this provider"
                                            disabled={testMutation.isPending}
                                            onClick={() => testMutation.mutate(p.id)}>
                                        {testMutation.isPending && testMutation.variables === p.id
                                            ? <Loader2 className="h-4 w-4 animate-spin" />
                                            : <Sparkles className="h-4 w-4" />}
                                    </Button>
                                    <Button variant="ghost" size="sm" title="Edit" onClick={() => openEdit(p)}>
                                        <Pencil className="h-4 w-4" />
                                    </Button>
                                    <Button variant="ghost" size="sm" title="Delete"
                                            onClick={() => {
                                                if (window.confirm(`Delete provider "${p.name}"?`)) {
                                                    deleteMutation.mutate(p.id);
                                                }
                                            }}>
                                        <Trash2 className="h-4 w-4 text-red-500" />
                                    </Button>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </CardContent>

            <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader>
                        <DialogTitle>{editing ? `Edit ${editing.name}` : 'Add AI provider'}</DialogTitle>
                    </DialogHeader>
                    <div className="space-y-3">
                        <div>
                            <Label>Name</Label>
                            <Input
                                placeholder='e.g. "Claude" or "Ollama (office)"'
                                value={form.name}
                                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                            />
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <Label>Provider type</Label>
                                <Select
                                    value={form.provider_type}
                                    onValueChange={(v) => setForm((f) => ({ ...f, provider_type: v }))}
                                >
                                    <SelectTrigger><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        {PROVIDER_TYPES.map((t) => (
                                            <SelectItem key={t} value={t}>{t}</SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                            <div>
                                <Label>Model</Label>
                                <Input
                                    placeholder="e.g. claude-opus-4-8, qwen2.5-coder:14b"
                                    value={form.model}
                                    onChange={(e) => setForm((f) => ({ ...f, model: e.target.value }))}
                                />
                            </div>
                        </div>
                        {(NEEDS_BASE_URL.includes(form.provider_type) || form.base_url) && (
                            <div>
                                <Label>Base URL {form.provider_type === 'ollama' ? '(default http://localhost:11434/v1)' : ''}</Label>
                                <Input
                                    placeholder="http://host:port/v1"
                                    value={form.base_url || ''}
                                    onChange={(e) => setForm((f) => ({ ...f, base_url: e.target.value }))}
                                />
                            </div>
                        )}
                        {NEEDS_KEY.includes(form.provider_type) && (
                            <div>
                                <Label>API key</Label>
                                <Input
                                    type="password"
                                    autoComplete="new-password"
                                    placeholder={editing?.api_key_set
                                        ? '•••••••• (set — type to replace)'
                                        : 'API key'}
                                    value={form.api_key || ''}
                                    onChange={(e) => setForm((f) => ({ ...f, api_key: e.target.value }))}
                                />
                            </div>
                        )}
                        <div className="flex items-center gap-6 pt-1">
                            <label className="flex items-center gap-2 text-sm">
                                <Checkbox
                                    checked={form.is_active !== false}
                                    onCheckedChange={(v) => setForm((f) => ({ ...f, is_active: v === true }))}
                                /> Active
                            </label>
                            <label className="flex items-center gap-2 text-sm">
                                <Checkbox
                                    checked={form.is_default === true}
                                    onCheckedChange={(v) => setForm((f) => ({ ...f, is_default: v === true }))}
                                /> Default provider
                            </label>
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setDialogOpen(false)}>Cancel</Button>
                        <Button disabled={!canSave || saveMutation.isPending} onClick={() => saveMutation.mutate()}>
                            {saveMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                            {editing ? 'Save changes' : 'Add provider'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </Card>
    );
}

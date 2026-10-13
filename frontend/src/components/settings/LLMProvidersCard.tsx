import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
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
import { Pill } from '@/components/redesign/primitives';
import { SettingsCard, checkboxClass, inputClass } from '@/components/redesign-local/groupD/settingsForm';

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
        <SettingsCard
            labelledBy="llm-providers-title"
            title="AI providers"
            description="Save several providers and turn on the ones people may pick for failure analysis. The default one runs automatic analysis when a run finishes. With no providers saved, the legacy single-provider settings below apply."
            actions={
                <Button onClick={openCreate}>
                    <Plus className="h-4 w-4" /> Add provider
                </Button>
            }
        >
            {isLoading ? (
                <p className="flex items-center gap-2 text-sm text-ink-soft">
                    <Loader2 className="h-4 w-4 animate-spin" /> Loading providers…
                </p>
            ) : !providers || providers.length === 0 ? (
                <p className="text-sm text-ink-soft">
                    No providers saved yet — the legacy single-provider settings below are in effect.
                </p>
            ) : (
                <ul className="overflow-hidden rounded-2xl border border-border">
                    {providers.map((p, i) => (
                        <li key={p.id}
                            className={`flex flex-wrap items-center justify-between gap-3 px-4 py-3 ${i > 0 ? 'border-t border-border' : ''}`}>
                            <div className="flex min-w-0 flex-1 items-center gap-3">
                                <input
                                    type="checkbox"
                                    className={checkboxClass}
                                    checked={p.is_active}
                                    title={p.is_active ? 'Active — click to deactivate' : 'Inactive — click to activate'}
                                    aria-label={`${p.name} active`}
                                    onChange={(e) =>
                                        toggleMutation.mutate({ id: p.id, patch: { is_active: e.target.checked } })}
                                />
                                <div className="min-w-0">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <span className="truncate text-sm font-semibold text-ink">{p.name}</span>
                                        {p.is_default && <Pill tone="lavender">Default</Pill>}
                                        {!p.is_active && <Pill tone="neutral">Inactive</Pill>}
                                    </div>
                                    <p className="truncate text-xs text-ink-soft">
                                        {p.provider_type} · {p.model}
                                        {p.base_url ? ` · ${p.base_url}` : ''}
                                        {NEEDS_KEY.includes(p.provider_type) && !p.api_key_set ? ' · no API key' : ''}
                                    </p>
                                </div>
                            </div>
                            <div className="flex shrink-0 items-center gap-1">
                                {!p.is_default && (
                                    <Button variant="ghost" size="icon" title="Make default"
                                            aria-label={`Make ${p.name} the default provider`}
                                            onClick={() => toggleMutation.mutate({ id: p.id, patch: { is_default: true } })}>
                                        <Star className="h-4 w-4" />
                                    </Button>
                                )}
                                <Button variant="ghost" size="icon" title="Test this provider"
                                        aria-label={`Test ${p.name}`}
                                        disabled={testMutation.isPending}
                                        onClick={() => testMutation.mutate(p.id)}>
                                    {testMutation.isPending && testMutation.variables === p.id
                                        ? <Loader2 className="h-4 w-4 animate-spin" />
                                        : <Sparkles className="h-4 w-4" />}
                                </Button>
                                <Button variant="ghost" size="icon" title="Edit"
                                        aria-label={`Edit ${p.name}`}
                                        onClick={() => openEdit(p)}>
                                    <Pencil className="h-4 w-4" />
                                </Button>
                                <Button variant="ghost" size="icon" title="Delete"
                                        aria-label={`Delete ${p.name}`}
                                        className="text-destructive hover:text-destructive"
                                        onClick={() => {
                                            if (window.confirm(`Delete provider "${p.name}"?`)) {
                                                deleteMutation.mutate(p.id);
                                            }
                                        }}>
                                    <Trash2 className="h-4 w-4" />
                                </Button>
                            </div>
                        </li>
                    ))}
                </ul>
            )}

            <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader>
                        <DialogTitle>{editing ? `Edit ${editing.name}` : 'Add AI provider'}</DialogTitle>
                    </DialogHeader>
                    <div className="flex flex-col gap-3">
                        <div className="flex flex-col gap-1.5">
                            <Label htmlFor="llm-name">Name</Label>
                            <Input
                                id="llm-name"
                                className={inputClass}
                                placeholder='e.g. "Claude" or "Ollama (office)"'
                                value={form.name}
                                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                            />
                        </div>
                        <div className="grid gap-3 sm:grid-cols-2">
                            <div className="flex flex-col gap-1.5">
                                <Label htmlFor="llm-type">Provider type</Label>
                                <Select
                                    value={form.provider_type}
                                    onValueChange={(v) => setForm((f) => ({ ...f, provider_type: v }))}
                                >
                                    <SelectTrigger id="llm-type" className="h-10 rounded-full"><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        {PROVIDER_TYPES.map((t) => (
                                            <SelectItem key={t} value={t}>{t}</SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="flex flex-col gap-1.5">
                                <Label htmlFor="llm-model">Model</Label>
                                <Input
                                    id="llm-model"
                                    className={inputClass}
                                    placeholder="e.g. claude-opus-4-8, qwen2.5-coder:14b"
                                    value={form.model}
                                    onChange={(e) => setForm((f) => ({ ...f, model: e.target.value }))}
                                />
                            </div>
                        </div>
                        {(NEEDS_BASE_URL.includes(form.provider_type) || form.base_url) && (
                            <div className="flex flex-col gap-1.5">
                                <Label htmlFor="llm-base-url">Base URL {form.provider_type === 'ollama' ? '(default http://localhost:11434/v1)' : ''}</Label>
                                <Input
                                    id="llm-base-url"
                                    className={inputClass}
                                    placeholder="http://host:port/v1"
                                    value={form.base_url || ''}
                                    onChange={(e) => setForm((f) => ({ ...f, base_url: e.target.value }))}
                                />
                            </div>
                        )}
                        {NEEDS_KEY.includes(form.provider_type) && (
                            <div className="flex flex-col gap-1.5">
                                <Label htmlFor="llm-api-key">API key</Label>
                                <Input
                                    id="llm-api-key"
                                    type="password"
                                    autoComplete="new-password"
                                    className={inputClass}
                                    placeholder={editing?.api_key_set
                                        ? '•••••••• (set — type to replace)'
                                        : 'API key'}
                                    value={form.api_key || ''}
                                    onChange={(e) => setForm((f) => ({ ...f, api_key: e.target.value }))}
                                />
                            </div>
                        )}
                        <div className="flex flex-wrap items-center gap-6 pt-1">
                            <label className="flex min-h-11 cursor-pointer items-center gap-2 text-sm text-ink">
                                <input
                                    type="checkbox"
                                    className={checkboxClass}
                                    checked={form.is_active !== false}
                                    onChange={(e) => setForm((f) => ({ ...f, is_active: e.target.checked }))}
                                /> Active
                            </label>
                            <label className="flex min-h-11 cursor-pointer items-center gap-2 text-sm text-ink">
                                <input
                                    type="checkbox"
                                    className={checkboxClass}
                                    checked={form.is_default === true}
                                    onChange={(e) => setForm((f) => ({ ...f, is_default: e.target.checked }))}
                                /> Default provider
                            </label>
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setDialogOpen(false)}>Cancel</Button>
                        <Button disabled={!canSave || saveMutation.isPending} onClick={() => saveMutation.mutate()}>
                            {saveMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                            {editing ? 'Save changes' : 'Add provider'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </SettingsCard>
    );
}

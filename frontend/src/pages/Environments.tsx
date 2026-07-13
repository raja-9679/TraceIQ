import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Globe, Plus, Trash2, PenLine, Star, RefreshCw, AlertCircle, X, Check,
  KeyRound, Info, Layers, EyeOff,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { toast } from 'sonner';
import { api, getProjects } from '@/lib/api';

// ---------------------------------------------------------------------------
// Types & API
// ---------------------------------------------------------------------------

interface ProjectEnvironment {
  id: number;
  project_id: number;
  name: string;
  base_url: string | null;
  variables: Record<string, string>;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

interface EnvironmentPayload {
  name: string;
  base_url?: string | null;
  variables: Record<string, string>;
  is_default: boolean;
}

interface SecretKeyInfo {
  id: number;
  key: string;
  created_at: string;
  updated_at: string;
}

interface ApiError extends Error {
  response?: { data?: { detail?: string } };
}

const environmentsApi = {
  list: async (projectId: number): Promise<ProjectEnvironment[]> => {
    const res = await api.get(`/projects/${projectId}/environments`);
    return res.data;
  },
  create: async (projectId: number, body: EnvironmentPayload): Promise<ProjectEnvironment> => {
    const res = await api.post(`/projects/${projectId}/environments`, body);
    return res.data;
  },
  update: async (id: number, body: Partial<EnvironmentPayload>): Promise<ProjectEnvironment> => {
    const res = await api.put(`/environments/${id}`, body);
    return res.data;
  },
  remove: async (id: number): Promise<void> => {
    await api.delete(`/environments/${id}`);
  },
};

const secretsApi = {
  list: async (projectId: number): Promise<SecretKeyInfo[]> => {
    const res = await api.get(`/projects/${projectId}/secrets`);
    return res.data;
  },
  upsert: async (projectId: number, key: string, value: string): Promise<void> => {
    await api.put(`/projects/${projectId}/secrets`, { key, value });
  },
  remove: async (projectId: number, key: string): Promise<void> => {
    await api.delete(`/projects/${projectId}/secrets/${encodeURIComponent(key)}`);
  },
};

const errorDetail = (err: unknown): string => {
  const e = err as ApiError;
  return e.response?.data?.detail || e.message || 'Unknown error';
};

const formatDate = (dateString?: string | null) => {
  if (!dateString) return '—';
  return new Date(dateString).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
};

// ---------------------------------------------------------------------------
// Variable key-value editor rows
// ---------------------------------------------------------------------------

interface VariableRow {
  key: string;
  value: string;
}

const toRows = (variables: Record<string, string>): VariableRow[] =>
  Object.entries(variables).map(([key, value]) => ({ key, value: String(value) }));

const toDict = (rows: VariableRow[]): Record<string, string> => {
  const dict: Record<string, string> = {};
  rows.forEach((r) => {
    const key = r.key.trim();
    if (key) dict[key] = r.value;
  });
  return dict;
};

function VariablesEditor({ rows, onChange }: { rows: VariableRow[]; onChange: (rows: VariableRow[]) => void }) {
  const update = (idx: number, patch: Partial<VariableRow>) =>
    onChange(rows.map((r, i) => (i === idx ? { ...r, ...patch } : r)));

  return (
    <div className="space-y-2">
      <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Variables</p>
      {rows.length === 0 && (
        <p className="text-xs text-slate-400 italic">No variables yet.</p>
      )}
      {rows.map((row, idx) => (
        <div key={idx} className="flex items-center gap-2">
          <Input
            placeholder="KEY"
            value={row.key}
            onChange={(e) => update(idx, { key: e.target.value })}
            className="h-9 rounded-lg font-mono text-xs bg-white border-slate-200 w-[40%]"
          />
          <Input
            placeholder="value"
            value={row.value}
            onChange={(e) => update(idx, { value: e.target.value })}
            className="h-9 rounded-lg font-mono text-xs bg-white border-slate-200 flex-1"
          />
          <Button
            variant="ghost"
            size="icon"
            className="h-9 w-9 shrink-0 text-slate-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg"
            onClick={() => onChange(rows.filter((_, i) => i !== idx))}
            title="Remove variable"
          >
            <X className="w-4 h-4" />
          </Button>
        </div>
      ))}
      <Button
        variant="outline"
        size="sm"
        className="h-8 rounded-lg text-xs border-slate-200 text-slate-600"
        onClick={() => onChange([...rows, { key: '', value: '' }])}
      >
        <Plus className="w-3.5 h-3.5 mr-1" /> Add variable
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Environment form (create + edit)
// ---------------------------------------------------------------------------

interface EnvFormState {
  name: string;
  baseUrl: string;
  isDefault: boolean;
  rows: VariableRow[];
}

function EnvironmentForm({
  initial, submitting, submitLabel, onSubmit, onCancel,
}: {
  initial: EnvFormState;
  submitting: boolean;
  submitLabel: string;
  onSubmit: (payload: EnvironmentPayload) => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<EnvFormState>(initial);

  const handleSubmit = () => {
    if (!form.name.trim()) {
      toast.error('Environment name is required');
      return;
    }
    onSubmit({
      name: form.name.trim(),
      base_url: form.baseUrl.trim() || null,
      variables: toDict(form.rows),
      is_default: form.isDefault,
    });
  };

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Name</p>
          <Input
            placeholder="e.g. staging"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            className="h-9 rounded-lg bg-white border-slate-200"
          />
        </div>
        <div>
          <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Base URL</p>
          <Input
            placeholder="https://staging.example.com"
            value={form.baseUrl}
            onChange={(e) => setForm({ ...form, baseUrl: e.target.value })}
            className="h-9 rounded-lg font-mono text-xs bg-white border-slate-200"
          />
        </div>
      </div>

      <VariablesEditor rows={form.rows} onChange={(rows) => setForm({ ...form, rows })} />

      <label className="flex items-center gap-2 text-sm text-slate-600 cursor-pointer select-none w-fit">
        <input
          type="checkbox"
          checked={form.isDefault}
          onChange={(e) => setForm({ ...form, isDefault: e.target.checked })}
          className="h-4 w-4 rounded border-slate-300 accent-indigo-600"
        />
        Use as the default environment for this project
      </label>

      <div className="flex gap-2 pt-1">
        <Button
          size="sm"
          disabled={submitting}
          onClick={handleSubmit}
          className="bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl h-9 px-4"
        >
          <Check className="w-4 h-4 mr-1.5" /> {submitLabel}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={submitting}
          onClick={onCancel}
          className="rounded-xl h-9 px-4 border-slate-200 text-slate-500"
        >
          Cancel
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Environment card
// ---------------------------------------------------------------------------

function EnvironmentCard({ env, projectId }: { env: ProjectEnvironment; projectId: number }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['environments', projectId] });

  const updateMutation = useMutation({
    mutationFn: (body: Partial<EnvironmentPayload>) => environmentsApi.update(env.id, body),
    onSuccess: () => {
      invalidate();
      setEditing(false);
      toast.success(`Environment "${env.name}" updated`);
    },
    onError: (err: unknown) => toast.error(`Failed to update environment: ${errorDetail(err)}`),
  });

  const deleteMutation = useMutation({
    mutationFn: () => environmentsApi.remove(env.id),
    onSuccess: () => {
      invalidate();
      toast.success(`Environment "${env.name}" deleted`);
    },
    onError: (err: unknown) => toast.error(`Failed to delete environment: ${errorDetail(err)}`),
  });

  const variables = Object.entries(env.variables || {});

  return (
    <div className="bg-white border border-slate-200 rounded-2xl shadow-sm overflow-hidden">
      <div className="flex items-center gap-3 px-5 py-4 flex-wrap">
        <Globe className="w-4 h-4 text-indigo-500 shrink-0" />
        <span className="font-bold text-slate-800">{env.name}</span>
        {env.is_default && (
          <Badge variant="outline" className="rounded-lg bg-amber-50 text-amber-700 border-amber-200 text-[10px] font-bold uppercase">
            <Star className="w-3 h-3 mr-1" /> Default
          </Badge>
        )}
        {env.base_url ? (
          <code className="text-xs text-slate-500 bg-slate-50 border border-slate-100 px-2 py-0.5 rounded-md break-all">
            {env.base_url}
          </code>
        ) : (
          <span className="text-xs text-slate-300 italic">no base URL</span>
        )}
        <div className="ml-auto flex items-center gap-1.5 shrink-0">
          {!env.is_default && (
            <Button
              size="sm"
              variant="outline"
              disabled={updateMutation.isPending}
              onClick={() => updateMutation.mutate({ is_default: true })}
              className="h-8 rounded-lg text-xs border-slate-200 text-slate-600 hover:bg-amber-50 hover:text-amber-700 hover:border-amber-200"
            >
              <Star className="w-3.5 h-3.5 mr-1" /> Set default
            </Button>
          )}
          <Button
            size="sm"
            variant="outline"
            onClick={() => setEditing((e) => !e)}
            className="h-8 rounded-lg text-xs border-slate-200 text-slate-600"
          >
            <PenLine className="w-3.5 h-3.5 mr-1" /> {editing ? 'Close' : 'Edit'}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={deleteMutation.isPending}
            onClick={() => {
              if (window.confirm(`Delete environment "${env.name}"? This cannot be undone.`)) {
                deleteMutation.mutate();
              }
            }}
            className="h-8 rounded-lg text-xs text-rose-600 hover:text-rose-700 hover:bg-rose-50 border-rose-200"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </Button>
        </div>
      </div>

      {editing ? (
        <div className="border-t border-slate-100 px-5 py-4 bg-slate-50/30">
          <EnvironmentForm
            initial={{
              name: env.name,
              baseUrl: env.base_url || '',
              isDefault: env.is_default,
              rows: toRows(env.variables || {}),
            }}
            submitting={updateMutation.isPending}
            submitLabel="Save changes"
            onSubmit={(payload) => updateMutation.mutate(payload)}
            onCancel={() => setEditing(false)}
          />
        </div>
      ) : variables.length > 0 ? (
        <div className="border-t border-slate-100 px-5 py-3 bg-slate-50/30">
          <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-2">
            Variables ({variables.length})
          </p>
          <div className="flex flex-wrap gap-1.5">
            {variables.map(([key, value]) => (
              <code key={key} className="text-xs bg-slate-100 text-slate-600 px-2 py-0.5 rounded" title={`{{env.${key}}}`}>
                <span className="font-bold text-indigo-600">{key}</span> = {String(value)}
              </code>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Environments section
// ---------------------------------------------------------------------------

function EnvironmentsSection({ projectId }: { projectId: number }) {
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);

  const { data: environments, isLoading, error } = useQuery({
    queryKey: ['environments', projectId],
    queryFn: () => environmentsApi.list(projectId),
  });

  const createMutation = useMutation({
    mutationFn: (body: EnvironmentPayload) => environmentsApi.create(projectId, body),
    onSuccess: (env) => {
      queryClient.invalidateQueries({ queryKey: ['environments', projectId] });
      setCreating(false);
      toast.success(`Environment "${env.name}" created`);
    },
    onError: (err: unknown) => toast.error(`Failed to create environment: ${errorDetail(err)}`),
  });

  return (
    <section>
      <div className="flex items-center gap-3 mb-2">
        <h2 className="text-xl font-bold text-slate-900 flex items-center gap-2">
          <Globe className="w-5 h-5 text-indigo-500" /> Environments
        </h2>
        <Button
          size="sm"
          onClick={() => setCreating((c) => !c)}
          className="ml-auto bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl h-9 px-4"
        >
          <Plus className="w-4 h-4 mr-1.5" /> New environment
        </Button>
      </div>
      <div className="flex items-start gap-2 text-xs text-slate-500 bg-indigo-50/60 border border-indigo-100 rounded-xl px-3 py-2.5 mb-4">
        <Info className="w-4 h-4 text-indigo-400 shrink-0 mt-px" />
        <p>
          Relative <code className="font-mono bg-white/70 px-1 rounded">goto</code> URLs in test steps resolve
          against the environment's <span className="font-semibold">base URL</span>, and variables are referenced
          in steps as <code className="font-mono bg-white/70 px-1 rounded">{'{{env.KEY}}'}</code>. The default
          environment is used when a run doesn't specify one.
        </p>
      </div>

      {creating && (
        <div className="bg-white border border-indigo-200 rounded-2xl shadow-sm px-5 py-4 mb-4">
          <p className="text-sm font-bold text-slate-800 mb-3">New environment</p>
          <EnvironmentForm
            initial={{ name: '', baseUrl: '', isDefault: false, rows: [] }}
            submitting={createMutation.isPending}
            submitLabel="Create environment"
            onSubmit={(payload) => createMutation.mutate(payload)}
            onCancel={() => setCreating(false)}
          />
        </div>
      )}

      {isLoading ? (
        <div className="p-10 flex justify-center"><RefreshCw className="animate-spin text-muted-foreground w-6 h-6" /></div>
      ) : error ? (
        <div className="p-8 text-center bg-rose-50 border border-rose-200 rounded-2xl">
          <AlertCircle className="w-8 h-8 text-rose-500 mx-auto mb-3" />
          <p className="text-rose-700 font-semibold">Failed to load environments</p>
          <p className="text-rose-600/80 text-sm">{errorDetail(error)}</p>
        </div>
      ) : !environments || environments.length === 0 ? (
        <div className="p-10 text-center bg-white border border-slate-200 rounded-2xl">
          <Globe className="w-10 h-10 text-slate-300 mx-auto mb-4" />
          <h3 className="text-lg font-bold text-slate-800 mb-1">No environments yet</h3>
          <p className="text-slate-500 text-sm max-w-md mx-auto">
            Create environments like dev, staging and prod so the same tests can run against different deployments.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {environments.map((env) => (
            <EnvironmentCard key={env.id} env={env} projectId={projectId} />
          ))}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Secrets section
// ---------------------------------------------------------------------------

const SECRET_KEY_PATTERN = /^[A-Za-z0-9_]+$/;

function SecretsSection({ projectId }: { projectId: number }) {
  const queryClient = useQueryClient();
  const [key, setKey] = useState('');
  const [value, setValue] = useState('');

  const { data: secrets, isLoading, error } = useQuery({
    queryKey: ['secrets', projectId],
    queryFn: () => secretsApi.list(projectId),
  });

  const existingKeys = new Set((secrets || []).map((s) => s.key));

  const upsertMutation = useMutation({
    mutationFn: ({ key: k, value: v }: { key: string; value: string }) =>
      secretsApi.upsert(projectId, k, v),
    onSuccess: (_data, vars) => {
      queryClient.invalidateQueries({ queryKey: ['secrets', projectId] });
      setKey('');
      setValue('');
      toast.success(`Secret "${vars.key}" stored — its value is now write-only`);
    },
    onError: (err: unknown) => toast.error(`Failed to store secret: ${errorDetail(err)}`),
  });

  const deleteMutation = useMutation({
    mutationFn: (k: string) => secretsApi.remove(projectId, k),
    onSuccess: (_data, k) => {
      queryClient.invalidateQueries({ queryKey: ['secrets', projectId] });
      toast.success(`Secret "${k}" deleted`);
    },
    onError: (err: unknown) => toast.error(`Failed to delete secret: ${errorDetail(err)}`),
  });

  const handleSave = () => {
    const trimmedKey = key.trim();
    if (!trimmedKey || !value) {
      toast.error('Both key and value are required');
      return;
    }
    if (!SECRET_KEY_PATTERN.test(trimmedKey)) {
      toast.error('Secret keys must contain only letters, numbers and underscores');
      return;
    }
    upsertMutation.mutate({ key: trimmedKey, value });
  };

  return (
    <section>
      <h2 className="text-xl font-bold text-slate-900 flex items-center gap-2 mb-2">
        <KeyRound className="w-5 h-5 text-indigo-500" /> Secrets
      </h2>
      <div className="flex items-start gap-2 text-xs text-slate-500 bg-indigo-50/60 border border-indigo-100 rounded-xl px-3 py-2.5 mb-4">
        <EyeOff className="w-4 h-4 text-indigo-400 shrink-0 mt-px" />
        <p>
          Secrets are referenced in test steps as{' '}
          <code className="font-mono bg-white/70 px-1 rounded">{'{{secret.KEY}}'}</code>. Values are{' '}
          <span className="font-semibold">write-only</span>: they are encrypted at rest, never returned by the
          API, and only decrypted into job payloads at dispatch time. Saving an existing key overwrites its value.
        </p>
      </div>

      <div className="bg-white border border-slate-200 rounded-2xl shadow-sm px-5 py-4 mb-4">
        <p className="text-sm font-bold text-slate-800 mb-3">Add / update secret</p>
        <div className="flex flex-col sm:flex-row gap-2">
          <Input
            placeholder="KEY (e.g. API_TOKEN)"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            className="h-9 rounded-lg font-mono text-xs bg-white border-slate-200 sm:w-[260px]"
          />
          <Input
            type="password"
            placeholder="Secret value"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            autoComplete="new-password"
            className="h-9 rounded-lg font-mono text-xs bg-white border-slate-200 flex-1"
          />
          <Button
            size="sm"
            disabled={upsertMutation.isPending}
            onClick={handleSave}
            className="bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl h-9 px-4 shrink-0"
          >
            <Check className="w-4 h-4 mr-1.5" />
            {existingKeys.has(key.trim()) ? 'Update secret' : 'Save secret'}
          </Button>
        </div>
        {key.trim() && existingKeys.has(key.trim()) && (
          <p className="text-xs text-amber-600 mt-2">
            "{key.trim()}" already exists — saving will overwrite its current value.
          </p>
        )}
      </div>

      {isLoading ? (
        <div className="p-10 flex justify-center"><RefreshCw className="animate-spin text-muted-foreground w-6 h-6" /></div>
      ) : error ? (
        <div className="p-8 text-center bg-rose-50 border border-rose-200 rounded-2xl">
          <AlertCircle className="w-8 h-8 text-rose-500 mx-auto mb-3" />
          <p className="text-rose-700 font-semibold">Failed to load secrets</p>
          <p className="text-rose-600/80 text-sm">{errorDetail(error)}</p>
        </div>
      ) : !secrets || secrets.length === 0 ? (
        <div className="p-10 text-center bg-white border border-slate-200 rounded-2xl">
          <KeyRound className="w-10 h-10 text-slate-300 mx-auto mb-4" />
          <h3 className="text-lg font-bold text-slate-800 mb-1">No secrets stored</h3>
          <p className="text-slate-500 text-sm max-w-md mx-auto">
            Store API tokens and passwords here instead of hard-coding them in test steps.
          </p>
        </div>
      ) : (
        <div className="bg-white border border-slate-200 rounded-2xl overflow-hidden shadow-sm">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader className="bg-slate-50/50">
                <TableRow className="border-slate-100 hover:bg-transparent">
                  <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12">Key</TableHead>
                  <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12">Reference</TableHead>
                  <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12 w-[160px]">Updated</TableHead>
                  <TableHead className="text-right font-bold text-slate-500 uppercase tracking-widest text-xs h-12 w-[90px]">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {secrets.map((s) => (
                  <TableRow key={s.id} className="border-slate-100 hover:bg-slate-50/50 transition-colors">
                    <TableCell className="py-3">
                      <span className="font-mono text-sm font-bold text-slate-800">{s.key}</span>
                      <span className="text-xs text-slate-300 ml-2 tracking-widest">••••••••</span>
                    </TableCell>
                    <TableCell className="py-3">
                      <code className="text-xs bg-slate-100 text-slate-600 px-2 py-0.5 rounded">
                        {`{{secret.${s.key}}}`}
                      </code>
                    </TableCell>
                    <TableCell className="py-3 text-xs text-slate-500 whitespace-nowrap">
                      {formatDate(s.updated_at || s.created_at)}
                    </TableCell>
                    <TableCell className="py-3 text-right">
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={deleteMutation.isPending}
                        onClick={() => {
                          if (window.confirm(`Delete secret "${s.key}"? Steps referencing {{secret.${s.key}}} will fail.`)) {
                            deleteMutation.mutate(s.key);
                          }
                        }}
                        className="h-8 rounded-lg text-xs text-rose-600 hover:text-rose-700 hover:bg-rose-50 border-rose-200"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function Environments() {
  const [projectId, setProjectId] = useState<number | null>(() => {
    const saved = localStorage.getItem('activeProjectId');
    return saved ? parseInt(saved) : null;
  });

  useEffect(() => {
    const handleProjectChange = () => {
      const saved = localStorage.getItem('activeProjectId');
      setProjectId(saved ? parseInt(saved) : null);
    };
    window.addEventListener('projectChanged', handleProjectChange);
    return () => window.removeEventListener('projectChanged', handleProjectChange);
  }, []);

  const { data: projects } = useQuery({
    queryKey: ['projects'],
    queryFn: () => getProjects(),
  });

  const handleProjectSelect = (idStr: string) => {
    const id = parseInt(idStr);
    setProjectId(id);
    localStorage.setItem('activeProjectId', id.toString());
    window.dispatchEvent(new Event('projectChanged'));
  };

  return (
    <div className="max-w-[1100px] mx-auto pb-16">
      <div className="pt-2 pb-6 mb-6 border-b border-slate-200/60">
        <div className="flex items-center text-sm text-slate-400 gap-2 mb-3">
          <span className="font-bold text-slate-700 bg-white px-2.5 py-1 rounded-md border border-slate-200 shadow-sm">Project Configuration</span>
        </div>
        <div className="flex flex-col sm:flex-row sm:items-end gap-4">
          <div>
            <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">Environments &amp; Secrets</h1>
            <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
              Configure deployment targets and credentials per project, so the same test suite can run anywhere.
            </p>
          </div>
          <div className="sm:ml-auto shrink-0">
            <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Project</p>
            <Select value={projectId?.toString() ?? ''} onValueChange={handleProjectSelect}>
              <SelectTrigger className="w-[240px] h-10 rounded-xl bg-white border-slate-200">
                <div className="flex items-center gap-2 min-w-0">
                  <Layers className="w-4 h-4 text-indigo-500 shrink-0" />
                  <SelectValue placeholder="Select a project" />
                </div>
              </SelectTrigger>
              <SelectContent>
                {(projects || []).map((p) => (
                  <SelectItem key={p.id} value={p.id.toString()}>{p.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      </div>

      {!projectId ? (
        <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl">
          <AlertCircle className="w-10 h-10 text-slate-300 mx-auto mb-4" />
          <h3 className="text-lg font-bold text-slate-800 mb-1">No Project Selected</h3>
          <p className="text-slate-500 text-sm">Select a project above to manage its environments and secrets.</p>
        </div>
      ) : (
        <div className="space-y-10">
          <EnvironmentsSection projectId={projectId} />
          <SecretsSection projectId={projectId} />
        </div>
      )}
    </div>
  );
}

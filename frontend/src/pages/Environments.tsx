import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Globe, Plus, Trash2, PenLine, Star, X, Check, KeyRound, ChevronRight, Network,
} from 'lucide-react';
import { HarFilesDetail } from '@/components/har/HarFilesDetail';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import {
  DetailCard, EmptyState, Eyebrow, ListGroup, ListRow, PageHeader, Panel, Pill, SplitView,
} from '@/components/redesign/primitives';
import { cn } from '@/lib/utils';
import { holderText, type EnvironmentLock } from '@/components/environments/environmentLock';

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
  // WP16c: one run at a time; `lock` = the current holder (null when free).
  exclusive?: boolean;
  lock?: EnvironmentLock | null;
  created_at: string;
  updated_at: string;
}

interface EnvironmentPayload {
  name: string;
  base_url?: string | null;
  variables: Record<string, string>;
  is_default: boolean;
  exclusive?: boolean;
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
  forceRelease: async (id: number): Promise<{ released: boolean }> => {
    const res = await api.delete(`/environments/${id}/lock`);
    return res.data;
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

const fieldLabel = 'text-[13px] font-semibold text-ink';

function IconChip({ icon: Icon, tone }: { icon: typeof Globe; tone: 'lavender' | 'butter' | 'neutral' }) {
  return (
    <span
      className={cn(
        'flex h-7 w-7 flex-none items-center justify-center rounded-[9px]',
        tone === 'lavender' && 'bg-pastel-lavender text-pastel-ink-lavender',
        tone === 'butter' && 'bg-pastel-butter text-pastel-ink-butter',
        tone === 'neutral' && 'bg-secondary text-ink-muted',
      )}
      aria-hidden="true"
    >
      <Icon className="h-[15px] w-[15px]" strokeWidth={2.2} />
    </span>
  );
}

function VariablesEditor({ rows, onChange }: { rows: VariableRow[]; onChange: (rows: VariableRow[]) => void }) {
  const update = (idx: number, patch: Partial<VariableRow>) =>
    onChange(rows.map((r, i) => (i === idx ? { ...r, ...patch } : r)));

  return (
    <div className="flex flex-col gap-2">
      <span className={fieldLabel}>Variables</span>
      {rows.length === 0 && (
        <p className="text-sm text-ink-soft">No variables yet.</p>
      )}
      {rows.map((row, idx) => (
        <div key={idx} className="flex items-center gap-2">
          <Input
            placeholder="KEY"
            aria-label={`Variable ${idx + 1} name`}
            value={row.key}
            onChange={(e) => update(idx, { key: e.target.value })}
            className="h-10 w-[40%] rounded-xl font-mono text-xs"
          />
          <Input
            placeholder="value"
            aria-label={`Variable ${idx + 1} value`}
            value={row.value}
            onChange={(e) => update(idx, { value: e.target.value })}
            className="h-10 flex-1 rounded-xl font-mono text-xs"
          />
          <Button
            variant="ghost"
            size="icon"
            className="shrink-0 text-ink-soft hover:text-ink"
            onClick={() => onChange(rows.filter((_, i) => i !== idx))}
            title="Remove variable"
            aria-label={`Remove variable ${row.key || idx + 1}`}
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      ))}
      <Button
        variant="outline"
        size="sm"
        className="self-start"
        onClick={() => onChange([...rows, { key: '', value: '' }])}
      >
        <Plus className="h-3.5 w-3.5" /> Add variable
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
    <div className="flex flex-col gap-5">
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <label htmlFor="env-name" className={fieldLabel}>Name</label>
          <Input
            id="env-name"
            placeholder="e.g. staging"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            className="h-11 rounded-xl"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="env-base-url" className={fieldLabel}>Base URL</label>
          <Input
            id="env-base-url"
            placeholder="https://staging.example.com"
            value={form.baseUrl}
            onChange={(e) => setForm({ ...form, baseUrl: e.target.value })}
            className="h-11 rounded-xl font-mono text-xs"
          />
        </div>
      </div>

      <VariablesEditor rows={form.rows} onChange={(rows) => setForm({ ...form, rows })} />

      <label className="flex min-h-11 w-fit cursor-pointer select-none items-center gap-3 text-sm text-ink">
        <input
          type="checkbox"
          checked={form.isDefault}
          onChange={(e) => setForm({ ...form, isDefault: e.target.checked })}
          className="h-[18px] w-[18px] accent-ink"
        />
        Use as the default environment for this project
      </label>

      <div className="flex flex-wrap gap-2.5">
        <Button size="lg" disabled={submitting} onClick={handleSubmit}>
          <Check className="h-4 w-4" /> {submitLabel}
        </Button>
        <Button size="lg" variant="outline" disabled={submitting} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Environment detail
// ---------------------------------------------------------------------------

function EnvironmentDetail({ env, projectId }: { env: ProjectEnvironment; projectId: number }) {
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

  const releaseMutation = useMutation({
    mutationFn: () => environmentsApi.forceRelease(env.id),
    onSuccess: (out) => {
      invalidate();
      toast.success(out.released ? `Released "${env.name}"` : `"${env.name}" was already free`);
    },
    onError: (err: unknown) => toast.error(`Failed to release the environment: ${errorDetail(err)}`),
  });

  const variables = Object.entries(env.variables || {});
  const held = !!env.exclusive && env.lock?.run_id != null;

  return (
    <DetailCard label="Selected environment">
      <div className="flex flex-col gap-2">
        {env.is_default && (
          <Pill tone="butter" className="self-start"><Star className="h-3 w-3" aria-hidden="true" /> Default</Pill>
        )}
        <h2 className="text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{env.name}</h2>
        <span className="text-sm text-ink-soft">Updated {formatDate(env.updated_at || env.created_at)}</span>
      </div>

      {editing ? (
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
      ) : (
        <>
          <div className="flex flex-col gap-2">
            <Eyebrow>Base URL</Eyebrow>
            {env.base_url ? (
              <p className="break-all rounded-2xl bg-background p-4 font-mono text-[13px] text-ink">{env.base_url}</p>
            ) : (
              <p className="rounded-2xl bg-background p-4 text-sm text-ink-soft">No base URL. Relative goto steps won't resolve in this environment.</p>
            )}
          </div>

          <div className="flex flex-col gap-2">
            <Eyebrow right={variables.length ? `${variables.length}` : undefined}>Variables</Eyebrow>
            {variables.length === 0 ? (
              <p className="rounded-2xl bg-background p-4 text-sm text-ink-soft">No variables yet. Edit the environment to add some.</p>
            ) : (
              <ul className="overflow-hidden rounded-2xl border border-border">
                {variables.map(([key, value]) => (
                  <li key={key} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-t border-[#F0EEE8] px-4 py-3 first:border-t-0" title={`{{env.${key}}}`}>
                    <span className="font-mono text-[13px] font-semibold text-ink">{key}</span>
                    <span className="min-w-0 flex-1 break-all font-mono text-[13px] text-ink-soft">{String(value)}</span>
                    <code className="font-mono text-xs text-ink-soft">{`{{env.${key}}}`}</code>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <section aria-label="Exclusive use" className="flex flex-col gap-2">
            <Eyebrow right={env.exclusive ? <Pill tone="lavender">One run at a time</Pill> : undefined}>Exclusive use</Eyebrow>
            <div className="flex flex-col gap-3 rounded-2xl bg-background p-4">
              <p className="text-sm text-ink-soft">
                {env.exclusive
                  ? 'Only one run uses this environment at a time. Other runs wait (pending) and start automatically when it is released. Several runs started together, for example a browser or persona matrix, queue behind each other.'
                  : 'Runs share this environment. Make it exclusive when runs would interfere with each other, such as a shared test account or database.'}
              </p>
              {env.exclusive && (
                <p className="text-sm font-semibold text-ink" role="status">
                  {held && env.lock?.run_id != null ? (
                    <Link to={`/runs/${env.lock.run_id}`} className="underline underline-offset-2">{holderText(env.lock)}</Link>
                  ) : holderText(env.lock)}
                </p>
              )}
              <div className="flex flex-wrap gap-2.5">
                <Button
                  size="sm"
                  variant="outline"
                  className="h-10 px-4"
                  disabled={updateMutation.isPending}
                  onClick={() => updateMutation.mutate({ exclusive: !env.exclusive })}
                >
                  {env.exclusive ? 'Allow parallel runs' : 'Make exclusive'}
                </Button>
                {held && (
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-10 px-4"
                    disabled={releaseMutation.isPending}
                    onClick={() => {
                      if (window.confirm(`Force-release "${env.name}"? Run #${env.lock?.run_id} keeps running, and the next waiting run starts on this environment alongside it.`)) {
                        releaseMutation.mutate();
                      }
                    }}
                  >
                    Force release
                  </Button>
                )}
              </div>
            </div>
          </section>

          <div className="flex flex-wrap gap-2.5">
            <Button size="lg" onClick={() => setEditing(true)}>
              <PenLine className="h-4 w-4" /> Edit
            </Button>
            {!env.is_default && (
              <Button
                size="lg"
                variant="outline"
                disabled={updateMutation.isPending}
                onClick={() => updateMutation.mutate({ is_default: true })}
              >
                <Star className="h-4 w-4" /> Set as default
              </Button>
            )}
            <Button
              size="lg"
              variant="outline"
              className="sm:ml-auto"
              disabled={deleteMutation.isPending}
              onClick={() => {
                if (window.confirm(`Delete environment "${env.name}"? This cannot be undone.`)) {
                  deleteMutation.mutate();
                }
              }}
            >
              <Trash2 className="h-4 w-4" /> Delete
            </Button>
          </div>
        </>
      )}
    </DetailCard>
  );
}

function NewEnvironmentCard({ projectId, onDone, onCreated }: {
  projectId: number;
  onDone: () => void;
  onCreated: (id: number) => void;
}) {
  const queryClient = useQueryClient();
  const createMutation = useMutation({
    mutationFn: (body: EnvironmentPayload) => environmentsApi.create(projectId, body),
    onSuccess: (env) => {
      queryClient.invalidateQueries({ queryKey: ['environments', projectId] });
      onCreated(env.id);
      toast.success(`Environment "${env.name}" created`);
    },
    onError: (err: unknown) => toast.error(`Failed to create environment: ${errorDetail(err)}`),
  });

  return (
    <DetailCard label="New environment">
      <h2 className="text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">New environment</h2>
      <EnvironmentForm
        initial={{ name: '', baseUrl: '', isDefault: false, rows: [] }}
        submitting={createMutation.isPending}
        submitLabel="Create environment"
        onSubmit={(payload) => createMutation.mutate(payload)}
        onCancel={onDone}
      />
    </DetailCard>
  );
}

// ---------------------------------------------------------------------------
// Secrets
// ---------------------------------------------------------------------------

const SECRET_KEY_PATTERN = /^[A-Za-z0-9_]+$/;

function SecretsDetail({ projectId, secrets, isLoading, error }: {
  projectId: number;
  secrets: SecretKeyInfo[] | undefined;
  isLoading: boolean;
  error: unknown;
}) {
  const queryClient = useQueryClient();
  const [key, setKey] = useState('');
  const [value, setValue] = useState('');

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
    <DetailCard label="Secrets">
      <div className="flex flex-col gap-1.5">
        <h2 className="text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">Secrets</h2>
        <span className="text-sm text-ink-soft">Shared by every environment in this project.</span>
      </div>

      <Panel title="How secrets work" callout>
        Reference a secret in a step as <code className="font-mono text-[13px]">{'{{secret.KEY}}'}</code>. Values are write-only:
        encrypted at rest, never returned by the API, and only decrypted into a job when it is dispatched.
        Saving an existing key overwrites its value.
      </Panel>

      <div className="flex flex-col gap-3 rounded-2xl border border-border p-4">
        <span className="text-sm font-semibold text-ink">Add or update a secret</span>
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            aria-label="Secret key"
            placeholder="KEY (e.g. API_TOKEN)"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            className="h-11 rounded-xl font-mono text-xs sm:w-[220px]"
          />
          <Input
            aria-label="Secret value"
            type="password"
            placeholder="Secret value"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            autoComplete="new-password"
            className="h-11 flex-1 rounded-xl font-mono text-xs"
          />
          <Button size="lg" className="shrink-0 px-5" disabled={upsertMutation.isPending} onClick={handleSave}>
            <Check className="h-4 w-4" />
            {existingKeys.has(key.trim()) ? 'Update secret' : 'Save secret'}
          </Button>
        </div>
        {key.trim() && existingKeys.has(key.trim()) && (
          <p className="text-xs text-pastel-ink-butter">
            "{key.trim()}" already exists. Saving will overwrite its current value.
          </p>
        )}
      </div>

      <div className="flex flex-col gap-2">
        <Eyebrow right={secrets?.length ? `${secrets.length}` : undefined}>Stored secrets</Eyebrow>
        {isLoading ? (
          <div className="h-[120px] animate-pulse rounded-2xl bg-background" aria-busy="true" aria-label="Loading" />
        ) : error ? (
          <p className="rounded-2xl bg-background p-4 text-sm text-ink">Couldn't load secrets: {errorDetail(error)}</p>
        ) : !secrets || secrets.length === 0 ? (
          <p className="rounded-2xl bg-background p-4 text-sm text-ink-soft">
            No secrets stored. Keep API tokens and passwords here instead of typing them into test steps.
          </p>
        ) : (
          <ul className="overflow-hidden rounded-2xl border border-border">
            {secrets.map((s) => (
              <li key={s.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-[#F0EEE8] px-4 py-2.5 first:border-t-0">
                <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="font-mono text-[13px] font-semibold text-ink">{s.key}</span>
                  <span className="font-mono text-xs text-ink-soft">{`{{secret.${s.key}}}`} · updated {formatDate(s.updated_at || s.created_at)}</span>
                </span>
                <Button
                  size="icon"
                  variant="ghost"
                  title="Delete secret"
                  aria-label={`Delete secret ${s.key}`}
                  disabled={deleteMutation.isPending}
                  onClick={() => {
                    if (window.confirm(`Delete secret "${s.key}"? Steps referencing {{secret.${s.key}}} will fail.`)) {
                      deleteMutation.mutate(s.key);
                    }
                  }}
                  className="text-ink-soft hover:text-ink"
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </DetailCard>
  );
}

// ---------------------------------------------------------------------------
// Split view
// ---------------------------------------------------------------------------

type Selection = { type: 'env'; id: number } | { type: 'secrets' } | { type: 'hars' } | { type: 'new' } | null;

function EnvironmentsView({ projectId, selection, setSelection }: {
  projectId: number;
  selection: Selection;
  setSelection: (s: Selection) => void;
}) {
  const { data: environments, isLoading, error } = useQuery({
    queryKey: ['environments', projectId],
    queryFn: () => environmentsApi.list(projectId),
    // Holders change as runs finish; keep the "in use" line honest.
    refetchInterval: (q) => (q.state.data?.some((e) => e.exclusive) ? 15000 : false),
  });
  const secretsQuery = useQuery({
    queryKey: ['secrets', projectId],
    queryFn: () => secretsApi.list(projectId),
  });

  const envs = environments ?? [];
  const selectedEnv = selection?.type === 'env'
    ? envs.find((e) => e.id === selection.id) ?? null
    : null;
  // Nothing picked yet (or the picked one was deleted): fall back to the
  // default environment, then the first, then secrets.
  const fallbackEnv = envs.find((e) => e.is_default) ?? envs[0] ?? null;
  const showHars = selection?.type === 'hars';
  const showSecrets = selection?.type === 'secrets' || (!selectedEnv && !showHars && selection?.type !== 'new' && !fallbackEnv && !isLoading);
  const activeEnv = selection?.type === 'new' || selection?.type === 'secrets' || showHars ? null : selectedEnv ?? fallbackEnv;

  const secretCount = secretsQuery.data?.length;

  return (
    <SplitView
      list={
        <>
          <ListGroup title="Environments" count={environments ? envs.length : undefined}>
            {isLoading ? (
              [0, 1].map((i) => <div key={i} className="h-[60px] animate-pulse rounded-2xl bg-white/70" />)
            ) : error ? (
              <p className="px-3 py-2 text-sm text-ink">Couldn't load environments: {errorDetail(error)}</p>
            ) : envs.length === 0 ? (
              <p className="px-3 py-2 text-sm text-ink-soft">
                None yet. Create environments like dev, staging and prod so the same tests can run against different deployments.
              </p>
            ) : (
              envs.map((env) => (
                <ListRow
                  key={env.id}
                  selected={activeEnv?.id === env.id}
                  onSelect={() => setSelection({ type: 'env', id: env.id })}
                  leading={<IconChip icon={Globe} tone="lavender" />}
                  title={env.name}
                  meta={<span className="break-all font-mono">{env.base_url || 'No base URL'}</span>}
                  trailing={env.exclusive && env.lock?.run_id != null
                    ? <Pill tone="lavender">In use</Pill>
                    : env.is_default ? <Pill tone="butter">Default</Pill> : undefined}
                />
              ))
            )}
          </ListGroup>

          <ListGroup title="Credentials">
            <ListRow
              selected={showSecrets}
              onSelect={() => setSelection({ type: 'secrets' })}
              leading={<IconChip icon={KeyRound} tone="butter" />}
              title="Secrets"
              meta={secretCount === undefined ? 'Write-only values' : `${secretCount} stored · write-only`}
              trailing={<ChevronRight className="h-4 w-4 text-ink-soft" aria-hidden="true" />}
            />
            <ListRow
              selected={showHars}
              onSelect={() => setSelection({ type: 'hars' })}
              leading={<IconChip icon={Network} tone="neutral" />}
              title="Recorded traffic (HAR)"
              meta="Replayed by har-replay steps"
              trailing={<ChevronRight className="h-4 w-4 text-ink-soft" aria-hidden="true" />}
            />
          </ListGroup>

          <Panel title="Using environments">
            <span className="text-sm text-ink-soft">
              Relative <code className="font-mono text-[13px] text-ink">goto</code> URLs resolve against the environment's base URL,
              and variables are referenced as <code className="font-mono text-[13px] text-ink">{'{{env.KEY}}'}</code>.
              The default environment is used when a run doesn't name one.
            </span>
          </Panel>
        </>
      }
      detail={
        selection?.type === 'new' ? (
          <NewEnvironmentCard
            projectId={projectId}
            onDone={() => setSelection(null)}
            onCreated={(id) => setSelection({ type: 'env', id })}
          />
        ) : showHars ? (
          <HarFilesDetail projectId={projectId} />
        ) : showSecrets ? (
          <SecretsDetail
            projectId={projectId}
            secrets={secretsQuery.data}
            isLoading={secretsQuery.isLoading}
            error={secretsQuery.error}
          />
        ) : activeEnv ? (
          <EnvironmentDetail key={activeEnv.id} env={activeEnv} projectId={projectId} />
        ) : isLoading ? (
          <div className="h-[320px] animate-pulse rounded-3xl bg-white/70" aria-busy="true" aria-label="Loading" />
        ) : null
      }
    />
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function Environments() {
  // The sidebar owns project selection.
  const projectId = useActiveProjectId();
  const [selection, setSelection] = useState<Selection>(null);

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        crumbs={<><Link to="/suites" className="hover:text-ink">Tests</Link><span>/</span><span>Environments</span></>}
        title="Environments and secrets"
        subtitle="Deployment targets and credentials for this project, so the same tests can run anywhere."
        actions={projectId ? (
          <Button onClick={() => setSelection({ type: 'new' })}><Plus className="h-4 w-4" /> New environment</Button>
        ) : undefined}
      />

      {!projectId ? (
        <EmptyState title="Pick a project">Choose a project in the sidebar to manage its environments and secrets.</EmptyState>
      ) : (
        <EnvironmentsView key={projectId} projectId={projectId} selection={selection} setSelection={setSelection} />
      )}
    </div>
  );
}

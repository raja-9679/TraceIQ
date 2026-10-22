import { useMemo, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { KeyRound, Plus, RefreshCw, Check, Copy, Ban } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { toast } from 'sonner';
import { getWorkspaces, getProjects, getRoles, Workspace, Project, Role } from '@/lib/api';
import { apiKeysApi, ApiKey, ApiKeyCreateResponse } from '@/api/apiKeys';
import {
  DetailCard, EmptyState, ListGroup, ListRow, PageHeader, Panel, Pill, SplitView,
} from '@/components/redesign/primitives';
import type { Tone } from '@/components/redesign/kinds';
import { Facts, Field, FormCard, ListSkeleton } from '@/components/redesign-local/groupE/bits';
import { cn } from '@/lib/utils';
import { parseCidrList } from '@/lib/fleet';
import { useActiveWorkspaceId } from '@/hooks/useActiveWorkspace';

interface ApiError extends Error {
  response?: { data?: { detail?: string } };
}

const errorDetail = (err: unknown): string => {
  const e = err as ApiError;
  return e.response?.data?.detail || e.message || 'Unknown error';
};

const formatDate = (dateString?: string | null) => {
  if (!dateString) return '—';
  return new Date(dateString).toLocaleString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
};

const keyStatus = (key: ApiKey): 'active' | 'revoked' | 'expired' => {
  if (key.revoked_at) return 'revoked';
  if (key.expires_at && new Date(key.expires_at) < new Date()) return 'expired';
  return 'active';
};

const STATUS_LOOK: Record<ReturnType<typeof keyStatus>, { tone: Tone; label: string }> = {
  active: { tone: 'mint', label: 'Active' },
  revoked: { tone: 'peach', label: 'Revoked' },
  expired: { tone: 'butter', label: 'Expired' },
};

// ---------------------------------------------------------------------------
// One-time secret reveal
// ---------------------------------------------------------------------------

function SecretReveal({ created, onDismiss }: { created: ApiKeyCreateResponse; onDismiss: () => void }) {
  const [copied, setCopied] = useState(false);

  const copySecret = async () => {
    try {
      await navigator.clipboard.writeText(created.secret);
      setCopied(true);
      toast.success('API key copied to clipboard');
      setTimeout(() => setCopied(false), 2500);
    } catch {
      toast.error('Could not copy — select and copy the key manually');
    }
  };

  return (
    <section
      aria-label="New API key"
      className="flex flex-col gap-3 rounded-3xl border border-border bg-white p-6 shadow-[inset_4px_0_0_#F2C94C]"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <Pill tone="butter" className="self-start">Shown once</Pill>
          <h2 className="text-base font-semibold text-ink">Copy “{created.name}” now</h2>
          <p className="max-w-[60ch] text-[13px] leading-relaxed text-ink-soft">
            This is the only time the full key is shown. You won't be able to see it again;
            if it's lost, revoke it and create a new one.
          </p>
        </div>
        <Button variant="outline" onClick={onDismiss}>Done</Button>
      </div>
      <div className="flex flex-col gap-2 sm:flex-row">
        <code className="flex-1 select-all break-all rounded-2xl border border-border bg-background px-4 py-2.5 font-mono text-xs text-ink">
          {created.secret}
        </code>
        <Button onClick={copySecret} className="shrink-0">
          {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
          {copied ? 'Copied' : 'Copy key'}
        </Button>
      </div>
      <p className="text-xs text-ink-soft">
        Pass it on requests via the <code className="rounded bg-background px-1 font-mono">X-API-Key</code> header.
      </p>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Create form
// ---------------------------------------------------------------------------

const NO_SCOPE = 'workspace';
// The key's role is a ceiling on what it can do (the backend refuses other
// roles with a 400); tenant-level roles are never offered.
const KEY_ROLE_CEILINGS = ['Project Viewer', 'Project Editor', 'Project Admin', 'Workspace Member', 'Workspace Admin'];
const NO_ROLE = 'default';

function CreateKeyForm({
  workspaceId, projects, roles, onCreated, onCancel,
}: {
  workspaceId: number;
  projects: Project[];
  roles: Role[];
  onCreated: (created: ApiKeyCreateResponse) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState('');
  const [projectId, setProjectId] = useState<string>(NO_SCOPE);
  const [roleId, setRoleId] = useState<string>(NO_ROLE);
  const [expiresInDays, setExpiresInDays] = useState('');
  const [cidrs, setCidrs] = useState('');

  const createMutation = useMutation({
    mutationFn: () => {
      const days = expiresInDays.trim() ? parseInt(expiresInDays.trim()) : null;
      return apiKeysApi.create(workspaceId, {
        name: name.trim(),
        workspace_id: workspaceId,
        project_id: projectId === NO_SCOPE ? null : parseInt(projectId),
        role_id: roleId === NO_ROLE ? null : parseInt(roleId),
        expires_in_days: days && days > 0 ? days : null,
        allowed_cidrs: parseCidrList(cidrs),
      });
    },
    onSuccess: (created) => onCreated(created),
    onError: (err: unknown) => toast.error(`Failed to create API key: ${errorDetail(err)}`),
  });

  const handleSubmit = () => {
    if (!name.trim()) {
      toast.error('Key name is required');
      return;
    }
    if (expiresInDays.trim() && (!/^\d+$/.test(expiresInDays.trim()) || parseInt(expiresInDays.trim()) <= 0)) {
      toast.error('Expiry must be a positive number of days');
      return;
    }
    createMutation.mutate();
  };

  return (
    <FormCard title="New API key" subtitle="Scope it to one project where you can. The full key appears once, right after you create it.">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Field id="key-name" label="Name">
          <Input
            id="key-name"
            placeholder="e.g. ci-regression-bot"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="h-10 rounded-full px-4"
          />
        </Field>
        <Field id="key-project" label="Project scope">
          <Select value={projectId} onValueChange={setProjectId}>
            <SelectTrigger id="key-project" className="h-10 rounded-full px-4">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NO_SCOPE}>Entire workspace</SelectItem>
              {projects.map((p) => (
                <SelectItem key={p.id} value={p.id.toString()}>{p.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field id="key-role" label="Role limit" hint="Caps the key's access — never more than yours.">
          <Select value={roleId} onValueChange={setRoleId}>
            <SelectTrigger id="key-role" className="h-10 rounded-full px-4">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NO_ROLE}>Default</SelectItem>
              {roles.filter((r) => KEY_ROLE_CEILINGS.includes(r.name)).map((r) => (
                <SelectItem key={r.id} value={r.id.toString()}>{r.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field id="key-expiry" label="Expires in (days)">
          <Input
            id="key-expiry"
            placeholder="never"
            inputMode="numeric"
            value={expiresInDays}
            onChange={(e) => setExpiresInDays(e.target.value)}
            className="h-10 rounded-full px-4"
          />
        </Field>
      </div>
      <Field
        id="api-key-cidrs"
        label="Allowed source IPs (optional)"
        hint={<>Comma-separated CIDRs; blank allows any address. Requests from other addresses are refused (403) and
          audited. Behind a proxy, an instance admin must set the trusted proxies in Instance settings → Network access
          control.</>}
      >
        <Input
          id="api-key-cidrs"
          placeholder="e.g. 203.0.113.0/24, 198.51.100.7"
          value={cidrs}
          onChange={(e) => setCidrs(e.target.value)}
          className="h-10 rounded-full px-4"
        />
      </Field>
      <div className="flex flex-wrap gap-2">
        <Button disabled={createMutation.isPending} onClick={handleSubmit}>
          {createMutation.isPending ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
          Create key
        </Button>
        <Button variant="outline" disabled={createMutation.isPending} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </FormCard>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ApiKeys() {
  const queryClient = useQueryClient();
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [createdKey, setCreatedKey] = useState<ApiKeyCreateResponse | null>(null);
  const [selectedKeyId, setSelectedKeyId] = useState<number | null>(null);

  const { data: workspaces } = useQuery<Workspace[]>({
    queryKey: ['workspaces'],
    queryFn: getWorkspaces,
  });

  // Default to the first workspace until the user explicitly picks one — derived
  // rather than set via an effect to avoid a cascading render.
  // Default to the sidebar project's workspace, not whichever is listed first.
  const activeWs = useActiveWorkspaceId();
  const workspaceId = selectedWorkspaceId ?? activeWs ?? workspaces?.[0]?.id ?? null;

  const { data: keys, isLoading, error } = useQuery({
    queryKey: ['api-keys', workspaceId],
    queryFn: () => apiKeysApi.list(workspaceId!),
    enabled: !!workspaceId,
  });

  const { data: projects } = useQuery<Project[]>({
    queryKey: ['projects', workspaceId],
    queryFn: () => getProjects(workspaceId!),
    enabled: !!workspaceId,
  });

  const { data: roles } = useQuery<Role[]>({
    queryKey: ['roles'],
    queryFn: getRoles,
  });

  const projectNames = useMemo(() => {
    const map = new Map<number, string>();
    (projects || []).forEach((p) => map.set(p.id, p.name));
    return map;
  }, [projects]);

  const cidrMutation = useMutation({
    mutationFn: ({ keyId, cidrs }: { keyId: number; cidrs: string[] | null }) =>
      apiKeysApi.setAllowedCidrs(workspaceId!, keyId, cidrs),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['api-keys', workspaceId] });
      toast.success('Source-IP allowlist updated');
    },
    onError: (err: unknown) => toast.error(`Failed to update allowlist: ${errorDetail(err)}`),
  });

  const editCidrs = (key: ApiKey) => {
    const next = window.prompt(
      `Allowed source IPs for "${key.name}" (CIDR, comma-separated). Leave empty to allow any address.`,
      (key.allowed_cidrs || []).join(', '),
    );
    if (next === null) return;
    cidrMutation.mutate({ keyId: key.id, cidrs: parseCidrList(next) });
  };

  const revokeMutation = useMutation({
    mutationFn: (keyId: number) => apiKeysApi.revoke(workspaceId!, keyId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['api-keys', workspaceId] });
      toast.success('API key revoked');
    },
    onError: (err: unknown) => toast.error(`Failed to revoke API key: ${errorDetail(err)}`),
  });

  const all = keys ?? [];
  const groups = [
    { name: 'Active keys', items: all.filter((k) => keyStatus(k) === 'active') },
    { name: 'Expired', items: all.filter((k) => keyStatus(k) === 'expired') },
    { name: 'Revoked', items: all.filter((k) => keyStatus(k) === 'revoked') },
  ].filter((g) => g.items.length > 0);
  const selected = all.find((k) => k.id === selectedKeyId) ?? groups[0]?.items[0] ?? null;
  const activeCount = all.filter((k) => keyStatus(k) === 'active').length;
  const scopeLabel = (k: ApiKey) =>
    k.project_id ? (projectNames.get(k.project_id) || `Project #${k.project_id}`) : 'Entire workspace';

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="API keys"
        subtitle={
          keys
            ? `${activeCount} active of ${all.length} · service credentials for CI pipelines and AI agents`
            : 'Service credentials for CI pipelines and AI agents, sent in the X-API-Key header.'
        }
        actions={
          <>
            {(workspaces?.length ?? 0) > 1 && (
              <Select
                value={workspaceId?.toString() ?? ''}
                onValueChange={(v) => { setSelectedWorkspaceId(parseInt(v)); setCreatedKey(null); setCreating(false); setSelectedKeyId(null); }}
              >
                <SelectTrigger className="h-10 w-[220px] rounded-full px-4" aria-label="Workspace">
                  <SelectValue placeholder="Select a workspace" />
                </SelectTrigger>
                <SelectContent>
                  {(workspaces || []).map((w) => (
                    <SelectItem key={w.id} value={w.id.toString()}>{w.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            {workspaceId && (
              <Button onClick={() => setCreating((c) => !c)}>
                <Plus className="h-4 w-4" /> New API key
              </Button>
            )}
          </>
        }
      />

      {!workspaceId ? (
        <EmptyState title="No workspace selected">Select a workspace to manage its API keys.</EmptyState>
      ) : (
        <>
          {createdKey && (
            <SecretReveal created={createdKey} onDismiss={() => setCreatedKey(null)} />
          )}

          {creating && (
            <CreateKeyForm
              workspaceId={workspaceId}
              projects={projects || []}
              roles={roles || []}
              onCreated={(created) => {
                setCreating(false);
                setCreatedKey(created);
                setSelectedKeyId(created.id);
                queryClient.invalidateQueries({ queryKey: ['api-keys', workspaceId] });
              }}
              onCancel={() => setCreating(false)}
            />
          )}

          {isLoading ? (
            <ListSkeleton />
          ) : error ? (
            <EmptyState title="Couldn't load API keys">{errorDetail(error)}</EmptyState>
          ) : all.length === 0 ? (
            <EmptyState title="No API keys yet">
              Create a key so CI pipelines, the GitHub Action or MCP-connected agents can trigger and consume
              regression runs without a human login.
            </EmptyState>
          ) : (
            <SplitView
              list={
                <>
                  {groups.map((g) => (
                    <ListGroup key={g.name} title={g.name} count={g.items.length}>
                      {g.items.map((key) => {
                        const status = keyStatus(key);
                        return (
                          <ListRow
                            key={key.id}
                            selected={selected?.id === key.id}
                            onSelect={() => setSelectedKeyId(key.id)}
                            leading={
                              <span
                                aria-hidden="true"
                                className={cn(
                                  'flex h-8 w-8 flex-none items-center justify-center rounded-[10px]',
                                  status === 'active' ? 'bg-pastel-lavender text-pastel-ink-lavender' : 'bg-secondary text-ink-muted'
                                )}
                              >
                                <KeyRound className="h-[15px] w-[15px]" strokeWidth={2.2} />
                              </span>
                            }
                            title={key.name}
                            meta={`${scopeLabel(key)} · last used ${key.last_used_at ? formatDate(key.last_used_at) : 'never'}`}
                            trailing={<code className="font-mono text-xs text-ink-soft">{key.prefix}…</code>}
                          />
                        );
                      })}
                    </ListGroup>
                  ))}
                  <p className="px-1 text-xs leading-relaxed text-ink-soft">
                    The full key (<code className="font-mono">tiq_…</code>) is shown once at creation. Afterwards only
                    the prefix remains visible so keys can be identified without being revealed.
                  </p>
                </>
              }
              detail={selected && (() => {
                const status = keyStatus(selected);
                return (
                  <DetailCard label="Selected API key">
                    <div className="flex flex-col gap-2">
                      <Pill tone={STATUS_LOOK[status].tone} className="self-start">{STATUS_LOOK[status].label}</Pill>
                      <h2 className="break-words text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{selected.name}</h2>
                      <span className="text-sm text-ink-soft">Created {formatDate(selected.created_at)}</span>
                    </div>

                    <Facts
                      items={[
                        { label: 'Key', value: <code className="font-mono text-xs">{selected.prefix}…</code> },
                        { label: 'Scope', value: scopeLabel(selected) },
                        {
                          label: 'Source IPs',
                          value: selected.allowed_cidrs && selected.allowed_cidrs.length
                            ? (
                              <span className="flex flex-wrap gap-1">
                                {selected.allowed_cidrs.map((c) => (
                                  <code key={c} className="rounded-md bg-secondary px-1.5 py-0.5 font-mono text-xs">{c}</code>
                                ))}
                              </span>
                            )
                            : 'Any address',
                        },
                        { label: 'Last used', value: selected.last_used_at ? formatDate(selected.last_used_at) : 'Never' },
                        {
                          label: status === 'revoked' ? 'Revoked' : 'Expires',
                          value: status === 'revoked'
                            ? formatDate(selected.revoked_at)
                            : (selected.expires_at ? formatDate(selected.expires_at) : 'Never'),
                        },
                      ]}
                    />

                    <Panel title="How it's used" callout>
                      {status === 'active'
                        ? <>Callers send this key in the <code className="font-mono text-sm">X-API-Key</code> header. Revoking it cuts off every caller using it immediately.</>
                        : status === 'expired'
                          ? 'This key has passed its expiry date and no longer authenticates. Create a new key to restore access.'
                          : 'This key was revoked and no longer authenticates. It stays listed so its history is traceable.'}
                    </Panel>

                    {status === 'active' && (
                      <div className="flex flex-wrap gap-2.5">
                        <Button
                          variant="outline"
                          size="lg"
                          disabled={cidrMutation.isPending}
                          onClick={() => editCidrs(selected)}
                        >
                          Edit source IPs
                        </Button>
                        <Button
                          variant="outline"
                          size="lg"
                          disabled={revokeMutation.isPending}
                          onClick={() => {
                            if (window.confirm(`Revoke API key "${selected.name}"? Callers using it will immediately lose access.`)) {
                              revokeMutation.mutate(selected.id);
                            }
                          }}
                          className="text-pastel-ink-peach"
                        >
                          <Ban className="h-4 w-4" /> Revoke key
                        </Button>
                      </div>
                    )}
                  </DetailCard>
                );
              })()}
            />
          )}
        </>
      )}
    </div>
  );
}

import { useMemo, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  KeyRound, Plus, RefreshCw, AlertCircle, Check, Copy, Ban, Building2,
  ShieldAlert, Layers, Info,
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
import { getWorkspaces, getProjects, getRoles, Workspace, Project, Role } from '@/lib/api';
import { apiKeysApi, ApiKey, ApiKeyCreateResponse } from '@/api/apiKeys';

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

const STATUS_STYLES: Record<string, string> = {
  active: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  revoked: 'bg-rose-50 text-rose-700 border-rose-200',
  expired: 'bg-amber-50 text-amber-700 border-amber-200',
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
    <div className="bg-amber-50/70 border border-amber-200 rounded-2xl px-5 py-4 mb-4">
      <div className="flex items-start gap-3">
        <ShieldAlert className="w-5 h-5 text-amber-500 shrink-0 mt-0.5" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-bold text-amber-800">
            API key "{created.name}" created — copy it now
          </p>
          <p className="text-xs text-amber-700/80 mt-0.5">
            This is the only time the full key is shown. You won't be able to see it again;
            if it's lost, revoke it and create a new one.
          </p>
          <div className="flex flex-col sm:flex-row gap-2 mt-3">
            <code className="flex-1 text-xs font-mono bg-white border border-amber-200 text-slate-700 px-3 py-2 rounded-lg break-all select-all">
              {created.secret}
            </code>
            <Button
              size="sm"
              onClick={copySecret}
              className="bg-amber-600 hover:bg-amber-700 text-white rounded-xl h-9 px-4 shrink-0"
            >
              {copied ? <Check className="w-4 h-4 mr-1.5" /> : <Copy className="w-4 h-4 mr-1.5" />}
              {copied ? 'Copied' : 'Copy key'}
            </Button>
          </div>
          <p className="text-xs text-amber-700/80 mt-2">
            Pass it on requests via the <code className="font-mono bg-white/70 px-1 rounded">X-API-Key</code> header.
          </p>
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={onDismiss}
          className="rounded-xl h-8 px-3 border-amber-200 text-amber-700 hover:bg-amber-100 shrink-0"
        >
          Done
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Create form
// ---------------------------------------------------------------------------

const NO_SCOPE = 'workspace';
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

  const createMutation = useMutation({
    mutationFn: () => {
      const days = expiresInDays.trim() ? parseInt(expiresInDays.trim()) : null;
      return apiKeysApi.create(workspaceId, {
        name: name.trim(),
        workspace_id: workspaceId,
        project_id: projectId === NO_SCOPE ? null : parseInt(projectId),
        role_id: roleId === NO_ROLE ? null : parseInt(roleId),
        expires_in_days: days && days > 0 ? days : null,
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
    <div className="bg-white border border-indigo-200 rounded-2xl shadow-sm px-5 py-4 mb-4">
      <p className="text-sm font-bold text-slate-800 mb-3">New API key</p>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        <div>
          <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Name</p>
          <Input
            placeholder="e.g. ci-regression-bot"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="h-9 rounded-lg bg-white border-slate-200"
          />
        </div>
        <div>
          <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Project scope</p>
          <Select value={projectId} onValueChange={setProjectId}>
            <SelectTrigger className="h-9 rounded-lg bg-white border-slate-200">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NO_SCOPE}>Entire workspace</SelectItem>
              {projects.map((p) => (
                <SelectItem key={p.id} value={p.id.toString()}>{p.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div>
          <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Role</p>
          <Select value={roleId} onValueChange={setRoleId}>
            <SelectTrigger className="h-9 rounded-lg bg-white border-slate-200">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NO_ROLE}>Default</SelectItem>
              {roles.map((r) => (
                <SelectItem key={r.id} value={r.id.toString()}>{r.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div>
          <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Expires in (days)</p>
          <Input
            placeholder="never"
            inputMode="numeric"
            value={expiresInDays}
            onChange={(e) => setExpiresInDays(e.target.value)}
            className="h-9 rounded-lg bg-white border-slate-200"
          />
        </div>
      </div>
      <div className="flex gap-2 pt-4">
        <Button
          size="sm"
          disabled={createMutation.isPending}
          onClick={handleSubmit}
          className="bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl h-9 px-4"
        >
          {createMutation.isPending
            ? <RefreshCw className="w-4 h-4 mr-1.5 animate-spin" />
            : <Check className="w-4 h-4 mr-1.5" />}
          Create key
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={createMutation.isPending}
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
// Page
// ---------------------------------------------------------------------------

export default function ApiKeys() {
  const queryClient = useQueryClient();
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [createdKey, setCreatedKey] = useState<ApiKeyCreateResponse | null>(null);

  const { data: workspaces } = useQuery<Workspace[]>({
    queryKey: ['workspaces'],
    queryFn: getWorkspaces,
  });

  // Default to the first workspace until the user explicitly picks one — derived
  // rather than set via an effect to avoid a cascading render.
  const workspaceId = selectedWorkspaceId ?? workspaces?.[0]?.id ?? null;

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

  const revokeMutation = useMutation({
    mutationFn: (keyId: number) => apiKeysApi.revoke(workspaceId!, keyId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['api-keys', workspaceId] });
      toast.success('API key revoked');
    },
    onError: (err: unknown) => toast.error(`Failed to revoke API key: ${errorDetail(err)}`),
  });

  return (
    <div className="max-w-[1200px] mx-auto pb-16">
      <div className="pt-2 pb-6 mb-6 border-b border-slate-200/60">
        <div className="flex items-center text-sm text-slate-400 gap-2 mb-3">
          <span className="font-bold text-slate-700 bg-white px-2.5 py-1 rounded-md border border-slate-200 shadow-sm">Workspace Integrations</span>
        </div>
        <div className="flex flex-col sm:flex-row sm:items-end gap-4">
          <div>
            <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">API Keys</h1>
            <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
              Service-account credentials for CI pipelines and AI agents. Keys authenticate via the{' '}
              <code className="font-mono bg-slate-100 px-1.5 py-0.5 rounded text-sm">X-API-Key</code> header and
              can be scoped to a single project.
            </p>
          </div>
          <div className="sm:ml-auto shrink-0">
            <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Workspace</p>
            <Select
              value={workspaceId?.toString() ?? ''}
              onValueChange={(v) => { setSelectedWorkspaceId(parseInt(v)); setCreatedKey(null); setCreating(false); }}
            >
              <SelectTrigger className="w-[240px] h-10 rounded-xl bg-white border-slate-200">
                <div className="flex items-center gap-2 min-w-0">
                  <Building2 className="w-4 h-4 text-indigo-500 shrink-0" />
                  <SelectValue placeholder="Select a workspace" />
                </div>
              </SelectTrigger>
              <SelectContent>
                {(workspaces || []).map((w) => (
                  <SelectItem key={w.id} value={w.id.toString()}>{w.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      </div>

      {!workspaceId ? (
        <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl">
          <AlertCircle className="w-10 h-10 text-slate-300 mx-auto mb-4" />
          <h3 className="text-lg font-bold text-slate-800 mb-1">No Workspace Selected</h3>
          <p className="text-slate-500 text-sm">Select a workspace above to manage its API keys.</p>
        </div>
      ) : (
        <>
          <div className="flex items-center gap-3 mb-4">
            <div className="flex items-start gap-2 text-xs text-slate-500 bg-indigo-50/60 border border-indigo-100 rounded-xl px-3 py-2.5 flex-1">
              <Info className="w-4 h-4 text-indigo-400 shrink-0 mt-px" />
              <p>
                The full key (<code className="font-mono bg-white/70 px-1 rounded">tiq_…</code>) is shown{' '}
                <span className="font-semibold">once</span> at creation. Afterwards only the prefix remains
                visible so keys can be identified without being revealed.
              </p>
            </div>
            <Button
              size="sm"
              onClick={() => setCreating((c) => !c)}
              className="bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl h-9 px-4 shrink-0"
            >
              <Plus className="w-4 h-4 mr-1.5" /> New API key
            </Button>
          </div>

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
                queryClient.invalidateQueries({ queryKey: ['api-keys', workspaceId] });
              }}
              onCancel={() => setCreating(false)}
            />
          )}

          {isLoading ? (
            <div className="p-12 flex justify-center"><RefreshCw className="animate-spin text-muted-foreground w-6 h-6" /></div>
          ) : error ? (
            <div className="p-10 text-center bg-rose-50 border border-rose-200 rounded-2xl">
              <AlertCircle className="w-8 h-8 text-rose-500 mx-auto mb-3" />
              <p className="text-rose-700 font-semibold">Failed to load API keys</p>
              <p className="text-rose-600/80 text-sm">{errorDetail(error)}</p>
            </div>
          ) : !keys || keys.length === 0 ? (
            <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl">
              <KeyRound className="w-10 h-10 text-slate-300 mx-auto mb-4" />
              <h3 className="text-lg font-bold text-slate-800 mb-1">No API keys yet</h3>
              <p className="text-slate-500 text-sm max-w-md mx-auto">
                Create a key so CI pipelines, the GitHub Action or MCP-connected agents can trigger and consume
                regression runs without a human login.
              </p>
            </div>
          ) : (
            <div className="bg-white border border-slate-200 rounded-2xl overflow-hidden shadow-sm">
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader className="bg-slate-50/50">
                    <TableRow className="border-slate-100 hover:bg-transparent">
                      <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12">Name</TableHead>
                      <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12">Key</TableHead>
                      <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12">Scope</TableHead>
                      <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12 w-[100px]">Status</TableHead>
                      <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12 w-[150px]">Last used</TableHead>
                      <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12 w-[150px]">Expires</TableHead>
                      <TableHead className="text-right font-bold text-slate-500 uppercase tracking-widest text-xs h-12 w-[110px]">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {keys.map((key) => {
                      const status = keyStatus(key);
                      return (
                        <TableRow key={key.id} className="border-slate-100 hover:bg-slate-50/50 transition-colors">
                          <TableCell className="py-3">
                            <span className="font-bold text-slate-800 text-sm">{key.name}</span>
                            <div className="text-xs text-slate-400 mt-0.5">created {formatDate(key.created_at)}</div>
                          </TableCell>
                          <TableCell className="py-3">
                            <code className="text-xs font-mono bg-slate-100 text-slate-600 px-2 py-0.5 rounded">
                              {key.prefix}…
                            </code>
                          </TableCell>
                          <TableCell className="py-3">
                            {key.project_id ? (
                              <Badge variant="outline" className="rounded-lg bg-sky-50 text-sky-700 border-sky-200 text-[10px] font-semibold">
                                <Layers className="w-3 h-3 mr-1" />
                                {projectNames.get(key.project_id) || `Project #${key.project_id}`}
                              </Badge>
                            ) : (
                              <Badge variant="outline" className="rounded-lg bg-slate-50 text-slate-600 border-slate-200 text-[10px] font-semibold">
                                <Building2 className="w-3 h-3 mr-1" /> Workspace
                              </Badge>
                            )}
                          </TableCell>
                          <TableCell className="py-3">
                            <Badge variant="outline" className={`rounded-md text-[10px] font-bold uppercase ${STATUS_STYLES[status]}`}>
                              {status}
                            </Badge>
                          </TableCell>
                          <TableCell className="py-3 text-xs text-slate-500 whitespace-nowrap">
                            {key.last_used_at ? formatDate(key.last_used_at) : 'never'}
                          </TableCell>
                          <TableCell className="py-3 text-xs text-slate-500 whitespace-nowrap">
                            {key.expires_at ? formatDate(key.expires_at) : 'never'}
                          </TableCell>
                          <TableCell className="py-3 text-right">
                            {status === 'active' ? (
                              <Button
                                size="sm"
                                variant="outline"
                                disabled={revokeMutation.isPending}
                                onClick={() => {
                                  if (window.confirm(`Revoke API key "${key.name}"? Callers using it will immediately lose access.`)) {
                                    revokeMutation.mutate(key.id);
                                  }
                                }}
                                className="h-8 rounded-lg text-xs text-rose-600 hover:text-rose-700 hover:bg-rose-50 border-rose-200"
                              >
                                <Ban className="w-3.5 h-3.5 mr-1" /> Revoke
                              </Button>
                            ) : (
                              <span className="text-xs text-slate-400">—</span>
                            )}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

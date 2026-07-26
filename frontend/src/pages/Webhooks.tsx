import { useMemo, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Webhook, Plus, RefreshCw, AlertCircle, Check, Copy, Trash2, Building2,
  ShieldAlert, Layers, Info, Power, PowerOff, Radio,
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
import { getWorkspaces, getProjects, Workspace, Project } from '@/lib/api';
import {
  webhooksApi, WorkspaceWebhook, WebhookCreateResponse, WEBHOOK_EVENTS,
} from '@/api/webhooks';

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

const parseEvents = (filter: string | null): string[] =>
  (filter || '').split(',').map((s) => s.trim()).filter(Boolean);

const deliveryTone = (status: number | null): string => {
  if (status === null) return 'bg-slate-50 text-slate-500 border-slate-200';
  if (status >= 200 && status < 300) return 'bg-emerald-50 text-emerald-700 border-emerald-200';
  return 'bg-rose-50 text-rose-700 border-rose-200';
};

// ---------------------------------------------------------------------------
// One-time secret reveal
// ---------------------------------------------------------------------------

function SecretReveal({ created, onDismiss }: { created: WebhookCreateResponse; onDismiss: () => void }) {
  const [copied, setCopied] = useState(false);

  const copySecret = async () => {
    try {
      await navigator.clipboard.writeText(created.secret);
      setCopied(true);
      toast.success('Signing secret copied to clipboard');
      setTimeout(() => setCopied(false), 2500);
    } catch {
      toast.error('Could not copy — select and copy the secret manually');
    }
  };

  return (
    <div className="bg-amber-50/70 border border-amber-200 rounded-2xl px-5 py-4 mb-4">
      <div className="flex items-start gap-3">
        <ShieldAlert className="w-5 h-5 text-amber-500 shrink-0 mt-0.5" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-bold text-amber-800">
            Webhook "{created.name}" created — copy its signing secret now
          </p>
          <p className="text-xs text-amber-700/80 mt-0.5">
            This is the only time the secret is shown. Use it to verify the{' '}
            <code className="font-mono bg-white/70 px-1 rounded">X-TraceIQ-Signature</code> HMAC-SHA256
            header on incoming deliveries. If it's lost, delete the webhook and create a new one.
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
              {copied ? 'Copied' : 'Copy secret'}
            </Button>
          </div>
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

function CreateWebhookForm({
  workspaceId, projects, onCreated, onCancel,
}: {
  workspaceId: number;
  projects: Project[];
  onCreated: (created: WebhookCreateResponse) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState('');
  const [url, setUrl] = useState('');
  const [projectId, setProjectId] = useState<string>(NO_SCOPE);
  const [events, setEvents] = useState<string[]>([]);

  const createMutation = useMutation({
    mutationFn: () =>
      webhooksApi.create(workspaceId, {
        name: name.trim(),
        url: url.trim(),
        workspace_id: workspaceId,
        project_id: projectId === NO_SCOPE ? null : parseInt(projectId),
        event_filter: events.length > 0 ? events.join(',') : null,
      }),
    onSuccess: (created) => onCreated(created),
    onError: (err: unknown) => toast.error(`Failed to create webhook: ${errorDetail(err)}`),
  });

  const toggleEvent = (evt: string) =>
    setEvents((prev) => (prev.includes(evt) ? prev.filter((e) => e !== evt) : [...prev, evt]));

  const handleSubmit = () => {
    if (!name.trim()) {
      toast.error('Webhook name is required');
      return;
    }
    if (!/^https?:\/\/.+/i.test(url.trim())) {
      toast.error('A valid http(s) URL is required');
      return;
    }
    createMutation.mutate();
  };

  return (
    <div className="bg-white border border-indigo-200 rounded-2xl shadow-sm px-5 py-4 mb-4">
      <p className="text-sm font-bold text-slate-800 mb-3">New webhook</p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Name</p>
          <Input
            placeholder="e.g. ci-slack-relay"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="h-9 rounded-lg bg-white border-slate-200"
          />
        </div>
        <div>
          <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Payload URL</p>
          <Input
            placeholder="https://example.com/hooks/traceiq"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            className="h-9 rounded-lg font-mono text-xs bg-white border-slate-200"
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
          <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Events</p>
          <div className="flex flex-wrap gap-1.5 pt-1">
            {WEBHOOK_EVENTS.map((evt) => {
              const on = events.includes(evt);
              return (
                <button
                  key={evt}
                  type="button"
                  onClick={() => toggleEvent(evt)}
                  className={`text-xs font-mono px-2.5 py-1 rounded-lg border transition-colors ${on
                    ? 'bg-indigo-50 text-indigo-700 border-indigo-200'
                    : 'bg-white text-slate-500 border-slate-200 hover:bg-slate-50'}`}
                >
                  {evt}
                </button>
              );
            })}
          </div>
          <p className="text-[11px] text-slate-400 mt-1.5">
            {events.length === 0 ? 'No filter — all run events are delivered.' : `${events.length} event(s) selected.`}
          </p>
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
          Create webhook
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

export default function Webhooks() {
  const queryClient = useQueryClient();
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [createdWebhook, setCreatedWebhook] = useState<WebhookCreateResponse | null>(null);

  const { data: workspaces } = useQuery<Workspace[]>({
    queryKey: ['workspaces'],
    queryFn: getWorkspaces,
  });

  // Default to the first workspace until the user explicitly picks one — derived
  // rather than set via an effect to avoid a cascading render.
  const workspaceId = selectedWorkspaceId ?? workspaces?.[0]?.id ?? null;

  const { data: webhooks, isLoading, error } = useQuery({
    queryKey: ['webhooks', workspaceId],
    queryFn: () => webhooksApi.list(workspaceId!),
    enabled: !!workspaceId,
  });

  const { data: projects } = useQuery<Project[]>({
    queryKey: ['projects', workspaceId],
    queryFn: () => getProjects(workspaceId!),
    enabled: !!workspaceId,
  });

  const projectNames = useMemo(() => {
    const map = new Map<number, string>();
    (projects || []).forEach((p) => map.set(p.id, p.name));
    return map;
  }, [projects]);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['webhooks', workspaceId] });

  const toggleMutation = useMutation({
    mutationFn: ({ id, isActive }: { id: number; isActive: boolean }) =>
      webhooksApi.patch(workspaceId!, id, { is_active: isActive }),
    onSuccess: (_data, vars) => {
      invalidate();
      toast.success(vars.isActive ? 'Webhook enabled' : 'Webhook disabled');
    },
    onError: (err: unknown) => toast.error(`Failed to update webhook: ${errorDetail(err)}`),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => webhooksApi.remove(workspaceId!, id),
    onSuccess: () => {
      invalidate();
      toast.success('Webhook deleted');
    },
    onError: (err: unknown) => toast.error(`Failed to delete webhook: ${errorDetail(err)}`),
  });

  return (
    <div className="max-w-[1200px] mx-auto pb-16">
      <div className="pt-2 pb-6 mb-6 border-b border-slate-200/60">
        <div className="flex items-center text-sm text-slate-400 gap-2 mb-3">
          <span className="font-bold text-slate-700 bg-white px-2.5 py-1 rounded-md border border-slate-200 shadow-sm">Workspace Integrations</span>
        </div>
        <div className="flex flex-col sm:flex-row sm:items-end gap-4">
          <div>
            <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">Webhooks</h1>
            <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
              Register endpoints to receive run events. TraceIQ POSTs a signed JSON payload on each
              matching event, with an HMAC-SHA256 signature in the{' '}
              <code className="font-mono bg-slate-100 px-1.5 py-0.5 rounded text-sm">X-TraceIQ-Signature</code> header.
            </p>
          </div>
          <div className="sm:ml-auto shrink-0">
            <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Workspace</p>
            <Select
              value={workspaceId?.toString() ?? ''}
              onValueChange={(v) => { setSelectedWorkspaceId(parseInt(v)); setCreatedWebhook(null); setCreating(false); }}
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
          <p className="text-slate-500 text-sm">Select a workspace above to manage its webhooks.</p>
        </div>
      ) : (
        <>
          <div className="flex items-center gap-3 mb-4">
            <div className="flex items-start gap-2 text-xs text-slate-500 bg-indigo-50/60 border border-indigo-100 rounded-xl px-3 py-2.5 flex-1">
              <Info className="w-4 h-4 text-indigo-400 shrink-0 mt-px" />
              <p>
                The signing secret is shown <span className="font-semibold">once</span> at creation. Deliveries
                that fail are retried and the failure count is tracked below — disable a webhook to stop delivery
                without deleting it.
              </p>
            </div>
            <Button
              size="sm"
              onClick={() => setCreating((c) => !c)}
              className="bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl h-9 px-4 shrink-0"
            >
              <Plus className="w-4 h-4 mr-1.5" /> New webhook
            </Button>
          </div>

          {createdWebhook && (
            <SecretReveal created={createdWebhook} onDismiss={() => setCreatedWebhook(null)} />
          )}

          {creating && (
            <CreateWebhookForm
              workspaceId={workspaceId}
              projects={projects || []}
              onCreated={(created) => {
                setCreating(false);
                setCreatedWebhook(created);
                invalidate();
              }}
              onCancel={() => setCreating(false)}
            />
          )}

          {isLoading ? (
            <div className="p-12 flex justify-center"><RefreshCw className="animate-spin text-muted-foreground w-6 h-6" /></div>
          ) : error ? (
            <div className="p-10 text-center bg-rose-50 border border-rose-200 rounded-2xl">
              <AlertCircle className="w-8 h-8 text-rose-500 mx-auto mb-3" />
              <p className="text-rose-700 font-semibold">Failed to load webhooks</p>
              <p className="text-rose-600/80 text-sm">{errorDetail(error)}</p>
            </div>
          ) : !webhooks || webhooks.length === 0 ? (
            <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl">
              <Webhook className="w-10 h-10 text-slate-300 mx-auto mb-4" />
              <h3 className="text-lg font-bold text-slate-800 mb-1">No webhooks yet</h3>
              <p className="text-slate-500 text-sm max-w-md mx-auto">
                Register a webhook so external systems — Slack relays, dashboards, or your own services — are
                notified when runs complete, pass or fail.
              </p>
            </div>
          ) : (
            <div className="bg-white border border-slate-200 rounded-2xl overflow-hidden shadow-sm">
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader className="bg-slate-50/50">
                    <TableRow className="border-slate-100 hover:bg-transparent">
                      <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12">Endpoint</TableHead>
                      <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12">Events</TableHead>
                      <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12">Scope</TableHead>
                      <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12 w-[100px]">Status</TableHead>
                      <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12 w-[200px]">Last delivery</TableHead>
                      <TableHead className="text-right font-bold text-slate-500 uppercase tracking-widest text-xs h-12 w-[150px]">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {webhooks.map((hook: WorkspaceWebhook) => {
                      const events = parseEvents(hook.event_filter);
                      return (
                        <TableRow key={hook.id} className="border-slate-100 hover:bg-slate-50/50 transition-colors align-top">
                          <TableCell className="py-3">
                            <span className="font-bold text-slate-800 text-sm">{hook.name}</span>
                            <div className="text-xs text-slate-400 mt-0.5 font-mono break-all max-w-[280px]">{hook.url}</div>
                          </TableCell>
                          <TableCell className="py-3">
                            {events.length === 0 ? (
                              <Badge variant="outline" className="rounded-lg bg-slate-50 text-slate-600 border-slate-200 text-[10px] font-semibold">
                                <Radio className="w-3 h-3 mr-1" /> all events
                              </Badge>
                            ) : (
                              <div className="flex flex-wrap gap-1 max-w-[220px]">
                                {events.map((e) => (
                                  <code key={e} className="text-[10px] bg-indigo-50 text-indigo-700 border border-indigo-100 px-1.5 py-0.5 rounded">{e}</code>
                                ))}
                              </div>
                            )}
                          </TableCell>
                          <TableCell className="py-3">
                            {hook.project_id ? (
                              <Badge variant="outline" className="rounded-lg bg-sky-50 text-sky-700 border-sky-200 text-[10px] font-semibold">
                                <Layers className="w-3 h-3 mr-1" />
                                {projectNames.get(hook.project_id) || `Project #${hook.project_id}`}
                              </Badge>
                            ) : (
                              <Badge variant="outline" className="rounded-lg bg-slate-50 text-slate-600 border-slate-200 text-[10px] font-semibold">
                                <Building2 className="w-3 h-3 mr-1" /> Workspace
                              </Badge>
                            )}
                          </TableCell>
                          <TableCell className="py-3">
                            <Badge variant="outline" className={`rounded-md text-[10px] font-bold uppercase ${hook.is_active
                              ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                              : 'bg-slate-50 text-slate-500 border-slate-200'}`}>
                              {hook.is_active ? 'active' : 'disabled'}
                            </Badge>
                          </TableCell>
                          <TableCell className="py-3">
                            {hook.last_delivery_at ? (
                              <div className="flex items-center gap-2">
                                <Badge variant="outline" className={`rounded-md text-[10px] font-bold ${deliveryTone(hook.last_delivery_status)}`}>
                                  {hook.last_delivery_status ?? '—'}
                                </Badge>
                                <span className="text-xs text-slate-500 whitespace-nowrap">{formatDate(hook.last_delivery_at)}</span>
                              </div>
                            ) : (
                              <span className="text-xs text-slate-400">never delivered</span>
                            )}
                            {hook.failure_count > 0 && (
                              <div className="text-[11px] text-rose-600 mt-0.5">{hook.failure_count} consecutive failure(s)</div>
                            )}
                          </TableCell>
                          <TableCell className="py-3 text-right">
                            <div className="flex justify-end gap-1.5">
                              <Button
                                size="sm"
                                variant="outline"
                                disabled={toggleMutation.isPending}
                                onClick={() => toggleMutation.mutate({ id: hook.id, isActive: !hook.is_active })}
                                className="h-8 rounded-lg text-xs border-slate-200 text-slate-600"
                                title={hook.is_active ? 'Disable delivery' : 'Enable delivery'}
                              >
                                {hook.is_active ? <PowerOff className="w-3.5 h-3.5" /> : <Power className="w-3.5 h-3.5" />}
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                disabled={deleteMutation.isPending}
                                onClick={() => {
                                  if (window.confirm(`Delete webhook "${hook.name}"? Deliveries to this endpoint will stop immediately.`)) {
                                    deleteMutation.mutate(hook.id);
                                  }
                                }}
                                className="h-8 rounded-lg text-xs text-rose-600 hover:text-rose-700 hover:bg-rose-50 border-rose-200"
                              >
                                <Trash2 className="w-3.5 h-3.5" />
                              </Button>
                            </div>
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

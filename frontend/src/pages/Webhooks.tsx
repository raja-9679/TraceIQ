import { useMemo, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Webhook, Plus, RefreshCw, Check, Copy, Trash2, Power, PowerOff,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { toast } from 'sonner';
import { getWorkspaces, getProjects, Workspace, Project } from '@/lib/api';
import {
  webhooksApi, WorkspaceWebhook, WebhookCreateResponse, WEBHOOK_EVENTS,
} from '@/api/webhooks';
import {
  DetailCard, EmptyState, Eyebrow, ListGroup, ListRow, PageHeader, Panel, Pill, SplitView,
} from '@/components/redesign/primitives';
import type { Tone } from '@/components/redesign/kinds';
import { Facts, Field, FormCard, ListSkeleton } from '@/components/redesign-local/groupE/bits';
import { cn } from '@/lib/utils';
import { DeliveryLog } from '@/components/webhooks/DeliveryLog';

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

const deliveryTone = (status: number | null): Tone => {
  if (status === null) return 'neutral';
  if (status >= 200 && status < 300) return 'mint';
  return 'peach';
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
    <section
      aria-label="New webhook signing secret"
      className="flex flex-col gap-3 rounded-3xl border border-border bg-white p-6 shadow-[inset_4px_0_0_#F2C94C]"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <Pill tone="butter" className="self-start">Shown once</Pill>
          <h2 className="text-base font-semibold text-ink">Copy the signing secret for “{created.name}” now</h2>
          <p className="max-w-[64ch] text-[13px] leading-relaxed text-ink-soft">
            This is the only time the secret is shown. Use it to verify the{' '}
            <code className="rounded bg-background px-1 font-mono">X-TraceIQ-Signature</code> HMAC-SHA256
            header on incoming deliveries. If it's lost, delete the webhook and create a new one.
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
          {copied ? 'Copied' : 'Copy secret'}
        </Button>
      </div>
    </section>
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
    <FormCard title="New webhook" subtitle="TraceIQ POSTs a signed JSON payload to this URL on each matching run event.">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field id="hook-name" label="Name">
          <Input
            id="hook-name"
            placeholder="e.g. ci-slack-relay"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="h-10 rounded-full px-4"
          />
        </Field>
        <Field id="hook-url" label="Payload URL">
          <Input
            id="hook-url"
            placeholder="https://example.com/hooks/traceiq"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            className="h-10 rounded-full px-4 font-mono text-xs"
          />
        </Field>
        <Field id="hook-project" label="Project scope">
          <Select value={projectId} onValueChange={setProjectId}>
            <SelectTrigger id="hook-project" className="h-10 rounded-full px-4">
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
        <fieldset className="flex min-w-0 flex-col gap-1.5">
          <legend className="mb-1.5 text-[13px] font-semibold text-ink">Events</legend>
          <div className="flex flex-wrap gap-1.5">
            {WEBHOOK_EVENTS.map((evt) => {
              const on = events.includes(evt);
              return (
                <button
                  key={evt}
                  type="button"
                  aria-pressed={on}
                  onClick={() => toggleEvent(evt)}
                  className={cn(
                    'min-h-10 rounded-full border px-3.5 font-mono text-xs transition-colors',
                    on ? 'border-transparent bg-pastel-lavender font-semibold text-pastel-ink-lavender' : 'border-border bg-white text-ink-soft hover:bg-background'
                  )}
                >
                  {evt}
                </button>
              );
            })}
          </div>
          <p className="text-xs text-ink-soft">
            {events.length === 0 ? 'No filter — all run events are delivered.' : `${events.length} event(s) selected.`}
            {' '}<span className="font-mono">run.completed</span> also covers errored, cancelled and timed-out runs.
          </p>
        </fieldset>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button disabled={createMutation.isPending} onClick={handleSubmit}>
          {createMutation.isPending ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
          Create webhook
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

export default function Webhooks() {
  const queryClient = useQueryClient();
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [createdWebhook, setCreatedWebhook] = useState<WebhookCreateResponse | null>(null);
  const [selectedHookId, setSelectedHookId] = useState<number | null>(null);

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

  const all: WorkspaceWebhook[] = webhooks ?? [];
  const groups = [
    { name: 'Failing', items: all.filter((h) => h.is_active && h.failure_count > 0) },
    { name: 'Active', items: all.filter((h) => h.is_active && h.failure_count === 0) },
    { name: 'Disabled', items: all.filter((h) => !h.is_active) },
  ].filter((g) => g.items.length > 0);
  const selected = all.find((h) => h.id === selectedHookId) ?? groups[0]?.items[0] ?? null;
  const scopeLabel = (h: WorkspaceWebhook) =>
    h.project_id ? (projectNames.get(h.project_id) || `Project #${h.project_id}`) : 'Entire workspace';

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Webhooks"
        subtitle={
          webhooks
            ? `${all.filter((h) => h.is_active).length} active of ${all.length} · signed run events sent to your endpoints`
            : 'Signed run events sent to your endpoints.'
        }
        actions={
          <>
            {(workspaces?.length ?? 0) > 1 && (
              <Select
                value={workspaceId?.toString() ?? ''}
                onValueChange={(v) => { setSelectedWorkspaceId(parseInt(v)); setCreatedWebhook(null); setCreating(false); setSelectedHookId(null); }}
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
                <Plus className="h-4 w-4" /> New webhook
              </Button>
            )}
          </>
        }
      />

      {!workspaceId ? (
        <EmptyState title="No workspace selected">Select a workspace to manage its webhooks.</EmptyState>
      ) : (
        <>
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
                setSelectedHookId(created.id);
                invalidate();
              }}
              onCancel={() => setCreating(false)}
            />
          )}

          {isLoading ? (
            <ListSkeleton />
          ) : error ? (
            (error as { response?: { status?: number } }).response?.status === 403
              ? <EmptyState title="Workspace admins only">
                  Webhooks receive every run of this workspace, and their URLs often carry credentials,
                  so only workspace admins can see and manage them.
                </EmptyState>
              : <EmptyState title="Couldn't load webhooks">{errorDetail(error)}</EmptyState>
          ) : all.length === 0 ? (
            <EmptyState title="No webhooks yet">
              Register a webhook so external systems — Slack relays, dashboards, or your own services — are
              notified when runs complete, pass or fail.
            </EmptyState>
          ) : (
            <SplitView
              list={
                <>
                  {groups.map((g) => (
                    <ListGroup key={g.name} title={g.name} count={g.items.length}>
                      {g.items.map((hook) => (
                        <ListRow
                          key={hook.id}
                          selected={selected?.id === hook.id}
                          onSelect={() => setSelectedHookId(hook.id)}
                          leading={
                            <span
                              aria-hidden="true"
                              className={cn(
                                'flex h-8 w-8 flex-none items-center justify-center rounded-[10px]',
                                !hook.is_active ? 'bg-secondary text-ink-muted'
                                  : hook.failure_count > 0 ? 'bg-pastel-peach text-pastel-ink-peach'
                                    : 'bg-pastel-mint text-pastel-ink-mint'
                              )}
                            >
                              <Webhook className="h-[15px] w-[15px]" strokeWidth={2.2} />
                            </span>
                          }
                          title={hook.name}
                          meta={<span className="block max-w-[260px] truncate font-mono">{hook.url}</span>}
                          trailing={
                            hook.last_delivery_at
                              ? <Pill tone={deliveryTone(hook.last_delivery_status)}>{hook.last_delivery_status ?? '—'}</Pill>
                              : undefined
                          }
                        />
                      ))}
                    </ListGroup>
                  ))}
                  <p className="px-1 text-xs leading-relaxed text-ink-soft">
                    The signing secret is shown once at creation. Failed deliveries are retried (1, 5 and 30 minutes)
                    and logged — disable a webhook to stop delivery without deleting it.
                  </p>
                </>
              }
              detail={selected && (() => {
                const events = parseEvents(selected.event_filter);
                return (
                  <DetailCard label="Selected webhook">
                    <div className="flex flex-col gap-2">
                      <div className="flex flex-wrap gap-2">
                        <Pill tone={selected.is_active ? 'mint' : 'neutral'}>{selected.is_active ? 'Active' : 'Disabled'}</Pill>
                        {selected.failure_count > 0 && (
                          <Pill tone="peach">{selected.failure_count} consecutive failure(s)</Pill>
                        )}
                      </div>
                      <h2 className="break-words text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{selected.name}</h2>
                      <span className="break-all font-mono text-xs text-ink-soft">{selected.url}</span>
                    </div>

                    <Facts
                      items={[
                        { label: 'Scope', value: scopeLabel(selected) },
                        {
                          label: 'Last delivery',
                          value: selected.last_delivery_at
                            ? <span className="flex flex-wrap items-center gap-2"><Pill tone={deliveryTone(selected.last_delivery_status)}>{selected.last_delivery_status ?? '—'}</Pill>{formatDate(selected.last_delivery_at)}</span>
                            : 'Never delivered',
                        },
                        { label: 'Created', value: formatDate(selected.created_at) },
                      ]}
                    />

                    <div className="flex flex-col gap-2">
                      <Eyebrow>Events</Eyebrow>
                      <div className="flex flex-wrap gap-1.5">
                        {events.length === 0
                          ? <Pill tone="neutral">All events</Pill>
                          : events.map((e) => <Pill key={e} tone="lavender" className="font-mono">{e}</Pill>)}
                      </div>
                    </div>

                    <Panel title="Verifying deliveries" callout>
                      Each POST carries an HMAC-SHA256 signature of the body in the{' '}
                      <code className="font-mono text-sm">X-TraceIQ-Signature</code> header, computed with the signing secret shown at creation.
                      {' '}<code className="font-mono text-sm">X-TraceIQ-Delivery</code> stays the same across retries — use it to drop duplicates.
                    </Panel>

                    <DeliveryLog workspaceId={workspaceId} webhookId={selected.id} />

                    <div className="flex flex-wrap gap-2.5">
                      <Button
                        size="lg"
                        variant="outline"
                        disabled={toggleMutation.isPending}
                        onClick={() => toggleMutation.mutate({ id: selected.id, isActive: !selected.is_active })}
                      >
                        {selected.is_active ? <PowerOff className="h-4 w-4" /> : <Power className="h-4 w-4" />}
                        {selected.is_active ? 'Disable delivery' : 'Enable delivery'}
                      </Button>
                      <Button
                        size="lg"
                        variant="outline"
                        disabled={deleteMutation.isPending}
                        onClick={() => {
                          if (window.confirm(`Delete webhook "${selected.name}"? Deliveries to this endpoint will stop immediately.`)) {
                            deleteMutation.mutate(selected.id);
                          }
                        }}
                        className="text-pastel-ink-peach"
                      >
                        <Trash2 className="h-4 w-4" /> Delete webhook
                      </Button>
                    </div>
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

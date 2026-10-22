import { useState, useMemo } from 'react';
import { useDebounce, cn } from '@/lib/utils';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Calendar, PauseCircle, PlayCircle, Trash2, Pen, FolderTree, FileText, Search, Plus,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { toast } from 'sonner';
import { schedulesApi, TestSchedule } from '@/api/schedules';
import { ScheduleModal } from '@/components/ScheduleModal';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { useProjectAccess } from '@/hooks/useProjectAccess';
import {
  DetailCard, EmptyState, ListGroup, ListRow, PageHeader, Panel, Pill, SplitView,
} from '@/components/redesign/primitives';
import { toneClasses } from '@/components/redesign/kinds';
import { Segmented, SkeletonRows } from '@/components/redesign-local/groupB/bits';
import { formatScheduleTime } from '@/lib/schedules';

// Small icon chip for a schedule: lavender while it is running on its timer,
// neutral while paused. Suite-level schedules get a folder, single cases a page.
function ScheduleChip({ schedule }: { schedule: TestSchedule }) {
  const Icon = schedule.test_case_id ? FileText : FolderTree;
  return (
    <span
      className={cn('flex h-7 w-7 flex-none items-center justify-center rounded-[9px]', toneClasses[schedule.is_active ? 'lavender' : 'neutral'].chip)}
      aria-hidden="true"
    >
      <Icon className="h-[15px] w-[15px]" strokeWidth={2.2} />
    </span>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1 rounded-2xl bg-background p-4">
      <span className="text-xs text-ink-soft">{label}</span>
      <span className="text-[15px] font-semibold text-ink">{children}</span>
    </div>
  );
}

export default function Schedules() {
  const queryClient = useQueryClient();
  const projectId = useActiveProjectId();

  const [editTarget, setEditTarget] = useState<TestSchedule | null>(null);
  const [creating, setCreating] = useState(false);
  // Create/update/delete all check project:create_suite (project or
  // workspace admins); editors and viewers only read this page.
  const canManage = useProjectAccess(projectId).can('project:create_suite');
  const [viewMode, setViewMode] = useState<'card' | 'list'>('list');
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const debouncedSearch = useDebounce(searchQuery, 300);

  const { data: schedules, isLoading, error } = useQuery({
    queryKey: ['schedules', projectId],
    queryFn: () => schedulesApi.list(projectId || undefined),
    enabled: !!projectId
  });

  const toggleMutation = useMutation({
    mutationFn: ({ id, is_active }: { id: number, is_active: boolean }) =>
      schedulesApi.update(id, { is_active }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] });
      toast.success('Schedule status updated');
    },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    onError: (err: any) => toast.error(`Failed to update schedule: ${err.message}`)
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => schedulesApi.delete(id),
    onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: ['schedules'] });
        toast.success('Schedule deleted successfully');
    },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    onError: (err: any) => toast.error(`Failed to delete schedule: ${err.message}`)
  });

  const handleToggle = (schedule: TestSchedule) => {
    toggleMutation.mutate({ id: schedule.id, is_active: !schedule.is_active });
  };

  const handleDelete = (id: number) => {
    if (confirm('Are you sure you want to delete this schedule?')) {
        deleteMutation.mutate(id);
    }
  };

  const formatDate = formatScheduleTime;

  const filteredSchedules: TestSchedule[] = useMemo(() => {
    if (!schedules) return [];
    if (!debouncedSearch.trim()) return schedules;

    const lowerQuery = debouncedSearch.toLowerCase();
    return schedules.filter((schedule: TestSchedule) =>
      schedule.name.toLowerCase().includes(lowerQuery) ||
      (schedule.description && schedule.description.toLowerCase().includes(lowerQuery)) ||
      schedule.cron_expression.toLowerCase().includes(lowerQuery)
    );
  }, [schedules, debouncedSearch]);

  const total: number = schedules?.length ?? 0;
  const activeCount: number = (schedules ?? []).filter((s: TestSchedule) => s.is_active).length;

  const groups = [
    { name: 'Active', items: filteredSchedules.filter((s) => s.is_active) },
    { name: 'Paused', items: filteredSchedules.filter((s) => !s.is_active) },
  ].filter((g) => g.items.length > 0);
  const selected = filteredSchedules.find((s) => s.id === selectedId) ?? groups[0]?.items[0] ?? null;

  const target = (s: TestSchedule) => (s.test_case_id ? `Test case #${s.test_case_id}` : `Module #${s.test_suite_id}`);
  const env = (s: TestSchedule) => [s.browser, s.device].filter(Boolean).join(' · ');

  const actions = (s: TestSchedule, size: 'default' | 'lg' = 'default') => !canManage ? null : (
    <>
      <Button size={size} variant={s.is_active ? 'outline' : 'default'} onClick={() => handleToggle(s)}>
        {s.is_active ? <><PauseCircle className="h-4 w-4" /> Pause</> : <><PlayCircle className="h-4 w-4" /> Resume</>}
      </Button>
      <Button size={size} variant="outline" onClick={() => setEditTarget(s)}>
        <Pen className="h-4 w-4" /> Edit
      </Button>
      <Button
        size={size}
        variant="outline"
        onClick={() => handleDelete(s.id)}
        aria-label={`Delete schedule ${s.name}`}
      >
        <Trash2 className="h-4 w-4" /> Delete
      </Button>
    </>
  );

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Schedules"
        subtitle={
          !projectId ? 'Pick a project in the sidebar.'
            : schedules ? `${total} schedule${total === 1 ? '' : 's'} · ${activeCount} active · times shown in your timezone`
              : 'Tests that run on their own, on a timer.'
        }
        actions={projectId && (total > 0 || canManage) ? (
          <>
            {canManage && (
              <Button onClick={() => setCreating(true)}><Plus className="h-4 w-4" /> New schedule</Button>
            )}
            {total > 0 && <>
            <label className="relative block w-full sm:w-64">
              <span className="sr-only">Search schedules</span>
              <Search className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-soft" aria-hidden="true" />
              <input
                type="text"
                placeholder="Search schedules"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="h-10 w-full rounded-full border border-border bg-white pl-10 pr-4 text-sm text-ink placeholder:text-ink-muted focus:outline-none focus:ring-2 focus:ring-ring/30"
              />
            </label>
            <Segmented
              label="Layout"
              value={viewMode}
              onChange={setViewMode}
              options={[{ id: 'list', label: 'List' }, { id: 'card', label: 'Cards' }]}
            />
            </>}
          </>
        ) : undefined}
      />

      {!projectId ? (
        <EmptyState title="Pick a project">Choose a project in the sidebar to view and manage its schedules.</EmptyState>
      ) : isLoading ? (
        <SkeletonRows count={4} className="max-w-[440px]" />
      ) : error ? (
        <EmptyState title="Couldn't load schedules">{(error as Error).message}</EmptyState>
      ) : total === 0 ? (
        <EmptyState title="No schedules yet">
          {canManage
            ? 'Schedules run tests automatically. Use New schedule, or open any suite or test case and pick Schedule.'
            : 'Schedules run tests automatically. A project admin can create them.'}
        </EmptyState>
      ) : filteredSchedules.length === 0 ? (
        <EmptyState title="No matches">No schedules matched "{searchQuery}".</EmptyState>
      ) : viewMode === 'card' ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {filteredSchedules.map((schedule) => (
            <article key={schedule.id} className="flex flex-col gap-4 rounded-3xl border border-border bg-white p-6">
              <div className="flex items-start gap-3">
                <ScheduleChip schedule={schedule} />
                <div className="flex min-w-0 flex-1 flex-col gap-1">
                  <div className="flex items-start justify-between gap-3">
                    <h2 className={cn('truncate text-base font-semibold', schedule.is_active ? 'text-ink' : 'text-ink-soft')}>{schedule.name}</h2>
                    <span className="flex flex-none gap-1.5">
                      {schedule.is_monitor && <Pill tone="lavender">Monitor</Pill>}
                      <Pill tone={schedule.is_active ? 'mint' : 'neutral'}>{schedule.is_active ? 'Active' : 'Paused'}</Pill>
                    </span>
                  </div>
                  <p className="line-clamp-2 text-sm text-ink-soft">
                    {schedule.description || <span className="italic">No description</span>}
                  </p>
                </div>
              </div>
              <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
                <div><dt className="text-xs text-ink-soft">Next run</dt><dd className="font-semibold text-ink">{schedule.is_active ? formatDate(schedule.next_run_at) : 'Paused'}</dd></div>
                <div><dt className="text-xs text-ink-soft">Last run</dt><dd className="font-semibold text-ink">{formatDate(schedule.last_run_at)}</dd></div>
                <div><dt className="text-xs text-ink-soft">Runs</dt><dd className="font-semibold text-ink">{target(schedule)}</dd></div>
                <div><dt className="text-xs text-ink-soft">Browser</dt><dd className="font-semibold text-ink">{env(schedule)}</dd></div>
              </dl>
              <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[#F0EEE8] pt-4">
                <span className="rounded-full bg-secondary px-2.5 py-0.5 font-mono text-xs text-ink-soft">{schedule.cron_expression} · {schedule.timezone || 'UTC'}</span>
                <div className="flex flex-wrap gap-2">{actions(schedule)}</div>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <SplitView
          list={groups.map((g) => (
            <ListGroup key={g.name} title={g.name} count={g.items.length}>
              {g.items.map((s) => (
                <ListRow
                  key={s.id}
                  selected={selected?.id === s.id}
                  onSelect={() => setSelectedId(s.id)}
                  leading={<ScheduleChip schedule={s} />}
                  title={s.name}
                  meta={s.is_active ? `Next ${formatDate(s.next_run_at)} · ${target(s)}` : `Paused · ${target(s)}`}
                  trailing={<span className="font-mono text-xs text-ink-soft">{s.cron_expression} · {s.timezone || 'UTC'}</span>}
                />
              ))}
            </ListGroup>
          ))}
          detail={selected && (
            <DetailCard label="Selected schedule">
              <div className="flex flex-col gap-2">
                <span className="flex gap-1.5 self-start">
                  <Pill tone={selected.is_active ? 'mint' : 'neutral'}>{selected.is_active ? 'Active' : 'Paused'}</Pill>
                  {selected.is_monitor && <Pill tone="lavender">Monitor</Pill>}
                </span>
                <h2 className="text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{selected.name}</h2>
                <span className="text-sm text-ink-soft">{selected.description || 'No description'}</span>
              </div>

              <div className="grid grid-cols-[repeat(auto-fit,minmax(160px,1fr))] gap-3">
                <Fact label="Next run">{selected.is_active ? formatDate(selected.next_run_at) : 'Paused'}</Fact>
                <Fact label="Last run">{formatDate(selected.last_run_at)}</Fact>
                <Fact label="Runs">{target(selected)}</Fact>
                <Fact label="Browser and device">{env(selected)}</Fact>
              </div>

              <Panel title="Timing" right={<span className="font-mono">{selected.cron_expression} · {selected.timezone || 'UTC'}</span>}>
                <span className="flex items-center gap-2">
                  <Calendar className="h-4 w-4 flex-none text-ink-soft" aria-hidden="true" />
                  {selected.is_active
                    ? `Runs on this cron schedule in ${selected.timezone || 'UTC'} time; daylight-saving changes are followed. Times shown here are in your own timezone. Pause it to stop new runs without losing the settings.`
                    : 'Paused. Nothing runs until you resume it; the schedule and its settings are kept.'}
                </span>
              </Panel>

              <div className="flex flex-wrap gap-2.5">{actions(selected, 'lg')}</div>
            </DetailCard>
          )}
        />
      )}

      {creating && projectId && (
        <ScheduleModal
          isOpen={creating}
          onClose={() => setCreating(false)}
          projectId={projectId}
          targetName=""
        />
      )}

      {editTarget && projectId && (
        <ScheduleModal
          isOpen={!!editTarget}
          onClose={() => setEditTarget(null)}
          projectId={projectId}
          targetName={editTarget.name}
          scheduleToEdit={editTarget}
        />
      )}
    </div>
  );
}

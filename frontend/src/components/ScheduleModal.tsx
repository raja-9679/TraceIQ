import { useState, useEffect, useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { schedulesApi, TestSchedule } from '@/api/schedules';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { getTestSuites } from '@/lib/api';
import { toast } from 'sonner';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { Loader2 } from 'lucide-react';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { parseRegions } from '@/lib/fleet';
import { browserTimezone, listTimezones } from '@/lib/schedules';

interface ScheduleModalProps {
  isOpen: boolean;
  onClose: () => void;
  projectId: number;
  /** With neither id (and no schedule to edit) the modal asks which suite to run. */
  testSuiteId?: number;
  testCaseId?: number;
  targetName: string;
  scheduleToEdit?: TestSchedule;
  /** Open with "Run as a monitor" already ticked (the Watch page's New monitor). */
  monitor?: boolean;
}

/** "a@x.com, b@y.com" -> ["a@x.com", "b@y.com"]; null when empty. */
function parseEmails(raw: string): string[] | null {
  const list = raw.split(/[\s,;]+/).map((e) => e.trim()).filter(Boolean);
  return list.length ? Array.from(new Set(list)) : null;
}

const COMMON_CRON_EXPRESSIONS = [
  { label: 'Every hour (0 * * * *)', value: '0 * * * *' },
  { label: 'Every day at midnight (0 0 * * *)', value: '0 0 * * *' },
  { label: 'Every Monday at 9AM (0 9 * * 1)', value: '0 9 * * 1' },
  { label: 'Every Friday at 5PM (0 17 * * 5)', value: '0 17 * * 5' },
  { label: 'Custom', value: 'custom' },
];

export function ScheduleModal({ isOpen, onClose, projectId, testSuiteId, testCaseId, targetName, scheduleToEdit, monitor = false }: ScheduleModalProps) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(`Schedule for ${targetName}`);
  const [description, setDescription] = useState('');
  const [cronExpression, setCronExpression] = useState('0 0 * * *');
  const [customCron, setCustomCron] = useState('');
  const [isCustom, setIsCustom] = useState(false);
  const [browser, setBrowser] = useState('chromium');
  const [device, setDevice] = useState('Desktop');
  const [regions, setRegions] = useState('');
  const [timezone, setTimezone] = useState(browserTimezone);
  const [isMonitor, setIsMonitor] = useState(monitor);
  const [alertAfter, setAlertAfter] = useState('1');
  const [alertOnRecovery, setAlertOnRecovery] = useState(true);
  const [alertEmails, setAlertEmails] = useState('');
  const [pickedSuiteId, setPickedSuiteId] = useState('');
  const timezones = useMemo(listTimezones, []);

  // Opened without a target (Watch -> New monitor, Schedules -> New schedule):
  // the person picks the suite here instead of going to the suite page first.
  const needsTarget = !scheduleToEdit && !testSuiteId && !testCaseId;
  const suites = useQuery({
    queryKey: ['schedule-modal-suites', projectId],
    queryFn: () => getTestSuites(projectId),
    enabled: isOpen && needsTarget,
  });

  useEffect(() => {
    if (scheduleToEdit && isOpen) {
      setName(scheduleToEdit.name);
      setDescription(scheduleToEdit.description || '');
      
      const isCommon = COMMON_CRON_EXPRESSIONS.some(c => c.value === scheduleToEdit.cron_expression);
      if (isCommon) {
        setIsCustom(false);
        setCronExpression(scheduleToEdit.cron_expression);
      } else {
        setIsCustom(true);
        setCustomCron(scheduleToEdit.cron_expression);
        setCronExpression('custom');
      }
      
      setBrowser(scheduleToEdit.browser);
      setDevice(scheduleToEdit.device || 'Desktop');
      setRegions((scheduleToEdit.regions || []).join(', '));
      setTimezone(scheduleToEdit.timezone || 'UTC');
      setIsMonitor(!!scheduleToEdit.is_monitor);
      setAlertAfter(String(scheduleToEdit.alert_after_failures ?? 1));
      setAlertOnRecovery(scheduleToEdit.alert_on_recovery ?? true);
      setAlertEmails((scheduleToEdit.alert_emails || []).join(', '));
    } else if (isOpen) {
      setName(`Schedule for ${targetName}`);
      setDescription('');
      setCronExpression('0 0 * * *');
      setIsCustom(false);
      setCustomCron('');
      setBrowser('chromium');
      setDevice('Desktop');
      setRegions('');
      setTimezone(browserTimezone());
      setIsMonitor(monitor);
      setAlertAfter('1');
      setAlertOnRecovery(true);
      setAlertEmails('');
      setPickedSuiteId('');
    }
  }, [scheduleToEdit, isOpen, targetName, monitor]);

  const createMutation = useMutation({
    mutationFn: (data: any) => schedulesApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] });
      queryClient.invalidateQueries({ queryKey: ['monitors'] });
      toast.success(isMonitor ? 'Monitor created' : 'Schedule created successfully');
      onClose();
    },
    onError: (err: any) => {
      toast.error(err?.response?.data?.detail || 'Failed to create schedule');
    }
  });

  const updateMutation = useMutation({
    mutationFn: (data: any) => schedulesApi.update(scheduleToEdit!.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] });
      queryClient.invalidateQueries({ queryKey: ['monitors'] });
      toast.success('Schedule updated successfully');
      onClose();
    },
    onError: (err: any) => {
      toast.error(err?.response?.data?.detail || 'Failed to update schedule');
    }
  });

  const handleCronChange = (val: string) => {
    if (val === 'custom') {
      setIsCustom(true);
      setCronExpression(customCron || '* * * * *');
    } else {
      setIsCustom(false);
      setCronExpression(val);
    }
  };

  const handleSave = () => {
    if (!name.trim()) {
      toast.error("Please provide a name for the schedule.");
      return;
    }
    
    const finalCron = isCustom ? customCron : cronExpression;
    if (!finalCron.trim()) {
      toast.error("Please provide a valid cron expression.");
      return;
    }

    const tz = timezone.trim() || 'UTC';
    if (!timezones.includes(tz)) {
      toast.error(`Unknown timezone "${tz}". Pick one from the list, e.g. Asia/Kolkata.`);
      return;
    }

    const threshold = parseInt(alertAfter, 10);
    if (isMonitor && (!Number.isFinite(threshold) || threshold < 1)) {
      toast.error('Alert after must be 1 or more failed checks.');
      return;
    }
    if (needsTarget && !pickedSuiteId) {
      toast.error('Pick the suite this schedule runs.');
      return;
    }

    const payload = {
      name,
      description,
      project_id: projectId,
      browser,
      device: device === 'Desktop' ? null : device,
      cron_expression: finalCron,
      timezone: tz,
      regions: parseRegions(regions),
      is_monitor: isMonitor,
      // Alert settings only mean something on a monitor; a plain schedule
      // keeps whatever it had so turning monitor mode back on restores them.
      ...(isMonitor ? {
        alert_after_failures: threshold,
        alert_on_recovery: alertOnRecovery,
        alert_emails: parseEmails(alertEmails),
      } : {}),
    };

    if (scheduleToEdit) {
      updateMutation.mutate(payload);
    } else {
      createMutation.mutate({
        ...payload,
        test_suite_id: needsTarget ? Number(pickedSuiteId) : testSuiteId,
        test_case_id: testCaseId,
        is_active: true
      });
    }
  };

  const isPending = createMutation.isPending || updateMutation.isPending;

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto rounded-3xl p-6 sm:max-w-[460px] sm:p-7">
        <DialogHeader>
          <DialogTitle className="text-xl font-bold text-ink">
            {scheduleToEdit ? 'Edit schedule' : isMonitor ? 'New monitor' : 'Schedule a test run'}
          </DialogTitle>
          <DialogDescription className="text-ink-soft">
            {scheduleToEdit ? 'Change when and where this schedule runs.'
              : needsTarget ? (isMonitor ? 'Run a suite on a timer as a production health check, with uptime and failure alerts.' : 'Run a suite automatically on a timer.')
                : <>Run <span className="font-semibold text-ink">{targetName}</span> automatically on a timer.</>}
          </DialogDescription>
        </DialogHeader>
        
        <div className="grid gap-4 py-4">
          {needsTarget && (
            <div className="grid gap-2">
              <Label htmlFor="schedule-target">Suite to run</Label>
              <Select value={pickedSuiteId} onValueChange={(v) => {
                setPickedSuiteId(v);
                const picked = (suites.data || []).find((x) => String(x.id) === v);
                if (picked) setName(`${isMonitor ? 'Monitor' : 'Schedule'} for ${picked.name}`);
              }}>
                <SelectTrigger id="schedule-target" className="h-10 rounded-full">
                  <SelectValue placeholder={suites.isLoading ? 'Loading suites…' : 'Choose a suite'} />
                </SelectTrigger>
                <SelectContent>
                  {(suites.data || []).map((x) => (
                    <SelectItem key={x.id} value={String(x.id)}>{x.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {suites.data && suites.data.length === 0 && (
                <p className="text-xs text-ink-soft">This project has no suites yet. Create one under Tests first.</p>
              )}
            </div>
          )}

          <div className="grid gap-2">
            <Label htmlFor="name">Name</Label>
            <Input 
              id="name" 
              className="h-10 rounded-full"
              value={name} 
              onChange={e => setName(e.target.value)} 
              placeholder="E.g., Nightly Regression" 
            />
          </div>
          
          <div className="grid gap-2">
            <Label htmlFor="description">Description (optional)</Label>
            <Input 
              id="description" 
              className="h-10 rounded-full"
              value={description} 
              onChange={e => setDescription(e.target.value)} 
              placeholder="Run every night to check critical flows" 
            />
          </div>
          
          <div className="grid gap-2">
            <Label htmlFor="schedule-frequency">How often</Label>
            <Select 
              value={isCustom ? 'custom' : cronExpression} 
              onValueChange={handleCronChange}
            >
              <SelectTrigger id="schedule-frequency" className="h-10 rounded-full">
                <SelectValue placeholder="Select frequency" />
              </SelectTrigger>
              <SelectContent>
                {COMMON_CRON_EXPRESSIONS.map(option => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            
            {isCustom && (
              <Input 
                className="mt-2 h-10 rounded-full font-mono"
                aria-label="Custom cron expression"
                placeholder="* * * * *" 
                value={customCron} 
                onChange={e => {
                  setCustomCron(e.target.value);
                  setCronExpression(e.target.value);
                }} 
              />
            )}
          </div>

          <div className="grid gap-2">
            <Label htmlFor="schedule-timezone">Timezone</Label>
            <Input
              id="schedule-timezone"
              className="h-10 rounded-full"
              list="schedule-timezone-options"
              value={timezone}
              onChange={e => setTimezone(e.target.value)}
              placeholder="e.g. Asia/Kolkata"
            />
            <datalist id="schedule-timezone-options">
              {timezones.map(z => <option key={z} value={z} />)}
            </datalist>
            <p className="text-xs text-ink-soft">
              The frequency above is read in this timezone; daylight-saving changes are followed.
            </p>
          </div>
          
          <div className="grid grid-cols-2 gap-4">
            <div className="grid gap-2">
              <Label htmlFor="schedule-browser">Browser</Label>
              <Select value={browser} onValueChange={setBrowser}>
                <SelectTrigger id="schedule-browser" className="h-10 rounded-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="chromium">Chromium</SelectItem>
                  <SelectItem value="firefox">Firefox</SelectItem>
                  <SelectItem value="webkit">WebKit</SelectItem>
                </SelectContent>
              </Select>
            </div>
            
            <div className="grid gap-2">
              <Label htmlFor="schedule-device">Device</Label>
              <Select value={device} onValueChange={setDevice}>
                <SelectTrigger id="schedule-device" className="h-10 rounded-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="Desktop">Desktop</SelectItem>
                  <SelectItem value="Mobile (Generic)">Mobile (Generic)</SelectItem>
                  <SelectItem value="iPhone 13">iPhone 13</SelectItem>
                  <SelectItem value="Pixel 5">Pixel 5</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="grid gap-3 rounded-2xl border border-border bg-white p-4">
            <div className="flex items-start gap-3">
              <Checkbox id="schedule-monitor" className="mt-0.5" checked={isMonitor} onCheckedChange={(v) => setIsMonitor(v === true)} />
              <div className="grid gap-1">
                <Label htmlFor="schedule-monitor">Run as a monitor</Label>
                <p className="text-xs text-ink-soft">
                  Each run becomes a production health check on the Watch page: uptime, failure streaks, and down/recovery alerts to the project's Slack/Teams and the emails below.
                </p>
              </div>
            </div>
            {isMonitor && (
              <>
                <div className="grid gap-2">
                  <Label htmlFor="schedule-alert-after">Alert after this many failed checks in a row</Label>
                  <Input
                    id="schedule-alert-after"
                    type="number"
                    min={1}
                    className="h-10 w-28 rounded-full"
                    value={alertAfter}
                    onChange={e => setAlertAfter(e.target.value)}
                  />
                  <p className="text-xs text-ink-soft">1 alerts on the first failure; higher numbers ignore single blips.</p>
                </div>
                <div className="flex items-center gap-3">
                  <Checkbox id="schedule-alert-recovery" checked={alertOnRecovery} onCheckedChange={(v) => setAlertOnRecovery(v === true)} />
                  <Label htmlFor="schedule-alert-recovery">Send a recovery alert when it passes again</Label>
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="schedule-alert-emails">Alert emails (optional)</Label>
                  <Input
                    id="schedule-alert-emails"
                    className="h-10 rounded-full"
                    value={alertEmails}
                    onChange={e => setAlertEmails(e.target.value)}
                    placeholder="oncall@example.com, team@example.com"
                  />
                </div>
              </>
            )}
          </div>

          <div className="grid gap-2">
            <Label htmlFor="schedule-regions">Regions (Optional)</Label>
            <Input
              id="schedule-regions"
              value={regions}
              onChange={e => setRegions(e.target.value)}
              placeholder="e.g. eu-west, us-east"
            />
            <p className="text-xs text-muted-foreground">
              One run per region per cycle; needs workers started with WORKER_REGION. Leave blank for the shared worker pool.
            </p>
          </div>
        </div>
        
        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={onClose} disabled={isPending}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={isPending}>
            {isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
            {scheduleToEdit ? 'Save changes' : isMonitor ? 'Create monitor' : 'Create schedule'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

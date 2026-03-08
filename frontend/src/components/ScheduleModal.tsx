import { useState, useEffect } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { schedulesApi, TestSchedule } from '@/api/schedules';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
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

interface ScheduleModalProps {
  isOpen: boolean;
  onClose: () => void;
  projectId: number;
  testSuiteId?: number;
  testCaseId?: number;
  targetName: string;
  scheduleToEdit?: TestSchedule;
}

const COMMON_CRON_EXPRESSIONS = [
  { label: 'Every hour (* * * * *)', value: '0 * * * *' },
  { label: 'Every day at midnight (0 0 * * *)', value: '0 0 * * *' },
  { label: 'Every Monday at 9AM (0 9 * * 1)', value: '0 9 * * 1' },
  { label: 'Every Friday at 5PM (0 17 * * 5)', value: '0 17 * * 5' },
  { label: 'Custom', value: 'custom' },
];

export function ScheduleModal({ isOpen, onClose, projectId, testSuiteId, testCaseId, targetName, scheduleToEdit }: ScheduleModalProps) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(`Schedule for ${targetName}`);
  const [description, setDescription] = useState('');
  const [cronExpression, setCronExpression] = useState('0 0 * * *');
  const [customCron, setCustomCron] = useState('');
  const [isCustom, setIsCustom] = useState(false);
  const [browser, setBrowser] = useState('chromium');
  const [device, setDevice] = useState('Desktop');

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
    } else if (isOpen) {
      setName(`Schedule for ${targetName}`);
      setDescription('');
      setCronExpression('0 0 * * *');
      setIsCustom(false);
      setCustomCron('');
      setBrowser('chromium');
      setDevice('Desktop');
    }
  }, [scheduleToEdit, isOpen, targetName]);

  const createMutation = useMutation({
    mutationFn: (data: any) => schedulesApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] });
      toast.success('Schedule created successfully');
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

    const payload = {
      name,
      description,
      project_id: projectId,
      browser,
      device: device === 'Desktop' ? null : device,
      cron_expression: finalCron,
    };

    if (scheduleToEdit) {
      updateMutation.mutate(payload);
    } else {
      createMutation.mutate({
        ...payload,
        test_suite_id: testSuiteId,
        test_case_id: testCaseId,
        is_active: true
      });
    }
  };

  const isPending = createMutation.isPending || updateMutation.isPending;

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle>{scheduleToEdit ? 'Edit Schedule' : 'Schedule Test Execution'}</DialogTitle>
          <DialogDescription>
            {scheduleToEdit ? 'Modify your existing test schedule' : <>Create a recurring schedule for <span className="font-semibold text-foreground">{targetName}</span></>}
          </DialogDescription>
        </DialogHeader>
        
        <div className="grid gap-4 py-4">
          <div className="grid gap-2">
            <Label htmlFor="name">Name</Label>
            <Input 
              id="name" 
              value={name} 
              onChange={e => setName(e.target.value)} 
              placeholder="E.g., Nightly Regression" 
            />
          </div>
          
          <div className="grid gap-2">
            <Label htmlFor="description">Description (Optional)</Label>
            <Input 
              id="description" 
              value={description} 
              onChange={e => setDescription(e.target.value)} 
              placeholder="Run every night to check critical flows" 
            />
          </div>
          
          <div className="grid gap-2">
            <Label>Frequency (Cron Expression)</Label>
            <Select 
              value={isCustom ? 'custom' : cronExpression} 
              onValueChange={handleCronChange}
            >
              <SelectTrigger>
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
                className="mt-2"
                placeholder="* * * * *" 
                value={customCron} 
                onChange={e => {
                  setCustomCron(e.target.value);
                  setCronExpression(e.target.value);
                }} 
              />
            )}
            <p className="text-xs text-muted-foreground">Times are evaluated in UTC.</p>
          </div>
          
          <div className="grid grid-cols-2 gap-4">
            <div className="grid gap-2">
              <Label>Browser</Label>
              <Select value={browser} onValueChange={setBrowser}>
                <SelectTrigger>
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
              <Label>Device</Label>
              <Select value={device} onValueChange={setDevice}>
                <SelectTrigger>
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
        </div>
        
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={isPending}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={isPending}>
            {isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
            {scheduleToEdit ? 'Save Changes' : 'Create Schedule'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

import { useState, useEffect, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { 
  Calendar, Clock, PlayCircle, StopCircle, Trash2, AlertCircle, RefreshCw, Pen,
  Smartphone, FolderTree, FileText, LayoutGrid, List, Search
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { 
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow 
} from '@/components/ui/table';
import { toast } from 'sonner';
import { schedulesApi, TestSchedule } from '@/api/schedules';
import { ScheduleModal } from '@/components/ScheduleModal';
import { motion } from 'framer-motion';

export default function Schedules() {
  const queryClient = useQueryClient();
  
  const [projectId, setProjectId] = useState<number | null>(() => {
    const saved = localStorage.getItem('activeProjectId');
    return saved ? parseInt(saved) : null;
  });

  const [editTarget, setEditTarget] = useState<TestSchedule | null>(null);
  const [viewMode, setViewMode] = useState<'card' | 'list'>('list');
  const [searchQuery, setSearchQuery] = useState('');

  useEffect(() => {
    const handleProjectChange = () => {
      const saved = localStorage.getItem('activeProjectId');
      setProjectId(saved ? parseInt(saved) : null);
    };
    window.addEventListener('projectChanged', handleProjectChange);
    return () => window.removeEventListener('projectChanged', handleProjectChange);
  }, []);

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
    onError: (err: any) => toast.error(`Failed to update schedule: ${err.message}`)
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => schedulesApi.delete(id),
    onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: ['schedules'] });
        toast.success('Schedule deleted successfully');
    },
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

  const formatDate = (dateString?: string) => {
      if (!dateString) return 'Never';
      return new Date(dateString).toLocaleString(undefined, {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
      });
  };

  const filteredSchedules = useMemo(() => {
    if (!schedules) return [];
    if (!searchQuery.trim()) return schedules;
    
    const lowerQuery = searchQuery.toLowerCase();
    return schedules.filter((schedule: TestSchedule) => 
      schedule.name.toLowerCase().includes(lowerQuery) || 
      (schedule.description && schedule.description.toLowerCase().includes(lowerQuery)) ||
      schedule.cron_expression.toLowerCase().includes(lowerQuery)
    );
  }, [schedules, searchQuery]);

  if (isLoading) return <div className="p-8 flex justify-center"><RefreshCw className="animate-spin text-muted-foreground w-8 h-8" /></div>;

  return (
    <div className="max-w-[1600px] mx-auto pb-16">
      <div className="sticky top-0 z-30 pt-4 pb-6 bg-slate-50/80 backdrop-blur-xl border-b border-slate-200/60 shadow-[0_4px_20px_-10px_rgba(0,0,0,0.05)] mb-8 -mx-4 sm:-mx-8 px-4 sm:px-8">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
            <div className="space-y-4">
                <div className="flex items-center text-sm text-slate-400 gap-2">
                    <span className="font-bold text-slate-700 bg-white px-2.5 py-1 rounded-md border border-slate-200 shadow-sm">Automated Schedules</span>
                </div>
                <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight text-slate-900">Test Schedules</h1>
                <p className="text-slate-500 max-w-2xl text-base leading-relaxed">
                  Manage all recurring, automated test executions across your project. Stay on top of next runs and pause jobs when necessary.
                </p>
            </div>
            
            {projectId && schedules?.length > 0 && (
              <div className="flex flex-col sm:flex-row items-center gap-4">
                  <div className="relative w-full sm:w-64">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                    <Input 
                      placeholder="Search schedules..." 
                      className="pl-9 bg-white border-slate-200 shadow-sm rounded-xl focus-visible:ring-indigo-500"
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                    />
                  </div>
                  
                  <div className="flex bg-white rounded-xl border border-slate-200 shadow-sm p-1 shrink-0">
                    <Button 
                      variant="ghost" 
                      size="sm" 
                      className={`px-3 py-1.5 h-8 rounded-lg ${viewMode === 'card' ? 'bg-slate-100 text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700 hover:bg-slate-50'}`}
                      onClick={() => setViewMode('card')}
                    >
                      <LayoutGrid className="w-4 h-4 mr-2" /> Cards
                    </Button>
                    <Button 
                      variant="ghost" 
                      size="sm" 
                      className={`px-3 py-1.5 h-8 rounded-lg ${viewMode === 'list' ? 'bg-slate-100 text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700 hover:bg-slate-50'}`}
                      onClick={() => setViewMode('list')}
                    >
                      <List className="w-4 h-4 mr-2" /> List
                    </Button>
                  </div>
                  
                  <div className="hidden sm:flex items-center gap-6 px-5 py-3 bg-white rounded-2xl border border-slate-200 shadow-sm shrink-0 h-[46px]">
                      <div className="flex items-center gap-2">
                          <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Total</span>
                          <span className="text-lg font-black text-slate-800">{schedules.length}</span>
                      </div>
                      <div className="w-px h-6 bg-slate-100" />
                      <div className="flex items-center gap-2">
                          <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Active</span>
                          <span className="text-lg font-black text-emerald-600">{schedules.filter((s:any) => s.is_active).length}</span>
                      </div>
                  </div>
              </div>
            )}
        </div>
      </div>

      {!projectId ? (
        <div className="p-16 text-center bg-white border border-slate-200 rounded-3xl shadow-sm max-w-2xl mx-auto mt-12">
          <div className="w-20 h-20 bg-slate-50 rounded-full flex items-center justify-center mx-auto mb-6 border border-slate-100 shadow-[0_2px_10px_-4px_rgba(0,0,0,0.05)]">
             <AlertCircle className="w-10 h-10 text-slate-400" />
          </div>
          <h3 className="text-2xl font-extrabold text-slate-900 mb-2">No Project Selected</h3>
          <p className="text-slate-500">Please select a project from the top navigation to view and manage its schedules.</p>
        </div>
      ) : error ? (
        <div className="p-16 text-center bg-rose-50 border border-rose-200 rounded-3xl shadow-sm max-w-2xl mx-auto mt-12">
          <div className="w-20 h-20 bg-white rounded-full flex items-center justify-center mx-auto mb-6 shadow-sm border border-rose-100">
            <AlertCircle className="w-10 h-10 text-rose-500" />
          </div>
          <h3 className="text-2xl font-extrabold text-rose-800 mb-2">Failed to load schedules</h3>
          <p className="text-rose-600/80">{(error as Error).message}</p>
        </div>
      ) : schedules?.length === 0 ? (
        <div className="p-16 text-center bg-white border border-slate-200 rounded-3xl shadow-sm max-w-2xl mx-auto mt-12">
          <div className="w-20 h-20 bg-indigo-50 rounded-full flex items-center justify-center mx-auto mb-6 shadow-sm border border-indigo-100">
             <Calendar className="w-10 h-10 text-indigo-500" />
          </div>
          <h3 className="text-2xl font-extrabold text-slate-900 mb-2">No Schedules found</h3>
          <p className="text-slate-500 mb-8 max-w-sm mx-auto">Schedules let you run tests automatically! Go to any Test Suite or Test Case to create your first schedule.</p>
        </div>
      ) : (
        <>
          {filteredSchedules.length === 0 ? (
            <div className="p-12 text-center bg-slate-50 border border-slate-200 border-dashed rounded-3xl mt-8">
               <Search className="w-8 h-8 text-slate-300 mx-auto mb-4" />
               <h3 className="text-lg font-semibold text-slate-700 mb-1">No matches found</h3>
               <p className="text-slate-500">No schedules matched your search query "{searchQuery}".</p>
            </div>
          ) : viewMode === 'card' ? (
            <div className="grid grid-cols-1 lg:grid-cols-2 lg:gap-6 gap-4">
              {filteredSchedules.map((schedule: TestSchedule, idx: number) => (
                <motion.div
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: idx * 0.05 }}
                  key={schedule.id}
                  className={`group flex flex-col bg-white border shadow-sm rounded-3xl overflow-hidden transition-all hover:shadow-lg hover:border-indigo-200 ${schedule.is_active ? 'border-slate-200' : 'border-slate-200/50 bg-slate-50/30'}`}
                >
                  <div className="p-6 pb-5 flex gap-4 items-start border-b border-slate-100">
                    <div className={`p-4 rounded-2xl ${schedule.is_active ? 'bg-indigo-50 text-indigo-500 shadow-[0_2px_10px_-4px_rgba(99,102,241,0.2)]' : 'bg-slate-100 text-slate-400'}`}>
                       {schedule.test_case_id ? <FileText className="w-8 h-8" /> : <FolderTree className="w-8 h-8" />}
                    </div>
                    
                    <div className="flex-1 min-w-0">
                      <div className="flex justify-between items-start gap-3">
                        <h3 className={`text-lg font-extrabold truncate ${schedule.is_active ? 'text-slate-900' : 'text-slate-500'}`}>
                          {schedule.name}
                        </h3>
                        <Badge 
                          variant="outline"
                          className={`font-mono text-[10px] tracking-wider shrink-0 rounded-lg px-2 py-0.5 ${schedule.is_active ? 'bg-indigo-50 text-indigo-700 border-indigo-200' : 'bg-slate-100 text-slate-500 border-slate-200'}`}
                        >
                          {schedule.cron_expression}
                        </Badge>
                      </div>
                      <p className="text-sm text-slate-500 mt-1 line-clamp-2 min-h-[40px]">
                        {schedule.description || <span className="italic opacity-60">No description provided</span>}
                      </p>
                    </div>
                  </div>

                  <div className="px-6 py-4 flex-1 flex flex-col justify-center bg-slate-50/20">
                    <div className="grid grid-cols-2 gap-6">
                      <div>
                         <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5 flex items-center gap-1"><Clock className="w-3 h-3"/> Next Run</p>
                         <p className={`text-sm font-semibold truncate ${schedule.is_active ? 'text-emerald-600' : 'text-slate-400'}`}>
                            {schedule.is_active ? formatDate(schedule.next_run_at) : 'Paused'}
                         </p>
                      </div>
                      <div>
                         <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5 flex items-center gap-1"><Clock className="w-3 h-3"/> Last Run</p>
                         <p className="text-sm font-semibold text-slate-700 truncate">
                            {formatDate(schedule.last_run_at)}
                         </p>
                      </div>
                    </div>
                    
                    <div className="mt-4 pt-4 border-t border-slate-100 grid grid-cols-2 gap-6">
                      <div>
                         <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Environment</p>
                         <div className="flex flex-wrap items-center gap-1.5">
                           <Badge variant="secondary" className="bg-white border border-slate-200 text-slate-600 font-medium px-2 py-0.5 rounded-md flex items-center gap-1 text-xs">
                              {schedule.browser}
                           </Badge>
                           {schedule.device && (
                             <Badge variant="secondary" className="bg-white border border-slate-200 text-slate-600 font-medium px-2 py-0.5 rounded-md flex items-center gap-1 text-xs">
                               <Smartphone className="w-3 h-3" /> {schedule.device}
                             </Badge>
                           )}
                         </div>
                      </div>
                      <div>
                        <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Target</p>
                        <p className="text-xs font-semibold text-slate-600">
                          {schedule.test_case_id ? `Test Case #${schedule.test_case_id}` : `Module #${schedule.test_suite_id}`}
                        </p>
                      </div>
                    </div>
                  </div>
                  
                  <div className="px-5 py-3 border-t border-slate-100 bg-slate-50 flex justify-between items-center group-hover:bg-indigo-50/50 transition-colors">
                     <div className="flex items-center gap-2">
                        <div className="flex items-center gap-2">
                          <span className="relative flex h-2.5 w-2.5">
                            {schedule.is_active && <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>}
                            <span className={`relative inline-flex rounded-full h-2.5 w-2.5 ${schedule.is_active ? 'bg-emerald-500' : 'bg-slate-300'}`}></span>
                          </span>
                          <span className={`text-xs font-bold uppercase tracking-wide ${schedule.is_active ? 'text-emerald-700' : 'text-slate-500'}`}>
                            {schedule.is_active ? 'Active' : 'Paused'}
                          </span>
                        </div>
                     </div>
                     
                     <div className="flex gap-2">
                        <Button
                          variant={schedule.is_active ? "outline" : "default"}
                          size="sm"
                          onClick={() => handleToggle(schedule)}
                          className={`h-9 px-4 rounded-xl ${schedule.is_active ? 'text-amber-600 hover:text-amber-700 hover:bg-amber-50 border-amber-200' : 'bg-emerald-600 hover:bg-emerald-700 text-white'}`}
                        >
                          {schedule.is_active ? <><StopCircle className="w-4 h-4 mr-1.5" /> Pause</> : <><PlayCircle className="w-4 h-4 mr-1.5" /> Resume</>}
                        </Button>
                        <Button
                          variant="outline"
                          size="icon"
                          onClick={() => setEditTarget(schedule)}
                          className="h-9 w-9 rounded-xl text-slate-500 hover:text-indigo-600 hover:bg-indigo-50 border-slate-200 transition-colors"
                          title="Edit Schedule"
                        >
                          <Pen className="w-4 h-4" />
                        </Button>
                        <Button
                          variant="outline"
                          size="icon"
                          onClick={() => handleDelete(schedule.id)}
                          className="h-9 w-9 rounded-xl text-slate-400 hover:text-rose-600 hover:bg-rose-50 border-slate-200 transition-colors"
                          title="Delete Schedule"
                        >
                          <Trash2 className="w-4 h-4" />
                        </Button>
                     </div>
                  </div>
                </motion.div>
              ))}
            </div>
          ) : (
            <div className="bg-white border border-slate-200 rounded-3xl overflow-hidden shadow-sm">
              <Table>
                <TableHeader className="bg-slate-50/50">
                  <TableRow className="border-slate-100 hover:bg-transparent">
                    <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12 w-[250px]">Name</TableHead>
                    <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12">Cron</TableHead>
                    <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12">Target & Env</TableHead>
                    <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12">Next Run</TableHead>
                    <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12 w-[120px]">Status</TableHead>
                    <TableHead className="text-right font-bold text-slate-500 uppercase tracking-widest text-xs h-12 w-[150px]">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredSchedules.map((schedule: TestSchedule) => (
                    <TableRow key={schedule.id} className="border-slate-100 hover:bg-slate-50/50 transition-colors">
                      <TableCell className="py-4">
                        <div className="font-bold text-slate-800">{schedule.name}</div>
                        {schedule.description && <div className="text-xs text-slate-500 mt-1 max-w-[220px] truncate" title={schedule.description}>{schedule.description}</div>}
                      </TableCell>
                      <TableCell className="py-4">
                        <Badge variant="outline" className="font-mono text-xs bg-slate-50 text-slate-600 border-slate-200 whitespace-nowrap">
                           {schedule.cron_expression}
                        </Badge>
                      </TableCell>
                      <TableCell className="py-4">
                        <div className="flex flex-col gap-1.5">
                          <span className="text-xs font-semibold text-slate-600 flex items-center gap-1">
                            {schedule.test_case_id ? <><FileText className="w-3 h-3"/> Case #{schedule.test_case_id}</> : <><FolderTree className="w-3 h-3"/> Module #{schedule.test_suite_id}</>}
                          </span>
                          <div className="flex flex-wrap items-center gap-1">
                            <span className="text-[10px] bg-slate-100 text-slate-600 px-1.5 py-0.5 rounded font-medium">{schedule.browser}</span>
                            {schedule.device && <span className="text-[10px] bg-slate-100 text-slate-600 px-1.5 py-0.5 rounded font-medium">{schedule.device}</span>}
                          </div>
                        </div>
                      </TableCell>
                      <TableCell className="py-4">
                        <div className="flex flex-col gap-1">
                          <span className={`text-sm font-semibold whitespace-nowrap ${schedule.is_active ? 'text-emerald-600' : 'text-slate-400'}`}>
                            {schedule.is_active ? formatDate(schedule.next_run_at) : 'Paused'}
                          </span>
                          <span className="text-[10px] text-slate-400 font-medium">Last: {formatDate(schedule.last_run_at)}</span>
                        </div>
                      </TableCell>
                      <TableCell className="py-4">
                         <div className="flex items-center gap-2">
                            <span className="relative flex h-2 w-2">
                              {schedule.is_active && <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>}
                              <span className={`relative inline-flex rounded-full h-2 w-2 ${schedule.is_active ? 'bg-emerald-500' : 'bg-slate-300'}`}></span>
                            </span>
                            <span className={`text-xs font-bold uppercase tracking-wide ${schedule.is_active ? 'text-emerald-700' : 'text-slate-500'}`}>
                              {schedule.is_active ? 'Active' : 'Paused'}
                            </span>
                          </div>
                      </TableCell>
                      <TableCell className="text-right py-4 pr-6">
                        <div className="flex justify-end gap-1">
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => handleToggle(schedule)}
                            className={`h-8 w-8 rounded-lg ${schedule.is_active ? 'text-amber-500 hover:text-amber-600 hover:bg-amber-50' : 'text-emerald-500 hover:text-emerald-600 hover:bg-emerald-50'}`}
                            title={schedule.is_active ? "Pause Schedule" : "Resume Schedule"}
                          >
                            {schedule.is_active ? <StopCircle className="w-4 h-4" /> : <PlayCircle className="w-4 h-4" />}
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => setEditTarget(schedule)}
                            className="h-8 w-8 rounded-lg text-slate-400 hover:text-indigo-600 hover:bg-indigo-50"
                            title="Edit Schedule"
                          >
                            <Pen className="w-4 h-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => handleDelete(schedule.id)}
                            className="h-8 w-8 rounded-lg text-slate-400 hover:text-rose-600 hover:bg-rose-50"
                            title="Delete Schedule"
                          >
                            <Trash2 className="w-4 h-4" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </>
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

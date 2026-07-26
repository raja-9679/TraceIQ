import { useState, useEffect, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Smartphone, Trash2, RefreshCw, AlertCircle, Layers, Upload, Download, Apple,
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
import {
  api, getProjects, getAppBuilds, uploadAppBuild, deleteAppBuild, AppBuild,
} from '@/lib/api';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
};

const formatSize = (bytes?: number | null) => {
  if (!bytes) return '—';
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const ACCEPT_BY_PLATFORM: Record<string, string> = {
  android: '.apk,.aab',
  ios: '.ipa',
};

// ---------------------------------------------------------------------------
// Upload form
// ---------------------------------------------------------------------------

function UploadSection({ projectId }: { projectId: number }) {
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [platform, setPlatform] = useState<'android' | 'ios'>('android');
  const [appName, setAppName] = useState('');
  const [versionName, setVersionName] = useState('');
  const [packageId, setPackageId] = useState('');

  const uploadMutation = useMutation({
    mutationFn: (formData: FormData) => uploadAppBuild(projectId, formData),
    onSuccess: (build) => {
      queryClient.invalidateQueries({ queryKey: ['app-builds', projectId] });
      toast.success(`Uploaded ${build.app_name}`);
      setAppName(''); setVersionName(''); setPackageId('');
      if (fileRef.current) fileRef.current.value = '';
    },
    onError: (err) => toast.error(`Upload failed: ${errorDetail(err)}`),
  });

  const handleUpload = () => {
    const file = fileRef.current?.files?.[0];
    if (!file) {
      toast.error('Choose a binary first (.apk / .aab / .ipa)');
      return;
    }
    const formData = new FormData();
    formData.append('file', file);
    formData.append('platform', platform);
    if (appName) formData.append('app_name', appName);
    if (versionName) formData.append('version_name', versionName);
    if (packageId) formData.append('package_id', packageId);
    uploadMutation.mutate(formData);
  };

  return (
    <div className="bg-white border border-slate-200 rounded-2xl p-6">
      <h2 className="text-lg font-bold text-slate-800 mb-1">Upload a build</h2>
      <p className="text-sm text-slate-500 mb-4">
        Mobile test runs install this binary on the device before executing steps.
        Pin a build when triggering a run from a suite.
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3 items-end">
        <div className="lg:col-span-1">
          <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Platform</p>
          <Select value={platform} onValueChange={(v) => setPlatform(v as 'android' | 'ios')}>
            <SelectTrigger className="h-10 rounded-xl bg-white border-slate-200">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="android">Android (.apk / .aab)</SelectItem>
              <SelectItem value="ios">iOS (.ipa)</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="lg:col-span-1">
          <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Binary</p>
          <Input ref={fileRef} type="file" accept={ACCEPT_BY_PLATFORM[platform]} className="h-10 rounded-xl" />
        </div>
        <Input placeholder="App name (optional)" value={appName} onChange={(e) => setAppName(e.target.value)} className="h-10 rounded-xl" />
        <Input placeholder="Version, e.g. 2.4.1 (optional)" value={versionName} onChange={(e) => setVersionName(e.target.value)} className="h-10 rounded-xl" />
        <Input placeholder="Package / bundle id (optional)" value={packageId} onChange={(e) => setPackageId(e.target.value)} className="h-10 rounded-xl" />
      </div>
      <div className="mt-4">
        <Button onClick={handleUpload} disabled={uploadMutation.isPending} className="rounded-xl">
          {uploadMutation.isPending
            ? <><RefreshCw className="w-4 h-4 mr-2 animate-spin" /> Uploading…</>
            : <><Upload className="w-4 h-4 mr-2" /> Upload build</>}
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Builds table
// ---------------------------------------------------------------------------

function BuildsSection({ projectId }: { projectId: number }) {
  const queryClient = useQueryClient();

  const { data: builds, isLoading, error } = useQuery({
    queryKey: ['app-builds', projectId],
    queryFn: () => getAppBuilds(projectId),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteAppBuild(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['app-builds', projectId] });
      toast.success('Build deleted');
    },
    onError: (err) => toast.error(`Delete failed: ${errorDetail(err)}`),
  });

  const handleDownload = async (build: AppBuild) => {
    try {
      const res = await api.get(`/app-builds/${build.id}`);
      const url = res.data?.download_url;
      if (url) window.open(url, '_blank');
      else toast.error('No download URL available');
    } catch (err) {
      toast.error(errorDetail(err));
    }
  };

  if (isLoading) {
    return (
      <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl">
        <RefreshCw className="w-6 h-6 text-slate-300 mx-auto animate-spin" />
      </div>
    );
  }
  if (error) {
    return (
      <div className="p-6 bg-rose-50 border border-rose-200 rounded-2xl text-rose-700 text-sm">
        Failed to load builds: {errorDetail(error)}
      </div>
    );
  }
  if (!builds || builds.length === 0) {
    return (
      <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl">
        <Smartphone className="w-10 h-10 text-slate-300 mx-auto mb-4" />
        <h3 className="text-lg font-bold text-slate-800 mb-1">No builds yet</h3>
        <p className="text-slate-500 text-sm">Upload an APK, AAB, or IPA above to start testing your mobile app.</p>
      </div>
    );
  }

  return (
    <div className="bg-white border border-slate-200 rounded-2xl overflow-hidden">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>App</TableHead>
            <TableHead>Platform</TableHead>
            <TableHead>Version</TableHead>
            <TableHead>Package / bundle id</TableHead>
            <TableHead>Size</TableHead>
            <TableHead>Uploaded</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {builds.map((b) => (
            <TableRow key={b.id}>
              <TableCell>
                <div className="font-semibold text-slate-800">{b.app_name}</div>
                <div className="text-xs text-slate-400">{b.original_filename}</div>
              </TableCell>
              <TableCell>
                <Badge variant="outline" className="gap-1">
                  {b.platform === 'ios' ? <Apple className="w-3 h-3" /> : <Smartphone className="w-3 h-3" />}
                  {b.platform}
                </Badge>
              </TableCell>
              <TableCell>{b.version_name || '—'}{b.build_number ? ` (${b.build_number})` : ''}</TableCell>
              <TableCell className="font-mono text-xs">{b.package_id || '—'}</TableCell>
              <TableCell>{formatSize(b.file_size)}</TableCell>
              <TableCell>{formatDate(b.created_at)}</TableCell>
              <TableCell className="text-right">
                <Button variant="ghost" size="sm" onClick={() => handleDownload(b)} title="Download binary">
                  <Download className="w-4 h-4" />
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-rose-500 hover:text-rose-700"
                  title="Delete build"
                  onClick={() => {
                    if (window.confirm(`Delete build "${b.app_name}"? Runs pinned to it will no longer dispatch a binary.`)) {
                      deleteMutation.mutate(b.id);
                    }
                  }}
                >
                  <Trash2 className="w-4 h-4" />
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function AppBuilds() {
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
            <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">App Builds</h1>
            <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
              Upload native app binaries (APK / AAB / IPA) for mobile test runs. Cases using
              mobile steps run on the Appium executor against the build you pin at run time.
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
          <p className="text-slate-500 text-sm">Select a project above to manage its app builds.</p>
        </div>
      ) : (
        <div className="space-y-10">
          <UploadSection projectId={projectId} />
          <BuildsSection projectId={projectId} />
        </div>
      )}
    </div>
  );
}

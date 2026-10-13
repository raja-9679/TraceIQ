import { useState, useRef } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Smartphone, Trash2, RefreshCw, Upload, Download, Apple, Plus,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { toast } from 'sonner';
import {
  api, getAppBuilds, uploadAppBuild, deleteAppBuild, AppBuild,
} from '@/lib/api';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import {
  DetailCard, EmptyState, Eyebrow, ListGroup, ListRow, PageHeader, Panel, Pill, SplitView,
} from '@/components/redesign/primitives';
import { cn } from '@/lib/utils';

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

const fieldLabel = 'text-[13px] font-semibold text-ink';

function PlatformChip({ platform }: { platform: string }) {
  const ios = platform === 'ios';
  const Icon = ios ? Apple : Smartphone;
  return (
    <span
      className={cn(
        'flex h-7 w-7 flex-none items-center justify-center rounded-[9px]',
        ios ? 'bg-pastel-lavender text-pastel-ink-lavender' : 'bg-pastel-mint text-pastel-ink-mint',
      )}
      aria-hidden="true"
    >
      <Icon className="h-[15px] w-[15px]" strokeWidth={2.2} />
    </span>
  );
}

// ---------------------------------------------------------------------------
// Upload form
// ---------------------------------------------------------------------------

function UploadCard({ projectId, onUploaded, onCancel }: {
  projectId: number;
  onUploaded: (build: AppBuild) => void;
  onCancel?: () => void;
}) {
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
      onUploaded(build);
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
    <DetailCard label="Upload a build">
      <div className="flex flex-col gap-1.5">
        <h2 className="text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">Upload a build</h2>
        <p className="text-sm text-ink-soft">
          Mobile test runs install this binary on the device before running steps. Pin a build when you start a run from a suite.
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <label htmlFor="build-platform" className={fieldLabel}>Platform</label>
          <Select value={platform} onValueChange={(v) => setPlatform(v as 'android' | 'ios')}>
            <SelectTrigger id="build-platform" className="h-11 rounded-xl">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="android">Android (.apk / .aab)</SelectItem>
              <SelectItem value="ios">iOS (.ipa)</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="build-file" className={fieldLabel}>Binary</label>
          <Input id="build-file" ref={fileRef} type="file" accept={ACCEPT_BY_PLATFORM[platform]} className="h-11 rounded-xl" />
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="build-name" className={fieldLabel}>App name <span className="font-normal text-ink-soft">(optional)</span></label>
          <Input id="build-name" placeholder="TodoLite" value={appName} onChange={(e) => setAppName(e.target.value)} className="h-11 rounded-xl" />
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="build-version" className={fieldLabel}>Version <span className="font-normal text-ink-soft">(optional)</span></label>
          <Input id="build-version" placeholder="2.4.1" value={versionName} onChange={(e) => setVersionName(e.target.value)} className="h-11 rounded-xl" />
        </div>
        <div className="flex flex-col gap-1.5 sm:col-span-2">
          <label htmlFor="build-package" className={fieldLabel}>Package or bundle id <span className="font-normal text-ink-soft">(optional)</span></label>
          <Input id="build-package" placeholder="com.example.app" value={packageId} onChange={(e) => setPackageId(e.target.value)} className="h-11 rounded-xl font-mono" />
        </div>
      </div>
      <div className="flex flex-wrap gap-2.5">
        <Button size="lg" onClick={handleUpload} disabled={uploadMutation.isPending}>
          {uploadMutation.isPending
            ? <><RefreshCw className="h-4 w-4 animate-spin" /> Uploading…</>
            : <><Upload className="h-4 w-4" /> Upload build</>}
        </Button>
        {onCancel && <Button size="lg" variant="outline" onClick={onCancel}>Cancel</Button>}
      </div>
    </DetailCard>
  );
}

// ---------------------------------------------------------------------------
// Builds list + detail
// ---------------------------------------------------------------------------

function Fact({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex flex-col gap-1 rounded-2xl bg-background p-4">
      <span className="text-xs text-ink-soft">{label}</span>
      <span className={cn('break-all text-[15px] font-semibold text-ink', mono && 'font-mono text-[13px]')}>{value}</span>
    </div>
  );
}

function BuildsView({ projectId, uploading, setUploading }: {
  projectId: number;
  uploading: boolean;
  setUploading: (v: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<number | null>(null);

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

  const onUploaded = (b: AppBuild) => { setSelectedId(b.id); setUploading(false); };

  if (isLoading) {
    return (
      <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading">
        {[0, 1, 2].map((i) => <div key={i} className="h-[60px] max-w-[440px] animate-pulse rounded-2xl bg-white/70" />)}
      </div>
    );
  }
  if (error) {
    return <EmptyState title="Couldn't load builds">{errorDetail(error)}</EmptyState>;
  }
  if (!builds || builds.length === 0) {
    return (
      <div className="flex flex-col gap-5">
        <EmptyState title="No builds yet">Upload an APK, AAB, or IPA to start testing your mobile app.</EmptyState>
        <div className="max-w-[760px]"><UploadCard projectId={projectId} onUploaded={onUploaded} /></div>
      </div>
    );
  }

  const groups = [
    { name: 'Android', items: builds.filter((b) => b.platform !== 'ios') },
    { name: 'iOS', items: builds.filter((b) => b.platform === 'ios') },
  ].filter((g) => g.items.length > 0);
  const selected = builds.find((b) => b.id === selectedId) ?? groups[0]?.items[0] ?? null;

  return (
    <SplitView
      list={groups.map((g) => (
        <ListGroup key={g.name} title={g.name} count={g.items.length}>
          {g.items.map((b) => (
            <ListRow
              key={b.id}
              selected={!uploading && selected?.id === b.id}
              onSelect={() => { setSelectedId(b.id); setUploading(false); }}
              leading={<PlatformChip platform={b.platform} />}
              title={b.app_name}
              meta={`${b.version_name || 'No version'}${b.build_number ? ` (${b.build_number})` : ''} · ${formatDate(b.created_at)}`}
              trailing={<span className="font-mono text-xs text-ink-soft">{formatSize(b.file_size)}</span>}
            />
          ))}
        </ListGroup>
      ))}
      detail={uploading ? (
        <UploadCard projectId={projectId} onUploaded={onUploaded} onCancel={() => setUploading(false)} />
      ) : selected && (
        <DetailCard label="Selected build">
          <div className="flex flex-col gap-2">
            <Pill tone={selected.platform === 'ios' ? 'lavender' : 'mint'} className="self-start">
              {selected.platform === 'ios' ? 'iOS' : 'Android'}
            </Pill>
            <h2 className="text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{selected.app_name}</h2>
            <span className="break-all text-sm text-ink-soft">{selected.original_filename}</span>
          </div>

          <div className="grid grid-cols-[repeat(auto-fit,minmax(160px,1fr))] gap-3">
            <Fact label="Version" value={`${selected.version_name || '—'}${selected.build_number ? ` (${selected.build_number})` : ''}`} />
            <Fact label="Size" value={formatSize(selected.file_size)} />
            <Fact label="Uploaded" value={formatDate(selected.created_at)} />
          </div>

          <div className="flex flex-col gap-2">
            <Eyebrow>Package or bundle id</Eyebrow>
            <p className="break-all rounded-2xl bg-background p-4 font-mono text-[13px] text-ink">{selected.package_id || '—'}</p>
          </div>

          <Panel title="How it's used" callout>
            Cases with mobile steps run on the Appium executor. Pick this build when you start a run from a suite and it is installed on the device first.
          </Panel>

          <div className="flex flex-wrap gap-2.5">
            <Button size="lg" variant="outline" onClick={() => handleDownload(selected)}>
              <Download className="h-4 w-4" /> Download binary
            </Button>
            <Button
              size="lg"
              variant="outline"
              className="sm:ml-auto"
              disabled={deleteMutation.isPending}
              onClick={() => {
                if (window.confirm(`Delete build "${selected.app_name}"? Runs pinned to it will no longer dispatch a binary.`)) {
                  deleteMutation.mutate(selected.id);
                }
              }}
            >
              <Trash2 className="h-4 w-4" /> Delete build
            </Button>
          </div>
        </DetailCard>
      )}
    />
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function AppBuilds() {
  // The sidebar owns project selection.
  const projectId = useActiveProjectId();
  const [uploading, setUploading] = useState(false);

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        crumbs={<><Link to="/suites" className="hover:text-ink">Tests</Link><span>/</span><span>App builds</span></>}
        title="App builds"
        subtitle="Native binaries (APK, AAB, IPA) for mobile test runs. Mobile cases run against the build you pin at run time."
        actions={projectId ? (
          <Button onClick={() => setUploading(true)}><Plus className="h-4 w-4" /> Upload build</Button>
        ) : undefined}
      />

      {!projectId ? (
        <EmptyState title="Pick a project">Choose a project in the sidebar to manage its app builds.</EmptyState>
      ) : (
        <BuildsView key={projectId} projectId={projectId} uploading={uploading} setUploading={setUploading} />
      )}
    </div>
  );
}

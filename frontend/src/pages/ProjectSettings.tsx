import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowUpRight } from 'lucide-react';
import { getProjects } from '@/lib/api';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { PageHeader } from '@/components/redesign/primitives';
import { SectionNav, SettingsCard } from '@/components/redesign-local/groupD/settingsForm';
import { DataPolicySection } from '@/components/settings/DataPolicySection';
import { OpenApiSpecSection } from '@/components/settings/OpenApiSpecSection';
import { ProjectNotificationsSection } from '@/components/project-settings/ProjectNotificationsSection';
import { TraceCorrelationSection } from '@/components/settings/TraceCorrelationSection';

type SectionId = 'notifications' | 'capture' | 'traces' | 'api' | 'more';

const SECTIONS: { id: SectionId; name: string }[] = [
    { id: 'notifications', name: 'Notifications' },
    { id: 'capture', name: 'Data capture and retention' },
    { id: 'traces', name: 'Backend traces' },
    { id: 'api', name: 'OpenAPI spec' },
    { id: 'more', name: 'Members and other policies' },
];

// Policies that already have a full page of their own: linked, not copied.
const ELSEWHERE: { title: string; text: string; href: string; cta: string }[] = [
    { title: 'Members and access', text: 'Who can see, edit and administer this project, directly or through teams.', href: '/workspace', cta: 'Open Workspace' },
    { title: 'Quality gate and CI', text: 'The rules a release is judged on, and how CI consumes the gate.', href: '/quality', cta: 'Open Quality gate' },
    { title: 'Network policy', text: 'Which hosts this project\'s workers may reach.', href: '/on-call', cta: 'Open On-call' },
    { title: 'Environments', text: 'Base URLs, variables and secrets per environment.', href: '/environments', cta: 'Open Environments' },
];

/**
 * Everything that belongs to the PROJECT selected in the sidebar (as opposed
 * to My settings, which belong to the person). Visible to every member;
 * changes need the project admin role, which the server enforces.
 */
export default function ProjectSettings() {
    const projectId = useActiveProjectId();
    const [section, setSection] = useState<SectionId>('notifications');
    const projects = useQuery({ queryKey: ['projects'], queryFn: () => getProjects() });
    const project = projects.data?.find((p) => p.id === projectId);
    const canEdit = project?.access_level === 'admin';

    return (
        <div className="flex flex-col gap-6">
            <PageHeader
                title="Project settings"
                subtitle={project
                    ? <>Settings for <strong className="text-ink">{project.name}</strong>, shared by everyone on the project. Your personal choices are in <Link to="/settings" className="font-medium text-ink underline underline-offset-2">My settings</Link>.</>
                    : 'Select a project in the sidebar to see its settings.'}
            />
            {!projectId ? null : (
                <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
                    <aside className="lg:sticky lg:top-6 lg:w-60 lg:flex-none">
                        <SectionNav label="Project settings sections" sections={SECTIONS} active={section} onChange={setSection} />
                    </aside>
                    <div className="flex min-w-0 flex-1 flex-col gap-5">
                        {project && !canEdit && section !== 'more' && (
                            <p className="rounded-2xl border border-border bg-white px-4 py-3 text-sm text-ink-soft">
                                {section === 'api'
                                    ? (project.access_level === 'editor'
                                        ? 'Editors can upload and remove the OpenAPI spec.'
                                        : 'You can see this; uploading a spec needs the editor role.')
                                    : 'You can see these settings; changing them needs the project admin role.'}
                            </p>
                        )}
                        {section === 'notifications' && <ProjectNotificationsSection projectId={projectId} canEdit={canEdit} />}
                        {section === 'capture' && <DataPolicySection projectId={projectId} />}
                        {section === 'traces' && <TraceCorrelationSection projectId={projectId} canEdit={canEdit} />}
                        {section === 'api' && <OpenApiSpecSection projectId={projectId} />}
                        {section === 'more' && ELSEWHERE.map((e) => (
                            <SettingsCard key={e.href} labelledBy={`ps-${e.href}`} title={e.title} description={e.text}>
                                <Link to={e.href} className="inline-flex items-center gap-1 self-start text-sm font-medium text-ink underline underline-offset-2">
                                    {e.cta} <ArrowUpRight className="h-4 w-4" aria-hidden="true" />
                                </Link>
                            </SettingsCard>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
}

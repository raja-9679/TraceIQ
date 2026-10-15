import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import * as Tabs from '@radix-ui/react-tabs';
import { AlertCircle, Layers } from 'lucide-react';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { getProjects } from '@/lib/api';
import CrawlerPanel from '@/components/frontend-quality/CrawlerPanel';
import BudgetsPanel from '@/components/frontend-quality/BudgetsPanel';
import SelectorHealthPanel from '@/components/frontend-quality/SelectorHealthPanel';
import StorybookPanel from '@/components/frontend-quality/StorybookPanel';

const TAB = 'px-4 py-2.5 text-sm font-medium text-slate-600 border-b-2 border-transparent hover:text-slate-900 data-[state=active]:border-indigo-600 data-[state=active]:text-indigo-700 outline-none';

export default function FrontendQuality() {
    const [projectId, setProjectId] = useState<number | null>(() => {
        const saved = localStorage.getItem('activeProjectId');
        return saved ? parseInt(saved) : null;
    });
    useEffect(() => {
        const onChange = () => {
            const saved = localStorage.getItem('activeProjectId');
            setProjectId(saved ? parseInt(saved) : null);
        };
        window.addEventListener('projectChanged', onChange);
        return () => window.removeEventListener('projectChanged', onChange);
    }, []);
    const { data: projects } = useQuery({ queryKey: ['projects'], queryFn: () => getProjects() });
    const selectProject = (idStr: string) => {
        const id = parseInt(idStr);
        setProjectId(id);
        localStorage.setItem('activeProjectId', id.toString());
        window.dispatchEvent(new Event('projectChanged'));
    };

    return (
        <div className="max-w-[1300px] mx-auto pb-16">
            <div className="pt-2 pb-6 mb-6 border-b border-slate-200/60 flex flex-col sm:flex-row sm:items-end gap-4">
                <div>
                    <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">Frontend Quality</h1>
                    <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
                        Broken links and assets, performance and SEO budgets per page, selectors that keep breaking, and
                        component checks from Storybook. Screen-size, locale and theme sweeps are run options on a suite.
                    </p>
                </div>
                <div className="sm:ml-auto shrink-0">
                    <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Project</p>
                    <Select value={projectId?.toString() ?? ''} onValueChange={selectProject}>
                        <SelectTrigger className="w-[240px] h-10 rounded-xl bg-white border-slate-200" aria-label="Project">
                            <div className="flex items-center gap-2 min-w-0">
                                <Layers className="w-4 h-4 text-indigo-500 shrink-0" />
                                <SelectValue placeholder="Select a project" />
                            </div>
                        </SelectTrigger>
                        <SelectContent>
                            {(projects || []).map((p) => <SelectItem key={p.id} value={p.id.toString()}>{p.name}</SelectItem>)}
                        </SelectContent>
                    </Select>
                </div>
            </div>
            {!projectId ? (
                <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl">
                    <AlertCircle className="w-10 h-10 text-slate-300 mx-auto mb-4" />
                    <h3 className="text-lg font-bold text-slate-800 mb-1">No Project Selected</h3>
                    <p className="text-slate-500 text-sm">Select a project above.</p>
                </div>
            ) : (
                <Tabs.Root defaultValue="links">
                    <Tabs.List className="flex border-b border-slate-200 mb-6" aria-label="Frontend quality views">
                        <Tabs.Trigger value="links" className={TAB}>Link &amp; asset crawler</Tabs.Trigger>
                        <Tabs.Trigger value="budgets" className={TAB}>Page budgets</Tabs.Trigger>
                        <Tabs.Trigger value="selectors" className={TAB}>Selector health</Tabs.Trigger>
                        <Tabs.Trigger value="storybook" className={TAB}>Storybook</Tabs.Trigger>
                    </Tabs.List>
                    <Tabs.Content value="links"><CrawlerPanel projectId={projectId} /></Tabs.Content>
                    <Tabs.Content value="budgets"><BudgetsPanel projectId={projectId} /></Tabs.Content>
                    <Tabs.Content value="selectors"><SelectorHealthPanel projectId={projectId} /></Tabs.Content>
                    <Tabs.Content value="storybook"><StorybookPanel projectId={projectId} /></Tabs.Content>
                </Tabs.Root>
            )}
        </div>
    );
}

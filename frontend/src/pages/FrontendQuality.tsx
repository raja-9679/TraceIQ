import { useState } from 'react';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { EmptyState, PageHeader, PanelTabs } from '@/components/redesign/primitives';
import CrawlerPanel from '@/components/frontend-quality/CrawlerPanel';
import BudgetsPanel from '@/components/frontend-quality/BudgetsPanel';
import SelectorHealthPanel from '@/components/frontend-quality/SelectorHealthPanel';
import StorybookPanel from '@/components/frontend-quality/StorybookPanel';

type View = 'links' | 'budgets' | 'selectors' | 'storybook';

export default function FrontendQuality() {
    const projectId = useActiveProjectId();
    const [view, setView] = useState<View>('links');

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Frontend quality"
                subtitle="Broken links and assets, performance and SEO budgets per page, selectors that keep breaking, and component checks from Storybook. Screen-size, locale and theme sweeps are run options on a suite."
                toolbar={projectId ? (
                    <PanelTabs
                        label="Frontend quality views"
                        value={view}
                        onChange={setView}
                        tabs={[
                            { id: 'links', label: 'Link & asset crawler' },
                            { id: 'budgets', label: 'Page budgets' },
                            { id: 'selectors', label: 'Selector health' },
                            { id: 'storybook', label: 'Storybook' },
                        ]}
                    />
                ) : undefined}
            />
            {!projectId ? (
                <EmptyState title="Pick a project">Choose a project in the sidebar to check its frontend quality.</EmptyState>
            ) : (
                <div role="tabpanel" aria-label={{ links: 'Link & asset crawler', budgets: 'Page budgets', selectors: 'Selector health', storybook: 'Storybook' }[view]}>
                    {view === 'links' && <CrawlerPanel projectId={projectId} />}
                    {view === 'budgets' && <BudgetsPanel projectId={projectId} />}
                    {view === 'selectors' && <SelectorHealthPanel projectId={projectId} />}
                    {view === 'storybook' && <StorybookPanel projectId={projectId} />}
                </div>
            )}
        </div>
    );
}

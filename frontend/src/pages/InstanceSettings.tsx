import InstanceSettingsSection from '@/components/settings/InstanceSettingsSection';
import { PageHeader } from '@/components/redesign/primitives';

// Server-wide configuration (email, AI providers, workers, policies) for the
// instance admin. Was a section of the personal Settings page; it affects
// every tenant, so it has its own page. Non-admins see the section's 403
// message.
export default function InstanceSettings() {
    return (
        <div className="flex flex-col gap-6">
            <PageHeader title="Instance settings" subtitle="Server-wide configuration for every workspace on this TraceIQ instance. Instance admins only." />
            <InstanceSettingsSection />
        </div>
    );
}

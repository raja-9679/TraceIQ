import { toast } from 'sonner';
import { Button, type ButtonProps } from '@/components/ui/button';
import { errorText } from '@/api/solution';
import { switchToProject, useDemoWorkspace } from './useDemoWorkspace';

/** One-click "explore a demo" for onboarding screens; opens it if it exists. */
export default function DemoWorkspaceButton({ variant = 'ghost', size }: Pick<ButtonProps, 'variant' | 'size'>) {
    const { demo, create } = useDemoWorkspace();
    if (demo.isLoading || demo.isError) return null;
    const d = demo.data;
    if (d) {
        return (
            <Button variant={variant} size={size} onClick={() => d.projects[0] && switchToProject(d.projects[0].id)}>
                Open the DEMO workspace
            </Button>
        );
    }
    return (
        <Button
            variant={variant}
            size={size}
            disabled={create.isPending}
            title="A separate workspace with 30 days of generated history; never sends notifications"
            onClick={() => create.mutate(undefined, {
                onSuccess: () => toast.success('Demo workspace created', { description: 'Switched to its web project.' }),
                onError: (e) => toast.error(errorText(e, 'The demo workspace could not be created')),
            })}
        >
            {create.isPending ? 'Seeding the demo…' : 'Explore a DEMO workspace'}
        </Button>
    );
}

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Sparkles, Loader2 } from 'lucide-react';
import { api } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { toast } from 'sonner';

interface GenerateCaseDialogProps {
    suiteId: number;
    open: boolean;
    onOpenChange: (open: boolean) => void;
}

/** "Generate with AI" dialog — describes a journey in plain English, calls
 *  POST /api/cases/generate (mode=direct), then opens the draft in the
 *  Test Builder for review before the user runs it. */
export function GenerateCaseDialog({ suiteId, open, onOpenChange }: GenerateCaseDialogProps) {
    const [description, setDescription] = useState('');
    const [targetUrl, setTargetUrl] = useState('');
    const [caseName, setCaseName] = useState('');
    const navigate = useNavigate();
    const queryClient = useQueryClient();

    const generateMutation = useMutation({
        mutationFn: async () => {
            const response = await api.post('/cases/generate', {
                description: description.trim(),
                target_url: targetUrl.trim() || null,
                case_name: caseName.trim() || null,
                test_suite_id: suiteId,
                mode: 'direct',
            });
            return response.data;
        },
        onSuccess: (testCase) => {
            queryClient.invalidateQueries({ queryKey: ['suite', String(suiteId)] });
            toast.success('Draft test case generated', {
                description: 'Review the steps in the builder, then save or run it.',
            });
            onOpenChange(false);
            navigate(`/suites/${suiteId}/cases/${testCase.id}/edit`);
        },
        onError: (err: Error & { response?: { data?: { detail?: string } } }) => {
            toast.error('Generation failed', {
                description: err.response?.data?.detail || err.message,
            });
        },
    });

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-lg rounded-2xl">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <Sparkles className="h-5 w-5 text-indigo-500" /> Generate Test Case with AI
                    </DialogTitle>
                    <DialogDescription>
                        Describe the user journey in plain English. The AI drafts the steps; you review
                        them in the builder before anything runs.
                    </DialogDescription>
                </DialogHeader>
                <div className="space-y-4 py-2">
                    <div>
                        <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">Journey description *</label>
                        <textarea
                            value={description}
                            onChange={(e) => setDescription(e.target.value)}
                            placeholder={'e.g. "Log in with valid credentials, then verify the dashboard greets the user by name"'}
                            rows={4}
                            className="mt-1.5 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/20"
                        />
                    </div>
                    <div>
                        <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">Target URL</label>
                        <Input
                            value={targetUrl}
                            onChange={(e) => setTargetUrl(e.target.value)}
                            placeholder="https://your-app.example.com/login"
                            className="mt-1.5 rounded-xl font-mono"
                        />
                    </div>
                    <div>
                        <label className="text-xs font-bold text-slate-500 uppercase tracking-wider">Case name (optional)</label>
                        <Input
                            value={caseName}
                            onChange={(e) => setCaseName(e.target.value)}
                            placeholder="Derived from the description if left blank"
                            className="mt-1.5 rounded-xl"
                        />
                    </div>
                </div>
                <DialogFooter>
                    <Button variant="ghost" onClick={() => onOpenChange(false)} className="rounded-xl">
                        Cancel
                    </Button>
                    <Button
                        onClick={() => generateMutation.mutate()}
                        disabled={!description.trim() || generateMutation.isPending}
                        className="rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white"
                    >
                        {generateMutation.isPending ? (
                            <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Generating…</>
                        ) : (
                            <><Sparkles className="mr-2 h-4 w-4" /> Generate</>
                        )}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

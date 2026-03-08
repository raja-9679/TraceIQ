import { useState, useEffect, useCallback, useRef } from 'react';
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Plus, Save, Loader2, ArrowLeft, Link2, MousePointerClick, TextCursorInput, Code2, CheckCircle2, FileJson, Zap } from "lucide-react";
import { toast } from 'sonner';
import { StepComponent, TestStep } from "@/components/test-builder/StepComponent";
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, getTestCase, updateTestCase } from '@/lib/api';
import { useNavigate, useParams } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';

// Helper to get draft key for localStorage
const getDraftKey = (suiteId: string | undefined, caseId: string | undefined) => {
    return caseId ? `testBuilder_draft_edit_${caseId}` : `testBuilder_draft_new_${suiteId}`;
};

// Helper to save draft to localStorage
const saveDraft = (key: string, data: { testName: string; steps: TestStep[] }) => {
    try {
        localStorage.setItem(key, JSON.stringify({ ...data, timestamp: Date.now() }));
    } catch (e) {
        console.warn('Failed to save draft:', e);
    }
};

// Helper to load draft from localStorage
const loadDraft = (key: string): { testName: string; steps: TestStep[]; timestamp: number } | null => {
    try {
        const data = localStorage.getItem(key);
        if (data) {
            return JSON.parse(data);
        }
    } catch (e) {
        console.warn('Failed to load draft:', e);
    }
    return null;
};

// Helper to clear draft from localStorage
const clearDraft = (key: string) => {
    try {
        localStorage.removeItem(key);
    } catch (e) {
        console.warn('Failed to clear draft:', e);
    }
};

export default function TestBuilder() {
    const { suiteId, caseId } = useParams();
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const isEditing = !!caseId;
    const draftKey = getDraftKey(suiteId, caseId);

    const [testName, setTestName] = useState('');
    const [steps, setSteps] = useState<TestStep[]>([]);
    const [isDirty, setIsDirty] = useState(false);
    const [originalData, setOriginalData] = useState<{ testName: string; steps: TestStep[] } | null>(null);
    const initialLoadDone = useRef(false);
    const serverDataLoaded = useRef(false);

    // Load existing data if editing
    const { data: serverData } = useQuery({
        queryKey: ['testCase', caseId],
        queryFn: async () => {
            if (!caseId) return null;
            const data = await getTestCase(parseInt(caseId));
            return data;
        },
        enabled: isEditing
    });

    // Initialize state from server data or draft
    useEffect(() => {
        if (initialLoadDone.current) return;

        const draft = loadDraft(draftKey);

        if (isEditing && serverData) {
            // Editing mode: check for draft
            serverDataLoaded.current = true;
            setOriginalData({ testName: serverData.name, steps: serverData.steps || [] });

            if (draft && draft.timestamp) {
                // We have a draft - ask user if they want to restore it
                const draftAge = Date.now() - draft.timestamp;
                const draftAgeMinutes = Math.floor(draftAge / 60000);
                
                if (draftAgeMinutes < 60) { // Only restore drafts less than 1 hour old
                    // Check if draft is different from server data
                    const isDraftDifferent = 
                        draft.testName !== serverData.name || 
                        JSON.stringify(draft.steps) !== JSON.stringify(serverData.steps || []);
                    
                    if (isDraftDifferent) {
                        toast.info(`Restored unsaved changes from ${draftAgeMinutes} minute(s) ago`, {
                            action: {
                                label: 'Discard',
                                onClick: () => {
                                    clearDraft(draftKey);
                                    setTestName(serverData.name);
                                    setSteps(serverData.steps || []);
                                    setIsDirty(false);
                                    toast.success('Draft discarded');
                                }
                            },
                            duration: 10000
                        });
                        setTestName(draft.testName);
                        setSteps(draft.steps);
                        setIsDirty(true);
                        initialLoadDone.current = true;
                        return;
                    }
                }
                clearDraft(draftKey); // Clear old or identical draft
            }

            setTestName(serverData.name);
            setSteps(serverData.steps || []);
            initialLoadDone.current = true;
        } else if (!isEditing) {
            // New test case mode: check for draft
            if (draft && draft.timestamp) {
                const draftAge = Date.now() - draft.timestamp;
                const draftAgeMinutes = Math.floor(draftAge / 60000);
                
                if (draftAgeMinutes < 60 && (draft.testName || draft.steps.length > 0)) {
                    toast.info(`Restored unsaved draft from ${draftAgeMinutes} minute(s) ago`, {
                        action: {
                            label: 'Discard',
                            onClick: () => {
                                clearDraft(draftKey);
                                setTestName('');
                                setSteps([]);
                                setIsDirty(false);
                                toast.success('Draft discarded');
                            }
                        },
                        duration: 10000
                    });
                    setTestName(draft.testName);
                    setSteps(draft.steps);
                    setIsDirty(true);
                } else {
                    clearDraft(draftKey);
                }
            }
            setOriginalData({ testName: '', steps: [] });
            initialLoadDone.current = true;
        }
    }, [isEditing, serverData, draftKey]);

    // Auto-save draft when data changes
    useEffect(() => {
        if (!initialLoadDone.current) return;

        // Check if data is dirty
        const currentData = { testName, steps };
        const isChanged = originalData && (
            currentData.testName !== originalData.testName ||
            JSON.stringify(currentData.steps) !== JSON.stringify(originalData.steps)
        );

        setIsDirty(!!isChanged);

        // Save draft if dirty
        if (isChanged) {
            saveDraft(draftKey, currentData);
        }
    }, [testName, steps, originalData, draftKey]);

    // Warn user before leaving with unsaved changes
    useEffect(() => {
        const handleBeforeUnload = (e: BeforeUnloadEvent) => {
            if (isDirty) {
                e.preventDefault();
                e.returnValue = 'You have unsaved changes. Are you sure you want to leave?';
                return e.returnValue;
            }
        };

        window.addEventListener('beforeunload', handleBeforeUnload);
        return () => window.removeEventListener('beforeunload', handleBeforeUnload);
    }, [isDirty]);

    // Clear draft on successful save
    const clearDraftOnSave = useCallback(() => {
        clearDraft(draftKey);
        setIsDirty(false);
    }, [draftKey]);

    const addStep = (type: TestStep['type'] = 'goto') => {
        const newStep: TestStep = {
            id: crypto.randomUUID(),
            type,
            selector: '',
            value: ''
        };
        setSteps([...steps, newStep]);
    };

    const updateStep = (id: string, field: keyof TestStep, value: string) => {
        setSteps(steps.map(step =>
            step.id === id ? { ...step, [field]: value } : step
        ));
    };

    const removeStep = (id: string) => {
        setSteps(steps.filter(step => step.id !== id));
    };

    const moveStep = (index: number, direction: 'up' | 'down') => {
        if (direction === 'up' && index === 0) return;
        if (direction === 'down' && index === steps.length - 1) return;

        const newSteps = [...steps];
        const targetIndex = direction === 'up' ? index - 1 : index + 1;
        [newSteps[index], newSteps[targetIndex]] = [newSteps[targetIndex], newSteps[index]];
        setSteps(newSteps);
    };

    const insertStep = (index: number) => {
        const newStep: TestStep = {
            id: crypto.randomUUID(),
            type: 'goto',
            selector: '',
            value: ''
        };
        const newSteps = [...steps];
        newSteps.splice(index + 1, 0, newStep);
        setSteps(newSteps);
    };

    const saveMutation = useMutation({
        mutationFn: async () => {
            const payload = {
                name: testName,
                test_suite_id: parseInt(suiteId || '0'),
                steps: steps
            };

            if (isEditing && caseId) {
                return updateTestCase(parseInt(caseId), payload);
            } else {
                const response = await api.post(`/suites/${suiteId}/cases`, payload);
                return response.data;
            }
        },
        onSuccess: () => {
            clearDraftOnSave();
            queryClient.invalidateQueries({ queryKey: ['suite', suiteId] });
            toast.success(isEditing ? 'Test case updated successfully' : 'Test case created successfully');
            navigate(`/suites/${suiteId}`);
        },
        onError: (error: any) => {
            toast.error(error?.response?.data?.detail || 'Failed to save test case');
        }
    });

    const handleCancel = () => {
        if (isDirty) {
            if (window.confirm('You have unsaved changes. Are you sure you want to leave? Your draft will be saved.')) {
                navigate(-1);
            }
        } else {
            clearDraft(draftKey);
            navigate(-1);
        }
    };

    return (
        <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="w-full"
        >
            {/* ── Sticky Frosted Header ── */}
            <div className="sticky top-0 z-40 bg-white/80 backdrop-blur-xl border-b border-slate-200 shadow-sm mb-8 -mx-6 px-6 py-4 flex flex-col md:flex-row md:items-center justify-between gap-4 transition-all">
                <div className="flex items-center gap-4 flex-1">
                    <Button variant="ghost" size="icon" onClick={handleCancel} className="shrink-0 text-slate-500 hover:text-slate-900 hover:bg-slate-100 rounded-full h-10 w-10">
                        <ArrowLeft className="h-5 w-5" />
                    </Button>
                    <div className="flex-1 max-w-2xl">
                        <p className="text-xs font-bold text-indigo-500 uppercase tracking-widest mb-1 ml-1">{isEditing ? 'Edit Test Case' : 'New Test Case'}</p>
                        <Input
                            placeholder="Enter Test Case Name..."
                            value={testName}
                            onChange={(e) => setTestName(e.target.value)}
                            className="text-2xl font-black text-slate-900 border-none shadow-none bg-transparent hover:bg-slate-50 focus-visible:ring-indigo-500 rounded-xl px-2 h-12 transition-colors placeholder:text-slate-300"
                        />
                    </div>
                </div>
                <div className="flex items-center gap-3 shrink-0">
                    {isDirty && <span className="text-xs font-bold font-mono text-amber-500 bg-amber-50 px-3 py-1.5 rounded-full border border-amber-200 mr-2 flex items-center gap-1.5"><div className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse"></div> Unsaved Changes</span>}
                    <Button variant="outline" onClick={handleCancel} className="rounded-xl font-semibold text-slate-700 bg-white hover:bg-slate-50 border-slate-200 shadow-sm h-11 px-6 transition-all hidden sm:flex">
                        Cancel
                    </Button>
                    <Button 
                        onClick={() => saveMutation.mutate()} 
                        disabled={!testName || steps.length === 0 || saveMutation.isPending}
                        className="rounded-xl font-bold bg-indigo-600 hover:bg-indigo-700 text-white shadow-md shadow-indigo-600/20 h-11 px-8 transition-all"
                    >
                        {saveMutation.isPending ? (
                            <>
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                Saving...
                            </>
                        ) : (
                            <>
                                <Save className="mr-2 h-4 w-4" /> {isEditing ? 'Update Test' : 'Save Test'}
                            </>
                        )}
                    </Button>
                </div>
            </div>

            <div className="max-w-4xl mx-auto pb-24">
                {/* ── Test Steps Canvas ── */}
                <div className="mb-10">
                    <div className="flex items-center justify-between mb-8 px-2">
                        <div>
                            <h2 className="text-2xl font-extrabold text-slate-900">Execution Timeline</h2>
                            <p className="text-slate-500 text-sm mt-1">Design the sequence of actions and assertions.</p>
                        </div>
                    </div>

                    <div className="relative pl-4 md:pl-10">
                        {/* Vertical Timeline Line */}
                        {steps.length > 0 && (
                            <div className="absolute left-[39px] md:left-[63px] top-6 bottom-0 w-0.5 bg-slate-200 shadow-inner rounded-full" />
                        )}

                        {steps.length === 0 ? (
                            <div className="text-center py-20 px-6 rounded-3xl border-2 border-dashed border-slate-200 bg-slate-50/50">
                                <div className="w-16 h-16 bg-white rounded-full flex items-center justify-center mx-auto mb-4 shadow-sm border border-slate-100">
                                    <Plus className="h-8 w-8 text-slate-300" />
                                </div>
                                <h3 className="text-lg font-bold text-slate-700 mb-1">No execution steps</h3>
                                <p className="text-slate-500 max-w-sm mx-auto">Start building your test case by adding a navigation step below.</p>
                            </div>
                        ) : (
                            <div className="space-y-6">
                                <AnimatePresence mode='popLayout'>
                                    {steps.map((step, index) => (
                                        <motion.div
                                            key={step.id}
                                            layout
                                            initial={{ opacity: 0, x: -20, scale: 0.95 }}
                                            animate={{ opacity: 1, x: 0, scale: 1 }}
                                            exit={{ opacity: 0, scale: 0.95, filter: 'blur(4px)' }}
                                            transition={{ duration: 0.2 }}
                                        >
                                            <StepComponent
                                                step={step}
                                                index={index}
                                                updateStep={updateStep}
                                                removeStep={removeStep}
                                                moveStep={moveStep}
                                                insertStep={insertStep}
                                                isFirst={index === 0}
                                                isLast={index === steps.length - 1}
                                            />
                                        </motion.div>
                                    ))}
                                </AnimatePresence>
                            </div>
                        )}
                    </div>
                </div>

                {/* ── Add Step Floating Bar ── */}
                <div className="fixed sm:sticky bottom-6 sm:bottom-8 left-1/2 sm:left-auto sm:translate-x-0 -translate-x-1/2 z-30 w-full sm:w-auto px-4 sm:px-0 pointer-events-none">
                    <div className="bg-slate-900/95 backdrop-blur-xl border border-slate-800 p-2 sm:p-2.5 rounded-3xl sm:rounded-full shadow-2xl flex flex-wrap sm:flex-nowrap justify-center gap-1.5 sm:gap-2 max-w-full sm:max-w-fit mx-auto pointer-events-auto">
                        <Button variant="ghost" className="h-10 px-3 sm:px-4 rounded-full text-slate-300 hover:text-white hover:bg-slate-800 transition-colors shrink-0 text-xs sm:text-sm" onClick={() => addStep('goto')}>
                            <Link2 className="mr-1.5 sm:mr-2 h-4 w-4 text-emerald-400" /> Navigate
                        </Button>
                        <div className="w-px h-6 bg-slate-800 my-auto hidden sm:block"></div>
                        <Button variant="ghost" className="h-10 px-3 sm:px-4 rounded-full text-slate-300 hover:text-white hover:bg-slate-800 transition-colors shrink-0 text-xs sm:text-sm" onClick={() => addStep('click')}>
                            <MousePointerClick className="mr-1.5 sm:mr-2 h-4 w-4 text-indigo-400" /> Click
                        </Button>
                        <Button variant="ghost" className="h-10 px-3 sm:px-4 rounded-full text-slate-300 hover:text-white hover:bg-slate-800 transition-colors shrink-0 text-xs sm:text-sm" onClick={() => addStep('fill')}>
                            <TextCursorInput className="mr-1.5 sm:mr-2 h-4 w-4 text-indigo-400" /> Fill
                        </Button>
                        <Button variant="ghost" className="h-10 px-3 sm:px-4 rounded-full text-slate-300 hover:text-white hover:bg-slate-800 transition-colors shrink-0 text-xs sm:text-sm" onClick={() => addStep('expect-visible')}>
                            <CheckCircle2 className="mr-1.5 sm:mr-2 h-4 w-4 text-cyan-400" /> Assert
                        </Button>
                        <div className="w-px h-6 bg-slate-800 my-auto hidden sm:block"></div>
                        <Button variant="ghost" className="h-10 px-3 sm:px-4 rounded-full text-slate-300 hover:text-white hover:bg-slate-800 transition-colors shrink-0 text-xs sm:text-sm" onClick={() => addStep('http-request')}>
                            <FileJson className="mr-1.5 sm:mr-2 h-4 w-4 text-amber-400" /> API
                        </Button>
                        <Button variant="ghost" className="h-10 px-3 sm:px-4 rounded-full text-slate-300 hover:text-white hover:bg-slate-800 transition-colors shrink-0 text-xs sm:text-sm" onClick={() => addStep('amp-validate')}>
                            <Zap className="mr-1.5 sm:mr-2 h-4 w-4 text-violet-400" /> AMP
                        </Button>
                        <Button variant="ghost" className="h-10 px-3 sm:px-4 rounded-full text-slate-300 hover:text-white hover:bg-slate-800 transition-colors shrink-0 text-xs sm:text-sm" onClick={() => addStep('run-script')}>
                            <Code2 className="mr-1.5 sm:mr-2 h-4 w-4 text-rose-400" /> Script
                        </Button>
                    </div>
                </div>
            </div>
        </motion.div>
    );
}

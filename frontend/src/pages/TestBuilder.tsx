import { useState, useEffect, useCallback, useRef } from 'react';
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Plus, Save, Loader2, Link2, MousePointerClick, TextCursorInput, Code2, CheckCircle2, FileJson, Zap } from "lucide-react";
import { toast } from 'sonner';
import { StepComponent, TestStep } from "@/components/test-builder/StepComponent";
import { CaseHistoryDialog } from "@/components/test-builder/CaseHistoryDialog";
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, getTestCase, updateTestCase } from '@/lib/api';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { PageHeader, Pill } from '@/components/redesign/primitives';
import { cn } from '@/lib/utils';


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
    const [isAuthSetup, setIsAuthSetup] = useState(false);
    const [useAuthSession, setUseAuthSession] = useState(true);
    const [datasetText, setDatasetText] = useState('');
    const [showDataset, setShowDataset] = useState(false);
    // Per-case execution matrix override. Empty lists → inherit the module's
    // settings chain (and ultimately the user's Settings defaults).
    const [matrixBrowsers, setMatrixBrowsers] = useState<string[]>([]);
    const [matrixDevices, setMatrixDevices] = useState<string[]>([]);
    const [showMatrix, setShowMatrix] = useState(false);
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
            setIsAuthSetup(!!serverData.is_auth_setup);
            setUseAuthSession(serverData.use_auth_session !== false);
            if (serverData.dataset && Array.isArray(serverData.dataset) && serverData.dataset.length > 0) {
                setDatasetText(JSON.stringify(serverData.dataset, null, 2));
                setShowDataset(true);
            }
            if (serverData.run_matrix && (serverData.run_matrix.browsers?.length || serverData.run_matrix.devices?.length)) {
                setMatrixBrowsers(serverData.run_matrix.browsers || []);
                setMatrixDevices(serverData.run_matrix.devices || []);
                setShowMatrix(true);
            }

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
            type: '' as TestStep['type'],  // blank — user picks the action from the dropdown
            selector: '',
            value: ''
        };
        const newSteps = [...steps];
        newSteps.splice(index + 1, 0, newStep);
        setSteps(newSteps);
    };

    const saveMutation = useMutation({
        mutationFn: async () => {
            const unchosen = steps.findIndex(s => !s.type);
            if (unchosen !== -1) {
                throw new Error(`Step ${unchosen + 1} has no action yet — choose one from its dropdown`);
            }
            let dataset: Record<string, unknown>[] | null = null;
            if (datasetText.trim()) {
                let parsed: unknown;
                try {
                    parsed = JSON.parse(datasetText);
                } catch {
                    throw new Error('Dataset is not valid JSON');
                }
                if (!Array.isArray(parsed) || parsed.some(r => typeof r !== 'object' || r === null || Array.isArray(r))) {
                    throw new Error('Dataset must be a JSON array of objects, e.g. [{"user": "a"}, {"user": "b"}]');
                }
                dataset = parsed as Record<string, unknown>[];
            }

            const payload = {
                name: testName,
                test_suite_id: parseInt(suiteId || '0'),
                steps: steps,
                is_auth_setup: isAuthSetup,
                use_auth_session: useAuthSession,
                dataset: dataset,
                run_matrix: (matrixBrowsers.length || matrixDevices.length)
                    ? { browsers: matrixBrowsers, devices: matrixDevices }
                    : null
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
            toast.error(error?.response?.data?.detail || error?.message || 'Failed to save test case');
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


    // Suite name for the breadcrumb only. Same key/fn as SuiteDetails, so it
    // is usually already cached when arriving from the suite.
    const { data: suiteForCrumb } = useQuery({
        queryKey: ['suite', suiteId],
        queryFn: () => api.get(`/suites/${suiteId}`).then(res => res.data),
        enabled: !!suiteId,
        retry: 1,
    });

    const hasMatrix = !!(matrixBrowsers.length || matrixDevices.length);
    const hasDataset = !!datasetText.trim();

    const toggleChip = (on: boolean, pressed: boolean, label: string, title: string, onClick: () => void) => (
        <button
            type="button"
            onClick={onClick}
            aria-pressed={pressed}
            title={title}
            className={cn(
                'inline-flex min-h-10 items-center gap-2 rounded-full border px-4 text-sm font-medium transition-colors',
                pressed ? 'border-ink bg-white text-ink' : 'border-border bg-white text-ink-soft hover:text-ink',
            )}
        >
            {label}
            {on && <><span className="h-2 w-2 rounded-full bg-[#8FD3B6]" aria-hidden="true" /><span className="sr-only">(set)</span></>}
        </button>
    );

    const addButton = (label: string, Icon: typeof Plus, type: TestStep['type']) => (
        <Button variant="ghost" className="h-10 shrink-0 px-3.5 text-ink-soft hover:text-ink" onClick={() => addStep(type)}>
            <Icon className="h-4 w-4" /> {label}
        </Button>
    );

    return (
        <div className="flex flex-col gap-5 pb-28">
            {/* Header stays in reach while scrolling a long case, as before. No
                backdrop-filter here: it would trap the fixed History modal. */}
            <div className="sticky top-14 z-30 -mt-2 bg-background pb-3 pt-2 lg:top-0">
                <PageHeader
                    crumbs={<>
                        <Link to="/suites" className="hover:text-ink">Tests</Link>
                        <span>/</span>
                        <Link to={`/suites/${suiteId}`} className="hover:text-ink">{suiteForCrumb?.name || 'Suite'}</Link>
                        <span>/</span>
                        <span>{isEditing ? (serverData?.name || 'Test') : 'New test'}</span>
                    </>}
                    title={isEditing ? 'Edit test' : 'New test'}
                    badge={isDirty ? <Pill tone="butter"><span className="h-1.5 w-1.5 rounded-full bg-[#F2C94C]" aria-hidden="true" /> Unsaved changes</Pill> : undefined}
                    actions={<>
                        {isEditing && caseId && (
                            <CaseHistoryDialog
                                caseId={parseInt(caseId)}
                                onRestored={() => {
                                    // The server-side case changed under the editor —
                                    // drop any local draft and rehydrate from scratch.
                                    localStorage.removeItem(draftKey);
                                    window.location.reload();
                                }}
                            />
                        )}
                        <Button variant="outline" onClick={handleCancel}>
                            Cancel
                        </Button>
                        <Button
                            onClick={() => saveMutation.mutate()}
                            disabled={!testName || steps.length === 0 || saveMutation.isPending}
                        >
                            {saveMutation.isPending ? (
                                <>
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                    Saving…
                                </>
                            ) : (
                                <>
                                    <Save className="h-4 w-4" /> {isEditing ? 'Update test' : 'Save test'}
                                </>
                            )}
                        </Button>
                    </>}
                />
            </div>

            <div className="mx-auto flex w-full max-w-4xl flex-col gap-5">
                {/* ── Name + case options ── */}
                <section className="flex flex-col gap-4 rounded-3xl border border-border bg-white p-5 sm:p-6" aria-label="Test details">
                    <div className="flex flex-col gap-1.5">
                        <label htmlFor="test-name" className="text-[13px] font-semibold text-ink">Test name</label>
                        <Input
                            id="test-name"
                            placeholder="e.g. Sign in and open the dashboard"
                            value={testName}
                            onChange={(e) => setTestName(e.target.value)}
                            className="h-12 rounded-xl text-lg font-semibold text-ink"
                        />
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                        {toggleChip(hasDataset, showDataset, 'Dataset', 'Run this case once per dataset row; reference values in steps as {{data.KEY}}', () => setShowDataset(!showDataset))}
                        {toggleChip(hasMatrix, showMatrix, 'Browsers and devices', 'Override which browsers/devices this case runs on. Empty = inherit from the module.', () => setShowMatrix(!showMatrix))}
                        <span className="mx-1 hidden h-6 w-px bg-border sm:block" aria-hidden="true" />
                        <label className="flex min-h-10 cursor-pointer select-none items-center gap-2 text-sm text-ink" title="A passing run of this case captures the logged-in session (cookies + storage) and stores it for the project. Other cases then start already logged in.">
                            <input type="checkbox" checked={isAuthSetup} onChange={(e) => { setIsAuthSetup(e.target.checked); setIsDirty(true); }} className="h-[18px] w-[18px] accent-ink" />
                            Auth setup case
                        </label>
                        {!isAuthSetup && (
                            <label className="flex min-h-10 cursor-pointer select-none items-center gap-2 text-sm text-ink" title="Uncheck for tests that must exercise the real login flow — they will start from a clean, logged-out browser.">
                                <input type="checkbox" checked={useAuthSession} onChange={(e) => { setUseAuthSession(e.target.checked); setIsDirty(true); }} className="h-[18px] w-[18px] accent-ink" />
                                Use auth session
                            </label>
                        )}
                    </div>
                </section>

                {/* ── Per-case execution matrix panel ── */}
                {showMatrix && (
                    <section className="flex flex-col gap-3 rounded-3xl border border-border bg-white p-5 sm:p-6" aria-label="Browsers and devices">
                        <div className="flex items-center justify-between gap-2">
                            <h2 className="text-base font-semibold text-ink">Browsers and devices for this test</h2>
                            <Button type="button" variant="ghost" size="sm" onClick={() => { setMatrixBrowsers([]); setMatrixDevices([]); setIsDirty(true); }}>Clear (inherit)</Button>
                        </div>
                        <p className="text-sm leading-relaxed text-ink-soft">
                            This test runs once per selected browser and device. Leave everything unticked to
                            inherit the module's matrix (Module settings, Execution matrix), which itself
                            inherits from parent modules and your Settings defaults.
                        </p>
                        <div className="grid gap-4 sm:grid-cols-2">
                            <fieldset className="flex flex-col gap-1.5">
                                <legend className="mb-1.5 text-[13px] font-semibold text-ink">Browsers</legend>
                                {['chromium', 'firefox', 'webkit'].map((b) => (
                                    <label key={b} className="flex min-h-11 cursor-pointer select-none items-center gap-3 rounded-xl border border-border bg-white px-3.5 text-sm text-ink">
                                        <input
                                            type="checkbox"
                                            className="h-[18px] w-[18px] accent-ink"
                                            checked={matrixBrowsers.includes(b)}
                                            onChange={(e) => { setMatrixBrowsers(e.target.checked ? [...matrixBrowsers, b] : matrixBrowsers.filter(x => x !== b)); setIsDirty(true); }}
                                        />
                                        <span className="capitalize">{b}</span>
                                    </label>
                                ))}
                            </fieldset>
                            <fieldset className="flex flex-col gap-1.5">
                                <legend className="mb-1.5 text-[13px] font-semibold text-ink">Devices</legend>
                                {['Desktop', 'Mobile (Generic)', 'iPhone 13', 'Pixel 5'].map((d) => (
                                    <label key={d} className="flex min-h-11 cursor-pointer select-none items-center gap-3 rounded-xl border border-border bg-white px-3.5 text-sm text-ink">
                                        <input
                                            type="checkbox"
                                            className="h-[18px] w-[18px] accent-ink"
                                            checked={matrixDevices.includes(d)}
                                            onChange={(e) => { setMatrixDevices(e.target.checked ? [...matrixDevices, d] : matrixDevices.filter(x => x !== d)); setIsDirty(true); }}
                                        />
                                        {d}
                                    </label>
                                ))}
                            </fieldset>
                        </div>
                    </section>
                )}

                {/* ── Data-driven dataset panel ── */}
                {showDataset && (
                    <section className="flex flex-col gap-3 rounded-3xl border border-border bg-white p-5 sm:p-6" aria-label="Dataset">
                        <div className="flex items-center justify-between gap-2">
                            <h2 className="text-base font-semibold text-ink">Dataset</h2>
                            <Button type="button" variant="ghost" size="sm" onClick={() => { setDatasetText(''); setIsDirty(true); }}>Clear</Button>
                        </div>
                        <p className="text-sm leading-relaxed text-ink-soft">
                            A JSON array of row objects. The test runs once per row; use a value in any step as <code className="font-mono text-[13px] text-ink">{'{{data.KEY}}'}</code>. Leave empty for a normal single run.
                        </p>
                        <textarea
                            aria-label="Dataset JSON"
                            value={datasetText}
                            onChange={(e) => { setDatasetText(e.target.value); setIsDirty(true); }}
                            placeholder={'[\n  {"query": "election results", "expect": "Bihar"},\n  {"query": "cricket score", "expect": "India"}\n]'}
                            spellCheck={false}
                            className="h-40 w-full rounded-2xl border border-border bg-background p-4 font-mono text-xs text-ink focus:outline-none focus:ring-2 focus:ring-ring"
                        />
                    </section>
                )}

                {/* ── Steps ── */}
                <section className="flex flex-col gap-5 rounded-3xl border border-border bg-white p-5 sm:p-6" aria-label="Steps">
                    <div className="flex items-baseline justify-between gap-2">
                        <h2 className="text-lg font-bold tracking-[-0.01em] text-ink">Steps</h2>
                        <span className="text-sm text-ink-soft">{steps.length} step{steps.length === 1 ? '' : 's'}</span>
                    </div>

                    {steps.length === 0 ? (
                        <div className="flex flex-col items-center gap-1.5 rounded-2xl border border-dashed border-border bg-background px-6 py-14 text-center">
                            <p className="text-base font-semibold text-ink">No steps yet</p>
                            <p className="max-w-sm text-sm text-ink-soft">Start with a Go to step, then add clicks, typing and checks from the bar below.</p>
                        </div>
                    ) : (
                        <div className="flex flex-col gap-6">
                            <AnimatePresence mode='popLayout'>
                                {steps.map((step, index) => (
                                    <motion.div
                                        key={step.id}
                                        layout
                                        initial={{ opacity: 0, y: 8 }}
                                        animate={{ opacity: 1, y: 0 }}
                                        exit={{ opacity: 0, scale: 0.98 }}
                                        transition={{ duration: 0.18 }}
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
                                            pickerUrl={steps.find(s => s.type === 'goto')?.value}
                                        />
                                    </motion.div>
                                ))}
                            </AnimatePresence>
                        </div>
                    )}
                </section>

                {/* ── Add step bar ── */}
                <div className="pointer-events-none fixed bottom-6 left-1/2 z-30 w-full -translate-x-1/2 px-4 sm:sticky sm:bottom-8 sm:left-auto sm:translate-x-0 sm:px-0">
                    <div role="toolbar" aria-label="Add a step" className="pointer-events-auto mx-auto flex max-w-full flex-wrap justify-center gap-1 rounded-3xl border border-border bg-white p-1.5 shadow-[0_12px_40px_rgba(28,27,34,0.12)] sm:max-w-fit sm:flex-nowrap sm:rounded-full">
                        <Button className="h-10 shrink-0 px-4" onClick={() => addStep('' as TestStep['type'])}>
                            <Plus className="h-4 w-4" /> Step
                        </Button>
                        {addButton('Go to', Link2, 'goto')}
                        {addButton('Click', MousePointerClick, 'click')}
                        {addButton('Type', TextCursorInput, 'fill')}
                        {addButton('Check', CheckCircle2, 'expect-visible')}
                        {addButton('API', FileJson, 'http-request')}
                        {addButton('AMP', Zap, 'amp-validate')}
                        {addButton('Script', Code2, 'run-script')}
                    </div>
                </div>
            </div>
        </div>
    );
}

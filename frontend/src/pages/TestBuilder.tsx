import { useState, useEffect, useCallback, useRef } from 'react';
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Plus, Save, Loader2, ArrowLeft, Link2, MousePointerClick, TextCursorInput, Code2, CheckCircle2, FileJson, Zap, Database, Monitor } from "lucide-react";
import { toast } from 'sonner';
import { StepComponent, TestStep } from "@/components/test-builder/StepComponent";
import { CaseHistoryDialog } from "@/components/test-builder/CaseHistoryDialog";
import { CaseOwnershipPanel } from "@/components/ownership/CaseOwnershipPanel";
import { UsedByNotice } from "@/components/test-builder/UsedByNotice";
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, getTestCase, updateTestCase } from '@/lib/api';
import { useNavigate, useParams } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { newId } from '@/lib/ids';
import { cn } from '@/lib/utils';
import { Eyebrow, PageHeader, Pill } from '@/components/redesign/primitives';

const CASE_PRIORITIES = ['', 'critical', 'high', 'medium', 'low'];

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
    // TestCase.priority: "critical" cases drive the release-readiness and
    // quality-gate critical-case rules, so automated cases need it too.
    const [priority, setPriority] = useState('');
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
            setPriority(serverData.priority || '');
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
            id: newId(),
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
            id: newId(),
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
                priority: priority || null,
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

    const hasMatrix = matrixBrowsers.length > 0 || matrixDevices.length > 0;
    const hasDataset = !!datasetText.trim();
    const toggleClass = (open: boolean, configured: boolean) => cn(
        'inline-flex min-h-9 items-center gap-1.5 rounded-full border px-3.5 text-sm font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ink/20',
        configured
            ? 'border-transparent bg-pastel-lavender text-pastel-ink-lavender'
            : open ? 'border-ink/30 bg-white text-ink' : 'border-border bg-white text-ink-soft hover:bg-background hover:text-ink'
    );
    const addButtonClass = 'h-9 shrink-0 rounded-full px-3 text-sm font-medium text-ink-soft hover:bg-background hover:text-ink';

    return (
        <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="flex w-full flex-col gap-5 pb-16"
        >
            {/* Header stays in view on wide screens so Save is always reachable. */}
            <div className="lg:sticky lg:top-0 lg:z-40 lg:-mt-3 lg:bg-background lg:pt-3">
            <PageHeader
                crumbs={<>
                    <button type="button" onClick={handleCancel} className="inline-flex items-center gap-1 hover:text-ink">
                        <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" /> Back
                    </button>
                    <span aria-hidden="true">/</span>
                    <span>{isEditing ? 'Edit Test Case' : 'New Test Case'}</span>
                </>}
                title={<span className="break-words [overflow-wrap:anywhere]">{testName.trim() || (isEditing ? 'Untitled test' : 'New test case')}</span>}
                badge={isDirty && (
                    <Pill tone="butter">
                        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" aria-hidden="true" /> Unsaved changes
                    </Pill>
                )}
                subtitle={`${steps.length} step${steps.length === 1 ? '' : 's'} · nothing is saved until you press ${isEditing ? 'Update Test' : 'Save Test'}.`}
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
                                Saving...
                            </>
                        ) : (
                            <>
                                <Save className="h-4 w-4" /> {isEditing ? 'Update Test' : 'Save Test'}
                            </>
                        )}
                    </Button>
                </>}
                toolbar={<>
                    <button
                        type="button"
                        onClick={() => setShowDataset(!showDataset)}
                        aria-expanded={showDataset}
                        aria-controls="test-builder-dataset"
                        className={toggleClass(showDataset, hasDataset)}
                        title="Run this case once per dataset row; reference values in steps as {{data.KEY}}"
                    >
                        <Database className="h-4 w-4" aria-hidden="true" />
                        Dataset{hasDataset ? ' ✓' : ''}
                    </button>
                    <button
                        type="button"
                        onClick={() => setShowMatrix(!showMatrix)}
                        aria-expanded={showMatrix}
                        aria-controls="test-builder-matrix"
                        className={toggleClass(showMatrix, hasMatrix)}
                        title="Override which browsers/devices this case runs on. Empty = inherit from the module."
                    >
                        <Monitor className="h-4 w-4" aria-hidden="true" />
                        Matrix{hasMatrix ? ' ✓' : ''}
                    </button>
                </>}
            />
            </div>

            {/* ── Name, priority and login behaviour ── */}
            <section aria-labelledby="test-builder-details" className="flex flex-col gap-4 rounded-3xl border border-border bg-white p-5 sm:p-6">
                <h2 id="test-builder-details" className="text-sm font-semibold text-ink">Test details</h2>
                <div className="flex flex-wrap items-end gap-4">
                    <label htmlFor="test-builder-name" className="flex min-w-[240px] flex-[3_1_320px] flex-col gap-1 text-xs font-semibold text-ink-soft">
                        Name
                        <Input
                            id="test-builder-name"
                            placeholder="Enter Test Case Name..."
                            value={testName}
                            onChange={(e) => setTestName(e.target.value)}
                            className="h-11 rounded-xl border-border bg-white px-3 text-base font-semibold text-ink shadow-none placeholder:font-normal placeholder:text-ink-soft/60"
                        />
                    </label>
                    <label className="flex min-w-[160px] flex-[1_1_160px] flex-col gap-1 text-xs font-semibold text-ink-soft" title="Critical cases count for release readiness and the quality gate's critical-case rule.">
                        Priority
                        <select
                            aria-label="Priority"
                            value={priority}
                            onChange={(e) => { setPriority(e.target.value); setIsDirty(true); }}
                            className="h-11 rounded-xl border border-border bg-white px-3 text-sm font-normal text-ink"
                        >
                            {CASE_PRIORITIES.map((p) => <option key={p} value={p}>{p ? p[0].toUpperCase() + p.slice(1) : 'None'}</option>)}
                        </select>
                    </label>
                </div>
                <div className="flex flex-wrap gap-x-6 gap-y-2 border-t border-[#F0EEE8] pt-4">
                    <label className="flex cursor-pointer select-none items-center gap-2 text-sm text-ink" title="A passing run of this case captures the logged-in session (cookies + storage) and stores it for the project. Other cases then start already logged in.">
                        <input type="checkbox" checked={isAuthSetup} onChange={(e) => { setIsAuthSetup(e.target.checked); setIsDirty(true); }} className="h-[18px] w-[18px] accent-ink" />
                        Auth setup case
                    </label>
                    {!isAuthSetup && (
                        <label className="flex cursor-pointer select-none items-center gap-2 text-sm text-ink" title="Uncheck for tests that must exercise the real login flow — they will start from a clean, logged-out browser.">
                            <input type="checkbox" checked={useAuthSession} onChange={(e) => { setUseAuthSession(e.target.checked); setIsDirty(true); }} className="h-[18px] w-[18px] accent-ink" />
                            Use auth session
                        </label>
                    )}
                </div>
            </section>

            {isEditing && caseId && serverData?.project_id && (
                // The panel carries its own bottom margin; the page's gap spaces it here.
                <div className="[&>section]:mb-0">
                    <CaseOwnershipPanel caseId={parseInt(caseId)} projectId={serverData.project_id} />
                </div>
            )}
            {isEditing && caseId && (
                <div className="empty:hidden [&>div]:mb-0">
                    <UsedByNotice caseId={parseInt(caseId)} />
                </div>
            )}

            {/* ── Per-case execution matrix panel ── */}
            {showMatrix && (
                <section id="test-builder-matrix" aria-labelledby="test-builder-matrix-title" className="flex flex-col gap-3 rounded-3xl border border-border bg-white p-5 sm:p-6">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        <h2 id="test-builder-matrix-title" className="text-sm font-semibold text-ink">Execution Matrix (this case only)</h2>
                        <Button type="button" variant="outline" size="sm" onClick={() => { setMatrixBrowsers([]); setMatrixDevices([]); setIsDirty(true); }}>Clear (inherit)</Button>
                    </div>
                    <p className="text-sm text-ink-soft">
                        This case runs once per selected browser × device. Leave everything unchecked to
                        inherit the module's matrix (Module Settings → Execution Matrix), which itself
                        inherits from parent modules and your Settings defaults.
                    </p>
                    <div className="grid gap-4 sm:grid-cols-2">
                        <fieldset className="flex flex-col gap-1.5">
                            <legend className="mb-1.5"><Eyebrow>Browsers</Eyebrow></legend>
                            {['chromium', 'firefox', 'webkit'].map((b) => (
                                <label key={b} className="flex min-h-10 cursor-pointer select-none items-center gap-2 rounded-xl border border-border bg-white px-3 py-2 text-sm text-ink hover:bg-background">
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
                            <legend className="mb-1.5"><Eyebrow>Devices</Eyebrow></legend>
                            {['Desktop', 'Mobile (Generic)', 'iPhone 13', 'Pixel 5'].map((d) => (
                                <label key={d} className="flex min-h-10 cursor-pointer select-none items-center gap-2 rounded-xl border border-border bg-white px-3 py-2 text-sm text-ink hover:bg-background">
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
                <section id="test-builder-dataset" aria-labelledby="test-builder-dataset-title" className="flex flex-col gap-3 rounded-3xl border border-border bg-white p-5 sm:p-6">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        <h2 id="test-builder-dataset-title" className="text-sm font-semibold text-ink">Dataset (data-driven runs)</h2>
                        <Button type="button" variant="outline" size="sm" onClick={() => { setDatasetText(''); setIsDirty(true); }}>Clear</Button>
                    </div>
                    <p className="text-sm text-ink-soft">
                        A JSON array of row objects. The case runs once per row; reference values in any step as <code className="rounded bg-background px-1 font-mono text-xs text-ink">{'{{data.KEY}}'}</code>. Leave empty for a normal single run.
                    </p>
                    <textarea
                        aria-labelledby="test-builder-dataset-title"
                        value={datasetText}
                        onChange={(e) => { setDatasetText(e.target.value); setIsDirty(true); }}
                        placeholder={'[\n  {"query": "election results", "expect": "Bihar"},\n  {"query": "cricket score", "expect": "India"}\n]'}
                        spellCheck={false}
                        className="h-40 w-full rounded-xl border border-border bg-white p-3 font-mono text-xs text-ink focus:outline-none focus:ring-2 focus:ring-ink/20"
                    />
                </section>
            )}

            {/* ── Test Steps ── */}
            <section aria-labelledby="test-builder-steps" className="flex flex-col rounded-3xl border border-border bg-white">
                <div className="flex flex-wrap items-end justify-between gap-2 border-b border-[#F0EEE8] px-5 py-4 sm:px-6">
                    <div className="flex flex-col gap-0.5">
                        <h2 id="test-builder-steps" className="text-lg font-semibold text-ink">
                            Execution Timeline <span className="ml-1 font-mono text-xs font-normal text-ink-soft">{steps.length}</span>
                        </h2>
                        <p className="text-sm text-ink-soft">Design the sequence of actions and assertions.</p>
                    </div>
                </div>

                <div className="px-4 py-5 sm:px-6">
                    {steps.length === 0 ? (
                        <div className="flex flex-col items-center gap-2 rounded-2xl bg-background px-6 py-14 text-center">
                            <span className="flex h-12 w-12 items-center justify-center rounded-full border border-border bg-white" aria-hidden="true">
                                <Plus className="h-6 w-6 text-ink-soft" />
                            </span>
                            <p className="text-base font-semibold text-ink">No execution steps</p>
                            <p className="max-w-sm text-sm text-ink-soft">Start building your test case by adding a navigation step below.</p>
                        </div>
                    ) : (
                        <div className="relative">
                            {/* Vertical timeline line through the step number badges */}
                            <div className="absolute bottom-6 left-[11px] top-6 w-0.5 rounded-full bg-[#ECEAE3] sm:left-[27px]" aria-hidden="true" />
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
                                                pickerUrl={steps.find(s => s.type === 'goto')?.value}
                                            />
                                        </motion.div>
                                    ))}
                                </AnimatePresence>
                            </div>
                        </div>
                    )}
                </div>

                {/* ── Add-step bar: sticks to the bottom of the viewport while the timeline scrolls ── */}
                <div className="sticky bottom-4 z-30 px-3 pb-4 sm:px-5">
                    <div role="toolbar" aria-label="Add a step" className="flex flex-wrap items-center justify-center gap-1 rounded-3xl border border-border bg-white/95 p-1.5 shadow-[0_12px_40px_rgba(28,27,34,0.10)] backdrop-blur sm:rounded-full">
                        <Button size="sm" className="h-9 px-4 text-sm" onClick={() => addStep('' as TestStep['type'])}>
                            <Plus className="h-4 w-4" /> Step
                        </Button>
                        <span className="mx-1 hidden h-5 w-px bg-border sm:block" aria-hidden="true" />
                        <Button variant="ghost" className={addButtonClass} onClick={() => addStep('goto')}>
                            <Link2 className="h-4 w-4" /> Navigate
                        </Button>
                        <span className="mx-1 hidden h-5 w-px bg-border sm:block" aria-hidden="true" />
                        <Button variant="ghost" className={addButtonClass} onClick={() => addStep('click')}>
                            <MousePointerClick className="h-4 w-4" /> Click
                        </Button>
                        <Button variant="ghost" className={addButtonClass} onClick={() => addStep('fill')}>
                            <TextCursorInput className="h-4 w-4" /> Fill
                        </Button>
                        <Button variant="ghost" className={addButtonClass} onClick={() => addStep('expect-visible')}>
                            <CheckCircle2 className="h-4 w-4" /> Assert
                        </Button>
                        <span className="mx-1 hidden h-5 w-px bg-border sm:block" aria-hidden="true" />
                        <Button variant="ghost" className={addButtonClass} onClick={() => addStep('http-request')}>
                            <FileJson className="h-4 w-4" /> API
                        </Button>
                        <Button variant="ghost" className={addButtonClass} onClick={() => addStep('amp-validate')}>
                            <Zap className="h-4 w-4" /> AMP
                        </Button>
                        <Button variant="ghost" className={addButtonClass} onClick={() => addStep('run-script')}>
                            <Code2 className="h-4 w-4" /> Script
                        </Button>
                    </div>
                </div>
            </section>
        </motion.div>
    );
}

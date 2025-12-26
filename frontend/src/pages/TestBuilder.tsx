import { useState } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Plus, Save, Loader2 } from "lucide-react";
import { toast } from 'sonner';
import { StepComponent, TestStep } from "@/components/test-builder/StepComponent";
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, getTestCase, updateTestCase } from '@/lib/api';
import { useNavigate, useParams } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';

export default function TestBuilder() {
    const { suiteId, caseId } = useParams();
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const isEditing = !!caseId;

    const [testName, setTestName] = useState('');
    const [steps, setSteps] = useState<TestStep[]>([]);

    // Load existing data if editing
    useQuery({
        queryKey: ['testCase', caseId],
        queryFn: async () => {
            if (!caseId) return null;
            const data = await getTestCase(parseInt(caseId));
            setTestName(data.name);
            setSteps(data.steps || []);
            return data;
        },
        enabled: isEditing
    });

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
            queryClient.invalidateQueries({ queryKey: ['suite', suiteId] });
            toast.success(isEditing ? 'Test case updated successfully' : 'Test case created successfully');
            navigate(`/suites/${suiteId}`);
        },
        onError: (error: any) => {
            toast.error(error?.response?.data?.detail || 'Failed to save test case');
        }
    });

    return (
        <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="max-w-4xl mx-auto space-y-6"
        >
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold text-gray-900">{isEditing ? 'Edit Test Case' : 'Create Test Case'}</h1>
                    <p className="text-gray-500">Design your automated test sequence</p>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" onClick={() => navigate(-1)}>Cancel</Button>
                    <Button onClick={() => saveMutation.mutate()} disabled={!testName || steps.length === 0 || saveMutation.isPending}>
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

            <Card>
                <CardHeader>
                    <CardTitle>Test Details</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid gap-2">
                        <Label htmlFor="name">Test Name</Label>
                        <Input
                            id="name"
                            placeholder="e.g., Verify Login Flow"
                            value={testName}
                            onChange={(e) => setTestName(e.target.value)}
                        />
                    </div>
                </CardContent>
            </Card>

            <div className="space-y-4">
                <div className="flex items-center justify-between">
                    <h2 className="text-xl font-semibold">Test Steps</h2>
                    <div className="flex gap-2">
                        <Button variant="secondary" size="sm" onClick={() => addStep('goto')}>+ URL</Button>
                        <Button variant="secondary" size="sm" onClick={() => addStep('click')}>+ Click</Button>
                        <Button variant="secondary" size="sm" onClick={() => addStep('fill')}>+ Fill</Button>
                        <Button variant="secondary" size="sm" onClick={() => addStep('expect-visible')}>+ Assert</Button>
                        <Button variant="secondary" size="sm" onClick={() => addStep('hover')}>+ Hover</Button>
                        <Button variant="secondary" size="sm" onClick={() => addStep('press-key')}>+ Key</Button>
                    </div>
                </div>

                <div className="bg-gray-50 p-4 rounded-lg min-h-[200px] border border-dashed border-gray-300">
                    {steps.length === 0 ? (
                        <div className="text-center py-10 text-gray-400">
                            No steps added yet. Start by adding a "Go to URL" step.
                        </div>
                    ) : (
                        <AnimatePresence mode='popLayout'>
                            {steps.map((step, index) => (
                                <motion.div
                                    key={step.id}
                                    layout
                                    initial={{ opacity: 0, x: -20 }}
                                    animate={{ opacity: 1, x: 0 }}
                                    exit={{ opacity: 0, x: 20 }}
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
                    )}

                    <Button
                        variant="outline"
                        className="w-full mt-4 border-dashed"
                        onClick={() => addStep()}
                    >
                        <Plus className="mr-2 h-4 w-4" /> Add Next Step
                    </Button>
                </div>
            </div>
        </motion.div >
    );
}

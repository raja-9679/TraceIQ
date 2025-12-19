import React from 'react';
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Trash2, GripVertical } from "lucide-react";

export interface TestStep {
    id: string;
    type: 'goto' | 'click' | 'fill' | 'check' | 'switch-frame' | 'expect-visible' | 'expect-hidden' | 'expect-text' | 'expect-url' | 'hover' | 'select-option' | 'press-key' | 'screenshot' | 'scroll-to' | 'wait-timeout';
    selector?: string;
    value?: string;
}

interface StepComponentProps {
    step: TestStep;
    index: number;
    updateStep: (id: string, field: keyof TestStep, value: string) => void;
    removeStep: (id: string) => void;
}

export const StepComponent: React.FC<StepComponentProps> = ({ step, updateStep, removeStep }) => {
    return (
        <Card className="mb-4 relative group hover:border-primary/50 transition-colors">
            <div className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-300 cursor-move opacity-0 group-hover:opacity-100 transition-opacity">
                <GripVertical size={20} />
            </div>
            <CardContent className="p-4 pl-10 flex items-start gap-4">
                <div className="flex-1 grid grid-cols-12 gap-4">
                    {/* Action Type */}
                    <div className="col-span-3">
                        <select
                            className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                            value={step.type}
                            onChange={(e) => updateStep(step.id, 'type', e.target.value)}
                        >
                            <option value="goto">Go to URL</option>
                            <option value="click">Click</option>
                            <option value="fill">Fill Input</option>
                            <option value="check">Check Box</option>
                            <option value="switch-frame">Switch Frame</option>
                            <option value="expect-visible">Expect Visible</option>
                            <option value="expect-hidden">Expect Hidden</option>
                            <option value="expect-text">Expect Text</option>
                            <option value="expect-url">Expect URL</option>
                            <option value="hover">Hover</option>
                            <option value="select-option">Select Option</option>
                            <option value="press-key">Press Key</option>
                            <option value="screenshot">Take Screenshot</option>
                            <option value="scroll-to">Scroll To</option>
                            <option value="wait-timeout">Wait (ms)</option>
                        </select>
                    </div>

                    {/* Selector / URL Input */}
                    <div className={`${step.type === 'goto' || step.type === 'expect-url' ? 'col-span-9' : 'col-span-5'}`}>
                        <Input
                            placeholder={
                                step.type === 'goto' ? "https://example.com" :
                                    step.type === 'press-key' ? "Key (e.g., Enter)" :
                                        step.type === 'wait-timeout' ? "Timeout in ms" :
                                            step.type === 'screenshot' ? "Screenshot name" :
                                                "Selector (e.g., #submit-btn)"
                            }
                            value={(step.type === 'goto' || step.type === 'expect-url' || step.type === 'press-key' || step.type === 'wait-timeout' || step.type === 'screenshot' ? step.value : step.selector) || ''}
                            onChange={(e) => updateStep(step.id, (step.type === 'goto' || step.type === 'expect-url' || step.type === 'press-key' || step.type === 'wait-timeout' || step.type === 'screenshot' ? 'value' : 'selector'), e.target.value)}
                        />
                    </div>

                    {/* Value Input (Only for specific types) */}
                    {(step.type === 'fill' || step.type === 'expect-text' || step.type === 'select-option') && (
                        <div className="col-span-4">
                            <Input
                                placeholder={step.type === 'fill' ? "Value to type" : step.type === 'select-option' ? "Option value" : "Expected text"}
                                value={step.value || ''}
                                onChange={(e) => updateStep(step.id, 'value', e.target.value)}
                            />
                        </div>
                    )}
                </div>

                {/* Delete Button */}
                <Button
                    variant="ghost"
                    size="icon"
                    className="text-gray-400 hover:text-red-500"
                    onClick={() => removeStep(step.id)}
                >
                    <Trash2 size={18} />
                </Button>
            </CardContent>
        </Card>
    );
};

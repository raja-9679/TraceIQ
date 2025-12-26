import React from 'react';
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Trash2, GripVertical, ArrowUp, ArrowDown, PlusCircle } from "lucide-react";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";

export interface TestStep {
    id: string;
    type: 'goto' | 'click' | 'fill' | 'check' | 'switch-frame' | 'expect-visible' | 'expect-hidden' | 'expect-text' | 'expect-url' | 'hover' | 'select-option' | 'press-key' | 'screenshot' | 'scroll-to' | 'wait-timeout';
    selector?: string;
    value?: string;
    params?: {
        wait_until?: 'load' | 'domcontentloaded' | 'networkidle' | 'commit';
        [key: string]: any;
    };
}
interface StepComponentProps {
    step: TestStep;
    index: number;
    updateStep: (id: string, field: keyof TestStep, value: any) => void;
    removeStep: (id: string) => void;
    moveStep: (index: number, direction: 'up' | 'down') => void;
    insertStep: (index: number) => void;
    isFirst: boolean;
    isLast: boolean;
}

export const StepComponent: React.FC<StepComponentProps> = ({ step, index, updateStep, removeStep, moveStep, insertStep, isFirst, isLast }) => {
    const updateParams = (key: string, value: any) => {
        const newParams = { ...(step.params || {}), [key]: value };
        updateStep(step.id, 'params', newParams);
    };

    return (
        <Card className="mb-4 relative group hover:border-primary/50 transition-colors">
            <div className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-300 cursor-move opacity-0 group-hover:opacity-100 transition-opacity">
                <GripVertical size={20} />
            </div>
            <CardContent className="p-4 pl-10 flex items-start gap-4">
                <div className="flex-1 grid grid-cols-12 gap-4">
                    {/* Action Type */}
                    <div className="col-span-4">
                        <Select
                            value={step.type}
                            onValueChange={(value) => updateStep(step.id, 'type', value)}
                        >
                            <SelectTrigger className="w-full">
                                <SelectValue placeholder="Select action" />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="goto">Go to URL</SelectItem>
                                <SelectItem value="click">Click</SelectItem>
                                <SelectItem value="fill">Fill Input</SelectItem>
                                <SelectItem value="check">Check Box</SelectItem>
                                <SelectItem value="switch-frame">Switch Frame</SelectItem>
                                <SelectItem value="expect-visible">Expect Visible</SelectItem>
                                <SelectItem value="expect-hidden">Expect Hidden</SelectItem>
                                <SelectItem value="expect-text">Expect Text</SelectItem>
                                <SelectItem value="expect-url">Expect URL</SelectItem>
                                <SelectItem value="hover">Hover</SelectItem>
                                <SelectItem value="select-option">Select Option</SelectItem>
                                <SelectItem value="press-key">Press Key</SelectItem>
                                <SelectItem value="screenshot">Take Screenshot</SelectItem>
                                <SelectItem value="scroll-to">Scroll To</SelectItem>
                                <SelectItem value="wait-timeout">Wait (ms)</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>

                    {/* Selector / URL Input */}
                    <div className={`${step.type === 'goto' ? 'col-span-5' :
                            step.type === 'expect-url' ? 'col-span-8' :
                                (step.type === 'fill' || step.type === 'expect-text' || step.type === 'select-option') ? 'col-span-4' :
                                    'col-span-8'
                        }`}>
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

                    {/* Wait Strategy for Goto */}
                    {step.type === 'goto' && (
                        <div className="col-span-3">
                            <Select
                                value={step.params?.wait_until || 'domcontentloaded'}
                                onValueChange={(value) => updateParams('wait_until', value)}
                            >
                                <SelectTrigger className="w-full">
                                    <SelectValue placeholder="Wait Until" />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="domcontentloaded">DOM Loaded</SelectItem>
                                    <SelectItem value="load">Fully Loaded</SelectItem>
                                    <SelectItem value="networkidle">Network Idle</SelectItem>
                                    <SelectItem value="commit">Commit</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                    )}

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

                {/* Actions: Move Up, Move Down, Insert, Delete */}
                <div className="flex items-center gap-1">
                    <Button
                        variant="ghost"
                        size="icon"
                        className="text-gray-400 hover:text-gray-700"
                        onClick={() => moveStep(index, 'up')}
                        disabled={isFirst}
                        title="Move Up"
                    >
                        <ArrowUp size={16} />
                    </Button>
                    <Button
                        variant="ghost"
                        size="icon"
                        className="text-gray-400 hover:text-gray-700"
                        onClick={() => moveStep(index, 'down')}
                        disabled={isLast}
                        title="Move Down"
                    >
                        <ArrowDown size={16} />
                    </Button>
                    <Button
                        variant="ghost"
                        size="icon"
                        className="text-gray-400 hover:text-primary"
                        onClick={() => insertStep(index)}
                        title="Insert Step After"
                    >
                        <PlusCircle size={16} />
                    </Button>
                    <div className="w-px h-6 bg-gray-200 mx-1"></div>
                    <Button
                        variant="ghost"
                        size="icon"
                        className="text-gray-400 hover:text-red-500"
                        onClick={() => removeStep(step.id)}
                        title="Delete Step"
                    >
                        <Trash2 size={18} />
                    </Button>
                </div>
            </CardContent>
        </Card>
    );
};

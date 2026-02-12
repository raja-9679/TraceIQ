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
import { SchemaGeneratorModal } from './SchemaGeneratorModal';
import { FeedAssertionGeneratorModal } from './FeedAssertionGeneratorModal';

export interface TestStep {
    id: string;
    type: 'goto' | 'click' | 'fill' | 'check' | 'switch-frame' | 'expect-visible' | 'expect-hidden' | 'expect-text' | 'expect-url' | 'hover' | 'select-option' | 'press-key' | 'screenshot' | 'scroll-to' | 'wait-timeout' | 'http-request' | 'feed-check' | 'extract-value' | 'run-script' | 'assert';
    selector?: string;
    value?: string;
    params?: {
        wait_until?: 'load' | 'domcontentloaded' | 'networkidle' | 'commit';
        method?: string;
        headers?: Record<string, string>;
        body?: string;
        assertions?: Array<{
            type: 'status' | 'json-path' | 'xpath' | 'text' | 'json-schema';
            path?: string;
            operator?: 'equals' | 'exists' | 'contains' | 'optional' | 'matches';
            value?: string;
        }>;
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
    const [localHeaders, setLocalHeaders] = React.useState(JSON.stringify(step.params?.headers || {}, null, 2));
    const [localParams, setLocalParams] = React.useState(JSON.stringify(step.params?.params || {}, null, 2));
    const [localBody, setLocalBody] = React.useState(step.params?.body || '');

    // Sync local state when step.params changes from parent (e.g., when loading a test case)
    React.useEffect(() => {
        const newHeaders = JSON.stringify(step.params?.headers || {}, null, 2);
        const newParams = JSON.stringify(step.params?.params || {}, null, 2);
        const newBody = step.params?.body || '';
        
        // Only update if different to avoid cursor jumps while typing
        if (newHeaders !== localHeaders && newHeaders !== '{}') {
            setLocalHeaders(newHeaders);
        }
        if (newParams !== localParams && newParams !== '{}') {
            setLocalParams(newParams);
        }
        if (newBody !== localBody) {
            setLocalBody(newBody);
        }
    }, [step.params?.headers, step.params?.params, step.params?.body]);

    const updateParams = (key: string, value: any) => {
        const newParams = { ...(step.params || {}), [key]: value };
        updateStep(step.id, 'params', newParams);
    };

    const addAssertion = () => {
        const currentAssertions = step.params?.assertions || [];
        updateParams('assertions', [...currentAssertions, { type: 'status', operator: 'equals', value: '200' }]);
    };

    const updateAssertion = (idx: number, field: string, value: string) => {
        const currentAssertions = [...(step.params?.assertions || [])];
        currentAssertions[idx] = { ...currentAssertions[idx], [field]: value };
        updateParams('assertions', currentAssertions);
    };

    const removeAssertion = (idx: number) => {
        const currentAssertions = [...(step.params?.assertions || [])];
        currentAssertions.splice(idx, 1);
        updateParams('assertions', currentAssertions);
    };

    return (
        <Card className="mb-4 relative group hover:border-primary/50 transition-colors">
            <div className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-300 cursor-move opacity-0 group-hover:opacity-100 transition-opacity">
                <GripVertical size={20} />
            </div>
            <CardContent className="p-4 pl-10 flex flex-col gap-4">
                <div className="flex items-start gap-4 w-full">
                    <div className="flex-1 grid grid-cols-12 gap-4">
                        {/* Action Type */}
                        <div className="col-span-3">
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
                                    <SelectItem value="http-request">API Request</SelectItem>
                                    <SelectItem value="feed-check">Feed Check</SelectItem>
                                    <SelectItem value="extract-value">Extract Value</SelectItem>
                                    <SelectItem value="extract-value">Extract Value</SelectItem>
                                    <SelectItem value="run-script">Run Script</SelectItem>
                                    <SelectItem value="assert">Assertion</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>

                        {/* Dynamic Fields based on Type */}
                        {step.type === 'http-request' ? (
                            <>
                                <div className="col-span-2">
                                    <Select
                                        value={step.params?.method || 'GET'}
                                        onValueChange={(value) => updateParams('method', value)}
                                    >
                                        <SelectTrigger>
                                            <SelectValue placeholder="Method" />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="GET">GET</SelectItem>
                                            <SelectItem value="POST">POST</SelectItem>
                                            <SelectItem value="PUT">PUT</SelectItem>
                                            <SelectItem value="DELETE">DELETE</SelectItem>
                                            <SelectItem value="PATCH">PATCH</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="col-span-7">
                                    <Input
                                        placeholder="API URL (e.g., https://api.example.com/users)"
                                        value={step.value || ''}
                                        onChange={(e) => updateStep(step.id, 'value', e.target.value)}
                                    />
                                </div>
                            </>
                        ) : step.type === 'feed-check' ? (
                            <div className="col-span-9">
                                <Input
                                    placeholder="Feed URL (RSS/Atom/XML)"
                                    value={step.value || ''}
                                    onChange={(e) => updateStep(step.id, 'value', e.target.value)}
                                />
                            </div>
                        ) : step.type === 'run-script' ? (
                            <>
                                <div className="col-span-2">
                                    <Select
                                        value={step.params?.language || 'javascript'}
                                        onValueChange={(value) => updateParams('language', value)}
                                    >
                                        <SelectTrigger>
                                            <SelectValue placeholder="Language" />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="javascript">JavaScript</SelectItem>
                                            <SelectItem value="python">Python</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="col-span-4">
                                    <Input
                                        placeholder={step.params?.language === 'python' ? "# Variables: context['variables']\n# Failure: raise Exception('Reason')\nprint('Log')\nreturn 'Result'" : "// Variables: variables['name']\n// Failure: throw new Error('Reason')\nreturn document.title;"}
                                        value={step.value || ''}
                                        onChange={(e) => updateStep(step.id, 'value', e.target.value)}
                                    />
                                </div>
                                <div className="col-span-3">
                                    <Input
                                        placeholder="Store Result in (Variable)"
                                        value={step.params?.variableName || ''}
                                        onChange={(e) => updateParams('variableName', e.target.value)}
                                    />
                                </div>
                            </>
                        ) : step.type === 'assert' ? (
                            <>
                                <div className="col-span-3">
                                    <Input
                                        placeholder="Selector"
                                        value={step.selector || ''}
                                        onChange={(e) => updateStep(step.id, 'selector', e.target.value)}
                                    />
                                </div>
                                <div className="col-span-2">
                                    <Select
                                        value={step.params?.source || 'text'}
                                        onValueChange={(value) => updateParams('source', value)}
                                    >
                                        <SelectTrigger>
                                            <SelectValue placeholder="Source" />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="text">Text Content</SelectItem>
                                            <SelectItem value="value">Input Value</SelectItem>
                                            <SelectItem value="attribute">Attribute</SelectItem>
                                            <SelectItem value="count">Element Count</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>

                                {step.params?.source === 'attribute' && (
                                    <div className="col-span-2">
                                        <Input
                                            placeholder="Attr Name"
                                            value={step.params?.attribute || ''}
                                            onChange={(e) => updateParams('attribute', e.target.value)}
                                        />
                                    </div>
                                )}

                                <div className="col-span-2">
                                    <Select
                                        value={step.params?.operator || 'equals'}
                                        onValueChange={(value) => updateParams('operator', value)}
                                    >
                                        <SelectTrigger>
                                            <SelectValue placeholder="Operator" />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="equals">Equals</SelectItem>
                                            <SelectItem value="contains">Contains</SelectItem>
                                            <SelectItem value="matches">Matches (Regex)</SelectItem>
                                            <SelectItem value="gt">Number &gt;</SelectItem>
                                            <SelectItem value="lt">Number &lt;</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className={step.params?.source === 'attribute' ? "col-span-3" : "col-span-2"}>
                                    <Input
                                        placeholder="Expected Value"
                                        value={step.value || ''}
                                        onChange={(e) => updateStep(step.id, 'value', e.target.value)}
                                    />
                                </div>
                            </>
                        ) : (
                            /* Default UI for other steps */
                            <>
                                <div className={`${step.type === 'goto' ? 'col-span-6' :
                                    step.type === 'expect-url' ? 'col-span-9' :
                                        (step.type === 'fill' || step.type === 'expect-text' || step.type === 'select-option' || step.type === 'extract-value') ? 'col-span-5' :
                                            'col-span-9'
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

                                {step.type === 'extract-value' && (
                                    <div className="col-span-4">
                                        <Input
                                            placeholder="Variable Name"
                                            value={step.value || ''}
                                            onChange={(e) => updateStep(step.id, 'value', e.target.value)}
                                        />
                                    </div>
                                )}

                                {step.type === 'goto' && (
                                    <div className="col-span-3">
                                        <Select
                                            value={step.params?.wait_until || 'domcontentloaded'}
                                            onValueChange={(value) => updateParams('wait_until', value)}
                                        >
                                            <SelectTrigger>
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

                                {(step.type === 'fill' || step.type === 'expect-text' || step.type === 'select-option') && (
                                    <div className="col-span-4">
                                        <Input
                                            placeholder={step.type === 'fill' ? "Value to type" : step.type === 'select-option' ? "Option value" : "Expected text"}
                                            value={step.value || ''}
                                            onChange={(e) => updateStep(step.id, 'value', e.target.value)}
                                        />
                                    </div>
                                )}
                            </>
                        )}
                    </div>

                    {/* Actions */}
                    <div className="flex items-center gap-1">
                        <Button variant="ghost" size="icon" onClick={() => moveStep(index, 'up')} disabled={isFirst}><ArrowUp size={16} /></Button>
                        <Button variant="ghost" size="icon" onClick={() => moveStep(index, 'down')} disabled={isLast}><ArrowDown size={16} /></Button>
                        <Button variant="ghost" size="icon" onClick={() => insertStep(index)}><PlusCircle size={16} /></Button>
                        <div className="w-px h-6 bg-gray-200 mx-1"></div>
                        <Button variant="ghost" size="icon" className="text-red-500" onClick={() => removeStep(step.id)}><Trash2 size={18} /></Button>
                    </div>
                </div>

                {/* Extended Configuration for API/Feed/Script */}
                {(step.type === 'http-request' || step.type === 'feed-check' || step.type === 'run-script') && (
                    <div className="w-full bg-slate-50 p-4 rounded-md border border-slate-200 space-y-4">
                        <div className="space-y-4">
                            {/* Headers & Params - Hide for Script */}
                            {step.type !== 'run-script' && (
                                <div className="grid grid-cols-2 gap-4">
                                    <div>
                                        <label className="text-xs font-medium text-gray-500 mb-1 block">Headers (JSON)</label>
                                        <textarea
                                            className="w-full h-24 p-2 text-xs font-mono border rounded-md bg-white"
                                            placeholder='{"Authorization": "Bearer token"}'
                                            value={localHeaders}
                                            onChange={(e) => {
                                                setLocalHeaders(e.target.value);
                                                try {
                                                    const parsed = JSON.parse(e.target.value);
                                                    updateParams('headers', parsed);
                                                } catch (err) { /* ignore invalid JSON while typing */ }
                                            }}
                                            onBlur={() => {
                                                // Ensure valid JSON is saved on blur, or revert to empty object
                                                try {
                                                    const parsed = JSON.parse(localHeaders);
                                                    updateParams('headers', parsed);
                                                } catch (err) {
                                                    // If invalid JSON, keep current step.params.headers
                                                    setLocalHeaders(JSON.stringify(step.params?.headers || {}, null, 2));
                                                }
                                            }}
                                        />
                                        <p className="text-[10px] text-gray-400 mt-1 italic">Merged with module-level headers (step headers override)</p>
                                    </div>
                                    <div>
                                        <label className="text-xs font-medium text-gray-500 mb-1 block">Query Parameters (JSON)</label>
                                        <textarea
                                            className="w-full h-24 p-2 text-xs font-mono border rounded-md bg-white"
                                            placeholder='{"key": "value"}'
                                            value={localParams}
                                            onChange={(e) => {
                                                setLocalParams(e.target.value);
                                                try {
                                                    const parsed = JSON.parse(e.target.value);
                                                    updateParams('params', parsed);
                                                } catch (err) { /* ignore invalid JSON while typing */ }
                                            }}
                                            onBlur={() => {
                                                // Ensure valid JSON is saved on blur, or revert to empty object
                                                try {
                                                    const parsed = JSON.parse(localParams);
                                                    updateParams('params', parsed);
                                                } catch (err) {
                                                    // If invalid JSON, keep current step.params.params
                                                    setLocalParams(JSON.stringify(step.params?.params || {}, null, 2));
                                                }
                                            }}
                                        />
                                        <p className="text-[10px] text-gray-400 mt-1 italic">Merged with module-level parameters (step params override)</p>
                                    </div>
                                </div>
                            )}



                            {step.type === 'run-script' && (
                                <div>
                                    <label className="text-xs font-medium text-gray-500 mb-1 block">Sort Code</label>
                                    <textarea
                                        className="w-full h-40 p-2 text-xs font-mono border rounded-md bg-white text-slate-800"
                                        placeholder={step.params?.language === 'python' ?
                                            "def run(context):\n    # Access variables via context\n    # context['variables']['myVar']\n    print('Hello World')\n    return True" :
                                            "// JavaScript code to execute in browser\nreturn document.title;"}
                                        value={localBody} // Reuse body for script content
                                        onChange={(e) => {
                                            setLocalBody(e.target.value);
                                            updateParams('body', e.target.value);
                                        }}
                                    />
                                    <p className="text-[10px] text-gray-400 mt-1 italic">
                                        {step.params?.language === 'python' ? "Stdout will be captured. Return values are logged." : "Return value will be captured in logs."}
                                    </p>
                                </div>
                            )}

                            {step.type === 'http-request' && (
                                <div className="grid grid-cols-3 gap-4">
                                    <div className="col-span-1">
                                        <label className="text-xs font-medium text-gray-500 mb-1 block">Response Format</label>
                                        <Select
                                            value={step.params?.response_format || 'json'}
                                            onValueChange={(value) => updateParams('response_format', value)}
                                        >
                                            <SelectTrigger className="h-8 text-xs bg-white">
                                                <SelectValue placeholder="Format" />
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="json">JSON</SelectItem>
                                                <SelectItem value="xml">XML</SelectItem>
                                                <SelectItem value="text">Plain Text</SelectItem>
                                            </SelectContent>
                                        </Select>
                                    </div>
                                    {(step.params?.method && step.params.method !== 'GET') && (
                                        <div className="col-span-2">
                                            <label className="text-xs font-medium text-gray-500 mb-1 block">Request Body</label>
                                            <textarea
                                                className="w-full h-20 p-2 text-xs font-mono border rounded-md bg-white"
                                                placeholder="Request Body (JSON/Text)"
                                                value={localBody}
                                                onChange={(e) => {
                                                    setLocalBody(e.target.value);
                                                    updateParams('body', e.target.value);
                                                }}
                                            />
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>

                        <div>
                            <div className="flex items-center justify-between mb-2">
                                <label className="text-xs font-medium text-gray-500">Assertions</label>
                                <div className="flex gap-2">
                                    {(step.type === 'feed-check' || (step.type === 'http-request' && step.params?.response_format === 'xml')) && (
                                        <FeedAssertionGeneratorModal
                                            onGenerate={(newAssertions) => {
                                                const currentAssertions = step.params?.assertions || [];
                                                const formattedAssertions = newAssertions.map(a => ({
                                                    type: a.type,
                                                    path: a.key,
                                                    operator: a.operator,
                                                    value: (a.operator !== 'exists' && a.operator !== 'optional') ? (a.value || '') : undefined
                                                }));
                                                updateParams('assertions', [...currentAssertions, ...formattedAssertions]);
                                            }}
                                        />
                                    )}
                                    <Button variant="outline" size="sm" className="h-6 text-xs" onClick={addAssertion}>+ Add Assertion</Button>
                                </div>
                            </div>
                            <div className="space-y-2">
                                {step.params?.assertions?.map((assertion: any, idx: number) => (
                                    <div key={idx} className="flex items-center gap-2">
                                        <Select
                                            value={assertion.type}
                                            onValueChange={(val) => updateAssertion(idx, 'type', val)}
                                        >
                                            <SelectTrigger className="w-[120px] h-8 text-xs">
                                                <SelectValue />
                                            </SelectTrigger>
                                            <SelectContent>
                                                {step.type === 'http-request' ? (
                                                    <>
                                                        <SelectItem value="status">Status Code</SelectItem>
                                                        <SelectItem value="text">Text Content</SelectItem>
                                                        {(step.params?.response_format === 'json' || !step.params?.response_format) && (
                                                            <>
                                                                <SelectItem value="json-path">JSON Path</SelectItem>
                                                                <SelectItem value="json-schema">JSON Schema</SelectItem>
                                                            </>
                                                        )}
                                                        {step.params?.response_format === 'xml' && (
                                                            <SelectItem value="xpath">XPath</SelectItem>
                                                        )}
                                                    </>
                                                ) : (
                                                    <>
                                                        <SelectItem value="xpath">XPath</SelectItem>
                                                        <SelectItem value="text">Text Content</SelectItem>
                                                    </>
                                                )}
                                            </SelectContent>
                                        </Select>

                                        {(assertion.type === 'json-path' || assertion.type === 'xpath') && (
                                            <Input
                                                className="h-8 text-xs flex-1"
                                                placeholder={assertion.type === 'json-path' ? "Path (e.g. data.id)" : "XPath (e.g. //title)"}
                                                value={assertion.path || ''}
                                                onChange={(e) => updateAssertion(idx, 'path', e.target.value)}
                                            />
                                        )}

                                        {assertion.type === 'json-schema' ? (
                                            <div className="flex-1 space-y-2">
                                                <textarea
                                                    className="w-full h-20 p-2 text-xs font-mono border rounded-md"
                                                    placeholder='{"type": "object", "properties": {...}}'
                                                    value={assertion.value || ''}
                                                    onChange={(e) => updateAssertion(idx, 'value', e.target.value)}
                                                />
                                                <div className="flex justify-end">
                                                    <SchemaGeneratorModal
                                                        onGenerate={(schema) => updateAssertion(idx, 'value', schema)}
                                                    />
                                                </div>
                                            </div>
                                        ) : (
                                            <>
                                                <Select
                                                    value={assertion.operator || 'equals'}
                                                    onValueChange={(val) => updateAssertion(idx, 'operator', val)}
                                                >
                                                    <SelectTrigger className="w-[100px] h-8 text-xs">
                                                        <SelectValue />
                                                    </SelectTrigger>
                                                    <SelectContent>
                                                        <SelectItem value="equals">Equals</SelectItem>
                                                        <SelectItem value="contains">Contains</SelectItem>
                                                        <SelectItem value="matches">Matches (Regex)</SelectItem>
                                                        {(assertion.type === 'json-path' || assertion.type === 'xpath') && (
                                                            <>
                                                                <SelectItem value="exists">Exists</SelectItem>
                                                                <SelectItem value="optional">Optional</SelectItem>
                                                            </>
                                                        )}
                                                    </SelectContent>
                                                </Select>

                                                <Input
                                                    className="h-8 text-xs flex-1"
                                                    placeholder="Expected Value"
                                                    value={assertion.value || ''}
                                                    onChange={(e) => updateAssertion(idx, 'value', e.target.value)}
                                                    disabled={assertion.operator === 'exists' || assertion.operator === 'optional'}
                                                />
                                            </>
                                        )}

                                        <Button variant="ghost" size="icon" className="h-8 w-8 text-red-500" onClick={() => removeAssertion(idx)}>
                                            <Trash2 size={14} />
                                        </Button>
                                    </div>
                                ))}
                                {(!step.params?.assertions || step.params.assertions.length === 0) && (
                                    <div className="text-xs text-gray-400 italic">No assertions defined.</div>
                                )}
                            </div>
                        </div>
                    </div>
                )}
            </CardContent>
        </Card >
    );
};

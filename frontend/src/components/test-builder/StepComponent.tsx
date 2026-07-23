import React from 'react';
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Trash2, ArrowUp, ArrowDown, PlusCircle, Link2, MousePointerClick, TextCursorInput, CheckSquare, Search, MousePointer2, Keyboard, Camera, ArrowDownToLine, Clock, FileJson, Rss, ArrowRightToLine, Code2, PlayCircle, SplitSquareHorizontal, Eye, EyeOff, CheckCircle2, Zap, Square, Move, Upload, Download, MessageSquare, ExternalLink, Activity, SearchX, Crosshair, KeyRound, Braces } from "lucide-react";
import { ElementPickerDialog } from './ElementPickerDialog';
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
    type: 'goto' | 'click' | 'fill' | 'check' | 'uncheck' | 'double-click' | 'right-click' | 'drag-and-drop' | 'upload-file' | 'download-file' | 'handle-dialog' | 'switch-tab' | 'switch-frame' | 'expect-visible' | 'expect-hidden' | 'expect-text' | 'expect-not-text' | 'expect-url' | 'expect-visual-match' | 'hover' | 'select-option' | 'press-key' | 'screenshot' | 'scroll-to' | 'wait-timeout' | 'wait-for-response' | 'http-request' | 'graphql' | 'oauth2-token' | 'feed-check' | 'extract-value' | 'run-script' | 'assert' | 'amp-validate';
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
    /** URL prefilled into the element picker (usually the case's goto step) */
    pickerUrl?: string;
}

export const StepComponent: React.FC<StepComponentProps> = ({ step, index, updateStep, removeStep, moveStep, insertStep, isFirst, isLast, pickerUrl }) => {
    const [showPicker, setShowPicker] = React.useState(false);
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

    // Helper to get styling and icons per step type
    const getStepMeta = (type: string) => {
        switch (type) {
            case 'goto': return { border: 'border-emerald-200', bg: 'bg-emerald-50 text-emerald-600', hue: 'emerald', icon: <Link2 size={18} />, label: 'Navigate' };
            case 'click': return { border: 'border-indigo-200', bg: 'bg-indigo-50 text-indigo-600', hue: 'indigo', icon: <MousePointerClick size={18} />, label: 'Click' };
            case 'fill': return { border: 'border-indigo-200', bg: 'bg-indigo-50 text-indigo-600', hue: 'indigo', icon: <TextCursorInput size={18} />, label: 'Fill Input' };
            case 'check': return { border: 'border-indigo-200', bg: 'bg-indigo-50 text-indigo-600', hue: 'indigo', icon: <CheckSquare size={18} />, label: 'Check Box' };
            case 'uncheck': return { border: 'border-indigo-200', bg: 'bg-indigo-50 text-indigo-600', hue: 'indigo', icon: <Square size={18} />, label: 'Uncheck Box' };
            case 'double-click': return { border: 'border-indigo-200', bg: 'bg-indigo-50 text-indigo-600', hue: 'indigo', icon: <MousePointerClick size={18} />, label: 'Double Click' };
            case 'right-click': return { border: 'border-indigo-200', bg: 'bg-indigo-50 text-indigo-600', hue: 'indigo', icon: <MousePointer2 size={18} />, label: 'Right Click' };
            case 'drag-and-drop': return { border: 'border-indigo-200', bg: 'bg-indigo-50 text-indigo-600', hue: 'indigo', icon: <Move size={18} />, label: 'Drag & Drop' };
            case 'upload-file': return { border: 'border-indigo-200', bg: 'bg-indigo-50 text-indigo-600', hue: 'indigo', icon: <Upload size={18} />, label: 'Upload File' };
            case 'download-file': return { border: 'border-indigo-200', bg: 'bg-indigo-50 text-indigo-600', hue: 'indigo', icon: <Download size={18} />, label: 'Download File' };
            case 'handle-dialog': return { border: 'border-indigo-200', bg: 'bg-indigo-50 text-indigo-600', hue: 'indigo', icon: <MessageSquare size={18} />, label: 'Handle Dialog' };
            case 'switch-tab': return { border: 'border-emerald-200', bg: 'bg-emerald-50 text-emerald-600', hue: 'emerald', icon: <ExternalLink size={18} />, label: 'Switch Tab' };
            case 'wait-for-response': return { border: 'border-rose-200', bg: 'bg-rose-50 text-rose-600', hue: 'rose', icon: <Activity size={18} />, label: 'Wait For Response' };
            case 'expect-not-text': return { border: 'border-cyan-200', bg: 'bg-cyan-50 text-cyan-600', hue: 'cyan', icon: <SearchX size={18} />, label: 'Expect Not Text' };
            case 'expect-visible': return { border: 'border-cyan-200', bg: 'bg-cyan-50 text-cyan-600', hue: 'cyan', icon: <Eye size={18} />, label: 'Expect Visible' };
            case 'expect-hidden': return { border: 'border-cyan-200', bg: 'bg-cyan-50 text-cyan-600', hue: 'cyan', icon: <EyeOff size={18} />, label: 'Expect Hidden' };
            case 'expect-text': return { border: 'border-cyan-200', bg: 'bg-cyan-50 text-cyan-600', hue: 'cyan', icon: <Search size={18} />, label: 'Expect Text' };
            case 'expect-url': return { border: 'border-cyan-200', bg: 'bg-cyan-50 text-cyan-600', hue: 'cyan', icon: <Link2 size={18} />, label: 'Expect URL' };
            case 'expect-visual-match': return { border: 'border-cyan-200', bg: 'bg-cyan-50 text-cyan-600', hue: 'cyan', icon: <Camera size={18} />, label: 'Visual Match' };
            case 'hover': return { border: 'border-indigo-200', bg: 'bg-indigo-50 text-indigo-600', hue: 'indigo', icon: <MousePointer2 size={18} />, label: 'Hover' };
            case 'press-key': return { border: 'border-indigo-200', bg: 'bg-indigo-50 text-indigo-600', hue: 'indigo', icon: <Keyboard size={18} />, label: 'Press Key' };
            case 'http-request': return { border: 'border-amber-200', bg: 'bg-amber-50 text-amber-600', hue: 'amber', icon: <FileJson size={18} />, label: 'API Request' };
            case 'graphql': return { border: 'border-amber-200', bg: 'bg-amber-50 text-amber-600', hue: 'amber', icon: <Braces size={18} />, label: 'GraphQL Request' };
            case 'oauth2-token': return { border: 'border-amber-200', bg: 'bg-amber-50 text-amber-600', hue: 'amber', icon: <KeyRound size={18} />, label: 'OAuth2 Token' };
            case 'feed-check': return { border: 'border-amber-200', bg: 'bg-amber-50 text-amber-600', hue: 'amber', icon: <Rss size={18} />, label: 'Feed Check' };
            case 'run-script': return { border: 'border-rose-200', bg: 'bg-rose-50 text-rose-600', hue: 'rose', icon: <Code2 size={18} />, label: 'Run Script' };
            case 'assert': return { border: 'border-cyan-200', bg: 'bg-cyan-50 text-cyan-600', hue: 'cyan', icon: <CheckCircle2 size={18} />, label: 'Assertion' };
            case 'amp-validate': return { border: 'border-violet-200', bg: 'bg-violet-50 text-violet-600', hue: 'violet', icon: <Zap size={18} />, label: 'AMP Validate' };
            case '': return { border: 'border-dashed border-slate-300', bg: 'bg-slate-100 text-slate-400', hue: 'slate', icon: <PlusCircle size={18} />, label: 'Choose action…' };
            default: return { border: 'border-slate-200', bg: 'bg-slate-100 text-slate-500', hue: 'slate', icon: <PlayCircle size={18} />, label: type };
        }
    };

    const meta = getStepMeta(step.type);

    return (
        <div className="relative group transition-all duration-300">
            {/* Timeline Connectors & Number Badge */}
            <div className="absolute left-[8px] sm:left-[24px] top-6 bottom-0 w-0.5 bg-slate-200 transition-colors group-hover:bg-indigo-100 -z-10 group-last:hidden" />
            <div className="absolute left-0 sm:left-4 top-5 w-6 h-6 rounded-full bg-white border-2 border-slate-200 flex items-center justify-center shadow-sm z-10 transition-transform group-hover:scale-110 group-hover:border-indigo-400 group-hover:text-indigo-600 font-bold text-[10px] text-slate-400">
                {index + 1}
            </div>

            {/* Main Node Card */}
            <div className={`ml-8 sm:ml-[60px] bg-white rounded-2xl border ${meta.border} shadow-sm transition-all hover:shadow-md hover:border-${meta.hue}-300 group overflow-visible relative`}>
                {/* Node Header & Core Inputs */}
                <div className={`flex flex-col md:flex-row p-1.5 gap-2 relative bg-gradient-to-r from-${meta.hue}-50/30 to-white rounded-t-2xl md:rounded-2xl`}>
                    {/* Action Selector Badge */}
                    <div className="relative w-full md:w-48 shrink-0">
                        <Select
                            value={step.type}
                            onValueChange={(value) => updateStep(step.id, 'type', value)}
                        >
                            <SelectTrigger className={`w-full h-12 rounded-xl focus:ring-${meta.hue}-500/20 bg-white border-none shadow-sm flex items-center px-4 hover:bg-slate-50 transition-colors`}>
                                <div className={`w-8 h-8 rounded-lg ${meta.bg} flex items-center justify-center mr-3 shrink-0`}>
                                    {meta.icon}
                                </div>
                                <span className="font-bold text-slate-700 truncate text-left">{meta.label}</span>
                            </SelectTrigger>
                            <SelectContent className="max-h-[400px]">
                                <div className="p-2 text-xs font-bold text-slate-400 uppercase tracking-widest">Navigation & Context</div>
                                <SelectItem value="goto"><div className="flex items-center gap-2"><Link2 size={14} className="text-emerald-500"/> Go to URL</div></SelectItem>
                                <SelectItem value="switch-frame"><div className="flex items-center gap-2"><SplitSquareHorizontal size={14} className="text-emerald-500"/> Switch Frame</div></SelectItem>
                                <SelectItem value="switch-tab"><div className="flex items-center gap-2"><ExternalLink size={14} className="text-emerald-500"/> Switch Tab</div></SelectItem>
                                <div className="p-2 text-xs font-bold text-slate-400 uppercase tracking-widest border-t mt-1">Interactions</div>
                                <SelectItem value="click"><div className="flex items-center gap-2"><MousePointerClick size={14} className="text-indigo-500"/> Click Element</div></SelectItem>
                                <SelectItem value="fill"><div className="flex items-center gap-2"><TextCursorInput size={14} className="text-indigo-500"/> Fill Input</div></SelectItem>
                                <SelectItem value="check"><div className="flex items-center gap-2"><CheckSquare size={14} className="text-indigo-500"/> Check Box</div></SelectItem>
                                <SelectItem value="uncheck"><div className="flex items-center gap-2"><Square size={14} className="text-indigo-500"/> Uncheck Box</div></SelectItem>
                                <SelectItem value="double-click"><div className="flex items-center gap-2"><MousePointerClick size={14} className="text-indigo-500"/> Double Click</div></SelectItem>
                                <SelectItem value="right-click"><div className="flex items-center gap-2"><MousePointer2 size={14} className="text-indigo-500"/> Right Click</div></SelectItem>
                                <SelectItem value="drag-and-drop"><div className="flex items-center gap-2"><Move size={14} className="text-indigo-500"/> Drag & Drop</div></SelectItem>
                                <SelectItem value="upload-file"><div className="flex items-center gap-2"><Upload size={14} className="text-indigo-500"/> Upload File</div></SelectItem>
                                <SelectItem value="download-file"><div className="flex items-center gap-2"><Download size={14} className="text-indigo-500"/> Download File</div></SelectItem>
                                <SelectItem value="handle-dialog"><div className="flex items-center gap-2"><MessageSquare size={14} className="text-indigo-500"/> Handle Dialog</div></SelectItem>
                                <SelectItem value="hover"><div className="flex items-center gap-2"><MousePointer2 size={14} className="text-indigo-500"/> Hover</div></SelectItem>
                                <SelectItem value="press-key"><div className="flex items-center gap-2"><Keyboard size={14} className="text-indigo-500"/> Press Key</div></SelectItem>
                                <SelectItem value="scroll-to"><div className="flex items-center gap-2"><ArrowDownToLine size={14} className="text-indigo-500"/> Scroll To</div></SelectItem>
                                <div className="p-2 text-xs font-bold text-slate-400 uppercase tracking-widest border-t mt-1">Assertions</div>
                                <SelectItem value="expect-visible"><div className="flex items-center gap-2"><Eye size={14} className="text-cyan-500"/> Expect Visible</div></SelectItem>
                                <SelectItem value="expect-hidden"><div className="flex items-center gap-2"><EyeOff size={14} className="text-cyan-500"/> Expect Hidden</div></SelectItem>
                                <SelectItem value="expect-text"><div className="flex items-center gap-2"><Search size={14} className="text-cyan-500"/> Expect Text</div></SelectItem>
                                <SelectItem value="expect-not-text"><div className="flex items-center gap-2"><SearchX size={14} className="text-cyan-500"/> Expect Not Text</div></SelectItem>
                                <SelectItem value="expect-url"><div className="flex items-center gap-2"><Link2 size={14} className="text-cyan-500"/> Expect URL</div></SelectItem>
                                <SelectItem value="expect-visual-match"><div className="flex items-center gap-2"><Camera size={14} className="text-cyan-500"/> Visual Match</div></SelectItem>
                                <SelectItem value="assert"><div className="flex items-center gap-2"><CheckCircle2 size={14} className="text-cyan-500"/> Custom Assert</div></SelectItem>
                                <div className="p-2 text-xs font-bold text-slate-400 uppercase tracking-widest border-t mt-1">API & Data</div>
                                <SelectItem value="http-request"><div className="flex items-center gap-2"><FileJson size={14} className="text-amber-500"/> API Request</div></SelectItem>
                                <SelectItem value="graphql"><div className="flex items-center gap-2"><FileJson size={14} className="text-amber-500"/> GraphQL Request</div></SelectItem>
                                <SelectItem value="oauth2-token"><div className="flex items-center gap-2"><KeyRound size={14} className="text-amber-500"/> OAuth2 Token</div></SelectItem>
                                <SelectItem value="feed-check"><div className="flex items-center gap-2"><Rss size={14} className="text-amber-500"/> Feed Check</div></SelectItem>
                                <SelectItem value="amp-validate"><div className="flex items-center gap-2"><Zap size={14} className="text-violet-500"/> AMP Validate</div></SelectItem>
                                <SelectItem value="extract-value"><div className="flex items-center gap-2"><ArrowRightToLine size={14} className="text-amber-500"/> Extract Value</div></SelectItem>
                                <div className="p-2 text-xs font-bold text-slate-400 uppercase tracking-widest border-t mt-1">Advanced</div>
                                <SelectItem value="wait-timeout"><div className="flex items-center gap-2"><Clock size={14} className="text-rose-500"/> Wait (ms)</div></SelectItem>
                                <SelectItem value="wait-for-response"><div className="flex items-center gap-2"><Activity size={14} className="text-rose-500"/> Wait For Response</div></SelectItem>
                                <SelectItem value="run-script"><div className="flex items-center gap-2"><Code2 size={14} className="text-rose-500"/> Run Script</div></SelectItem>
                                <SelectItem value="screenshot"><div className="flex items-center gap-2"><Camera size={14} className="text-rose-500"/> Take Screenshot</div></SelectItem>
                            </SelectContent>
                        </Select>
                    </div>

                    {/* Inputs Area */}
                    <div className="flex-1 flex flex-col sm:flex-row gap-2">
                        {step.type === 'http-request' ? (
                            <>
                                <Select
                                    value={step.params?.method || 'GET'}
                                    onValueChange={(value) => updateParams('method', value)}
                                >
                                    <SelectTrigger className={`w-32 shrink-0 h-12 rounded-xl focus:ring-${meta.hue}-500/20 bg-white border-none shadow-sm font-mono font-bold text-${meta.hue}-600`}>
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
                                <Input
                                    placeholder="API Endpoint (e.g. https://api.example.com/v1/users)"
                                    value={step.value || ''}
                                    onChange={(e) => updateStep(step.id, 'value', e.target.value)}
                                    className={`flex-1 min-w-0 h-12 rounded-xl border-none shadow-sm focus-visible:ring-2 focus-visible:ring-${meta.hue}-500/20 bg-white px-4 font-mono`}
                                />
                            </>
                        ) : step.type === 'feed-check' ? (
                            <Input
                                placeholder="Feed URL (RSS/Atom/JSON)"
                                value={step.value || ''}
                                onChange={(e) => updateStep(step.id, 'value', e.target.value)}
                                className={`w-full min-w-0 h-12 rounded-xl border-none shadow-sm focus-visible:ring-2 focus-visible:ring-${meta.hue}-500/20 bg-white px-4 font-mono`}
                            />
                        ) : step.type === 'amp-validate' ? (
                            <Input
                                placeholder="AMP Page URL (e.g. https://example.com/amp/article)"
                                value={step.value || ''}
                                onChange={(e) => updateStep(step.id, 'value', e.target.value)}
                                className={`w-full min-w-0 h-12 rounded-xl border-none shadow-sm focus-visible:ring-2 focus-visible:ring-${meta.hue}-500/20 bg-white px-4 font-mono`}
                            />
                        ) : step.type === 'run-script' ? (
                            <>
                                <Select
                                    value={step.params?.language || 'javascript'}
                                    onValueChange={(value) => updateParams('language', value)}
                                >
                                    <SelectTrigger className={`w-32 shrink-0 h-12 rounded-xl focus:ring-${meta.hue}-500/20 bg-white border-none shadow-sm font-mono font-bold text-slate-700`}>
                                        <SelectValue placeholder="Lang" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="javascript">Node.js</SelectItem>
                                        <SelectItem value="python">Python</SelectItem>
                                    </SelectContent>
                                </Select>
                                <Input
                                    placeholder="Inline script snippet (optional)"
                                    value={step.value || ''}
                                    onChange={(e) => updateStep(step.id, 'value', e.target.value)}
                                    className={`flex-1 min-w-0 h-12 rounded-xl border-none shadow-sm focus-visible:ring-2 focus-visible:ring-${meta.hue}-500/20 bg-white px-4 font-mono text-slate-500 italic`}
                                />
                                <Input
                                    placeholder="Store in var..."
                                    value={step.params?.variableName || ''}
                                    onChange={(e) => updateParams('variableName', e.target.value)}
                                    className={`w-32 shrink-0 h-12 rounded-xl border-none shadow-sm focus-visible:ring-2 focus-visible:ring-${meta.hue}-500/20 bg-white px-4`}
                                />
                            </>
                        ) : step.type === 'assert' ? (
                            <div className="flex-1 flex gap-2 w-full flex-wrap sm:flex-nowrap">
                                <Input
                                    placeholder="Selector/Location"
                                    value={step.selector || ''}
                                    onChange={(e) => updateStep(step.id, 'selector', e.target.value)}
                                    className={`flex-1 min-w-[150px] h-12 rounded-xl border-none shadow-sm focus-visible:ring-2 focus-visible:ring-${meta.hue}-500/20 bg-white px-4 font-mono text-sm`}
                                />
                                <Select
                                    value={step.params?.source || 'text'}
                                    onValueChange={(value) => updateParams('source', value)}
                                >
                                    <SelectTrigger className={`w-32 shrink-0 h-12 rounded-xl focus:ring-${meta.hue}-500/20 bg-white border-none shadow-sm font-bold text-slate-700`}>
                                        <SelectValue placeholder="Source" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="text">Text Content</SelectItem>
                                        <SelectItem value="value">Input Value</SelectItem>
                                        <SelectItem value="attribute">Attribute</SelectItem>
                                        <SelectItem value="count">Count</SelectItem>
                                    </SelectContent>
                                </Select>

                                {step.params?.source === 'attribute' && (
                                    <Input
                                        placeholder="Attr"
                                        value={step.params?.attribute || ''}
                                        onChange={(e) => updateParams('attribute', e.target.value)}
                                        className={`w-28 shrink-0 h-12 rounded-xl border-none shadow-sm focus-visible:ring-2 focus-visible:ring-${meta.hue}-500/20 bg-white px-4`}
                                    />
                                )}

                                <Select
                                    value={step.params?.operator || 'equals'}
                                    onValueChange={(value) => updateParams('operator', value)}
                                >
                                    <SelectTrigger className={`w-32 shrink-0 h-12 rounded-xl focus:ring-${meta.hue}-500/20 bg-white border-none shadow-sm font-bold text-slate-700`}>
                                        <SelectValue placeholder="Op" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="equals">Equals</SelectItem>
                                        <SelectItem value="contains">Contains</SelectItem>
                                        <SelectItem value="matches">Regex</SelectItem>
                                        <SelectItem value="gt">Greater &gt;</SelectItem>
                                        <SelectItem value="lt">Less &lt;</SelectItem>
                                    </SelectContent>
                                </Select>
                                
                                <Input
                                    placeholder="Expected"
                                    value={step.value || ''}
                                    onChange={(e) => updateStep(step.id, 'value', e.target.value)}
                                    className={`flex-1 min-w-[100px] h-12 rounded-xl border-none shadow-sm focus-visible:ring-2 focus-visible:ring-${meta.hue}-500/20 bg-emerald-50 text-emerald-700 font-bold px-4`}
                                />
                            </div>
                        ) : (step.type as string) === '' ? (
                            <div className="flex-1 min-w-0 h-12 rounded-xl px-4 flex items-center text-sm text-slate-400 italic">
                                ← Choose an action from the dropdown to configure this step
                            </div>
                        ) : step.type === 'expect-visual-match' ? (
                            <div className={`flex-1 min-w-0 h-12 rounded-xl shadow-sm bg-white px-4 flex items-center text-sm text-slate-500`}>
                                Captures a full-page screenshot and compares it against the pinned baseline (manage in Visual Review). First run is capture-only.
                            </div>
                        ) : (
                            /* Default UI for other steps */
                            <div className="flex-1 flex gap-2 w-full flex-col sm:flex-row">
                                <Input
                                    placeholder={
                                        step.type === 'goto' ? "https://example.com" :
                                            step.type === 'press-key' ? "Key (e.g., Enter)" :
                                                step.type === 'wait-timeout' ? "Timeout in ms" :
                                                    step.type === 'screenshot' ? "Screenshot name" :
                                                        step.type === 'switch-tab' ? "'latest', tab index, or URL substring" :
                                                            step.type === 'wait-for-response' ? "URL substring (e.g., /api/search)" :
                                                                step.type === 'download-file' ? "(optional) trigger selector" :
                                                                    "Selector (e.g., #submit-btn)"
                                    }
                                    value={(step.type === 'goto' || step.type === 'expect-url' || step.type === 'press-key' || step.type === 'wait-timeout' || step.type === 'screenshot' || step.type === 'switch-tab' || step.type === 'wait-for-response' ? step.value : step.selector) || ''}
                                    onChange={(e) => updateStep(step.id, (step.type === 'goto' || step.type === 'expect-url' || step.type === 'press-key' || step.type === 'wait-timeout' || step.type === 'screenshot' || step.type === 'switch-tab' || step.type === 'wait-for-response' ? 'value' : 'selector'), e.target.value)}
                                    className={`flex-[2] min-w-0 h-12 rounded-xl border-none shadow-sm focus-visible:ring-2 focus-visible:ring-${meta.hue}-500/20 bg-white px-4 font-mono`}
                                />

                                {!(step.type === 'goto' || step.type === 'expect-url' || step.type === 'press-key' || step.type === 'wait-timeout' || step.type === 'screenshot' || step.type === 'switch-tab' || step.type === 'wait-for-response') && (
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        onClick={() => setShowPicker(true)}
                                        title="Pick element from a rendered page"
                                        className="h-12 w-12 shrink-0 rounded-xl bg-white shadow-sm text-slate-400 hover:text-indigo-600 hover:bg-indigo-50"
                                    >
                                        <Crosshair size={18} />
                                    </Button>
                                )}

                                {step.type === 'extract-value' && (
                                    <Input
                                        placeholder="Variable Name"
                                        value={step.value || ''}
                                        onChange={(e) => updateStep(step.id, 'value', e.target.value)}
                                        className={`flex-1 min-w-0 h-12 rounded-xl border-none shadow-sm focus-visible:ring-2 focus-visible:ring-${meta.hue}-500/20 bg-white px-4`}
                                    />
                                )}

                                {step.type === 'goto' && (
                                    <Select
                                        value={step.params?.wait_until || 'domcontentloaded'}
                                        onValueChange={(value) => updateParams('wait_until', value)}
                                    >
                                        <SelectTrigger className={`w-36 shrink-0 h-12 rounded-xl focus:ring-${meta.hue}-500/20 bg-white border-none shadow-sm text-slate-600`}>
                                            <SelectValue placeholder="Wait Until" />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="domcontentloaded">DOM Loaded</SelectItem>
                                            <SelectItem value="load">Fully Loaded</SelectItem>
                                            <SelectItem value="networkidle">Network Idle</SelectItem>
                                            <SelectItem value="commit">Commit</SelectItem>
                                        </SelectContent>
                                    </Select>
                                )}

                                {(step.type === 'fill' || step.type === 'expect-text' || step.type === 'expect-not-text' || step.type === 'select-option' || step.type === 'drag-and-drop' || step.type === 'upload-file') && (
                                    <Input
                                        placeholder={
                                            step.type === 'fill' ? "Value to format" :
                                                step.type === 'select-option' ? "Option value" :
                                                    step.type === 'drag-and-drop' ? "Target selector" :
                                                        step.type === 'upload-file' ? "Worker file path(s) (optional)" :
                                                            step.type === 'expect-not-text' ? "Text that must be absent" :
                                                                "Expected text"
                                        }
                                        value={step.value || ''}
                                        onChange={(e) => updateStep(step.id, 'value', e.target.value)}
                                        className={`flex-1 min-w-0 h-12 rounded-xl border-none shadow-sm focus-visible:ring-2 focus-visible:ring-${meta.hue}-500/20 bg-white px-4`}
                                    />
                                )}

                                {step.type === 'handle-dialog' && (
                                    <>
                                        <Select
                                            value={step.params?.action || 'accept'}
                                            onValueChange={(value) => updateParams('action', value)}
                                        >
                                            <SelectTrigger className={`w-32 shrink-0 h-12 rounded-xl focus:ring-${meta.hue}-500/20 bg-white border-none shadow-sm text-slate-600`}>
                                                <SelectValue placeholder="Action" />
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="accept">Accept</SelectItem>
                                                <SelectItem value="dismiss">Dismiss</SelectItem>
                                            </SelectContent>
                                        </Select>
                                        <Input
                                            placeholder="Prompt text (optional)"
                                            value={step.params?.prompt_text || ''}
                                            onChange={(e) => updateParams('prompt_text', e.target.value)}
                                            className={`flex-1 min-w-0 h-12 rounded-xl border-none shadow-sm focus-visible:ring-2 focus-visible:ring-${meta.hue}-500/20 bg-white px-4`}
                                        />
                                    </>
                                )}

                                {(step.type === 'switch-tab' || step.type === 'wait-for-response') && (
                                    <Input
                                        placeholder="Trigger selector (optional click)"
                                        value={step.params?.trigger_selector || ''}
                                        onChange={(e) => updateParams('trigger_selector', e.target.value)}
                                        className={`flex-1 min-w-0 h-12 rounded-xl border-none shadow-sm focus-visible:ring-2 focus-visible:ring-${meta.hue}-500/20 bg-white px-4 font-mono`}
                                    />
                                )}

                                {step.type === 'wait-for-response' && (
                                    <Input
                                        placeholder="Status (optional)"
                                        value={step.params?.status || ''}
                                        onChange={(e) => updateParams('status', e.target.value)}
                                        className={`w-28 shrink-0 h-12 rounded-xl border-none shadow-sm focus-visible:ring-2 focus-visible:ring-${meta.hue}-500/20 bg-white px-4`}
                                    />
                                )}

                                {step.type === 'download-file' && (
                                    <Input
                                        placeholder="Filename contains (optional)"
                                        value={step.params?.filename_contains || ''}
                                        onChange={(e) => updateParams('filename_contains', e.target.value)}
                                        className={`flex-1 min-w-0 h-12 rounded-xl border-none shadow-sm focus-visible:ring-2 focus-visible:ring-${meta.hue}-500/20 bg-white px-4`}
                                    />
                                )}
                            </div>
                        )}
                    </div>
                </div>

                {/* Extended Configuration for GraphQL */}
                {step.type === 'graphql' && (
                    <div className="w-full bg-slate-50 border-t border-slate-100 p-5 space-y-4 rounded-b-2xl">
                        <div>
                            <label className="text-xs font-bold text-slate-500 uppercase tracking-widest mb-1.5 flex items-center gap-1.5"><Braces size={14} className="text-amber-500" /> Query</label>
                            <textarea
                                className="w-full h-32 p-3 text-xs font-mono font-medium border border-slate-200 outline-none focus:border-amber-500/50 rounded-xl bg-white text-amber-700 placeholder:text-slate-400 transition-colors shadow-inner resize-none"
                                placeholder={'query($id: ID!) {\n  user(id: $id) { name email }\n}'}
                                value={step.params?.query || ''}
                                onChange={(e) => updateParams('query', e.target.value)}
                            />
                        </div>
                        <div>
                            <label className="text-xs font-bold text-slate-500 uppercase tracking-widest mb-1.5 flex items-center gap-1.5"><FileJson size={14} className="text-amber-500" /> Variables (JSON)</label>
                            <textarea
                                className="w-full h-20 p-3 text-xs font-mono font-medium border border-slate-200 outline-none focus:border-amber-500/50 rounded-xl bg-white text-indigo-600 placeholder:text-slate-400 transition-colors shadow-inner resize-none"
                                placeholder='{"id": "1"}'
                                defaultValue={JSON.stringify(step.params?.variables || {}, null, 2)}
                                onBlur={(e) => {
                                    try { updateParams('variables', JSON.parse(e.target.value)); } catch { /* keep last valid */ }
                                }}
                            />
                            <p className="text-[10px] text-gray-400 mt-1 italic">Assertions/extract are editable via the case JSON or an AI agent (data-path assertions on the response).</p>
                        </div>
                    </div>
                )}

                {/* Extended Configuration for OAuth2 token */}
                {step.type === 'oauth2-token' && (
                    <div className="w-full bg-slate-50 border-t border-slate-100 p-5 rounded-b-2xl">
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <label className="text-xs font-bold text-slate-500 uppercase tracking-widest mb-1.5 block">Client ID</label>
                                <Input className="h-10 bg-white" placeholder="{{secret.CLIENT_ID}}" value={step.params?.client_id || ''}
                                    onChange={(e) => updateParams('client_id', e.target.value)} />
                            </div>
                            <div>
                                <label className="text-xs font-bold text-slate-500 uppercase tracking-widest mb-1.5 block">Client Secret</label>
                                <Input className="h-10 bg-white" placeholder="{{secret.CLIENT_SECRET}}" value={step.params?.client_secret || ''}
                                    onChange={(e) => updateParams('client_secret', e.target.value)} />
                            </div>
                            <div>
                                <label className="text-xs font-bold text-slate-500 uppercase tracking-widest mb-1.5 block">Scope (optional)</label>
                                <Input className="h-10 bg-white" placeholder="read write" value={step.params?.scope || ''}
                                    onChange={(e) => updateParams('scope', e.target.value)} />
                            </div>
                            <div>
                                <label className="text-xs font-bold text-slate-500 uppercase tracking-widest mb-1.5 block">Store token as</label>
                                <Input className="h-10 bg-white" placeholder="access_token" value={step.params?.variable || ''}
                                    onChange={(e) => updateParams('variable', e.target.value)} />
                            </div>
                        </div>
                        <p className="text-[10px] text-gray-400 mt-2 italic">Client-credentials grant against the app under test. Use the token in later steps: {'{"Authorization": "Bearer {{access_token}}"}'}. Reference project secrets, never paste real secrets here.</p>
                    </div>
                )}

                {/* Extended Configuration for API/Feed/Script */}
                {(step.type === 'http-request' || step.type === 'feed-check' || step.type === 'run-script') && (
                    <div className="w-full bg-slate-50 border-t border-slate-100 p-5 space-y-4 rounded-b-2xl">
                        <div className="space-y-4">
                            {/* Headers & Params - Hide for Script */}
                            {step.type !== 'run-script' && (
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                    <div>
                                        <label className="text-xs font-bold text-slate-500 uppercase tracking-widest mb-1.5 flex items-center gap-1.5"><FileJson size={14} className="text-amber-500" /> Headers</label>
                                        <textarea
                                            className="w-full h-24 p-3 text-xs font-mono font-medium border border-slate-200 outline-none focus:border-amber-500/50 rounded-xl bg-white text-emerald-600 placeholder:text-slate-400 transition-colors shadow-inner drop-shadow-sm resize-none"
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
                                        <label className="text-xs font-bold text-slate-500 uppercase tracking-widest mb-1.5 flex items-center gap-1.5"><Link2 size={14} className="text-amber-500" /> Query Parameters</label>
                                        <textarea
                                            className="w-full h-24 p-3 text-xs font-mono font-medium border border-slate-200 outline-none focus:border-amber-500/50 rounded-xl bg-white text-indigo-600 placeholder:text-slate-400 transition-colors shadow-inner drop-shadow-sm resize-none"
                                            placeholder='{"page": "1"}'
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
                                    <label className="text-xs font-bold text-slate-500 uppercase tracking-widest mb-1.5 flex items-center gap-1.5"><Code2 size={14} className="text-rose-500" /> Source Code</label>
                                    <textarea
                                        className="w-full h-40 p-3 text-xs font-mono font-medium border border-slate-200 outline-none focus:border-rose-500/50 rounded-xl bg-white text-rose-600 placeholder:text-slate-400 transition-colors shadow-inner drop-shadow-sm resize-none"
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
                                        <label className="text-xs font-bold text-slate-500 uppercase tracking-widest mb-1.5 flex items-center gap-1.5">Response</label>
                                        <Select
                                            value={step.params?.response_format || 'json'}
                                            onValueChange={(value) => updateParams('response_format', value)}
                                        >
                                            <SelectTrigger className="h-10 text-xs bg-white border-slate-200 text-slate-700 font-medium shadow-sm rounded-lg">
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
                                            <label className="text-xs font-bold text-slate-500 uppercase tracking-widest mb-1.5 flex items-center gap-1.5">Payload Body</label>
                                            <textarea
                                                className="w-full h-24 p-3 text-xs font-mono font-medium border border-slate-200 outline-none focus:border-amber-500/50 rounded-xl bg-white text-slate-700 placeholder:text-slate-400 transition-colors shadow-inner drop-shadow-sm resize-none"
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

                        <div className="pt-4 border-t border-slate-200">
                            <div className="flex items-center justify-between mb-4">
                                <label className="text-xs font-bold text-slate-500 uppercase tracking-widest flex items-center gap-1.5"><CheckCircle2 size={14} className="text-cyan-500" /> Lifecycle Assertions</label>
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
                                    <Button variant="outline" size="sm" className="h-8 text-xs bg-white border-indigo-200 text-indigo-600 hover:bg-indigo-50 hover:text-indigo-700 rounded-lg transition-colors border-dashed" onClick={addAssertion}><PlusCircle className="mr-1.5 h-3.5 w-3.5"/> Assertion</Button>
                                </div>
                            </div>
                            <div className="space-y-3">
                                {step.params?.assertions?.map((assertion: any, idx: number) => (
                                    <div key={idx} className="flex flex-wrap sm:flex-nowrap items-center gap-2 bg-slate-50/50 p-2 rounded-xl border border-slate-200 shadow-sm">
                                        <Select
                                            value={assertion.type}
                                            onValueChange={(val) => updateAssertion(idx, 'type', val)}
                                        >
                                            <SelectTrigger className="w-full sm:w-[130px] shrink-0 h-10 border-slate-200 bg-white text-slate-700 rounded-lg shadow-sm font-medium">
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
                                                className="h-10 border-slate-200 bg-white text-slate-700 font-mono text-sm placeholder:text-slate-400 rounded-lg min-w-0 flex-[2] font-medium shadow-sm"
                                                placeholder={assertion.type === 'json-path' ? "e.g. data.id" : "e.g. //title"}
                                                value={assertion.path || ''}
                                                onChange={(e) => updateAssertion(idx, 'path', e.target.value)}
                                            />
                                        )}

                                        {assertion.type === 'json-schema' ? (
                                            <div className="flex-1 min-w-[200px] flex items-start gap-2">
                                                <textarea
                                                    className="flex-1 w-full h-10 min-h-[40px] p-2 text-xs font-mono font-medium border border-slate-200 bg-white text-slate-700 rounded-lg resize-y focus:outline-none focus:border-cyan-500/50 shadow-sm"
                                                    placeholder='{"type": "object"}'
                                                    value={assertion.value || ''}
                                                    onChange={(e) => updateAssertion(idx, 'value', e.target.value)}
                                                />
                                                <SchemaGeneratorModal
                                                    onGenerate={(schema) => updateAssertion(idx, 'value', schema)}
                                                />
                                            </div>
                                        ) : (
                                            <div className="flex-1 flex gap-2 min-w-[200px]">
                                                <Select
                                                    value={assertion.operator || 'equals'}
                                                    onValueChange={(val) => updateAssertion(idx, 'operator', val)}
                                                >
                                                    <SelectTrigger className="w-[110px] shrink-0 h-10 border-slate-200 bg-white text-slate-700 rounded-lg font-bold shadow-sm">
                                                        <SelectValue />
                                                    </SelectTrigger>
                                                    <SelectContent>
                                                        <SelectItem value="equals">Equals</SelectItem>
                                                        <SelectItem value="contains">Contains</SelectItem>
                                                        <SelectItem value="matches">Regex</SelectItem>
                                                        {(assertion.type === 'json-path' || assertion.type === 'xpath') && (
                                                            <>
                                                                <SelectItem value="exists">Exists</SelectItem>
                                                                <SelectItem value="optional">Optional</SelectItem>
                                                            </>
                                                        )}
                                                    </SelectContent>
                                                </Select>

                                                <Input
                                                    className="h-10 border-slate-200 bg-white font-bold text-emerald-600 font-mono text-sm placeholder:text-slate-400 rounded-lg min-w-0 flex-1 disabled:opacity-50 shadow-sm"
                                                    placeholder="Expected"
                                                    value={assertion.value || ''}
                                                    onChange={(e) => updateAssertion(idx, 'value', e.target.value)}
                                                    disabled={assertion.operator === 'exists' || assertion.operator === 'optional'}
                                                />
                                            </div>
                                        )}

                                        <Button variant="ghost" size="icon" className="h-10 w-10 shrink-0 text-slate-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition-colors" onClick={() => removeAssertion(idx)}>
                                            <Trash2 size={14} />
                                        </Button>
                                    </div>
                                ))}
                                {(!step.params?.assertions || step.params.assertions.length === 0) && (
                                    <div className="text-xs text-slate-500 italic bg-slate-100/50 p-4 rounded-xl border border-dashed border-slate-200 text-center">No lifecycle assertions defined. API status checks automatically default to 200 without assertion records.</div>
                                )}
                            </div>
                        </div>
                    </div>
                )}
            </div>

            {/* Quick Actions overlay via group hover - Dropped below step to avoid input overlap */}
            <div className="absolute -bottom-4 right-4 items-center flex pointer-events-none z-30 opacity-0 group-hover:opacity-100 transition-opacity translate-y-2 group-hover:translate-y-0">
                <div className="bg-slate-900 border border-slate-700 shadow-xl rounded-full px-2 py-1 flex flex-row items-center gap-1 pointer-events-auto transform transition-transform">
                    <Button variant="ghost" size="icon" title="Move Up" onClick={() => moveStep(index, 'up')} disabled={isFirst} className="h-7 w-7 rounded-full text-slate-400 hover:text-white hover:bg-slate-700 disabled:opacity-30 disabled:hover:bg-transparent"><ArrowUp size={14} /></Button>
                    <div className="h-4 w-px bg-slate-700 my-auto mx-0.5" />
                    <Button variant="ghost" size="icon" title="Move Down" onClick={() => moveStep(index, 'down')} disabled={isLast} className="h-7 w-7 rounded-full text-slate-400 hover:text-white hover:bg-slate-700 disabled:opacity-30 disabled:hover:bg-transparent"><ArrowDown size={14} /></Button>
                    <div className="h-4 w-px bg-slate-700 my-auto mx-0.5" />
                    <Button variant="ghost" size="icon" title="Insert Step Below" onClick={() => insertStep(index)} className="h-7 w-7 rounded-full text-slate-400 hover:text-emerald-400 hover:bg-slate-700"><CheckSquare size={14} className="rotate-180" /></Button>
                    <div className="h-4 w-px bg-slate-700 my-auto mx-0.5" />
                    <Button variant="ghost" size="icon" title="Delete Step" onClick={() => removeStep(step.id)} className="h-7 w-7 rounded-full text-slate-400 hover:text-rose-400 hover:bg-slate-700"><Trash2 size={13} /></Button>
                </div>
            </div>

            <ElementPickerDialog
                open={showPicker}
                onOpenChange={setShowPicker}
                initialUrl={pickerUrl}
                onPick={(selector) => updateStep(step.id, 'selector', selector)}
            />
        </div>
    );
};

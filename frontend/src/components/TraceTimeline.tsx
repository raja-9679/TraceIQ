import { useEffect, useState } from 'react';
import JSZip from 'jszip';
import { XCircle, Clock } from 'lucide-react';
import { Checkbox } from "@/components/ui/checkbox";

interface TraceTimelineProps {
    url: string;
    executionLog?: any[];
}

interface TraceAction {
    id: string;
    apiName: string;
    params?: any;
    startTime: number;
    endTime: number;
    error: any;
    status: 'passed' | 'failed' | 'timedOut';
}

export function TraceTimeline({ url, executionLog: _executionLog }: TraceTimelineProps) {
    const [actions, setActions] = useState<TraceAction[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [showAllEvents, setShowAllEvents] = useState(false); // Default to filtered view

    useEffect(() => {
        const loadTrace = async () => {
            try {
                setLoading(true);
                const response = await fetch(url);
                if (!response.ok) throw new Error('Failed to fetch trace file');

                const blob = await response.blob();
                const zip = await JSZip.loadAsync(blob);

                const traceFile = zip.file('trace.trace');
                if (!traceFile) throw new Error('Invalid trace file: trace.trace not found');

                const content = await traceFile.async('string');
                const lines = content.split('\n');

                const parsedActions: TraceAction[] = [];
                const actionMap = new Map<string, Partial<TraceAction>>();

                for (const line of lines) {
                    if (!line.trim()) continue;
                    try {
                        const event = JSON.parse(line);

                        // Handle 'before' event (Action Start)
                        if (event.type === 'before') {
                            const callId = event.callId;
                            if (callId) {
                                actionMap.set(callId, {
                                    id: callId,
                                    apiName: event.apiName || event.method || 'Unknown Action',
                                    params: event.params,
                                    startTime: event.startTime,
                                    status: 'passed' // Default, updated on failure
                                });
                            }
                        }

                        // Handle 'after' event (Action End)
                        else if (event.type === 'after') {
                            const callId = event.callId;
                            const action = actionMap.get(callId);
                            if (action) {
                                action.endTime = event.endTime;
                                if (event.error) {
                                    action.error = event.error;
                                    action.status = 'failed';
                                }

                                // We push all actions here and filter at render time
                                parsedActions.push(action as TraceAction);
                                actionMap.delete(callId);
                            }
                        }
                    } catch (e) {
                        console.warn('Failed to parse line', e);
                    }
                }

                // Sort by start time just in case
                parsedActions.sort((a, b) => a.startTime - b.startTime);
                setActions(parsedActions);

            } catch (err: any) {
                setError(err.message);
            } finally {
                setLoading(false);
            }
        };

        if (url) {
            loadTrace();
        }
    }, [url]);

    if (loading) return (
        <div className="flex flex-col items-center justify-center p-12 text-gray-400 bg-gray-50/50 rounded-xl border border-dashed border-gray-200">
            <div className="w-8 h-8 rounded-full border-2 border-primary border-t-transparent animate-spin mb-4" />
            <p className="text-sm font-medium">Parsing trace data...</p>
        </div>
    );

    if (error) return (
        <div className="p-6 bg-rose-50 border border-rose-200 rounded-xl text-rose-700 shadow-sm flex items-center gap-3">
            <XCircle size={20} className="text-rose-600" />
            <span className="font-medium">Error loading trace: {error}</span>
        </div>
    );

    if (actions.length === 0) {
        return (
            <div className="flex flex-col items-center justify-center p-12 text-gray-400 bg-gray-50/50 rounded-xl border border-dashed border-gray-200">
                <p className="text-sm italic">No relevant actions found in trace.</p>
            </div>
        );
    }

    // Filter actions based on toggle
    const filteredActions = actions.filter(action => {
        if (!action.apiName) return false;

        const lowerApiName = action.apiName.toLowerCase();

        // Hide extremely noisy internal events (like route.continue) unless they physically failed
        if (action.status !== 'failed' && (lowerApiName.includes('continue') || lowerApiName.includes('fulfill') || lowerApiName.includes('route.'))) {
            return false;
        }

        if (showAllEvents) return true;

        const allowedActions = [
            'goto',
            'click',
            'fill',
            'check',
            'selectOption',
            'press',
            'waitForURL',
            'expect',
            'assert',
            'screenshot',
            'hover',
            'scrollIntoViewIfNeeded',
            'waitForTimeout',
            'waitForSelector',
            'textContent',
            'locator',
            'evaluate'
        ];

        return allowedActions.some(allowed => lowerApiName.includes(allowed.toLowerCase()));
    });

    return (
        <div className="bg-white rounded-xl">
            <div className="flex justify-between items-center mb-6">
                <span className="font-bold text-sm text-gray-800 uppercase tracking-wider flex items-center gap-2">
                    <span className="bg-primary/10 text-primary px-2 py-0.5 rounded-full text-xs">
                        {filteredActions.length} Steps
                    </span>
                    Execution Flow
                </span>
                <label className="flex items-center gap-2 text-xs font-semibold text-gray-500 cursor-pointer hover:text-gray-900 transition-colors uppercase tracking-wider">
                    <Checkbox
                        checked={showAllEvents}
                        onCheckedChange={(checked) => setShowAllEvents(checked as boolean)}
                        className="data-[state=checked]:bg-primary data-[state=checked]:border-primary"
                    />
                    Verbose Mode
                </label>
            </div>

            <div className="relative pl-4 space-y-6 before:absolute before:inset-0 before:ml-[23px] before:-translate-x-px md:before:mx-auto md:before:translate-x-0 before:h-full before:w-0.5 before:bg-gradient-to-b before:from-transparent before:via-gray-200 before:to-transparent">
                {filteredActions.length > 0 ? (
                    filteredActions.map((action, index) => (
                        <TraceActionItem key={index} action={action} />
                    ))
                ) : (
                    <div className="p-8 text-center text-gray-500 italic border border-dashed rounded-xl border-gray-200 bg-gray-50/50">
                        No steps to display. Try checking "Verbose Mode".
                    </div>
                )}
            </div>
        </div>
    );
}

function getActionIcon(apiName: string, status: string) {
    const lowerName = apiName.toLowerCase();

    if (lowerName.includes('goto') || lowerName.includes('navigate')) return <div className={`w-2.5 h-2.5 rounded-full bg-blue-500 shadow-[0_0_0_4px_rgba(59,130,246,0.1)]`} />;
    if (lowerName.includes('click') || lowerName.includes('check')) return <div className={`w-2.5 h-2.5 rounded-full bg-purple-500 shadow-[0_0_0_4px_rgba(168,85,247,0.1)]`} />;
    if (lowerName.includes('fill') || lowerName.includes('type')) return <div className={`w-2.5 h-2.5 rounded-full bg-amber-500 shadow-[0_0_0_4px_rgba(245,158,11,0.1)]`} />;
    if (lowerName.includes('expect') || lowerName.includes('assert')) return <div className={`w-2.5 h-2.5 rounded-full bg-emerald-500 shadow-[0_0_0_4px_rgba(16,185,129,0.1)]`} />;
    if (lowerName.includes('screenshot')) return <div className={`w-2.5 h-2.5 rounded-full bg-indigo-500 shadow-[0_0_0_4px_rgba(99,102,241,0.1)]`} />;
    if (lowerName.includes('locator') || lowerName.includes('selector')) return <div className={`w-2.5 h-2.5 rounded-full bg-teal-500 shadow-[0_0_0_4px_rgba(20,184,166,0.1)]`} />;
    if (lowerName.includes('evaluate')) return <div className={`w-2.5 h-2.5 rounded-full bg-orange-500 shadow-[0_0_0_4px_rgba(249,115,22,0.1)]`} />;

    // Default fallback
    if (status === 'failed') return <XCircle size={14} className="text-rose-500 bg-white" />;
    return <div className={`w-2 h-2 rounded-full bg-gray-300 ring-4 ring-white`} />;
}

function TraceActionItem({ action }: { action: TraceAction }) {
    const duration = action.endTime - action.startTime;

    // Format apiName nicely (e.g. locator.click -> click)
    const displayActionName = action.apiName.includes('.') ? action.apiName.split('.').pop() : action.apiName;
    const parentEntity = action.apiName.includes('.') ? action.apiName.split('.')[0] : '';

    // Extract details based on action type
    let details = null;
    const lowerName = action.apiName.toLowerCase();
    const params = action.params || {};

    if (lowerName.includes('goto')) {
        details = <span className="text-blue-600 truncate max-w-sm block font-mono bg-blue-50 px-2 py-1 rounded" title={params.url}>{params.url}</span>;
    } else if (lowerName.includes('click') || lowerName.includes('check') || lowerName.includes('hover') || lowerName.includes('fill') || lowerName.includes('locator')) {
        details = (
            <div className="flex flex-col gap-1.5 mt-1.5">
                {params.selector && (
                    <div className="flex items-center gap-1.5">
                        <span className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">Target</span>
                        <span className="text-purple-700 font-mono text-xs bg-purple-50/80 px-2 py-0.5 rounded border border-purple-100" title={params.selector}>{params.selector}</span>
                    </div>
                )}
                {params.value && (
                    <div className="flex items-center gap-1.5">
                        <span className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">Input</span>
                        <span className="text-gray-700 font-mono text-xs bg-gray-50 px-2 py-0.5 rounded border border-gray-100 font-semibold max-w-sm truncate whitespace-nowrap overflow-hidden">"{params.value}"</span>
                    </div>
                )}
            </div>
        );
    } else if (lowerName.includes('press')) {
        details = <span className="font-mono text-xs bg-gray-100 px-2 py-1 rounded border border-gray-200 shadow-sm">{params.key}</span>;
    } else if (lowerName.includes('waitforurl')) {
        details = <span className="text-blue-600 truncate max-w-sm block font-mono bg-blue-50 px-2 py-1 rounded border border-blue-100">{params.url}</span>;
    } else if (lowerName.includes('waitfortimeout')) {
        const timeout = params.waitTimeout || params.millis || params.timeout;
        if (timeout) {
            details = <span className="text-gray-600 text-xs font-mono bg-gray-50 px-2 py-1 rounded border border-gray-100 inline-flex items-center gap-1.5"><Clock size={12} className="text-gray-400" /> {timeout}ms</span>;
        }
    } else if (lowerName.includes('expect') || lowerName.includes('assert')) {
        details = (
            <div className="flex flex-col gap-1.5 mt-1.5">
                {params.expression && (
                    <span className="text-emerald-700 font-mono text-xs bg-emerald-50/80 px-2 py-0.5 rounded border border-emerald-100">{params.expression}</span>
                )}
            </div>
        );
    } else if (lowerName.includes('evaluate')) {
        const expression = params.expression || params.pageFunction || params.arg;
        if (expression) {
            details = (
                <div className="flex flex-col gap-1.5 mt-1.5">
                    <span className="text-orange-800 font-mono text-xs bg-orange-50 px-2 py-1.5 rounded border border-orange-100 max-h-32 overflow-y-auto whitespace-pre-wrap flex items-start gap-2">
                        <span className="text-[10px] font-bold text-orange-400 uppercase tracking-wider shrink-0 mt-0.5">Eval</span>
                        {expression}
                    </span>
                </div>
            );
        }
    }

    return (
        <div className="relative flex items-start gap-6 group pl-2 py-2">
            {/* Timeline Line Connector handled by container before pseudo element */}

            {/* Timeline Dot */}
            <div className="absolute left-[5px] mt-1.5 z-10 w-4 h-4 rounded-full bg-white flex items-center justify-center border-2 border-white shadow-sm transition-transform group-hover:scale-110">
                {getActionIcon(action.apiName, action.status)}
            </div>

            {/* Content Box */}
            <div className={`flex-1 overflow-hidden transition-all duration-300 ml-5 ${action.status === 'failed' ? 'bg-rose-50 border-rose-200 shadow-sm' : 'hover:bg-gray-50/80 bg-white border-transparent hover:border-gray-200'} border rounded-xl p-3 pr-4`}>
                <div className="flex justify-between items-start gap-4">
                    <div className="flex flex-col min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                            <h4 className={`font-bold text-sm truncate ${action.status === 'failed' ? 'text-rose-800' : 'text-gray-800'}`} title={action.apiName}>
                                {parentEntity && <span className="text-gray-400 font-normal mr-1">{parentEntity}.</span>}
                                {displayActionName}
                            </h4>
                            {action.status === 'failed' && (
                                <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-rose-100 text-rose-700 uppercase tracking-widest">Failed</span>
                            )}
                        </div>
                        {details && <div className="mt-2.5 mb-1">{details}</div>}
                    </div>

                    <div className="text-xs font-mono text-gray-400 flex items-center gap-1.5 shrink-0 bg-gray-50 px-2 py-1 rounded border border-gray-100 shadow-sm">
                        <Clock size={12} className="text-gray-300" />
                        {duration > 0 ? `${duration.toFixed(0)} ms` : '<1 ms'}
                    </div>
                </div>

                {action.error && (
                    <div className="mt-3 text-xs bg-white text-rose-700 p-3 rounded-lg border border-rose-100 font-mono whitespace-pre-wrap shadow-inner overflow-x-auto max-h-48 custom-scrollbar">
                        {action.error.message || JSON.stringify(action.error)}
                    </div>
                )}
            </div>
        </div>
    );
}

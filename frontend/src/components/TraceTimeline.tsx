import { useEffect, useState } from 'react';
import JSZip from 'jszip';
import { CheckCircle, XCircle, Clock } from 'lucide-react';

interface TraceTimelineProps {
    url: string;
    executionLog?: any[];
}

interface TraceAction {
    id: string;
    apiName: string;
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

    if (loading) return <div className="p-4 text-gray-500">Loading trace data...</div>;
    if (error) return <div className="p-4 text-red-500">Error loading trace: {error}</div>;

    if (actions.length === 0) {
        return <div className="p-4 text-gray-500">No relevant actions found in trace.</div>;
    }

    // Filter actions based on toggle
    const filteredActions = actions.filter(action => {
        if (showAllEvents) return true;

        const allowedActions = [
            'page.goto',
            'locator.click',
            'locator.fill',
            'locator.check',
            'locator.selectOption',
            'keyboard.press',
            'page.waitForURL',
            'expect',
            'assert'
        ];

        // Check if apiName exists and matches allowed list or starts with expect/assert
        if (!action.apiName) return false;

        return allowedActions.includes(action.apiName) ||
            action.apiName.startsWith('expect') ||
            action.apiName.startsWith('assert');
    });

    // Group actions by execution log if available
    const ungroupedActions: TraceAction[] = [];

    // Note: Trace timestamps are monotonic (microseconds), executionLog is wall time (ms).
    // They won't match directly. For now, we'll just show ungrouped if we can't align them.

    filteredActions.forEach(action => {
        ungroupedActions.push(action);
    });

    return (
        <div className="border border-gray-200 rounded-lg overflow-hidden bg-white">
            <div className="bg-gray-50 border-b border-gray-200 px-4 py-3 flex justify-between items-center">
                <span className="font-medium text-sm text-gray-700">Execution Steps ({filteredActions.length})</span>
                <label className="flex items-center gap-2 text-xs text-gray-600 cursor-pointer">
                    <input
                        type="checkbox"
                        checked={showAllEvents}
                        onChange={(e) => setShowAllEvents(e.target.checked)}
                        className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                    />
                    Show System Events
                </label>
            </div>
            <div className="divide-y divide-gray-100 max-h-[600px] overflow-y-auto">
                {ungroupedActions.length > 0 ? (
                    ungroupedActions.map((action, index) => (
                        <TraceActionItem key={index} action={action} />
                    ))
                ) : (
                    <div className="p-4 text-center text-gray-500 italic">
                        No steps to display. Try checking "Show System Events".
                    </div>
                )}
            </div>
        </div>
    );
}

function TraceActionItem({ action }: { action: TraceAction }) {
    const duration = action.endTime - action.startTime;
    return (
        <div className="p-3 hover:bg-gray-50 flex items-start gap-3">
            <div className="mt-1">
                {action.status === 'failed' ? (
                    <XCircle size={16} className="text-red-500" />
                ) : (
                    <CheckCircle size={16} className="text-green-500" />
                )}
            </div>
            <div className="flex-1 min-w-0">
                <div className="flex justify-between items-center mb-1">
                    <p className="font-mono text-sm font-medium text-gray-900 truncate">
                        {action.apiName}
                    </p>
                    <span className="text-xs text-gray-500 flex items-center gap-1">
                        <Clock size={12} />
                        {duration.toFixed(0)}ms
                    </span>
                </div>
                {action.error && (
                    <div className="mt-2 text-xs bg-red-50 text-red-700 p-2 rounded border border-red-100 font-mono whitespace-pre-wrap">
                        {action.error.message || JSON.stringify(action.error)}
                    </div>
                )}
            </div>
        </div>
    );
}

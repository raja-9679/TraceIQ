import { useCallback, useEffect, useRef, useState } from 'react';
import { CircleDot, Download, ExternalLink, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
    askRecorder, parseRecorderMessage, startUrl, RECORDER_STORE_URL, RECORDER_ZIP_URL, type RecordedStep,
} from '@/lib/recorder';

// "Record" in the test editor: starts the browser extension in a new tab and
// adds what it recorded to the open test.
//
// Always mounted (only the dialog is hidden when closed), because the steps
// arrive while the user is in another tab and may arrive after they closed
// the dialog.

type Status = 'checking' | 'missing' | 'ready' | 'approval' | 'recording';

const LAST_URL_KEY = 'tiq_recorder_url';
const readLastUrl = () => { try { return localStorage.getItem(LAST_URL_KEY) ?? ''; } catch { return ''; } };

export function RecorderDialog({ open, onOpenChange, onSteps }: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    onSteps: (steps: RecordedStep[]) => void;
}) {
    const [status, setStatus] = useState<Status>('checking');
    const [pending, setPending] = useState(0);
    const [url, setUrl] = useState(readLastUrl);
    const [problem, setProblem] = useState('');
    const answered = useRef(false);
    const onStepsRef = useRef(onSteps);
    useEffect(() => { onStepsRef.current = onSteps; }, [onSteps]);

    useEffect(() => {
        const onMessage = (ev: MessageEvent) => {
            const msg = parseRecorderMessage(ev);
            if (!msg) return;
            switch (msg.type) {
                case 'pong':
                    answered.current = true;
                    setPending(msg.pending);
                    setStatus(msg.recording ? 'recording' : 'ready');
                    break;
                case 'needs-approval':
                    setStatus('approval');
                    break;
                case 'starting':
                case 'started':
                    setStatus('recording');
                    break;
                case 'denied':
                    setStatus('ready');
                    setProblem('The recorder was not allowed to record for this site. Press Start recording again and choose Allow.');
                    break;
                case 'steps':
                    // Tell the extension first: it keeps the steps until it hears this.
                    askRecorder('received');
                    setPending(0);
                    setStatus('ready');
                    onStepsRef.current(msg.steps);
                    onOpenChange(false);
                    break;
                case 'error':
                    setStatus((s) => (s === 'approval' || s === 'recording' ? 'ready' : s));
                    setProblem(msg.error);
                    break;
                default:
                    break;
            }
        };
        window.addEventListener('message', onMessage);
        return () => window.removeEventListener('message', onMessage);
    }, [onOpenChange]);

    // No answer within a moment means the extension is not installed (or was
    // installed after this page loaded and has not reached it yet).
    const timer = useRef<number | undefined>(undefined);
    const ping = useCallback(() => {
        answered.current = false;
        window.clearTimeout(timer.current);
        askRecorder('ping');
        timer.current = window.setTimeout(() => { if (!answered.current) setStatus('missing'); }, 800);
    }, []);
    const check = () => {
        setProblem('');
        setStatus('checking');
        ping();
    };

    useEffect(() => {
        if (open) ping();
        return () => window.clearTimeout(timer.current);
    }, [open, ping]);

    // Closing forgets "not installed" and any message, so the next opening
    // looks again; a recording in progress is kept.
    const handleOpenChange = (next: boolean) => {
        if (!next) {
            setProblem('');
            setStatus((s) => (s === 'missing' ? 'checking' : s));
        }
        onOpenChange(next);
    };

    const start = () => {
        const target = startUrl(url);
        if (!target) {
            setProblem('Enter the address of the page to start on, for example https://staging.example.com/login.');
            return;
        }
        setProblem('');
        try { localStorage.setItem(LAST_URL_KEY, target); } catch { /* a convenience only */ }
        askRecorder('start', { url: target });
    };

    return (
        <Dialog open={open} onOpenChange={handleOpenChange}>
            <DialogContent className="max-h-[90vh] overflow-y-auto rounded-3xl border-border bg-white sm:max-w-[560px] sm:rounded-3xl">
                <DialogHeader>
                    <DialogTitle className="text-xl font-bold tracking-[-0.02em] text-ink">Record steps</DialogTitle>
                    <DialogDescription className="text-sm text-ink-soft">
                        Use your site in a new tab while the TraceIQ Recorder writes down each click and entry as a step.
                        The steps are added to this test for you to review; nothing is saved until you save the test.
                    </DialogDescription>
                </DialogHeader>

                {status === 'checking' && (
                    <p role="status" className="text-sm text-ink-soft">Looking for the recorder in this browser…</p>
                )}

                {status === 'missing' && (
                    <div className="flex flex-col gap-3">
                        <p className="text-sm font-semibold text-ink">The recorder is not installed in this browser yet.</p>
                        {RECORDER_STORE_URL ? (
                            <ol className="flex list-decimal flex-col gap-2 pl-5 text-sm text-ink">
                                <li>
                                    <a href={RECORDER_STORE_URL} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-semibold underline">
                                        Add TraceIQ Recorder to Chrome <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                                    </a>
                                </li>
                                <li>Come back to this tab and press <strong>Check again</strong>.</li>
                            </ol>
                        ) : (
                            <ol className="flex list-decimal flex-col gap-2 pl-5 text-sm text-ink">
                                <li>
                                    <a href={RECORDER_ZIP_URL} download className="inline-flex items-center gap-1 font-semibold underline">
                                        <Download className="h-3.5 w-3.5" aria-hidden="true" /> Download the recorder
                                    </a>{' '}
                                    and unzip it.
                                </li>
                                <li>In Chrome or Edge, open <span className="font-mono text-[13px]">chrome://extensions</span> and turn on <strong>Developer mode</strong>.</li>
                                <li>Press <strong>Load unpacked</strong> and choose the unzipped folder.</li>
                                <li>Come back to this tab and press <strong>Check again</strong>.</li>
                            </ol>
                        )}
                        <p className="text-xs text-ink-soft">Works in Chrome, Edge and other Chromium browsers on a computer.</p>
                        <div>
                            <Button type="button" variant="outline" onClick={check}>
                                <RefreshCw className="h-4 w-4" /> Check again
                            </Button>
                        </div>
                    </div>
                )}

                {status === 'ready' && (
                    <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); start(); }}>
                        {pending > 0 && (
                            <div className="flex flex-wrap items-center justify-between gap-2 rounded-2xl border border-border bg-background p-3">
                                <p className="text-sm text-ink">
                                    {pending} recorded step{pending === 1 ? ' is' : 's are'} waiting from an earlier recording.
                                </p>
                                <Button type="button" variant="outline" size="sm" onClick={() => askRecorder('collect')}>Add them to this test</Button>
                            </div>
                        )}
                        <div className="flex flex-col gap-1.5">
                            <Label htmlFor="recorder-url" className="text-sm font-semibold text-ink">Page to start on</Label>
                            <Input id="recorder-url" value={url} onChange={(e) => setUrl(e.target.value)}
                                placeholder="https://staging.example.com/login" inputMode="url" autoComplete="url" />
                            <p className="text-xs text-ink-soft">It opens in a new tab with a small recorder bar at the bottom right.</p>
                        </div>
                        <ul className="flex list-disc flex-col gap-1 pl-5 text-sm text-ink-soft">
                            <li>Use <strong className="text-ink">Add check</strong> on the bar to make the test confirm a text, an element, the address or the title.</li>
                            <li>Passwords are never recorded. The step gets a placeholder such as <span className="font-mono text-[13px]">{'{{secret.PASSWORD}}'}</span>.</li>
                            <li>Press <strong className="text-ink">Finish</strong> on the bar to come back here with the steps.</li>
                        </ul>
                        <div>
                            <Button type="submit"><CircleDot className="h-4 w-4" /> Start recording</Button>
                        </div>
                    </form>
                )}

                {status === 'approval' && (
                    <div className="flex flex-col gap-3">
                        <p role="status" className="text-sm text-ink">
                            The recorder opened a small window asking whether this site may use it. Choose <strong>Allow and start recording</strong> there.
                            You are asked only once.
                        </p>
                        <div>
                            <Button type="button" variant="outline" onClick={check}>I closed that window</Button>
                        </div>
                    </div>
                )}

                {status === 'recording' && (
                    <div className="flex flex-col gap-3">
                        <p role="status" className="text-sm text-ink">
                            Recording in the other tab. When you are done, press <strong>Finish</strong> on the recorder bar there
                            and the steps appear in this test.
                        </p>
                        <div>
                            <Button type="button" variant="outline" onClick={() => { askRecorder('cancel'); setStatus('ready'); }}>
                                Discard this recording
                            </Button>
                        </div>
                    </div>
                )}

                {problem && <p role="alert" className="rounded-2xl border border-border bg-background p-3 text-sm text-ink">{problem}</p>}
            </DialogContent>
        </Dialog>
    );
}

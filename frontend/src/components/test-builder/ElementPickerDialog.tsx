import { useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Crosshair, Loader2 } from 'lucide-react';
import { api } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { toast } from 'sonner';

interface InspectedElement {
    selector: string;
    tag: string;
    text: string;
    x: number;
    y: number;
    width: number;
    height: number;
}

interface InspectResult {
    url: string;
    screenshot: string; // base64 png
    width: number;
    height: number;
    elements: InspectedElement[];
}

interface ElementPickerDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    initialUrl?: string;
    onPick: (selector: string) => void;
}

/** Element picker: renders the target page server-side (headless Chromium via
 *  POST /api/inspect/page), shows the screenshot, and lets the user click an
 *  element to capture its selector. Real rendered DOM — SPAs work, no
 *  cross-origin iframe issues. */
export function ElementPickerDialog({ open, onOpenChange, initialUrl, onPick }: ElementPickerDialogProps) {
    const [url, setUrl] = useState(initialUrl || '');
    const [result, setResult] = useState<InspectResult | null>(null);
    const [hovered, setHovered] = useState<InspectedElement | null>(null);
    const imgRef = useRef<HTMLImageElement>(null);

    const inspectMutation = useMutation({
        mutationFn: async () => {
            const response = await api.post('/inspect/page', { url: url.trim() });
            return response.data as InspectResult;
        },
        onSuccess: (data) => {
            setResult(data);
            setHovered(null);
        },
        onError: (err: Error & { response?: { data?: { detail?: string } } }) => {
            toast.error('Could not load page', {
                description: err.response?.data?.detail || err.message,
            });
        },
    });

    /** Map a mouse event to page-pixel coordinates, then find the
     *  smallest-area element containing that point. */
    const elementAt = (e: React.MouseEvent): InspectedElement | null => {
        if (!result || !imgRef.current) return null;
        const rect = imgRef.current.getBoundingClientRect();
        const scale = result.width / rect.width;
        const px = (e.clientX - rect.left) * scale;
        const py = (e.clientY - rect.top) * scale;
        let best: InspectedElement | null = null;
        let bestArea = Infinity;
        for (const el of result.elements) {
            if (px >= el.x && px <= el.x + el.width && py >= el.y && py <= el.y + el.height) {
                const area = el.width * el.height;
                if (area < bestArea) {
                    best = el;
                    bestArea = area;
                }
            }
        }
        return best;
    };

    // Highlight box in rendered (scaled) coordinates
    const highlightStyle = (): React.CSSProperties | undefined => {
        if (!hovered || !result || !imgRef.current) return undefined;
        const scale = imgRef.current.getBoundingClientRect().width / result.width;
        return {
            position: 'absolute',
            left: hovered.x * scale,
            top: hovered.y * scale,
            width: hovered.width * scale,
            height: hovered.height * scale,
            border: '2px solid #6366f1',
            background: 'rgba(99, 102, 241, 0.15)',
            borderRadius: 3,
            pointerEvents: 'none',
        };
    };

    const handlePick = (e: React.MouseEvent) => {
        const el = elementAt(e);
        if (!el || !el.selector) return;
        onPick(el.selector);
        toast.success('Selector captured', { description: el.selector });
        onOpenChange(false);
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-4xl rounded-2xl max-h-[90vh] flex flex-col">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <Crosshair className="h-5 w-5 text-indigo-500" /> Pick Element from Page
                    </DialogTitle>
                    <DialogDescription>
                        Load the page, then click the element you want — its selector is filled into the step.
                    </DialogDescription>
                </DialogHeader>

                <div className="flex gap-2">
                    <Input
                        value={url}
                        onChange={(e) => setUrl(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter' && url.trim()) inspectMutation.mutate(); }}
                        placeholder="https://your-app.example.com/login"
                        className="rounded-xl font-mono flex-1"
                    />
                    <Button
                        onClick={() => inspectMutation.mutate()}
                        disabled={!url.trim() || inspectMutation.isPending}
                        className="rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white shrink-0"
                    >
                        {inspectMutation.isPending
                            ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Rendering…</>
                            : 'Load Page'}
                    </Button>
                </div>

                {hovered && (
                    <div className="text-xs font-mono bg-slate-900 text-emerald-300 rounded-lg px-3 py-2 truncate">
                        {hovered.selector}
                        {hovered.text ? <span className="text-slate-400">  — “{hovered.text}”</span> : null}
                    </div>
                )}

                <div className="overflow-auto overscroll-contain rounded-xl border border-slate-200 bg-slate-50 min-h-[200px] max-h-[62vh]">
                    {result ? (
                        <div className="relative cursor-crosshair" style={{ lineHeight: 0 }}>
                            <img
                                ref={imgRef}
                                src={`data:image/png;base64,${result.screenshot}`}
                                alt="Rendered page"
                                className="w-full select-none"
                                draggable={false}
                                onMouseMove={(e) => setHovered(elementAt(e))}
                                onMouseLeave={() => setHovered(null)}
                                onClick={handlePick}
                            />
                            {hovered && <div style={highlightStyle()} />}
                        </div>
                    ) : (
                        <div className="h-[300px] flex items-center justify-center text-slate-400 text-sm">
                            {inspectMutation.isPending
                                ? 'Rendering page in headless browser…'
                                : 'Enter a URL and press Load Page'}
                        </div>
                    )}
                </div>
            </DialogContent>
        </Dialog>
    );
}
